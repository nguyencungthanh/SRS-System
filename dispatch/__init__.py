"""Thanh's deterministic Dispatch Controller (TH-01 through TH-06)."""

from contracts.config import (
    DEFAULT_POLICY,
    WEAR_MAX_PENALTY_S,
    WEAR_PENALTY_S_PER_FLOOR,
    Policy,
)
from contracts.enums import DispatchAlgorithm

from .controller import Dispatch


def create(
    policy: Policy = DEFAULT_POLICY,
    algorithm: DispatchAlgorithm = DispatchAlgorithm.COMMITMENT,
    wear_gain: float | None = WEAR_PENALTY_S_PER_FLOOR,
    wear_cap_s: float = WEAR_MAX_PENALTY_S,
) -> Dispatch:
    """Create an independent dispatcher using only the shared message contracts.

    Wear balancing (D-78): a car's ETA gets ``wear_gain`` seconds per floor it has
    travelled beyond the least-worn candidate, at most ``wear_cap_s``; ``wear_gain=None``
    turns balancing off.
    """
    return Dispatch(policy, algorithm, wear_gain, wear_cap_s)


__all__ = ["Dispatch", "create"]
