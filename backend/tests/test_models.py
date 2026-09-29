import math
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.models import AnalyzeRequest, PhysicalDisplay, RendererPageManifest, RoomGeometry, SeatGrid


def test_nan_inf_rejected():
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            PhysicalDisplay(width_m=bad, height_m=1.0)


def test_201_seats_rejected():
    with pytest.raises(ValidationError):
        SeatGrid(rows=1,seats_per_row=201,first_row_distance_m=1,row_spacing_m=1,seat_spacing_m=1)


def test_display_must_fit_room_width():
    with pytest.raises(ValidationError):
        AnalyzeRequest(request_id=uuid4(),presentation_id=uuid4(),display=PhysicalDisplay(width_m=6,height_m=2),
            room=RoomGeometry(width_m=5,depth_m=10),seat_grid=SeatGrid(rows=1,seats_per_row=1,first_row_distance_m=2,row_spacing_m=1,seat_spacing_m=1),renderer_manifest=())


def test_renderer_manifest_page_numbers_unique():
    m=RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=100,viewport_height=100)
    with pytest.raises(ValidationError):
        AnalyzeRequest(request_id=uuid4(),presentation_id=uuid4(),display=PhysicalDisplay(width_m=2,height_m=2),
            room=RoomGeometry(width_m=5,depth_m=10),seat_grid=SeatGrid(rows=1,seats_per_row=1,first_row_distance_m=2,row_spacing_m=1,seat_spacing_m=1),renderer_manifest=(m,m))
