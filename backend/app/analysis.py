from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID, uuid4

from .geometry import active_image, attest_geometry, generate_seats, seat_geometry_valid
from .models import (
    AnalysisResponse,
    AnalyzeRequest,
    CellState,
    CoverageState,
    ElementMetric,
    GeometryState,
    PageAnalysisModel,
    PresentationModel,
    ResultState,
    Seat,
    SeatDetailResponse,
    SeatElementDetail,
    SeatSummary,
    SlideSummary,
    TextCoverage,
)
from .reference import lookup_bdm_target
from .numeric import meets_or_equals


@dataclass(frozen=True)
class AnalysisSnapshot:
    analysis_id: UUID
    request_id: UUID
    presentation_id: UUID
    request: AnalyzeRequest
    seats: tuple[Seat, ...]
    response: AnalysisResponse


@dataclass(frozen=True)
class PageStaticContext:
    page: PageAnalysisModel
    geometry_ok: bool
    geometry_reason: str | None
    active_image_height_m: float


def _manifest_map(req: AnalyzeRequest):
    return {m.page_number: m for m in req.renderer_manifest}


def _page_warning_reasons(page: PageAnalysisModel) -> tuple[str, ...]:
    # Reasons represented by an element are not duplicated as page-warning pseudo-items.
    element_reasons = {reason for e in page.elements for reason in e.reason_codes}
    # Graphics are represented by `non_text_content`, not fabricated text-level
    # unknown comparisons. A logo must not downgrade complete text coverage.
    return tuple(
        reason for reason in page.reason_codes
        if reason not in element_reasons and reason != "GRAPHICAL_CONTENT_NOT_ANALYZED"
    )


def _static_contexts(presentation: PresentationModel, req: AnalyzeRequest) -> tuple[PageStaticContext, ...]:
    manifest_by_page = _manifest_map(req)
    contexts: list[PageStaticContext] = []
    for page in presentation.pages:
        manifest = manifest_by_page.get(page.page_number)
        attest = attest_geometry(page.geometry, manifest)
        if attest.state == "MATCH":
            geometry_ok = True
            geometry_reason = None
        elif attest.state == "MISMATCH":
            geometry_ok = False
            geometry_reason = "GEOMETRY_ENGINE_MISMATCH"
        else:
            geometry_ok = False
            geometry_reason = "GEOMETRY_NOT_ATTESTED"
        try:
            active = active_image(req.display, page.geometry.display_aspect_ratio)
            active_height = active.height_m
        except ValueError:
            geometry_ok = False
            geometry_reason = "INVALID_ACTIVE_IMAGE_GEOMETRY"
            active_height = 1.0  # never used for a numeric conclusion while geometry_ok is false
        contexts.append(PageStaticContext(page, geometry_ok, geometry_reason, active_height))
    return tuple(contexts)


def _element_state(
    el: ElementMetric,
    context: PageStaticContext,
    seat: Seat,
) -> tuple[CellState, float | None, float | None, tuple[str, ...]]:
    reasons = list(el.reason_codes)
    if not context.geometry_ok:
        if context.geometry_reason:
            reasons.append(context.geometry_reason)
        return CellState.NOT_ANALYZED, None, None, tuple(dict.fromkeys(reasons))
    if el.support_state != "ANALYZABLE" or el.element_height_ratio is None:
        return CellState.NOT_ANALYZED, None, None, tuple(dict.fromkeys(reasons))

    lookup = lookup_bdm_target(seat.y_m / context.active_image_height_m)
    if lookup.target_ratio is None:
        if lookup.reason_code:
            reasons.append(lookup.reason_code)
        return CellState.OUTSIDE_REFERENCE_RANGE, None, None, tuple(dict.fromkeys(reasons))

    target = lookup.target_ratio
    state = CellState.PASS_TARGET if meets_or_equals(el.element_height_ratio, target) else CellState.BELOW_TARGET
    margin = el.element_height_ratio / target if target > 0 else None
    return state, target, margin, tuple(dict.fromkeys(reasons))


def _synthetic_page_detail(
    analysis_id: UUID,
    page: PageAnalysisModel,
    seat: Seat,
    state: CellState,
    reasons: tuple[str, ...],
) -> SeatElementDetail:
    del analysis_id  # kept in the signature to make call sites explicit about snapshot identity
    preview = "No extractable text on this slide" if not page.elements else "Some page text geometry was not analyzable"
    return SeatElementDetail(
        slide_id=f"slide-{page.page_number}",
        page_number=page.page_number,
        element_id=f"p{page.page_number}-coverage",
        text_preview=preview,
        logical_bbox=None,
        state=state,
        reason_codes=reasons or ("PAGE_CONTENT_NOT_ANALYZED",),
    )


def analyze_presentation(presentation: PresentationModel, req: AnalyzeRequest) -> AnalysisSnapshot:
    """Compute compact summaries only; never materialize element×seat detail cells.

    Detailed cells are derived for one requested seat from the immutable snapshot by
    `derive_seat_detail`. This keeps stored memory O(elements + seats + pages), rather
    than O(elements × seats).
    """
    seats = generate_seats(req.seat_grid, req.room)
    contexts = _static_contexts(presentation, req)
    seat_summaries: list[SeatSummary] = []
    slide_below_unique: dict[str, set[str]] = defaultdict(set)
    slide_below_comparisons: dict[str, int] = defaultdict(int)
    affected_valid = 0
    valid_count = 0
    invalid_count = 0

    for seat in seats:
        meets = below = outside = unknown = 0
        if not seat_geometry_valid(seat, req.room):
            invalid_count += 1
            summary = SeatSummary(
                seat_id=seat.seat_id,
                row=seat.row,
                number=seat.number,
                x_m=seat.x_m,
                y_m=seat.y_m,
                geometry_state=GeometryState.INVALID,
                result_state=ResultState.NO_ANALYZABLE_RESULTS,
                coverage_state=CoverageState.NONE,
                has_outside_reference_results=False,
                meets_target_count=0,
                below_target_count=0,
                outside_range_count=0,
                not_analyzed_count=0,
            )
            seat_summaries.append(summary)
            continue

        valid_count += 1
        for context in contexts:
            page = context.page
            page_warning = _page_warning_reasons(page)
            if not page.elements:
                unknown += 1
            elif page_warning:
                unknown += 1

            if not context.geometry_ok:
                unknown += len(page.elements)
                continue

            lookup = lookup_bdm_target(seat.y_m / context.active_image_height_m)
            for el in page.elements:
                if el.support_state != "ANALYZABLE" or el.element_height_ratio is None:
                    unknown += 1
                elif lookup.target_ratio is None:
                    outside += 1
                elif meets_or_equals(el.element_height_ratio, lookup.target_ratio):
                    meets += 1
                else:
                    below += 1
                    slide_below_unique[el.slide_id].add(el.element_id)
                    slide_below_comparisons[el.slide_id] += 1

        if below > 0:
            result_state = ResultState.REVIEW_RECOMMENDED
            affected_valid += 1
        elif meets > 0:
            result_state = ResultState.MEETS_TARGET
        else:
            result_state = ResultState.NO_ANALYZABLE_RESULTS

        if unknown == 0:
            coverage = CoverageState.COMPLETE
        elif meets + below + outside > 0:
            coverage = CoverageState.PARTIAL
        else:
            coverage = CoverageState.NONE

        seat_summaries.append(
            SeatSummary(
                seat_id=seat.seat_id,
                row=seat.row,
                number=seat.number,
                x_m=seat.x_m,
                y_m=seat.y_m,
                geometry_state=GeometryState.VALID,
                result_state=result_state,
                coverage_state=coverage,
                has_outside_reference_results=outside > 0,
                meets_target_count=meets,
                below_target_count=below,
                outside_range_count=outside,
                not_analyzed_count=unknown,
            )
        )

    slides = tuple(
        SlideSummary(
            slide_id=f"slide-{p.page_number}",
            page_number=p.page_number,
            text_coverage=p.text_coverage,
            non_text_content=p.non_text_content,
            unique_below_target_elements=len(slide_below_unique[f"slide-{p.page_number}"]),
            below_target_comparisons=slide_below_comparisons[f"slide-{p.page_number}"],
        )
        for p in presentation.pages
    )
    analysis_id = uuid4()
    response = AnalysisResponse(
        request_id=req.request_id,
        analysis_id=analysis_id,
        presentation_id=presentation.presentation_id,
        configured_seat_count=len(seats),
        valid_seat_count=valid_count,
        invalid_seat_count=invalid_count,
        affected_valid_seat_count=affected_valid,
        seats=tuple(seat_summaries),
        slides=slides,
    )
    return AnalysisSnapshot(
        analysis_id=analysis_id,
        request_id=req.request_id,
        presentation_id=presentation.presentation_id,
        request=req,
        seats=seats,
        response=response,
    )


def derive_seat_detail(
    presentation: PresentationModel,
    snapshot: AnalysisSnapshot,
    seat_id: str,
) -> SeatDetailResponse | None:
    seat = next((s for s in snapshot.seats if s.seat_id == seat_id), None)
    if seat is None:
        return None
    contexts = _static_contexts(presentation, snapshot.request)
    details: list[SeatElementDetail] = []
    seat_valid = seat_geometry_valid(seat, snapshot.request.room)

    for context in contexts:
        page = context.page
        page_warning = _page_warning_reasons(page)
        if not seat_valid:
            for el in page.elements:
                details.append(
                    SeatElementDetail(
                        slide_id=el.slide_id,
                        page_number=page.page_number,
                        element_id=el.element_id,
                        text_preview=el.text_preview,
                        logical_bbox=el.logical_bbox,
                        state=CellState.INVALID_GEOMETRY,
                        reason_codes=("INVALID_SEAT_POSITION",),
                    )
                )
            if not page.elements or page_warning:
                details.append(
                    _synthetic_page_detail(
                        snapshot.analysis_id,
                        page,
                        seat,
                        CellState.INVALID_GEOMETRY,
                        ("INVALID_SEAT_POSITION",) + page_warning,
                    )
                )
            continue

        for el in page.elements:
            state, target, margin, reasons = _element_state(el, context, seat)
            element_height_pct = (
                el.element_height_ratio * 100
                if state in (CellState.PASS_TARGET, CellState.BELOW_TARGET, CellState.OUTSIDE_REFERENCE_RANGE)
                and el.element_height_ratio is not None
                else None
            )
            details.append(
                SeatElementDetail(
                    slide_id=el.slide_id,
                    page_number=page.page_number,
                    element_id=el.element_id,
                    text_preview=el.text_preview,
                    logical_bbox=el.logical_bbox,
                    state=state,
                    element_height_pct=element_height_pct,
                    target_height_pct=(target * 100 if target is not None else None),
                    margin=margin,
                    reason_codes=reasons,
                )
            )

        if not page.elements:
            reasons = page.reason_codes or ("PAGE_CONTENT_NOT_ANALYZED",)
            if not context.geometry_ok and context.geometry_reason:
                reasons = tuple(dict.fromkeys(reasons + (context.geometry_reason,)))
            details.append(
                _synthetic_page_detail(snapshot.analysis_id, page, seat, CellState.NOT_ANALYZED, reasons)
            )
        elif page_warning:
            details.append(
                _synthetic_page_detail(snapshot.analysis_id, page, seat, CellState.NOT_ANALYZED, page_warning)
            )

    return SeatDetailResponse(analysis_id=snapshot.analysis_id, seat=seat, details=tuple(details))
