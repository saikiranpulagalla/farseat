from __future__ import annotations

from dataclasses import dataclass
import math

from .numeric import snap_to_known_boundary

PROFILE_ID = "PUBLIC_AVIXA_BDM_REFERENCE_V1"

# Public BDM reference profile encoded as lower-inclusive intervals. At a shared
# boundary FarSeat intentionally chooses the higher target.
_BANDS = (
    (0.80, 1.00, 0.0050),
    (1.00, 1.50, 0.0075),
    (1.50, 2.00, 0.0100),
    (2.00, 3.00, 0.0150),
    (3.00, 4.00, 0.0200),
    (4.00, 5.00, 0.0250),
    (5.00, 6.00, 0.0300),
    (6.00, 7.00, 0.0350),
    (7.00, 8.00, 0.0400),
    (8.00, 9.00, 0.0450),
    (9.00, 10.00, 0.0500),
)
_BOUNDARIES = tuple(dict.fromkeys([b for low, high, _ in _BANDS for b in (low, high)]))


@dataclass(frozen=True)
class ReferenceLookup:
    target_ratio: float | None
    reason_code: str | None


def lookup_bdm_target(viewing_ratio: float) -> ReferenceLookup:
    if math.isnan(viewing_ratio):
        return ReferenceLookup(None, "INVALID_VIEWING_RATIO")
    if viewing_ratio == math.inf:
        return ReferenceLookup(None, "ABOVE_PUBLIC_BDM_RANGE")
    if viewing_ratio == -math.inf:
        return ReferenceLookup(None, "BELOW_PUBLIC_BDM_RANGE")

    # Snap only representation-level error (<=2 ULP) to one of the finite contractual
    # boundaries. This fixes 0.3/0.2 -> 1.4999999999999998 without moving a materially
    # near-boundary value into another band.
    vr = snap_to_known_boundary(viewing_ratio, _BOUNDARIES)
    if vr < 0.80:
        return ReferenceLookup(None, "BELOW_PUBLIC_BDM_RANGE")
    if vr > 10.0:
        return ReferenceLookup(None, "ABOVE_PUBLIC_BDM_RANGE")
    if vr == 10.0:
        return ReferenceLookup(0.0500, None)
    for low, high, target in _BANDS:
        if low <= vr < high:
            return ReferenceLookup(target, None)
    return ReferenceLookup(None, "ABOVE_PUBLIC_BDM_RANGE")
