from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import math
import multiprocessing as mp
import os
import re
import time
import unicodedata
import traceback
from typing import Iterable
from uuid import UUID

import pdfplumber
from pdfminer.layout import LTChar, LTContainer, LTPage
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfinterp import PDFPageInterpreter, LITERAL_FORM
from pdfminer.pdffont import PDFType3Font
from pdfminer.pdftypes import dict_value, list_value, resolve1, stream_value
from pdfminer.psparser import literal_name
from pdfminer.utils import apply_matrix_pt
from pdfplumber.page import PDFPageAggregatorWithMarkedContent

from .models import (
    CanonicalPageGeometry,
    ElementMetric,
    MeasurementConfidence,
    NonTextContentState,
    NormalizedBBox,
    PageAnalysisModel,
    PresentationModel,
    TextCoverage,
    TextVisibility,
)

MAX_PAGES = 75
MAX_RAW_CHARS = 250_000
MAX_RUNS = 20_000
PREVIEW_LIMIT = 120
SUPPORTED_ROTATION_EPSILON_DEG = 2.0
_DEDUPE_TOL = 0.0005
_ALPHA_EPS = 1e-6
MAX_SERIALIZED_MODEL_BYTES = 64 * 1024 * 1024


class ParseFailure(Exception):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


class RenderModeAggregator(PDFPageAggregatorWithMarkedContent):
    """Attach rendering-state facts that pdfplumber does not expose per LTChar."""

    current_render_mode: int | None = None
    farseat_fill_alpha: float = 1.0
    farseat_stroke_alpha: float = 1.0
    farseat_complex_transparency: bool = False
    farseat_clip_rect: tuple[float, float, float, float] | None = None
    farseat_clip_complex: bool = False
    farseat_clip_empty: bool = False

    def render_string(self, textstate, seq, ncs, graphicstate):  # type: ignore[override]
        previous = self.current_render_mode
        self.current_render_mode = int(textstate.render)
        try:
            return super().render_string(textstate, seq, ncs, graphicstate)
        finally:
            self.current_render_mode = previous

    def render_char(self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate):  # type: ignore[override]
        adv = super().render_char(matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate)
        if self.cur_item._objs:
            obj = self.cur_item._objs[-1]
            if isinstance(obj, LTChar):
                obj.farseat_render_mode = self.current_render_mode  # type: ignore[attr-defined]
                obj.farseat_fill_alpha = float(self.farseat_fill_alpha)  # type: ignore[attr-defined]
                obj.farseat_stroke_alpha = float(self.farseat_stroke_alpha)  # type: ignore[attr-defined]
                obj.farseat_complex_transparency = bool(self.farseat_complex_transparency)  # type: ignore[attr-defined]
                obj.farseat_type3 = isinstance(font, PDFType3Font)  # type: ignore[attr-defined]
                obj.farseat_clip_rect = self.farseat_clip_rect  # type: ignore[attr-defined]
                obj.farseat_clip_complex = bool(self.farseat_clip_complex)  # type: ignore[attr-defined]
                obj.farseat_clip_empty = bool(self.farseat_clip_empty)  # type: ignore[attr-defined]
        return adv


class VisibilityTrackingInterpreter(PDFPageInterpreter):
    """Track visibility-relevant PDF graphics state conservatively.

    FarSeat is not a full PDF compositor. It proves only the subset needed for safe
    structural text measurement: alpha/soft masks and simple axis-aligned clipping
    rectangles. If clipping becomes geometrically complex, subsequent text is marked
    uncertain instead of being allowed to produce a plausible PASS.
    """

    def init_resources(self, resources):  # type: ignore[override]
        super().init_resources(resources)
        self._farseat_extgstates: dict[str, dict] = {}
        try:
            for key, value in dict_value(resources).items():
                if str(key) != "ExtGState":
                    continue
                for name, spec in dict_value(value).items():
                    self._farseat_extgstates[literal_name(name)] = dict_value(resolve1(spec))
        except Exception:
            self._farseat_extgstates = {}

    def init_state(self, ctm):  # type: ignore[override]
        super().init_state(ctm)
        self._farseat_fill_alpha = 1.0
        self._farseat_stroke_alpha = 1.0
        self._farseat_complex_transparency = False
        self._farseat_clip_rect: tuple[float, float, float, float] | None = None
        self._farseat_clip_complex = False
        self._farseat_clip_empty = False
        self._farseat_pending_clip: tuple[str, tuple[float, float, float, float] | None] | None = None
        self._farseat_text_clip_pending = False
        self._farseat_state_stack: list[
            tuple[float, float, bool, tuple[float, float, float, float] | None, bool, bool]
        ] = []

        inherited = getattr(self, "_farseat_inherited_state", None)
        if inherited is not None:
            (
                self._farseat_fill_alpha,
                self._farseat_stroke_alpha,
                self._farseat_complex_transparency,
                self._farseat_clip_rect,
                self._farseat_clip_complex,
                self._farseat_clip_empty,
            ) = inherited
        if getattr(self, "_farseat_initial_form_bbox_uncertain", False):
            self._farseat_clip_complex = True
            self._farseat_clip_rect = None
            self._farseat_clip_empty = False
        else:
            form_bbox = getattr(self, "_farseat_initial_form_bbox", None)
            if form_bbox is not None:
                rect = self._rect_from_bbox(form_bbox, ctm)
                if rect is None:
                    self._farseat_clip_complex = True
                    self._farseat_clip_rect = None
                    self._farseat_clip_empty = False
                else:
                    self._intersect_proven_clip_rect(rect)
        self._sync_visibility_state()

    def _sync_visibility_state(self) -> None:
        device = self.device
        if isinstance(device, RenderModeAggregator):
            device.farseat_fill_alpha = self._farseat_fill_alpha
            device.farseat_stroke_alpha = self._farseat_stroke_alpha
            device.farseat_complex_transparency = self._farseat_complex_transparency
            device.farseat_clip_rect = self._farseat_clip_rect
            device.farseat_clip_complex = self._farseat_clip_complex
            device.farseat_clip_empty = self._farseat_clip_empty

    @staticmethod
    def _alpha(value) -> float | None:
        try:
            result = float(resolve1(value))
        except Exception:
            return None
        return result if math.isfinite(result) and 0.0 <= result <= 1.0 else None

    @staticmethod
    def _rect_from_path(path, ctm) -> tuple[float, float, float, float] | None:
        """Return an exact axis-aligned device-space rectangle, else None.

        A bounding box of an arbitrary path is *not* enough to prove visibility, so
        curves, multiple subpaths, skewed quadrilaterals, and malformed paths are
        intentionally treated as complex clipping.
        """
        points: list[tuple[float, float]] = []
        saw_close = False
        for segment in path:
            op = segment[0]
            if op in ("m", "l") and len(segment) >= 3:
                try:
                    point = apply_matrix_pt(ctm, (float(segment[1]), float(segment[2])))
                except Exception:
                    return None
                if not all(math.isfinite(v) for v in point):
                    return None
                points.append(point)
            elif op == "h":
                saw_close = True
            else:
                return None
        if len(points) != 4 or not saw_close:
            return None
        x0, x1 = min(x for x, _ in points), max(x for x, _ in points)
        y0, y1 = min(y for _, y in points), max(y for _, y in points)
        scale = max(abs(x0), abs(x1), abs(y0), abs(y1), 1.0)
        eps = scale * 1e-9
        if x1 - x0 <= eps or y1 - y0 <= eps:
            return None
        corners = {(x0, y0), (x0, y1), (x1, y0), (x1, y1)}
        matched: set[tuple[float, float]] = set()
        for px, py in points:
            found = None
            for cx, cy in corners:
                if abs(px - cx) <= eps and abs(py - cy) <= eps:
                    found = (cx, cy)
                    break
            if found is None:
                return None
            matched.add(found)
        return (x0, y0, x1, y1) if len(matched) == 4 else None

    @staticmethod
    def _rect_from_bbox(bbox, ctm) -> tuple[float, float, float, float] | None:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox)
        except Exception:
            return None
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
            return None
        points = [
            apply_matrix_pt(ctm, (x0, y0)),
            apply_matrix_pt(ctm, (x0, y1)),
            apply_matrix_pt(ctm, (x1, y0)),
            apply_matrix_pt(ctm, (x1, y1)),
        ]
        if not all(all(math.isfinite(v) for v in point) for point in points):
            return None
        rx0, rx1 = min(x for x, _ in points), max(x for x, _ in points)
        ry0, ry1 = min(y for _, y in points), max(y for _, y in points)
        scale = max(abs(rx0), abs(rx1), abs(ry0), abs(ry1), 1.0)
        eps = scale * 1e-9
        corners = ((rx0, ry0), (rx0, ry1), (rx1, ry0), (rx1, ry1))
        matched = set()
        for px, py in points:
            found = None
            for cx, cy in corners:
                if abs(px - cx) <= eps and abs(py - cy) <= eps:
                    found = (cx, cy)
                    break
            if found is None:
                return None
            matched.add(found)
        return (rx0, ry0, rx1, ry1) if len(matched) == 4 and rx1 - rx0 > eps and ry1 - ry0 > eps else None

    def _intersect_proven_clip_rect(self, rect: tuple[float, float, float, float]) -> None:
        if self._farseat_clip_complex or self._farseat_clip_empty:
            return
        if self._farseat_clip_rect is None:
            self._farseat_clip_rect = rect
        else:
            ax0, ay0, ax1, ay1 = self._farseat_clip_rect
            bx0, by0, bx1, by1 = rect
            x0, y0, x1, y1 = max(ax0, bx0), max(ay0, by0), min(ax1, bx1), min(ay1, by1)
            if x1 <= x0 or y1 <= y0:
                self._farseat_clip_rect = None
                self._farseat_clip_empty = True
            else:
                self._farseat_clip_rect = (x0, y0, x1, y1)

    def _stage_clip(self) -> None:
        rect = self._rect_from_path(self.curpath, self.ctm)
        self._farseat_pending_clip = ("RECT", rect) if rect is not None else ("COMPLEX", None)

    def _apply_pending_clip(self) -> None:
        pending = self._farseat_pending_clip
        self._farseat_pending_clip = None
        if pending is None:
            return
        kind, rect = pending
        if kind != "RECT" or rect is None:
            self._farseat_clip_complex = True
            self._farseat_clip_rect = None
            self._farseat_clip_empty = False
            self._sync_visibility_state()
            return
        if self._farseat_clip_complex:
            return
        if self._farseat_clip_empty:
            return
        self._intersect_proven_clip_rect(rect)
        self._sync_visibility_state()

    def dup(self):  # type: ignore[override]
        """Propagate visibility state into pdfminer's Form-XObject interpreter.

        pdfminer invokes Forms through ``self.dup()``, not ``subinterp()``.  The
        former implementation attached parent clipping state only to an unused hook,
        so every Form started with an unconstrained visibility model.
        """
        interp = super().dup()
        if isinstance(interp, VisibilityTrackingInterpreter):
            interp._farseat_inherited_state = (
                self._farseat_fill_alpha,
                self._farseat_stroke_alpha,
                self._farseat_complex_transparency,
                self._farseat_clip_rect,
                self._farseat_clip_complex,
                self._farseat_clip_empty,
            )
            interp._farseat_initial_form_bbox = getattr(self, "_farseat_next_form_bbox", None)
            interp._farseat_initial_form_bbox_uncertain = getattr(self, "_farseat_next_form_bbox_uncertain", False)
        return interp

    def do_Do(self, xobjid_arg) -> None:  # type: ignore[override]
        # pdfminer processes Form XObjects in a sub-interpreter. FarSeat explicitly
        # carries the caller's visibility state into that child and adds the Form
        # /BBox as an additional clipping region. If the BBox cannot be proven as an
        # axis-aligned device-space rectangle, the child fails closed.
        self._farseat_next_form_bbox = None
        self._farseat_next_form_bbox_uncertain = False
        try:
            xobjid = literal_name(xobjid_arg)
            xobj = stream_value(self.xobjmap[xobjid])
            if xobj.get("Subtype") is LITERAL_FORM:
                if "BBox" not in xobj:
                    self._farseat_next_form_bbox_uncertain = True
                else:
                    try:
                        bbox = tuple(float(resolve1(v)) for v in list_value(xobj["BBox"]))
                        if len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
                            raise ValueError
                        self._farseat_next_form_bbox = bbox
                    except Exception:
                        self._farseat_next_form_bbox_uncertain = True
        except Exception:
            # Let pdfminer retain its normal undefined/unsupported-XObject behavior;
            # the FarSeat state is only relevant if a child Form interpreter exists.
            pass
        try:
            super().do_Do(xobjid_arg)
        finally:
            self._farseat_next_form_bbox = None
            self._farseat_next_form_bbox_uncertain = False
            # The device is shared with the child interpreter. Restore the parent's
            # state so subsequent page text cannot inherit Form-local state.
            self._sync_visibility_state()

    def do_q(self) -> None:  # type: ignore[override]
        self._farseat_state_stack.append(
            (
                self._farseat_fill_alpha,
                self._farseat_stroke_alpha,
                self._farseat_complex_transparency,
                self._farseat_clip_rect,
                self._farseat_clip_complex,
                self._farseat_clip_empty,
            )
        )
        super().do_q()

    def do_Q(self) -> None:  # type: ignore[override]
        super().do_Q()
        if self._farseat_state_stack:
            (
                self._farseat_fill_alpha,
                self._farseat_stroke_alpha,
                self._farseat_complex_transparency,
                self._farseat_clip_rect,
                self._farseat_clip_complex,
                self._farseat_clip_empty,
            ) = self._farseat_state_stack.pop()
        self._farseat_pending_clip = None
        self._sync_visibility_state()

    def do_W(self) -> None:  # type: ignore[override]
        self._stage_clip()

    def do_W_a(self) -> None:  # type: ignore[override]
        self._stage_clip()

    def do_n(self) -> None:  # type: ignore[override]
        super().do_n()
        self._apply_pending_clip()

    def do_S(self) -> None:  # type: ignore[override]
        super().do_S()
        self._apply_pending_clip()

    def do_s(self) -> None:  # type: ignore[override]
        # super().do_s() routes through self.do_S(), which finalizes the clip.
        super().do_s()

    def do_F(self) -> None:  # type: ignore[override]
        # PDF `F` is the obsolete synonym for nonzero-winding fill. pdfminer keeps
        # its base implementation as a no-op, so explicitly end/paint the path here.
        super().do_f()
        self._apply_pending_clip()

    def do_f(self) -> None:  # type: ignore[override]
        super().do_f()
        self._apply_pending_clip()

    def do_f_a(self) -> None:  # type: ignore[override]
        super().do_f_a()
        self._apply_pending_clip()

    def do_B(self) -> None:  # type: ignore[override]
        super().do_B()
        self._apply_pending_clip()

    def do_B_a(self) -> None:  # type: ignore[override]
        super().do_B_a()
        self._apply_pending_clip()

    def do_b(self) -> None:  # type: ignore[override]
        # super().do_b() closes then routes through self.do_B().
        super().do_b()

    def do_b_a(self) -> None:  # type: ignore[override]
        super().do_b_a()

    def do_BT(self) -> None:  # type: ignore[override]
        super().do_BT()
        self._farseat_text_clip_pending = False

    def do_Tr(self, render) -> None:  # type: ignore[override]
        super().do_Tr(render)
        if getattr(self.textstate, "render", 0) in (4, 5, 6, 7):
            self._farseat_text_clip_pending = True

    def do_ET(self) -> None:  # type: ignore[override]
        super().do_ET()
        if self._farseat_text_clip_pending:
            # Glyph-outline clipping is deliberately outside V1's provable geometry.
            self._farseat_clip_complex = True
            self._farseat_clip_rect = None
            self._farseat_clip_empty = False
            self._sync_visibility_state()
        self._farseat_text_clip_pending = False

    def do_gs(self, name) -> None:  # type: ignore[override]
        try:
            state = self._farseat_extgstates.get(literal_name(name))
        except Exception:
            state = None
        if state is None:
            self._farseat_complex_transparency = True
            self._sync_visibility_state()
            return

        if "ca" in state:
            value = self._alpha(state["ca"])
            if value is None:
                self._farseat_complex_transparency = True
            else:
                self._farseat_fill_alpha = value
        if "CA" in state:
            value = self._alpha(state["CA"])
            if value is None:
                self._farseat_complex_transparency = True
            else:
                self._farseat_stroke_alpha = value

        if "SMask" in state:
            try:
                smask = resolve1(state["SMask"])
                is_none = literal_name(smask) == "None"
            except Exception:
                is_none = False
            if not is_none:
                self._farseat_complex_transparency = True

        self._sync_visibility_state()


@dataclass(frozen=True)
class ExtractedChar:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_name: str
    size: float
    render_mode: int | None
    rotation_deg: float
    fill_alpha: float
    stroke_alpha: float
    complex_transparency: bool
    type3: bool
    clip_rect: tuple[float, float, float, float] | None = None
    clip_complex: bool = False
    clip_empty: bool = False

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(frozen=True)
class NormalizedChar:
    source: ExtractedChar
    bbox: NormalizedBBox
    partially_clipped: bool = False
    clip_uncertain: bool = False
    combining_uncertain: bool = False


def _iter_chars(obj) -> Iterable[LTChar]:
    if isinstance(obj, LTChar):
        yield obj
    elif isinstance(obj, LTContainer):
        for child in obj:
            yield from _iter_chars(child)


def _rotation_from_matrix(matrix) -> float:
    a, b, _c, _d, _e, _f = matrix
    deg = math.degrees(math.atan2(b, a))
    while deg <= -180:
        deg += 360
    while deg > 180:
        deg -= 360
    return deg


def _visible_box(page) -> tuple[float, float, float, float, str]:
    media = tuple(float(x) for x in page.mediabox)
    crop = tuple(float(x) for x in (page.cropbox or page.mediabox))
    mx0, my0, mx1, my1 = media
    cx0, cy0, cx1, cy1 = crop
    x0, y0 = max(mx0, cx0), max(my0, cy0)
    x1, y1 = min(mx1, cx1), min(my1, cy1)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise ParseFailure("ZERO_AREA_VISIBLE_FRAME")
    source = "CROPBOX_INTERSECT_MEDIABOX" if crop != media else "MEDIABOX"
    return x0, y0, x1, y1, source


def _fallback_geometry(page, reason: str) -> CanonicalPageGeometry:
    rotation = int(page.rotation or 0) % 360
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    try:
        width, height = float(page.width), float(page.height)
    except Exception:
        width = height = 1.0
    if not math.isfinite(width) or width <= 0:
        width = 1.0
    if not math.isfinite(height) or height <= 0:
        height = 1.0
    return CanonicalPageGeometry(
        page_number=page.page_number,
        visible_box_source=f"UNANALYZABLE_PAGE:{reason}",
        page_rotation_deg=rotation,
        display_width_units=width,
        display_height_units=height,
        display_aspect_ratio=width / height,
    )


def _extract_chars(pdf, page) -> list[ExtractedChar]:
    device = RenderModeAggregator(pdf.rsrcmgr, pageno=page.page_number, laparams=pdf.laparams)
    interpreter = VisibilityTrackingInterpreter(pdf.rsrcmgr, device)
    interpreter.process_page(page.page_obj)
    layout: LTPage = device.get_result()
    out: list[ExtractedChar] = []
    for ch in _iter_chars(layout):
        text = ch.get_text()
        if not text:
            continue
        out.append(
            ExtractedChar(
                text=text,
                x0=float(ch.x0),
                y0=float(ch.y0),
                x1=float(ch.x1),
                y1=float(ch.y1),
                font_name=str(ch.fontname),
                size=float(ch.size),
                render_mode=getattr(ch, "farseat_render_mode", None),
                rotation_deg=_rotation_from_matrix(ch.matrix),
                fill_alpha=float(getattr(ch, "farseat_fill_alpha", 1.0)),
                stroke_alpha=float(getattr(ch, "farseat_stroke_alpha", 1.0)),
                complex_transparency=bool(getattr(ch, "farseat_complex_transparency", False)),
                type3=bool(getattr(ch, "farseat_type3", False)),
                clip_rect=getattr(ch, "farseat_clip_rect", None),
                clip_complex=bool(getattr(ch, "farseat_clip_complex", False)),
                clip_empty=bool(getattr(ch, "farseat_clip_empty", False)),
            )
        )
    return out


def _visibility(ch: ExtractedChar) -> tuple[TextVisibility, str | None]:
    mode = ch.render_mode
    if mode in (3, 7):
        return TextVisibility.INVISIBLE, "INVISIBLE_TEXT_LAYER"
    if mode not in (0, 1, 2, 4, 5, 6):
        return TextVisibility.UNCERTAIN, "UNCERTAIN_TEXT_VISIBILITY"
    if ch.type3:
        return TextVisibility.UNCERTAIN, "UNSUPPORTED_TYPE3_TEXT"
    if ch.complex_transparency:
        return TextVisibility.UNCERTAIN, "UNSUPPORTED_TEXT_TRANSPARENCY"

    relevant: tuple[float, ...]
    if mode in (0, 4):
        relevant = (ch.fill_alpha,)
    elif mode in (1, 5):
        relevant = (ch.stroke_alpha,)
    else:
        relevant = (ch.fill_alpha, ch.stroke_alpha)

    if not all(math.isfinite(a) and 0.0 <= a <= 1.0 for a in relevant):
        return TextVisibility.UNCERTAIN, "UNCERTAIN_TEXT_VISIBILITY"
    if max(relevant) <= _ALPHA_EPS:
        return TextVisibility.INVISIBLE, "INVISIBLE_TEXT_LAYER"
    if any(a < 1.0 - _ALPHA_EPS for a in relevant):
        return TextVisibility.UNCERTAIN, "UNSUPPORTED_TEXT_TRANSPARENCY"
    return TextVisibility.VISIBLE, None


def _norm_char(ch: ExtractedChar, frame: tuple[float, float, float, float]) -> NormalizedChar | None:
    fx0, fy0, fx1, fy1 = frame
    raw = (ch.x0, ch.y0, ch.x1, ch.y1)
    if not all(math.isfinite(v) for v in raw) or ch.x1 <= ch.x0 or ch.y1 <= ch.y0:
        return None
    if ch.clip_empty:
        return None

    # First account for a *proven* extra rectangular clipping path in the same device
    # coordinate system as LTChar. Completely clipped text is invisible and ignored;
    # partial clipping is retained only as NOT_ANALYZED evidence.
    cx0, cy0, cx1, cy1 = ch.x0, ch.y0, ch.x1, ch.y1
    partial_clip = False
    if ch.clip_rect is not None and not ch.clip_complex:
        rx0, ry0, rx1, ry1 = ch.clip_rect
        ix0, iy0 = max(cx0, rx0), max(cy0, ry0)
        ix1, iy1 = min(cx1, rx1), min(cy1, ry1)
        if ix1 <= ix0 or iy1 <= iy0:
            return None
        scale = max(abs(rx0), abs(ry0), abs(rx1), abs(ry1), 1.0)
        eps_clip = scale * 1e-9
        partial_clip = (
            cx0 < rx0 - eps_clip or cy0 < ry0 - eps_clip or cx1 > rx1 + eps_clip or cy1 > ry1 + eps_clip
        )
        cx0, cy0, cx1, cy1 = ix0, iy0, ix1, iy1

    ix0, iy0 = max(cx0, fx0), max(cy0, fy0)
    ix1, iy1 = min(cx1, fx1), min(cy1, fy1)
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    w, h = fx1 - fx0, fy1 - fy0
    eps = max(w, h) * 1e-9
    partial_page = cx0 < fx0 - eps or cy0 < fy0 - eps or cx1 > fx1 + eps or cy1 > fy1 + eps
    x0 = (ix0 - fx0) / w
    x1 = (ix1 - fx0) / w
    y0 = (fy1 - iy1) / h
    y1 = (fy1 - iy0) / h
    vals = [x0, y0, x1, y1]
    if not all(math.isfinite(v) for v in vals):
        return None
    box = NormalizedBBox(
        x0=max(0.0, min(1.0, x0)),
        y0=max(0.0, min(1.0, y0)),
        x1=max(0.0, min(1.0, x1)),
        y1=max(0.0, min(1.0, y1)),
    )
    return NormalizedChar(
        ch,
        box,
        partially_clipped=partial_clip or partial_page,
        clip_uncertain=ch.clip_complex,
    )


def _mark_combining_uncertainty(raw: list[ExtractedChar], normalized: list[NormalizedChar]) -> tuple[list[NormalizedChar], bool]:
    """Attach an unmeasurable combining mark to its closest same-line base glyph."""
    marked: set[int] = set()
    isolated = False
    for mark in raw:
        if not any(unicodedata.combining(ch) for ch in mark.text):
            continue
        candidates = [
            (idx, item) for idx, item in enumerate(normalized)
            if item.source is not mark
            and abs((item.source.y0 + item.source.y1 - mark.y0 - mark.y1) / 2)
            <= max(item.source.height, mark.height, 1.0)
        ]
        if not candidates:
            isolated = True
            continue
        preceding = [(idx, item) for idx, item in candidates if item.source.x1 <= mark.x0 + 1e-6]
        idx, _item = min(preceding or candidates, key=lambda pair: abs(pair[1].source.x1 - mark.x0))
        marked.add(idx)
    return [replace(item, combining_uncertain=index in marked or item.combining_uncertain) for index, item in enumerate(normalized)], isolated


def _union(boxes: list[NormalizedBBox]) -> NormalizedBBox:
    return NormalizedBBox(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )


def _normalize_font(font: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", font)


def _dedupe(chars: list[NormalizedChar]) -> list[NormalizedChar]:
    """Near-linear conservative dedupe with neighbor-bucket checks.

    Checking adjacent buckets avoids the old boundary bug where two near-identical
    glyphs could fall on opposite sides of a bucket boundary and survive.
    """
    buckets: dict[tuple[int, int], list[NormalizedChar]] = {}
    kept: list[NormalizedChar] = []
    cell = _DEDUPE_TOL
    ordered = sorted(chars, key=lambda item: (item.bbox.y0, item.bbox.x0, item.source.text, item.source.font_name))
    for item in ordered:
        ch, box = item.source, item.bbox
        bx = math.floor(box.x0 / cell)
        by = math.floor(box.y0 / cell)
        dup = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other_item in buckets.get((bx + dx, by + dy), ()):
                    other, obox = other_item.source, other_item.bbox
                    if ch.text != other.text or _normalize_font(ch.font_name) != _normalize_font(other.font_name):
                        continue
                    if max(
                        abs(box.x0 - obox.x0),
                        abs(box.y0 - obox.y0),
                        abs(box.x1 - obox.x1),
                        abs(box.y1 - obox.y1),
                    ) <= _DEDUPE_TOL:
                        dup = True
                        break
                if dup:
                    break
            if dup:
                break
        if not dup:
            kept.append(item)
            buckets.setdefault((bx, by), []).append(item)
    return kept


def _center(box: NormalizedBBox) -> tuple[float, float]:
    return (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0


def _script_pair(a: NormalizedBBox, b: NormalizedBBox) -> bool:
    ah, bh = a.y1 - a.y0, b.y1 - b.y0
    big, small = max(ah, bh), min(ah, bh)
    if big <= 0 or small / big > 0.82:
        return False
    _ax, acy = _center(a)
    _bx, bcy = _center(b)
    gap = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
    return abs(acy - bcy) >= 0.12 * big and gap <= 1.0 * big


def _text_layout_reason(text: str) -> str | None:
    """Fail closed outside the validated V1 left-to-right Latin-like pipeline."""
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi in {"R", "AL", "AN"}:
            return "UNSUPPORTED_TEXT_DIRECTION"
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if not name.startswith("LATIN "):
            return "UNSUPPORTED_TEXT_SHAPING"
    return None


def _line_and_segment(chars: list[NormalizedChar], page_number: int) -> list[ElementMetric]:
    if not chars:
        return []

    lines: list[list[NormalizedChar]] = []
    for item in sorted(chars, key=lambda it: (_center(it.bbox)[1], it.bbox.x0)):
        box = item.bbox
        _cx, cy = _center(box)
        h = box.y1 - box.y0
        target: list[NormalizedChar] | None = None
        for line in reversed(lines[-12:]):
            lcy = sum(_center(x.bbox)[1] for x in line) / len(line)
            heights = sorted(x.bbox.y1 - x.bbox.y0 for x in line)
            median_h = heights[len(heights) // 2]
            normal = abs(cy - lcy) <= max(0.002, median_h * 0.45)
            adjacent_script = False
            if h > 0 and median_h > 0 and min(h, median_h) / max(h, median_h) <= 0.82:
                nearest_gap = min(
                    max(0.0, max(box.x0, x.bbox.x0) - min(box.x1, x.bbox.x1)) for x in line
                )
                adjacent_script = abs(cy - lcy) <= 1.2 * max(h, median_h) and nearest_gap <= max(h, median_h)
            if normal or adjacent_script:
                target = line
                break
        if target is None:
            lines.append([item])
        else:
            target.append(item)

    # Merge small script-only clusters into an adjacent larger line. A superscript can
    # be encountered before the base line when sorting by vertical center, so a one-pass
    # line assignment is insufficient. The merge is conservative: the smaller cluster
    # must be materially smaller, vertically close, and horizontally touching/adjacent.
    changed = True
    while changed and len(lines) > 1:
        changed = False
        line_stats = []
        for idx, line in enumerate(lines):
            heights = sorted(x.bbox.y1 - x.bbox.y0 for x in line)
            median_h = heights[len(heights) // 2]
            center_y = sum(_center(x.bbox)[1] for x in line) / len(line)
            line_stats.append((idx, median_h, center_y))
        for small_idx, small_h, small_cy in sorted(line_stats, key=lambda x: x[1]):
            if small_idx >= len(lines):
                continue
            best: tuple[float, int] | None = None
            for big_idx, big_h, big_cy in line_stats:
                if big_idx == small_idx or big_idx >= len(lines) or small_h <= 0 or big_h <= 0:
                    continue
                if small_h / big_h > 0.82:
                    continue
                if abs(small_cy - big_cy) > 1.2 * big_h:
                    continue
                gap = min(
                    max(0.0, max(a.bbox.x0, b.bbox.x0) - min(a.bbox.x1, b.bbox.x1))
                    for a in lines[small_idx] for b in lines[big_idx]
                )
                if gap <= big_h and (best is None or gap < best[0]):
                    best = (gap, big_idx)
            if best is not None:
                big_idx = best[1]
                if small_idx == big_idx:
                    continue
                lines[big_idx].extend(lines[small_idx])
                del lines[small_idx]
                changed = True
                break

    elements: list[ElementMetric] = []
    eid = 0
    for line in lines:
        line.sort(key=lambda it: it.bbox.x0)
        segments: list[list[NormalizedChar]] = []
        for item in line:
            if not segments:
                segments.append([item])
                continue
            prev = segments[-1][-1]
            prev_h = max(prev.bbox.y1 - prev.bbox.y0, 1e-9)
            cur_h = max(item.bbox.y1 - item.bbox.y0, 1e-9)
            gap = item.bbox.x0 - prev.bbox.x1
            size_ratio = max(prev_h, cur_h) / min(prev_h, cur_h)
            if gap > max(prev_h, cur_h) * 1.6 or (size_ratio > 1.18 and not _script_pair(prev.bbox, item.bbox)):
                segments.append([item])
            else:
                segments[-1].append(item)

        for seg in segments:
            text = "".join(item.source.text for item in seg).strip()
            if not text:
                continue
            eid += 1
            logical = _union([item.bbox for item in seg])
            reasons: list[str] = []

            visibility_states: list[TextVisibility] = []
            for item in seg:
                vis, reason = _visibility(item.source)
                visibility_states.append(vis)
                if reason:
                    reasons.append(reason)
            if TextVisibility.INVISIBLE in visibility_states:
                visibility = TextVisibility.INVISIBLE
            elif TextVisibility.UNCERTAIN in visibility_states:
                visibility = TextVisibility.UNCERTAIN
            else:
                visibility = TextVisibility.VISIBLE

            rotations = [abs(item.source.rotation_deg) for item in seg if not item.source.text.isspace()]
            unsupported_rot = any(r > SUPPORTED_ROTATION_EPSILON_DEG for r in rotations)
            if unsupported_rot:
                reasons.append("UNSUPPORTED_TEXT_ROTATION")
            if any(item.partially_clipped for item in seg):
                reasons.append("CLIPPED_TEXT_GEOMETRY")
            if any(item.clip_uncertain for item in seg):
                reasons.append("UNSUPPORTED_CLIPPING_PATH")
            if any(item.combining_uncertain for item in seg):
                reasons.append("UNSUPPORTED_COMBINING_SHAPING")
            layout_reason = _text_layout_reason(text)
            if layout_reason:
                reasons.append(layout_reason)

            core = [item for item in seg if not item.source.text.isspace()]
            heights = [item.bbox.y1 - item.bbox.y0 for item in core]
            if heights:
                max_h = max(heights)
                large_centers = [
                    _center(item.bbox)[1]
                    for item in core
                    if (item.bbox.y1 - item.bbox.y0) >= 0.85 * max_h
                ]
                ref_center = sorted(large_centers)[len(large_centers) // 2] if large_centers else _center(core[0].bbox)[1]
                measurement_core = []
                for item in core:
                    h = item.bbox.y1 - item.bbox.y0
                    cy = _center(item.bbox)[1]
                    is_script = h <= 0.82 * max_h and abs(cy - ref_center) >= 0.12 * max_h
                    if not is_script:
                        measurement_core.append(item)
                measurement = min((item.bbox.y1 - item.bbox.y0 for item in measurement_core), default=None)
            else:
                measurement = None

            analyzable = (
                visibility == TextVisibility.VISIBLE
                and not unsupported_rot
                and "CLIPPED_TEXT_GEOMETRY" not in reasons
                and "UNSUPPORTED_CLIPPING_PATH" not in reasons
                and "UNSUPPORTED_COMBINING_SHAPING" not in reasons
                and layout_reason is None
                and measurement is not None
                and measurement > 0
            )
            confidence = MeasurementConfidence.SUPPORTED if analyzable else MeasurementConfidence.UNCERTAIN
            elements.append(
                ElementMetric(
                    slide_id=f"slide-{page_number}",
                    element_id=f"p{page_number}-e{eid}",
                    logical_bbox=logical,
                    text_preview=text[:PREVIEW_LIMIT],
                    support_state="ANALYZABLE" if analyzable else "NOT_ANALYZED",
                    visibility_state=visibility,
                    measurement_confidence=confidence,
                    measurement_method="FARSEAT_RENDERED_TEXT_HEIGHT_V1" if analyzable else None,
                    element_height_ratio=measurement if analyzable else None,
                    reason_codes=tuple(dict.fromkeys(reasons)),
                )
            )
    return elements


def _object_census(page) -> int:
    try:
        return len(page.images) + len(page.lines) + len(page.rects) + len(page.curves)
    except Exception:
        return 0


def parse_pdf_bytes(data: bytes, presentation_id: UUID, filename: str) -> PresentationModel:
    if not data:
        raise ParseFailure("EMPTY_FILE")
    try:
        pdf = pdfplumber.open(BytesIO(data))
    except PDFPasswordIncorrect as exc:
        raise ParseFailure("UNSUPPORTED_ENCRYPTED_PDF") from exc
    except Exception as exc:
        raise ParseFailure("INVALID_PDF") from exc
    try:
        if getattr(pdf.doc, "encryption", None) is not None or getattr(pdf.doc, "is_extractable", True) is False:
            raise ParseFailure("UNSUPPORTED_ENCRYPTED_PDF")
        if len(pdf.pages) == 0:
            raise ParseFailure("INVALID_PDF")
        if len(pdf.pages) > MAX_PAGES:
            raise ParseFailure("PAGE_LIMIT_EXCEEDED")

        pages: list[PageAnalysisModel] = []
        raw_total = run_total = unsupported_total = analyzable_total = 0
        try:
            optional_content_present = "OCProperties" in dict_value(getattr(pdf.doc, "catalog", {}))
        except Exception:
            optional_content_present = False
        for page in pdf.pages:
            graphics = _object_census(page)
            non_text = NonTextContentState.PRESENT if graphics > 0 else NonTextContentState.ABSENT
            try:
                frame_x0, frame_y0, frame_x1, frame_y1, source = _visible_box(page)
                rotation = int(page.rotation or 0) % 360
                if rotation not in (0, 90, 180, 270):
                    raise ParseFailure("INVALID_PAGE_ROTATION")
                raw_w, raw_h = frame_x1 - frame_x0, frame_y1 - frame_y0
                geometry = CanonicalPageGeometry(
                    page_number=page.page_number,
                    visible_box_source=source,
                    page_rotation_deg=rotation,
                    display_width_units=raw_w,
                    display_height_units=raw_h,
                    display_aspect_ratio=raw_w / raw_h,
                    visible_x0_units=frame_x0,
                    visible_y0_units=frame_y0,
                    visible_x1_units=frame_x1,
                    visible_y1_units=frame_y1,
                )
            except ParseFailure as exc:
                # A bad page must not destroy valid pages. Keep an explicit unknown page.
                geometry = _fallback_geometry(page, exc.code)
                pages.append(
                    PageAnalysisModel(
                        page_number=page.page_number,
                        geometry=geometry,
                        text_coverage=TextCoverage.NONE,
                        non_text_content=non_text,
                        elements=(),
                        reason_codes=(exc.code,),
                    )
                )
                unsupported_total += 1
                continue

            try:
                raw = _extract_chars(pdf, page)
            except Exception:
                pages.append(
                    PageAnalysisModel(
                        page_number=page.page_number,
                        geometry=geometry,
                        text_coverage=TextCoverage.NONE,
                        non_text_content=non_text,
                        elements=(),
                        reason_codes=("PAGE_TEXT_EXTRACTION_FAILED",),
                    )
                )
                unsupported_total += 1
                continue

            raw_total += len(raw)
            if raw_total > MAX_RAW_CHARS:
                raise ParseFailure("CHARACTER_LIMIT_EXCEEDED")

            normalized: list[NormalizedChar] = []
            invalid_char_geometry = False
            for ch in raw:
                item = _norm_char(ch, (frame_x0, frame_y0, frame_x1, frame_y1))
                if item is None:
                    # Wholly outside the visible frame is intentionally ignored. Invalid
                    # geometry is conservatively surfaced when the raw box itself is bad.
                    if not all(math.isfinite(v) for v in (ch.x0, ch.y0, ch.x1, ch.y1)) or ch.x1 <= ch.x0 or ch.y1 <= ch.y0:
                        invalid_char_geometry = True
                    continue
                normalized.append(item)
            normalized = _dedupe(normalized)
            normalized, isolated_combining = _mark_combining_uncertainty(raw, normalized)
            elements = _line_and_segment(normalized, page.page_number)
            if optional_content_present and elements:
                elements = [
                    element.model_copy(
                        update={
                            "support_state": "NOT_ANALYZED",
                            "measurement_confidence": MeasurementConfidence.UNCERTAIN,
                            "measurement_method": None,
                            "element_height_ratio": None,
                            "reason_codes": tuple(dict.fromkeys(element.reason_codes + ("UNSUPPORTED_OPTIONAL_CONTENT",))),
                        }
                    )
                    for element in elements
                ]
            run_total += len(elements)
            if run_total > MAX_RUNS:
                raise ParseFailure("RUN_LIMIT_EXCEEDED")

            analyzable = sum(1 for e in elements if e.support_state == "ANALYZABLE")
            unsupported = sum(1 for e in elements if e.support_state != "ANALYZABLE")
            analyzable_total += analyzable
            unsupported_total += unsupported
            reasons: list[str] = []
            if graphics:
                # Text coverage and non-text presence are orthogonal. The text may be
                # completely analyzed, but FarSeat does not prove arbitrary graphics
                # semantics/occlusion, so aggregate seat coverage must remain partial.
                reasons.append("GRAPHICAL_CONTENT_NOT_ANALYZED")
            if optional_content_present:
                reasons.append("UNSUPPORTED_OPTIONAL_CONTENT")
            if invalid_char_geometry:
                reasons.append("INVALID_CHARACTER_GEOMETRY")
            if isolated_combining:
                reasons.append("UNSUPPORTED_COMBINING_SHAPING")
            if any("CLIPPED_TEXT_GEOMETRY" in e.reason_codes for e in elements):
                reasons.append("CLIPPED_TEXT_GEOMETRY")

            if not elements:
                coverage = TextCoverage.NONE
                reasons.append("NO_EXTRACTABLE_TEXT_WITH_GRAPHICS" if graphics else "NO_EXTRACTABLE_TEXT")
            elif unsupported or invalid_char_geometry:
                coverage = TextCoverage.PARTIAL if analyzable else TextCoverage.NONE
            else:
                coverage = TextCoverage.COMPLETE

            pages.append(
                PageAnalysisModel(
                    page_number=page.page_number,
                    geometry=geometry,
                    text_coverage=coverage,
                    non_text_content=non_text,
                    elements=tuple(elements),
                    reason_codes=tuple(dict.fromkeys(reasons)),
                )
            )

        return PresentationModel(
            presentation_id=presentation_id,
            filename=filename,
            pages=tuple(pages),
            page_count=len(pages),
            analyzable_element_count=analyzable_total,
            unsupported_element_count=unsupported_total,
            unanalyzable_page_count=sum(1 for page in pages if page.text_coverage == TextCoverage.NONE),
        )
    finally:
        pdf.close()


def _worker(data: bytes, presentation_id: str, filename: str, memory_limit_bytes: int | None, out):
    try:
        if memory_limit_bytes and os.name == "posix":
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
        model = parse_pdf_bytes(data, UUID(presentation_id), filename)
        payload = model.model_dump_json().encode("utf-8")
        if len(payload) > MAX_SERIALIZED_MODEL_BYTES:
            out.send_bytes(b"P" + b"PARSER_OUTPUT_LIMIT_EXCEEDED")
        else:
            out.send_bytes(b"O" + payload)
    except ParseFailure as exc:
        out.send_bytes(b"P" + exc.code.encode("ascii", "replace"))
    except BaseException:
        out.send_bytes(b"I" + traceback.format_exc(limit=8).encode("utf-8", "replace"))
    finally:
        try:
            out.close()
        except Exception:
            pass


class SubprocessParserRunner:
    def __init__(self, timeout_seconds: float = 20.0, memory_limit_bytes: int | None = 1024 * 1024 * 1024, worker_target=None):
        self.timeout_seconds = timeout_seconds
        self.memory_limit_bytes = memory_limit_bytes
        self._worker_target = worker_target or _worker

    def parse(self, data: bytes, presentation_id: UUID, filename: str) -> PresentationModel:
        """Parse in a spawned child while draining its result concurrently.

        Waiting for child exit before reading a multiprocessing Queue can deadlock when
        the parsed model exceeds the pipe buffer. A one-way Pipe plus `poll()` lets the
        parent drain the payload while the child is alive, preserving the process/time
        isolation for both tiny and large valid presentations.
        """
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=self._worker_target,
            args=(data, str(presentation_id), filename, self.memory_limit_bytes, child),
        )
        deadline = time.monotonic() + self.timeout_seconds
        proc.start()
        child.close()
        message: bytes | None = None
        try:
            remaining = max(0.0, deadline - time.monotonic())
            if parent.poll(remaining):
                try:
                    message = parent.recv_bytes(MAX_SERIALIZED_MODEL_BYTES + 4096)
                except (EOFError, OSError):
                    message = None
            if message is None:
                if proc.is_alive():
                    proc.terminate()
                proc.join(2)
                code = "PARSER_RESOURCE_LIMIT" if proc.exitcode not in (0, None) or time.monotonic() >= deadline else "INTERNAL_PARSE_ERROR"
                raise ParseFailure(code)

            # The payload has already been drained, so joining can no longer deadlock
            # on a full IPC buffer. Give the child only a small bounded cleanup window.
            proc.join(max(0.0, min(2.0, deadline - time.monotonic())))
            if proc.is_alive():
                proc.terminate()
                proc.join(2)
            if not message:
                raise ParseFailure("INTERNAL_PARSE_ERROR")
            kind, payload = message[:1], message[1:]
            if kind == b"O":
                try:
                    return PresentationModel.model_validate_json(payload)
                except Exception as exc:
                    raise ParseFailure("INTERNAL_PARSE_ERROR") from exc
            if kind == b"P":
                raise ParseFailure(payload.decode("ascii", "replace") or "INTERNAL_PARSE_ERROR")
            raise ParseFailure("INTERNAL_PARSE_ERROR")
        finally:
            parent.close()
            if proc.is_alive():
                proc.terminate()
                proc.join(2)

