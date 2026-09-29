from uuid import uuid4
import math
import pytest

from app.analysis import analyze_presentation, derive_seat_detail
from app.models import (
    AnalyzeRequest, CanonicalPageGeometry, ElementMetric, MeasurementConfidence,
    NonTextContentState, NormalizedBBox, PageAnalysisModel, PhysicalDisplay,
    PresentationModel, RendererPageManifest, RoomGeometry, SeatGrid, TextCoverage,
    TextVisibility,
)


def presentation(eh: float = 0.02, *, rotation: int = 0, aspect: float = 16/9, empty_page: bool=False) -> PresentationModel:
    pid = uuid4()
    geom = CanonicalPageGeometry(page_number=1, visible_box_source="MEDIABOX",
        page_rotation_deg=rotation, display_width_units=16 if aspect>=1 else 9, display_height_units=9 if aspect>=1 else 16,
        display_aspect_ratio=aspect)
    elements=() if empty_page else (ElementMetric(slide_id="slide-1", element_id="p1-e1",
        logical_bbox=NormalizedBBox(x0=.1,y0=.1,x1=.5,y1=.12), text_preview="Test",
        support_state="ANALYZABLE", visibility_state=TextVisibility.VISIBLE,
        measurement_confidence=MeasurementConfidence.SUPPORTED,
        measurement_method="FARSEAT_RENDERED_TEXT_HEIGHT_V1", element_height_ratio=eh),)
    page = PageAnalysisModel(page_number=1, geometry=geom, text_coverage=TextCoverage.NONE if empty_page else TextCoverage.COMPLETE,
        non_text_content=NonTextContentState.PRESENT if empty_page else NonTextContentState.ABSENT, elements=elements,
        reason_codes=("NO_EXTRACTABLE_TEXT_WITH_GRAPHICS",) if empty_page else ())
    return PresentationModel(presentation_id=pid, filename="x.pdf", pages=(page,), page_count=1,
        analyzable_element_count=0 if empty_page else 1, unsupported_element_count=0)


def request(pid, y=4.5, *, rotation=0, aspect=16/9):
    width,height=(1600,900) if aspect>=1 else (900,1600)
    return AnalyzeRequest(request_id=uuid4(), presentation_id=pid,
        display=PhysicalDisplay(width_m=4.0,height_m=2.25),
        room=RoomGeometry(width_m=8,depth_m=20),
        seat_grid=SeatGrid(rows=1,seats_per_row=1,first_row_distance_m=y,row_spacing_m=1,seat_spacing_m=1),
        renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=rotation,viewport_width=width,viewport_height=height),))


def test_hand_oracle_vr2_target_1_5_eh2_pass():
    p = presentation(.02)
    snapshot = analyze_presentation(p, request(p.presentation_id, 4.5))
    s = snapshot.response.seats[0]
    assert s.result_state == "MEETS_TARGET"
    d = derive_seat_detail(p,snapshot,"r1-s1").details[0]
    assert d.target_height_pct == pytest.approx(1.5)
    assert d.state == "PASS_TARGET"


def test_farther_seat_requires_review():
    p = presentation(.02)
    snapshot = analyze_presentation(p, request(p.presentation_id, 9.0))
    assert snapshot.response.seats[0].result_state == "REVIEW_RECOMMENDED"
    assert derive_seat_detail(p,snapshot,"r1-s1").details[0].target_height_pct == pytest.approx(2.5)


def test_geometry_manifest_missing_never_becomes_pass():
    p = presentation(.10)
    req = request(p.presentation_id, 4.5).model_copy(update={"renderer_manifest": ()})
    snapshot = analyze_presentation(p, req)
    assert snapshot.response.seats[0].coverage_state == "NONE"
    d=derive_seat_detail(p,snapshot,"r1-s1").details[0]
    assert d.state == "NOT_ANALYZED"
    assert d.element_height_pct is None
    assert "GEOMETRY_NOT_ATTESTED" in d.reason_codes


def test_renderer_rotation_mismatch_never_attests_even_when_aspect_matches():
    p=presentation(.10,rotation=0,aspect=1.0)
    req=AnalyzeRequest(request_id=uuid4(),presentation_id=p.presentation_id,
        display=PhysicalDisplay(width_m=2,height_m=2),room=RoomGeometry(width_m=5,depth_m=5),
        seat_grid=SeatGrid(rows=1,seats_per_row=1,first_row_distance_m=2,row_spacing_m=1,seat_spacing_m=1),
        renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=90,viewport_width=1000,viewport_height=1000),))
    snapshot=analyze_presentation(p,req)
    d=derive_seat_detail(p,snapshot,"r1-s1").details[0]
    assert d.state=="NOT_ANALYZED"
    assert "GEOMETRY_ENGINE_MISMATCH" in d.reason_codes


def test_x_position_does_not_change_bdm_result():
    p = presentation(.02)
    req = AnalyzeRequest(request_id=uuid4(), presentation_id=p.presentation_id,
        display=PhysicalDisplay(width_m=4,height_m=2.25), room=RoomGeometry(width_m=20,depth_m=20),
        seat_grid=SeatGrid(rows=1,seats_per_row=3,first_row_distance_m=9,row_spacing_m=1,seat_spacing_m=5),
        renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=1600,viewport_height=900),))
    snapshot = analyze_presentation(p,req)
    assert len({s.result_state for s in snapshot.response.seats}) == 1
    assert len({s.below_target_count for s in snapshot.response.seats}) == 1


def test_empty_unknown_slide_has_drilldown_detail():
    p=presentation(empty_page=True)
    snapshot=analyze_presentation(p,request(p.presentation_id))
    assert snapshot.response.seats[0].not_analyzed_count==1
    details=derive_seat_detail(p,snapshot,"r1-s1").details
    assert len(details)==1
    assert details[0].state=="NOT_ANALYZED"
    assert details[0].logical_bbox is None
    assert "NO_EXTRACTABLE_TEXT_WITH_GRAPHICS" in details[0].reason_codes


def test_summary_counts_match_derived_detail_states():
    p=presentation(.012)
    req=AnalyzeRequest(request_id=uuid4(),presentation_id=p.presentation_id,
        display=PhysicalDisplay(width_m=4,height_m=2.25),room=RoomGeometry(width_m=8,depth_m=20),
        seat_grid=SeatGrid(rows=3,seats_per_row=2,first_row_distance_m=2,row_spacing_m=3,seat_spacing_m=1),
        renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=1600,viewport_height=900),))
    snapshot=analyze_presentation(p,req)
    for summary in snapshot.response.seats:
        details=derive_seat_detail(p,snapshot,summary.seat_id).details
        counts={state:sum(1 for d in details if d.state==state) for state in ['PASS_TARGET','BELOW_TARGET','NOT_ANALYZED','OUTSIDE_REFERENCE_RANGE']}
        assert summary.meets_target_count==counts['PASS_TARGET']
        assert summary.below_target_count==counts['BELOW_TARGET']
        assert summary.not_analyzed_count==counts['NOT_ANALYZED']
        assert summary.outside_range_count==counts['OUTSIDE_REFERENCE_RANGE']



def test_invalid_seat_uses_invalid_state_not_not_analyzed_count():
    p=presentation(.02)
    req=AnalyzeRequest(request_id=uuid4(),presentation_id=p.presentation_id,
        display=PhysicalDisplay(width_m=4,height_m=2.25),room=RoomGeometry(width_m=4,depth_m=3),
        seat_grid=SeatGrid(rows=1,seats_per_row=3,first_row_distance_m=2,row_spacing_m=1,seat_spacing_m=3),
        renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=1600,viewport_height=900),))
    snap=analyze_presentation(p,req)
    invalid=next(s for s in snap.response.seats if s.geometry_state=='INVALID')
    assert invalid.not_analyzed_count==0
    details=derive_seat_detail(p,snap,invalid.seat_id).details
    assert details and all(d.state=='INVALID_GEOMETRY' for d in details)


def test_computed_vr_boundary_never_creates_false_pass():
    # Choose a square slide/display so active-image height is exactly 0.2 m.
    p = presentation(.009, aspect=1.0)
    req = AnalyzeRequest(
        request_id=uuid4(), presentation_id=p.presentation_id,
        display=PhysicalDisplay(width_m=.2, height_m=.2),
        room=RoomGeometry(width_m=1.0, depth_m=1.0),
        seat_grid=SeatGrid(rows=1, seats_per_row=1, first_row_distance_m=.3, row_spacing_m=.1, seat_spacing_m=.1),
        renderer_manifest=(RendererPageManifest(page_number=1, rotation_deg=0, viewport_width=1000, viewport_height=1000),),
    )
    snap = analyze_presentation(p, req)
    summary = snap.response.seats[0]
    detail = derive_seat_detail(p, snap, summary.seat_id).details[0]
    assert detail.target_height_pct == pytest.approx(1.0)
    assert detail.state == "BELOW_TARGET"
    assert summary.result_state == "REVIEW_RECOMMENDED"


def test_element_height_representation_level_equality_passes_but_materially_lower_does_not():
    p = presentation(math.nextafter(.01, -math.inf), aspect=1.0)
    req = AnalyzeRequest(
        request_id=uuid4(), presentation_id=p.presentation_id,
        display=PhysicalDisplay(width_m=1.0, height_m=1.0),
        room=RoomGeometry(width_m=2.0, depth_m=3.0),
        seat_grid=SeatGrid(rows=1, seats_per_row=1, first_row_distance_m=1.5, row_spacing_m=.5, seat_spacing_m=.5),
        renderer_manifest=(RendererPageManifest(page_number=1, rotation_deg=0, viewport_width=1000, viewport_height=1000),),
    )
    snap = analyze_presentation(p, req)
    assert derive_seat_detail(p, snap, "r1-s1").details[0].state == "PASS_TARGET"

    materially_lower = .01
    for _ in range(8):
        materially_lower = math.nextafter(materially_lower, -math.inf)
    p2 = presentation(materially_lower, aspect=1.0)
    req2 = req.model_copy(update={"presentation_id": p2.presentation_id})
    snap2 = analyze_presentation(p2, req2)
    assert derive_seat_detail(p2, snap2, "r1-s1").details[0].state == "BELOW_TARGET"


def test_mixed_text_and_graphics_keeps_text_coverage_complete_and_discloses_graphics():
    p = presentation(.02)
    page = p.pages[0].model_copy(update={
        "non_text_content": NonTextContentState.PRESENT,
        "reason_codes": ("GRAPHICAL_CONTENT_NOT_ANALYZED",),
    })
    p = p.model_copy(update={"pages": (page,)})
    snap = analyze_presentation(p, request(p.presentation_id, 4.5))
    s = snap.response.seats[0]
    assert s.result_state == "MEETS_TARGET"
    assert s.coverage_state == "COMPLETE"
    assert s.not_analyzed_count == 0
    details = derive_seat_detail(p, snap, s.seat_id).details
    assert not any("GRAPHICAL_CONTENT_NOT_ANALYZED" in d.reason_codes for d in details)
    assert snap.response.slides[0].non_text_content == "PRESENT"


def test_equivalent_distance_units_keep_identical_semantic_result():
    # Independent unit conversions at the API boundary must not alter BDM state.
    p=presentation(.02)
    meters=(3.6576, 12 * .3048, 144 * .0254, 365.76 / 100)
    states=[]
    for distance in meters:
        snap=analyze_presentation(p,request(p.presentation_id,distance))
        detail=derive_seat_detail(p,snap,"r1-s1").details[0]
        states.append((snap.response.seats[0].result_state,detail.state,detail.target_height_pct))
    assert states == [states[0]] * len(states)
