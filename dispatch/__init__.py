"""Thanh's deterministic Dispatch Controller (TH-01 through TH-06)."""

from contracts.config import DEFAULT_POLICY, Policy
from contracts.enums import DispatchAlgorithm

from .controller import Dispatch


def create(
    policy: Policy = DEFAULT_POLICY,
    algorithm: DispatchAlgorithm = DispatchAlgorithm.COMMITMENT,
) -> Dispatch:
    """Create an independent dispatcher using only the shared message contracts."""
    return Dispatch(policy, algorithm)


__all__ = ["Dispatch", "create"]
