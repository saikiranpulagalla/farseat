import math
import pytest

from app.reference import lookup_bdm_target


@pytest.mark.parametrize("vr,target", [
    (0.80, 0.0050), (0.999999, 0.0050), (1.00, 0.0075), (1.499999, 0.0075),
    (1.50, 0.0100), (2.00, 0.0150), (3.00, 0.0200), (4.00, 0.0250),
    (5.00, 0.0300), (6.00, 0.0350), (7.00, 0.0400), (8.00, 0.0450),
    (9.00, 0.0500), (10.00, 0.0500),
])
def test_public_bdm_boundaries(vr, target):
    got = lookup_bdm_target(vr)
    assert got.reason_code is None
    assert got.target_ratio == pytest.approx(target)


def test_reference_never_extrapolates():
    assert lookup_bdm_target(0.799999).reason_code == "BELOW_PUBLIC_BDM_RANGE"
    assert lookup_bdm_target(10.000001).reason_code == "ABOVE_PUBLIC_BDM_RANGE"


def test_decimal_division_boundary_snaps_to_conservative_higher_band():
    # 0.3 / 0.2 is one binary ULP below 1.5, but mathematically equals the
    # contractual boundary. The conservative higher band must win.
    vr = 0.3 / 0.2
    assert vr < 1.5
    assert lookup_bdm_target(vr).target_ratio == pytest.approx(0.0100)


def test_ulp_snapping_does_not_broaden_band_materially():
    # A value several ULPs below the boundary is genuinely in the lower band.
    vr = 1.5
    for _ in range(8):
        vr = math.nextafter(vr, -math.inf)
    assert lookup_bdm_target(vr).target_ratio == pytest.approx(0.0075)


def test_decimal_division_lower_range_boundary_is_not_false_outside():
    vr = 2.4 / 3.0
    assert vr < 0.8
    got = lookup_bdm_target(vr)
    assert got.reason_code is None
    assert got.target_ratio == pytest.approx(0.0050)
