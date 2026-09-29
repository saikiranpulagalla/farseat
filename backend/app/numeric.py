from __future__ import annotations

import math
from decimal import Decimal, localcontext

# FarSeat uses a deliberately tiny ULP-based tolerance only to neutralize binary
# floating-point representation error at exact contractual boundaries. It is not a
# general epsilon and must never broaden a reference band by a human-scale amount.
MAX_BOUNDARY_ULPS = 2


def within_ulps(a: float, b: float, *, max_ulps: int = MAX_BOUNDARY_ULPS) -> bool:
    if not (math.isfinite(a) and math.isfinite(b)):
        return a == b
    tolerance = max(math.ulp(a), math.ulp(b)) * max_ulps
    return abs(a - b) <= tolerance


def snap_to_known_boundary(value: float, boundaries: tuple[float, ...], *, max_ulps: int = MAX_BOUNDARY_ULPS) -> float:
    if not math.isfinite(value):
        return value
    for boundary in boundaries:
        if within_ulps(value, boundary, max_ulps=max_ulps):
            return boundary
    return value


def meets_or_equals(actual: float, target: float) -> bool:
    """Return true for actual >= target, including representation-level equality.

    Values materially below the target remain below. Only a <=2 ULP discrepancy is
    treated as equality, which is enough for cases such as 4/400 vs 0.01 without
    introducing a broad classification epsilon.
    """
    if not (math.isfinite(actual) and math.isfinite(target)):
        return False
    return actual >= target or within_ulps(actual, target)


def decimal_affine(start: float, step: float, index: float) -> float:
    """Evaluate start + step*index using decimal spellings of user-facing inputs.

    Room/seat dimensions originate as JSON decimal numbers. Using Decimal(str(x))
    prevents repeated binary addition from moving a mathematically exact seat just
    beyond a room boundary (for example 0.1 + 2*0.1 -> 0.30000000000000004).
    """
    with localcontext() as ctx:
        ctx.prec = 34
        return float(Decimal(str(start)) + Decimal(str(step)) * Decimal(str(index)))


def le_with_ulp(a: float, b: float) -> bool:
    return a < b or a == b or within_ulps(a, b)


def ge_with_ulp(a: float, b: float) -> bool:
    return a > b or a == b or within_ulps(a, b)
