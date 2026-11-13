"""Helpers shared by the contract tests (owned by An). Uses only contracts/, fakes/, simulation/."""

from typing import Any

from contracts.codec import to_json
from contracts.config import CARS
from contracts.enums import (
    CabinMode,
    Direction,
    DoorState,
    NackReason,
    ServiceEventType,
    StopKind,
)
from contracts.interfaces import Module
from contracts.messages import (
    TIMER_START,
    CabinInit,
    CabinTelemetry,
    Message,
    Passenger,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    ServiceEvent,
    Stop,
    Timer,
    payload_key,
)
from fakes.cabin import FakeCabin
from simulation.bus import Bus

EPS = 1e-9

# --- driving a module directly ---------------------------------------------------------


def start(module: Module, t: float = 0.0) -> Timer:
    """The start timer the engine sends every module at t=0 (D-08)."""
    return Timer(t_sim=t, target=module.name, key=TIMER_START)


def feed(module: Module, *msgs: Message) -> list[Message]:
    """Deliver ``msgs`` in order, each at its own t_sim, and collect everything returned."""
    out: list[Message] = []
    for msg in msgs:
        out.extend(module.handle(msg, msg.t_sim))
    return out


def tel(car_id: str, position: float, t: float = 0.0, **over: Any) -> CabinTelemetry:
    """Telemetry with sensible defaults: idle, doors closed and locked, AUTO, empty."""
    fields: dict[str, Any] = {
        "t_sim": t,
        "car_id": car_id,
        "position": float(position),
        "direction": Direction.IDLE,
        "moving": False,
        "door": DoorState.CLOSED,
        "locked": True,
        "door_zone": float(position).is_integer(),
        "obstruction": False,
        "load_kg": 0.0,
        "passengers": 0,
        "mode": CabinMode.AUTO,
        "route_version": 0,
    }
    fields.update(over)
    return CabinTelemetry(**fields)


def of_type(msgs: list[Message], cls: type, **where: Any) -> list[Any]:
    return [m for m in msgs if type(m) is cls and all(getattr(m, k) == v for k, v in where.items())]


# --- stops ----------------------------------------------------------------------------


def hall(floor: int, direction: Direction, request_id: str) -> Stop:
    return Stop(floor=floor, kind=StopKind.HALL_PICKUP, ref_id=request_id, direction=direction)


def dest(floor: int, passenger_id: str) -> Stop:
    return Stop(floor=floor, kind=StopKind.CAR_DEST, ref_id=passenger_id)


def floors(stops: tuple[Stop, ...]) -> list[int]:
    return [s.floor for s in stops]


# --- fleets from the SRS examples ------------------------------------------------------


def at01_fleet() -> list[CabinInit]:
    """P1 idle at 31; every other car parked far away at floor 1."""
    return [CabinInit(car_id="P1", floor=31)] + [
        CabinInit(car_id=c, floor=1) for c in CARS if c != "P1"
    ]


def at03_fleet() -> list[CabinInit]:
    """SRS 4.9 C / TC-MED-01."""
    return [
        CabinInit(car_id="P1", floor=36, passengers=(Passenger(passenger_id="A", destination=40),)),
        CabinInit(car_id="P2", floor=12),
        CabinInit(car_id="P3", floor=5, mode=CabinMode.MAINTENANCE),
        CabinInit(car_id="P4", floor=28, passengers=(Passenger(passenger_id="B", destination=22),)),
        CabinInit(car_id="G1", floor=3),
    ]


def at04_fleet() -> list[CabinInit]:
    """SRS 4.9 D / TC-FRT-02: P1 carries a passenger 27 -> 5; G1 is unavailable."""
    return [
        CabinInit(
            car_id="P1", floor=27, passengers=(Passenger(passenger_id="PAX-0001", destination=5),)
        ),
        CabinInit(car_id="G1", floor=3, mode=CabinMode.MAINTENANCE),
    ]


# --- running on the bus ----------------------------------------------------------------


def make_bus(*modules: Module) -> Bus:
    bus = Bus(modules)
    bus.start()
    return bus


def events(bus: Bus, car_id: str, *kinds: ServiceEventType) -> list[ServiceEvent]:
    return [
        m
        for m in bus.delivered
        if isinstance(m, ServiceEvent) and m.car_id == car_id and (not kinds or m.event in kinds)
    ]


def times(msgs: list[Message]) -> list[float]:
    return [m.t_sim for m in msgs]


def telemetry(bus: Bus, car_id: str) -> list[CabinTelemetry]:
    return [m for m in bus.delivered if isinstance(m, CabinTelemetry) and m.car_id == car_id]


def last_tel(bus: Bus, car_id: str) -> CabinTelemetry:
    return telemetry(bus, car_id)[-1]


def proposals(bus: Bus, car_id: str | None = None) -> list[RouteProposal]:
    return [
        m
        for m in bus.delivered
        if isinstance(m, RouteProposal) and (car_id is None or m.car_id == car_id)
    ]


def proposals_with(bus: Bus, request_id: str) -> list[RouteProposal]:
    return [p for p in proposals(bus) if any(s.ref_id == request_id for s in p.ordered_stops)]


def calls(module: Module) -> dict[str, dict]:
    return {c["request_id"]: c for c in module.snapshot()["calls"]}


def log_text(bus: Bus) -> list[str]:
    return [to_json(m) for m in bus.delivered]


# --- invariants -----------------------------------------------------------------------


def assert_single_owner(bus: Bus) -> None:
    """SAF-04: no hall call is listed in the stops of two cars at the same time."""
    stops_by_car: dict[str, set[str]] = {}
    for msg in bus.delivered:
        if not isinstance(msg, CabinTelemetry):
            continue
        stops_by_car[msg.car_id] = {
            s.ref_id for s in msg.stops if s.kind == StopKind.HALL_PICKUP and s.ref_id
        }
        owners: dict[str, list[str]] = {}
        for car, refs in stops_by_car.items():
            for ref in refs:
                owners.setdefault(ref, []).append(car)
        doubled = {ref: cars for ref, cars in owners.items() if len(cars) > 1}
        assert not doubled, f"t={msg.t_sim}: hall call owned by two cars: {doubled}"


def assert_command_ids_consistent(bus: Bus) -> None:
    """D-17: every RouteProposal with the same command_id has the same payload."""
    seen: dict[str, tuple] = {}
    for p in proposals(bus):
        key = payload_key(p)
        assert seen.setdefault(p.command_id, key) == key, f"{p.command_id} reused differently"


def assert_door_interlocks(bus: Bus, car_id: str) -> None:
    """SAF-01, SAF-02, MOVE-02, DOOR-02 on every telemetry sample of one car."""
    for t in telemetry(bus, car_id):
        if t.moving:
            assert t.door == DoorState.CLOSED and t.locked, (
                f"moving with door {t.door} at {t.t_sim}"
            )
        if t.door != DoorState.CLOSED:
            assert not t.moving and t.door_zone, f"door {t.door} while moving/out of zone"


# --- a cabin that misbehaves on purpose (AT-05, NACK paths) ----------------------------


class ScriptedCabin:
    """A FakeCabin whose answers to RouteProposal are scripted.

    mode="ack_lost":   the cabin accepts the route but its RouteReply is lost.
    mode="drop_first": the first RouteProposal never arrives; later ones work normally.
    mode="nack":       every RouteProposal gets NACK(reason); the route never changes.
    """

    def __init__(self, init: CabinInit, mode: str, reason: NackReason = NackReason.FAULT_HOLD):
        self.inner = FakeCabin(init)
        self.name = self.inner.name
        self.car_id = self.inner.car_id
        self.mode = mode
        self.reason = reason
        self.dropped: set[str] = set()

    def handle(self, msg: Message, now: float) -> list[Message]:
        if isinstance(msg, RouteProposal):
            if self.mode == "nack":
                return [
                    RouteReply(
                        t_sim=now,
                        command_id=msg.command_id,
                        car_id=self.car_id,
                        accepted=False,
                        committed_version=self.inner.route_version,
                        reason=self.reason,
                    )
                ]
            if self.mode == "drop_first" and not self.dropped:
                self.dropped.add(msg.command_id)
                return []
            out = self.inner.handle(msg, now)
            if self.mode == "ack_lost":
                out = [m for m in out if not isinstance(m, RouteReply)]
            return out
        if isinstance(msg, RouteVersionQuery) and msg.command_id in self.dropped:
            return [
                RouteVersionReport(
                    t_sim=now,
                    command_id=msg.command_id,
                    car_id=self.car_id,
                    seen=False,
                    accepted=None,
                    route_version=self.inner.route_version,
                )
            ]
        return self.inner.handle(msg, now)

    def snapshot(self) -> dict:
        return self.inner.snapshot()
