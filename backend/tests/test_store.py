from uuid import uuid4

from app.analysis import analyze_presentation
from app.models import (
    AnalyzeRequest, CanonicalPageGeometry, ElementMetric, MeasurementConfidence, NonTextContentState,
    NormalizedBBox, PageAnalysisModel, PhysicalDisplay, PresentationModel, RendererPageManifest,
    RoomGeometry, SeatGrid, TextCoverage, TextVisibility,
)
import app.store as store_module
from app.store import EphemeralStore


def make_presentation():
    pid=uuid4(); geom=CanonicalPageGeometry(page_number=1,visible_box_source='MEDIABOX',page_rotation_deg=0,display_width_units=16,display_height_units=9,display_aspect_ratio=16/9)
    el=ElementMetric(slide_id='slide-1',element_id='p1-e1',logical_bbox=NormalizedBBox(x0=.1,y0=.1,x1=.2,y1=.12),text_preview='x',support_state='ANALYZABLE',visibility_state=TextVisibility.VISIBLE,measurement_confidence=MeasurementConfidence.SUPPORTED,measurement_method='FARSEAT_RENDERED_TEXT_HEIGHT_V1',element_height_ratio=.02)
    page=PageAnalysisModel(page_number=1,geometry=geom,text_coverage=TextCoverage.COMPLETE,non_text_content=NonTextContentState.ABSENT,elements=(el,))
    return PresentationModel(presentation_id=pid,filename='x.pdf',pages=(page,),page_count=1,analyzable_element_count=1,unsupported_element_count=0)


def make_request(pid):
    return AnalyzeRequest(request_id=uuid4(),presentation_id=pid,display=PhysicalDisplay(width_m=4,height_m=2.25),room=RoomGeometry(width_m=8,depth_m=10),seat_grid=SeatGrid(rows=1,seats_per_row=1,first_row_distance_m=4,row_spacing_m=1,seat_spacing_m=1),renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=1600,viewport_height=900),))


def test_store_analysis_is_factorized_no_detail_matrix():
    s=EphemeralStore(); p=make_presentation(); token=s.put_presentation(p); snap=analyze_presentation(p,make_request(p.presentation_id)); assert s.put_analysis(snap,token)=='OK'
    stored=s.get_analysis(snap.analysis_id,token)
    assert stored is not None
    assert not hasattr(stored,'details_by_seat')


def test_expired_session_only_revealed_to_matching_capability(monkeypatch):
    monkeypatch.setattr(store_module,'TTL_SECONDS',-1)
    s=EphemeralStore(); p=make_presentation(); token=s.put_presentation(p)
    status,_=s.lookup_presentation(p.presentation_id,token)
    assert status=='EXPIRED'
    status,_=s.lookup_presentation(p.presentation_id,'wrong')
    assert status=='NOT_FOUND'



def test_analysis_commit_cannot_resurrect_deleted_parent():
    s=EphemeralStore(); p=make_presentation(); token=s.put_presentation(p)
    snap=analyze_presentation(p,make_request(p.presentation_id))
    s.delete_presentation(p.presentation_id)
    assert s.put_analysis(snap,token)=='NOT_FOUND'
    assert s.get_analysis(snap.analysis_id,token) is None


def test_store_capacity_rejects_new_presentation_without_evicting_existing(monkeypatch):
    monkeypatch.setattr(store_module,'MAX_PRESENTATIONS',1)
    monkeypatch.setattr(store_module,'MAX_PRESENTATION_WEIGHT',10_000_000)
    s=EphemeralStore(); first=make_presentation(); token=s.put_presentation(first)
    second=make_presentation()
    import pytest
    with pytest.raises(ValueError, match='STORE_CAPACITY_REACHED'):
        s.put_presentation(second)
    assert s.get_presentation(first.presentation_id,token) is not None


def test_analysis_capacity_rejects_new_snapshot_without_evicting_existing(monkeypatch):
    monkeypatch.setattr(store_module,'MAX_ANALYSES',1)
    s=EphemeralStore(); p=make_presentation(); token=s.put_presentation(p)
    first=analyze_presentation(p,make_request(p.presentation_id)); assert s.put_analysis(first,token)=='OK'
    second=analyze_presentation(p,make_request(p.presentation_id)); assert s.put_analysis(second,token)=='CAPACITY'
    assert s.get_analysis(first.analysis_id,token) is not None
    assert s.get_analysis(second.analysis_id,token) is None
