import random
import pytest

from app.geometry import active_image, attest_geometry, generate_seats, seat_geometry_valid, generate_seats, seat_geometry_valid
from app.models import CanonicalPageGeometry, PhysicalDisplay, RendererPageManifest, RoomGeometry, SeatGrid


def test_contain_16_9():
    d = PhysicalDisplay(width_m=4.0, height_m=2.25)
    a = active_image(d, 16/9)
    assert a.width_m == pytest.approx(4.0)
    assert a.height_m == pytest.approx(2.25)


def test_contain_4_3_on_16_9():
    d = PhysicalDisplay(width_m=4.0, height_m=2.25)
    a = active_image(d, 4/3)
    assert a.height_m == pytest.approx(2.25)
    assert a.width_m == pytest.approx(3.0)


def test_generated_grid_and_containment():
    room = RoomGeometry(width_m=6.0, depth_m=10.0)
    grid = SeatGrid(rows=2, seats_per_row=3, first_row_distance_m=2.0,
                    row_spacing_m=2.0, seat_spacing_m=1.0)
    seats = generate_seats(grid, room)
    assert [s.seat_id for s in seats] == ["r1-s1","r1-s2","r1-s3","r2-s1","r2-s2","r2-s3"]
    assert all(seat_geometry_valid(s, room) for s in seats)


def test_active_image_properties_1000_cases():
    rng = random.Random(20260927)
    for _ in range(1000):
        w = rng.uniform(0.5, 20); h = rng.uniform(0.5, 10); aspect = rng.uniform(0.3, 4.0)
        a = active_image(PhysicalDisplay(width_m=w, height_m=h), aspect)
        assert a.width_m <= w + 1e-12
        assert a.height_m <= h + 1e-12
        assert a.width_m / a.height_m == pytest.approx(aspect, rel=1e-12)


def test_geometry_attestation_checks_rotation_and_aspect():
    g=CanonicalPageGeometry(page_number=1,visible_box_source='MEDIABOX',page_rotation_deg=0,display_width_units=10,display_height_units=10,display_aspect_ratio=1)
    assert attest_geometry(g,RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=500,viewport_height=500)).state=='MATCH'
    assert attest_geometry(g,RendererPageManifest(page_number=1,rotation_deg=90,viewport_width=500,viewport_height=500)).state=='MISMATCH'



def test_geometry_attestation_checks_visible_frame_origin_not_only_size():
    g=CanonicalPageGeometry(page_number=1,visible_box_source='CROPBOX_INTERSECT_MEDIABOX',page_rotation_deg=0,
        display_width_units=400,display_height_units=300,display_aspect_ratio=4/3,
        visible_x0_units=10,visible_y0_units=20,visible_x1_units=410,visible_y1_units=320)
    good=RendererPageManifest(page_number=1,rotation_deg=0,viewport_width=400,viewport_height=300,
        view_x0=10,view_y0=20,view_x1=410,view_y1=320)
    shifted=good.model_copy(update={'view_x0':0.0,'view_x1':400.0})
    assert attest_geometry(g,good).state=='MATCH'
    assert attest_geometry(g,shifted).state=='MISMATCH'


def test_active_image_rejects_finite_underflow_geometry():
    with pytest.raises(ValueError):
        active_image(PhysicalDisplay(width_m=1e-300,height_m=1.0),1e300)


def test_decimal_grid_exact_room_boundary_remains_valid():
    room=RoomGeometry(width_m=2.0, depth_m=0.3)
    grid=SeatGrid(rows=3,seats_per_row=1,first_row_distance_m=0.1,row_spacing_m=0.1,seat_spacing_m=0.5)
    seats=generate_seats(grid,room)
    assert seats[-1].y_m == 0.3
    assert seat_geometry_valid(seats[-1],room)
