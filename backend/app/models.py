from __future__ import annotations

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CellState(str, Enum):
    PASS_TARGET = "PASS_TARGET"
    BELOW_TARGET = "BELOW_TARGET"
    NOT_ANALYZED = "NOT_ANALYZED"
    OUTSIDE_REFERENCE_RANGE = "OUTSIDE_REFERENCE_RANGE"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"


class GeometryState(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"


class ResultState(str, Enum):
    MEETS_TARGET = "MEETS_TARGET"
    REVIEW_RECOMMENDED = "REVIEW_RECOMMENDED"
    NO_ANALYZABLE_RESULTS = "NO_ANALYZABLE_RESULTS"


class CoverageState(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class TextVisibility(str, Enum):
    VISIBLE = "VISIBLE"
    INVISIBLE = "INVISIBLE"
    UNCERTAIN = "UNCERTAIN"


class MeasurementConfidence(str, Enum):
    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    UNCERTAIN = "UNCERTAIN"


class TextCoverage(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class NonTextContentState(str, Enum):
    ABSENT = "ABSENT"
    PRESENT = "PRESENT"


class NormalizedBBox(StrictModel):
    x0: float = Field(strict=True)
    y0: float = Field(strict=True)
    x1: float = Field(strict=True)
    y1: float = Field(strict=True)

    @model_validator(mode="after")
    def validate_box(self):
        if not (0 <= self.x0 <= self.x1 <= 1 and 0 <= self.y0 <= self.y1 <= 1):
            raise ValueError("normalized bbox must lie within [0,1]")
        return self


class CanonicalPageGeometry(StrictModel):
    page_number: int = Field(ge=1, strict=True)
    visible_box_source: str
    page_rotation_deg: Literal[0, 90, 180, 270]
    display_width_units: float = Field(gt=0, strict=True)
    display_height_units: float = Field(gt=0, strict=True)
    display_aspect_ratio: float = Field(gt=0, strict=True)
    visible_x0_units: float | None = Field(default=None, strict=True)
    visible_y0_units: float | None = Field(default=None, strict=True)
    visible_x1_units: float | None = Field(default=None, strict=True)
    visible_y1_units: float | None = Field(default=None, strict=True)


class RendererPageManifest(StrictModel):
    page_number: int = Field(ge=1, strict=True)
    rotation_deg: Literal[0, 90, 180, 270]
    viewport_width: float = Field(gt=0, strict=True)
    viewport_height: float = Field(gt=0, strict=True)
    view_x0: float | None = Field(default=None, strict=True)
    view_y0: float | None = Field(default=None, strict=True)
    view_x1: float | None = Field(default=None, strict=True)
    view_y1: float | None = Field(default=None, strict=True)


class ElementMetric(StrictModel):
    slide_id: str
    element_id: str
    logical_bbox: NormalizedBBox
    text_preview: str | None = None
    support_state: Literal["ANALYZABLE", "NOT_ANALYZED"]
    visibility_state: TextVisibility
    measurement_confidence: MeasurementConfidence
    measurement_method: str | None = None
    element_height_ratio: float | None = Field(default=None, ge=0, strict=True)
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unknown_has_no_measurement(self):
        if self.support_state != "ANALYZABLE" or self.measurement_confidence == MeasurementConfidence.UNCERTAIN:
            if self.element_height_ratio is not None:
                raise ValueError("unanalyzable/uncertain element must not have a measurement")
        return self


class PageAnalysisModel(StrictModel):
    page_number: int = Field(ge=1, strict=True)
    geometry: CanonicalPageGeometry
    text_coverage: TextCoverage
    non_text_content: NonTextContentState
    elements: tuple[ElementMetric, ...] = ()
    reason_codes: tuple[str, ...] = ()


class PresentationModel(StrictModel):
    presentation_id: UUID
    filename: str
    pages: tuple[PageAnalysisModel, ...]
    page_count: int = Field(ge=1, strict=True)
    analyzable_element_count: int = Field(ge=0, strict=True)
    unsupported_element_count: int = Field(ge=0, strict=True)
    unanalyzable_page_count: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def counts_match(self):
        if self.page_count != len(self.pages):
            raise ValueError("page_count must match pages")
        return self


class PhysicalDisplay(StrictModel):
    width_m: float = Field(gt=0, strict=True)
    height_m: float = Field(gt=0, strict=True)
    center_x_m: float = Field(default=0.0, strict=True)
    bottom_z_m: float = Field(default=0.0, strict=True)


class RoomGeometry(StrictModel):
    width_m: float = Field(gt=0, strict=True)
    depth_m: float = Field(gt=0, strict=True)


class SeatGrid(StrictModel):
    rows: int = Field(ge=1, le=200, strict=True)
    seats_per_row: int = Field(ge=1, le=200, strict=True)
    first_row_distance_m: float = Field(gt=0, strict=True)
    row_spacing_m: float = Field(gt=0, strict=True)
    seat_spacing_m: float = Field(gt=0, strict=True)
    center_x_m: float = Field(default=0.0, strict=True)

    @model_validator(mode="after")
    def validate_count(self):
        if self.rows * self.seats_per_row > 200:
            raise ValueError("seat count exceeds 200")
        return self


class Seat(StrictModel):
    seat_id: str
    row: int = Field(ge=1, strict=True)
    number: int = Field(ge=1, strict=True)
    x_m: float = Field(strict=True)
    y_m: float = Field(strict=True)


class AnalyzeRequest(StrictModel):
    schema_version: Literal["1.2"] = "1.2"
    request_id: UUID
    presentation_id: UUID
    display: PhysicalDisplay
    room: RoomGeometry
    seat_grid: SeatGrid
    profile: Literal["PUBLIC_AVIXA_BDM_REFERENCE_V1"] = "PUBLIC_AVIXA_BDM_REFERENCE_V1"
    renderer_manifest: tuple[RendererPageManifest, ...] = ()

    @model_validator(mode="after")
    def validate_cross_geometry(self):
        half_room = self.room.width_m / 2.0
        half_display = self.display.width_m / 2.0
        if abs(self.display.center_x_m) + half_display > half_room + 1e-12:
            raise ValueError("usable display area does not fit within room width")
        page_numbers = [m.page_number for m in self.renderer_manifest]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("renderer manifest contains duplicate page numbers")
        return self


class SeatSummary(StrictModel):
    seat_id: str
    row: int = Field(strict=True)
    number: int = Field(strict=True)
    x_m: float = Field(strict=True)
    y_m: float = Field(strict=True)
    geometry_state: GeometryState
    result_state: ResultState
    coverage_state: CoverageState
    has_outside_reference_results: bool
    meets_target_count: int = Field(ge=0, strict=True)
    below_target_count: int = Field(ge=0, strict=True)
    outside_range_count: int = Field(ge=0, strict=True)
    not_analyzed_count: int = Field(ge=0, strict=True)


class SlideSummary(StrictModel):
    slide_id: str
    page_number: int = Field(strict=True)
    text_coverage: TextCoverage
    non_text_content: NonTextContentState
    unique_below_target_elements: int = Field(ge=0, strict=True)
    below_target_comparisons: int = Field(ge=0, strict=True)


class AnalysisResponse(StrictModel):
    schema_version: Literal["1.2"] = "1.2"
    request_id: UUID
    analysis_id: UUID
    presentation_id: UUID
    configured_seat_count: int = Field(strict=True)
    valid_seat_count: int = Field(strict=True)
    invalid_seat_count: int = Field(strict=True)
    affected_valid_seat_count: int = Field(strict=True)
    seats: tuple[SeatSummary, ...]
    slides: tuple[SlideSummary, ...]


class SeatElementDetail(StrictModel):
    slide_id: str
    page_number: int = Field(strict=True)
    element_id: str
    text_preview: str | None
    logical_bbox: NormalizedBBox | None = None
    state: CellState
    element_height_pct: float | None = Field(default=None, strict=True)
    target_height_pct: float | None = Field(default=None, strict=True)
    margin: float | None = Field(default=None, strict=True)
    reason_codes: tuple[str, ...] = ()


class SeatDetailResponse(StrictModel):
    schema_version: Literal["1.2"] = "1.2"
    analysis_id: UUID
    seat: Seat
    details: tuple[SeatElementDetail, ...]


class PresentationResponse(StrictModel):
    schema_version: Literal["1.2"] = "1.2"
    presentation_id: UUID
    capability_token: str | None = None
    filename: str
    page_count: int = Field(strict=True)
    analyzable_element_count: int = Field(strict=True)
    unsupported_element_count: int = Field(strict=True)
    unanalyzable_page_count: int = Field(default=0, strict=True)
    pages: tuple[PageAnalysisModel, ...]
