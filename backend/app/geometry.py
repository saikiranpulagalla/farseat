from __future__ import annotations

from dataclasses import dataclass
import math

from .models import CanonicalPageGeometry, PhysicalDisplay, RendererPageManifest, Seat, SeatGrid, RoomGeometry
from .numeric import decimal_affine, le_with_ulp, ge_with_ulp


@dataclass(frozen=True)
class ActiveImage:
    width_m: float
    height_m: float


@dataclass(frozen=True)
class GeometryAttestation:
    state: str
    backend_aspect_ratio: float
    renderer_aspect_ratio: float | None
    backend_rotation_deg: int
    renderer_rotation_deg: int | None


def active_image(display: PhysicalDisplay, slide_aspect_ratio: float) -> ActiveImage:
    if not math.isfinite(slide_aspect_ratio) or slide_aspect_ratio <= 0:
        raise ValueError("invalid slide aspect ratio")
    display_aspect = display.width_m / display.height_m
    if not math.isfinite(display_aspect) or display_aspect <= 0:
        raise ValueError("invalid display aspect ratio")
    if slide_aspect_ratio >= display_aspect:
        width = display.width_m
        height = width / slide_aspect_ratio
    else:
        height = display.height_m
        width = height * slide_aspect_ratio
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("invalid active image geometry")
    return ActiveImage(width_m=width, height_m=height)


def attest_geometry(geometry: CanonicalPageGeometry, manifest: RendererPageManifest | None, tol: float = 1e-6) -> GeometryAttestation:
    if manifest is None:
        return GeometryAttestation(
            "UNVERIFIED", geometry.display_aspect_ratio, None, geometry.page_rotation_deg, None
        )
    renderer_aspect = manifest.viewport_width / manifest.viewport_height
    if not math.isfinite(renderer_aspect) or renderer_aspect <= 0:
        return GeometryAttestation(
            "MISMATCH", geometry.display_aspect_ratio, renderer_aspect, geometry.page_rotation_deg, manifest.rotation_deg
        )
    aspect_matches = math.isclose(
        geometry.display_aspect_ratio, renderer_aspect, rel_tol=tol, abs_tol=tol
    )
    rotation_matches = geometry.page_rotation_deg == manifest.rotation_deg

    backend_view = (geometry.visible_x0_units, geometry.visible_y0_units, geometry.visible_x1_units, geometry.visible_y1_units)
    renderer_view = (manifest.view_x0, manifest.view_y0, manifest.view_x1, manifest.view_y1)
    if any(v is not None for v in backend_view):
        if any(v is None for v in renderer_view) or any(v is None for v in backend_view):
            view_matches = False
        else:
            assert all(v is not None for v in backend_view + renderer_view)
            scale = max(*(abs(float(v)) for v in backend_view + renderer_view), 1.0)
            view_matches = all(
                math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol * scale)
                for a, b in zip(backend_view, renderer_view)
            )
    else:
        view_matches = True

    return GeometryAttestation(
        "MATCH" if aspect_matches and rotation_matches and view_matches else "MISMATCH",
        geometry.display_aspect_ratio,
        renderer_aspect,
        geometry.page_rotation_deg,
        manifest.rotation_deg,
    )


def generate_seats(grid: SeatGrid, room: RoomGeometry) -> tuple[Seat, ...]:
    seats: list[Seat] = []
    offset0 = (grid.seats_per_row - 1) / 2.0
    for r in range(grid.rows):
        y = decimal_affine(grid.first_row_distance_m, grid.row_spacing_m, r)
        for s in range(grid.seats_per_row):
            x = decimal_affine(grid.center_x_m, grid.seat_spacing_m, s - offset0)
            seats.append(Seat(seat_id=f"r{r+1}-s{s+1}", row=r + 1, number=s + 1, x_m=x, y_m=y))
    return tuple(seats)


def seat_geometry_valid(seat: Seat, room: RoomGeometry) -> bool:
    if not (math.isfinite(seat.x_m) and math.isfinite(seat.y_m)):
        return False
    if seat.y_m <= 0 or not le_with_ulp(seat.y_m, room.depth_m):
        return False
    half = room.width_m / 2.0
    return ge_with_ulp(seat.x_m, -half) and le_with_ulp(seat.x_m, half)
