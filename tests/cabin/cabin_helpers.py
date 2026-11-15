"""Helpers for Hai's cabin tests. One real cabin on the bus; the test plays everyone else."""

from typing import Any

import cabin
from contracts.config import ACK_TIMEOUT_S
from contracts.enums import Direction, ServiceEventType, StopKind
from contracts.messages import (
    CabinInit,
    CabinTelemetry,
    Message,
    PassengerBoard,
    RouteProposal,
    Stop,
)
from tests.contract.helpers import events, last_tel, make_bus, telemetry

E = ServiceEventType
UP, DOWN = Direction.UP, Direction.DOWN


def bus_with(init: CabinInit):
    module = cabin.create(init)
    return make_bus(module), module


def hall(floor: int, direction: Direction, ref: str) -> Stop:
    return Stop(floor=floor, kind=StopKind.HALL_PICKUP, ref_id=ref, direction=direction)


def dest(floor: int, pid: str) -> Stop:
    return Stop(floor=floor, kind=StopKind.CAR_DEST, ref_id=pid)


def pickup(floor: int, mid: str = "MS-1") -> Stop:
    return Stop(floor=floor, kind=StopKind.MISSION_PICKUP, ref_id=mid)


def dropoff(floor: int, mid: str = "MS-1") -> Stop:
    return Stop(floor=floor, kind=StopKind.MISSION_DROPOFF, ref_id=mid)


def propose(bus, t, stops, version, cmd="RC-1", car="P1", **extra) -> RouteProposal:
    return bus.inject(
        RouteProposal(
            t_sim=t,
            command_id=cmd,
            car_id=car,
            expected_route_version=version,
            ordered_stops=tuple(stops),
            expiry_ts=t + ACK_TIMEOUT_S,
            **extra,
        )
    )


def version(bus, car: str) -> int:
    return last_tel(bus, car).route_version


def stops_now(bus, car: str) -> tuple[Stop, ...]:
    return last_tel(bus, car).stops


def at(bus, car: str, kind: ServiceEventType, floor: int | None = None) -> list[float]:
    return [e.t_sim for e in events(bus, car, kind) if floor is None or e.floor == floor]


def tel_between(bus, car: str, start: float, end: float) -> list[CabinTelemetry]:
    return [t for t in telemetry(bus, car) if start < t.t_sim < end]


def inject_all(bus, msgs: list[Message]) -> None:
    """Queue future inputs up front, so equal-time ordering is decided by priority."""
    for msg in sorted(msgs, key=lambda m: m.t_sim):
        bus.inject(msg)


def board_many(bus, t: float, car: str, count: int, kg: float = 75.0, prefix: str = "P"):
    pids = [f"{prefix}{i:02d}" for i in range(1, count + 1)]
    for pid in pids:
        bus.inject(PassengerBoard(t_sim=t, car_id=car, passenger_id=pid, weight_kg=kg))
    return pids


def doors_open_at_12(car: str = "P2"):
    """Car idle at floor 12 serves a call there: OPENING 0-1 s, OPEN 1-5 s, CLOSING 5-6 s."""
    bus, module = bus_with(CabinInit(car_id=car, floor=12))
    propose(bus, 0.0, [hall(12, UP, "HC-1")], 0, car=car)
    bus.run_until(1.0)
    return bus, module


def first(values: list[Any]) -> Any:
    return values[0] if values else None
