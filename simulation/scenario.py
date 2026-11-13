"""What a scenario is: initial fleet, virtual passengers, scripted inputs, network rules."""

from dataclasses import dataclass

from contracts.config import DEFAULT_PASSENGER_KG, DEFAULT_POLICY, Policy
from contracts.enums import Direction
from contracts.messages import CabinInit, Message

DROP = "drop"
DELAY = "delay"


@dataclass(frozen=True, kw_only=True)
class PassengerIntent:
    """One virtual passenger: presses the Hall Call at ``t`` and rides to ``destination`` (D-56)."""

    t: float
    passenger_id: str
    origin: int
    destination: int
    weight_kg: float = DEFAULT_PASSENGER_KG

    @property
    def direction(self) -> Direction:
        return Direction.UP if self.destination > self.origin else Direction.DOWN


@dataclass(frozen=True, kw_only=True)
class NetworkRule:
    """Drop or delay the ``nth`` module output matching ``msg_type`` / ``car_id`` (D-57)."""

    msg_type: str  # message class name, e.g. "RouteReply"
    action: str  # DROP or DELAY
    car_id: str | None = None
    nth: int = 1
    delay_s: float = 0.0


@dataclass(frozen=True, kw_only=True)
class Scenario:
    name: str
    fleet: tuple[CabinInit, ...]
    duration_s: float
    description: str = ""
    passengers: tuple[PassengerIntent, ...] = ()
    inputs: tuple[Message, ...] = ()  # scripted inputs (faults, missions, LOADED, ...)
    policy: Policy = DEFAULT_POLICY
    network: tuple[NetworkRule, ...] = ()
