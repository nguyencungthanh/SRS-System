"""Cabin Controller (Hai): motion, doors, load, route ACK/NACK, faults, mission execution.

``create(init)`` is called once per car (contracts/interfaces.py CabinFactory).
"""

from cabin.cabin import Cabin
from contracts.messages import CabinInit


def create(init: CabinInit) -> Cabin:
    return Cabin(init)


__all__ = ["Cabin", "create"]
