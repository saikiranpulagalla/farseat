from uuid import uuid4

from app.analysis import analyze_presentation, derive_seat_detail
from app.models import (
    AnalyzeRequest, CanonicalPageGeometry, ElementMetric, MeasurementConfidence, NonTextContentState,
    NormalizedBBox, PageAnalysisModel, PhysicalDisplay, PresentationModel, RendererPageManifest,
    RoomGeometry, SeatGrid, TextCoverage, TextVisibility,
)


def test_many_cells_are_not_materialized_in_snapshot():
    geom=CanonicalPageGeometry(page_number=1,visible_box_source='MEDIABOX',page_rotation_deg=0,display_width_units=16,display_height_units=9,display_aspect_ratio=16/9)
    elements=tuple(ElementMetric(slide_id='slide-1',element_id=f'e{i}',logical_bbox=NormalizedBBox(x0=.1,y0=.1,x1=.2,y1=.11),text_preview='x',support_state='ANALYZABLE',visibility_state=TextVisibility.VISIBLE,measurement_confidence=MeasurementConfidence.SUPPORTED,measurement_method='FARSEAT_RENDERED_TEXT_HEIGHT_V1',element_height_ratio=.01+(i%5)*.005) for i in range(500))
    pid=uuid4(); page=PageAnalysisModel(page_number=1,geometry=geom,text_coverage=TextCoverage.COMPLETE,non_text_content=NonTextContentState.ABSENT,elements=elements)
    p=PresentationModel(presentation_id=pid,filename='many.pdf',pages=(page,),page_count=1,analyzable_element_count=len(elements),unsupported_element_count=0)
    req=AnalyzeRequest(request_id=uuid4(),presentation_id=pid,display=PhysicalDisplay(width_m=4,height_m=2.25),room=RoomGeometry(width_m=20,depth_m=20),seat_grid=SeatGrid(rows=10,seats_per_row=20,first_row_distance_m=1,row_spacing_m=1,seat_spacing_m=.8),renderer_manifest=(RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=1600,viewport_height=900),))
    snap=analyze_presentation(p,req)
    assert len(snap.response.seats)==200
    assert not hasattr(snap,'details_by_seat')
    one=derive_seat_detail(p,snap,'r10-s20')
    assert one is not None and len(one.details)==500
