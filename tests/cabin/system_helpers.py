"""Whole-system test harness (Hai). Runs Dispatch, Cabin, Control Center and the virtual
passengers together through An's engine, exactly like ``python -m app.headless``.

Only public entry points are used: ``app.modules.module_set`` builds the real modules and
``simulation.engine.Engine`` runs them. No module internals are read, so a failure here is
an observable system behaviour, not an implementation detail.
"""

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from app.modules import module_set
from contracts.config import CARS, MAX_FLOOR, MAX_LOAD_KG, MIN_FLOOR
from contracts.enums import (
    AlertKind,
    DoorState,
    MissionAction,
    MissionStatus,
    ServiceEventType,
)
from contracts.messages import (
    AlarmPressed,
    Alert,
    CabinInit,
    CabinTelemetry,
    HallCallAck,
    MissionApproval,
    MissionLoadConfirmed,
    MissionRequest,
    MissionStatusUpdate,
    RouteProposal,
    ServiceEvent,
)
from simulation.engine import Engine
from simulation.scenario import PassengerIntent, Scenario
from tests.contract.helpers import assert_door_interlocks, assert_single_owner

E = ServiceEventType
FINAL_MISSION = {MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.REJECTED}


def fleet(**floors: int) -> tuple[CabinInit, ...]:
    """Five idle cars; override a car's floor with e.g. ``fleet(P1=31)``."""
    default = {"P1": 1, "P2": 10, "P3": 20, "P4": 30, "G1": 1}
    default.update(floors)
    return tuple(CabinInit(car_id=c, floor=default[c]) for c in CARS)


def traffic(
    rng: random.Random, n: int, span_s: float, pattern: str, start: int = 0
) -> tuple[PassengerIntent, ...]:
    """Seeded passenger traffic. ``pattern``: up (lobby to floors), down (floors to
    lobby), inter (between random floors) or mixed."""
    out = []
    for i in range(n):
        kind = rng.choice(["up", "down", "inter"]) if pattern == "mixed" else pattern
        if kind == "up":
            origin, dest = MIN_FLOOR, rng.randint(MIN_FLOOR + 1, MAX_FLOOR)
        elif kind == "down":
            origin, dest = rng.randint(MIN_FLOOR + 1, MAX_FLOOR), MIN_FLOOR
        else:
            origin, dest = rng.sample(range(MIN_FLOOR, MAX_FLOOR + 1), 2)
        out.append(
            PassengerIntent(
                t=round(rng.uniform(0.0, span_s), 1),
                passenger_id=f"PAX-{start + i + 1:04d}",
                origin=origin,
                destination=dest,
                weight_kg=rng.choice([55.0, 65.0, 75.0, 75.0, 85.0, 100.0]),
            )
        )
    return tuple(sorted(out, key=lambda p: (p.t, p.passenger_id)))


@dataclass
class StaffBot:
    """Building staff / medical team: confirm loading when the mission car stands open at
    the pickup, and unloading at the destination. ``weight_kg`` is what the scale reads."""

    weight_kg: dict[str, float] = field(default_factory=dict)
    sent: set[tuple[str, MissionAction]] = field(default_factory=set)

    def __call__(self, engine: Engine) -> None:
        snap = engine.snapshot()
        cars = {c["car_id"]: c for c in snap["cars"]}
        for m in snap["missions"]:
            car = cars.get(m["car_id"] or "")
            if car is None or car["mission_id"] != m["mission_id"] or car["door"] != "OPEN":
                continue
            if car["position"] == m["origin"]:
                action = MissionAction.LOADED
            elif car["position"] == m["destination"]:
                action = MissionAction.UNLOADED
            else:
                continue
            if (m["mission_id"], action) in self.sent:
                continue
            if (
                action == MissionAction.UNLOADED
                and (m["mission_id"], MissionAction.LOADED) not in self.sent
            ):
                continue
            self.sent.add((m["mission_id"], action))
            engine.submit(
                MissionLoadConfirmed(
                    t_sim=0.0,
                    mission_id=m["mission_id"],
                    car_id=car["car_id"],
                    action=action,
                    weight_kg=self.weight_kg.get(m["mission_id"], 0.0),
                )
            )


@dataclass
class Run:
    engine: Engine
    snapshots: list[dict] = field(default_factory=list)

    @property
    def bus(self):
        return self.engine.bus

    def of(self, cls, **where):
        return self.bus.select(cls, **where)

    def events(self, kind: ServiceEventType, car: str | None = None) -> list[ServiceEvent]:
        return [
            m
            for m in self.bus.delivered
            if isinstance(m, ServiceEvent) and m.event == kind and (car is None or m.car_id == car)
        ]

    def statuses(self, mission_id: str) -> list[MissionStatus]:
        return [u.status for u in self.of(MissionStatusUpdate, mission_id=mission_id)]

    def final_status(self, mission_id: str) -> MissionStatus | None:
        s = self.statuses(mission_id)
        return s[-1] if s else None

    def alerts(self, kind: AlertKind | None = None) -> list[dict]:
        """Alerts as the operator sees them: the Control Center's record (snapshot). CC
        raises ALARM, FIRE and UNAVAILABLE itself, so those never travel as messages."""
        snap = self.engine.snapshot()["alerts"]
        return [a for a in snap if kind is None or a["kind"] == kind.value]

    def alert_messages(self, kind: AlertKind | None = None) -> list[Alert]:
        """Alerts sent by other modules (e.g. the cabin's FAULT alert)."""
        return [a for a in self.of(Alert) if kind is None or a.kind == kind]


def run(
    scenario: Scenario,
    *,
    step_s: float = 0.5,
    staff: StaffBot | None = None,
    during: Callable[[Engine], None] | None = None,
    snapshot_every_s: float | None = None,
) -> Run:
    """Run ``scenario`` to its end with every module real. ``staff`` confirms missions;
    ``during(engine)`` may submit operator inputs; snapshots are kept if requested."""
    modules, labels = module_set("real", "real", "real")
    assert set(labels.values()) == {"real"}, labels
    engine = Engine(modules, scenario)
    result = Run(engine)
    staff = staff if staff is not None else StaffBot()
    engine.start()
    t, next_snap = 0.0, 0.0
    while engine.now < engine.end_t:
        t = min(t + step_s, engine.end_t)
        engine.advance_to(t)
        if engine.state.value == "STOPPED":
            break
        staff(engine)
        if during is not None:
            during(engine)
        if snapshot_every_s is not None and t >= next_snap:
            result.snapshots.append(engine.snapshot())
            next_snap = t + snapshot_every_s
    return result


# --- system-wide invariants -----------------------------------------------------------


def check_system(r: Run) -> None:
    """Rules that must hold in every whole-system run."""
    bus = r.bus
    cars = sorted({m.car_id for m in bus.delivered if isinstance(m, CabinTelemetry)})
    # S1 SAF-04: a hall call never belongs to two cars at the same time
    assert_single_owner(bus)
    for car in cars:
        # S2 SAF-01, SAF-02: interlocks on every car
        assert_door_interlocks(bus, car)
        # S3 SEN-03: no car moves over the rated load
        heavy = [
            t
            for t in bus.delivered
            if isinstance(t, CabinTelemetry)
            and t.car_id == car
            and t.moving
            and t.load_kg > MAX_LOAD_KG
        ]
        assert not heavy, f"{car} moved with {heavy[0].load_kg} kg at t={heavy[0].t_sim}"
    # S4 CB-04: each hall call is completed once
    served = Counter(m.ref_id for m in r.events(E.HALL_CALL_SERVED))
    twice = {ref: n for ref, n in served.items() if n > 1}
    assert not twice, f"hall calls served more than once: {twice}"
    # S5 EMG-01: an alarm never creates a medical mission
    requests = len(r.of(MissionRequest))
    approvals = len(r.of(MissionApproval))
    assert approvals <= requests, "a mission was approved that nobody requested"
    # S6 G1 is goods-only: no ordinary hall pickups for G1
    g1 = [
        p
        for p in r.of(RouteProposal, car_id="G1")
        if any(s.kind.value == "HALL_PICKUP" for s in p.ordered_stops)
    ]
    assert not g1, f"G1 was offered an ordinary hall call at t={g1[0].t_sim}"


def check_everyone_arrived(r: Run) -> None:
    """S7: every valid passenger reached their destination (nobody stranded)."""
    agent = r.engine.passengers
    snap = agent.snapshot()
    assert snap["waiting"] == 0 and snap["onboard"] == 0, (
        f"stranded passengers: {snap['waiting']} waiting, {snap['onboard']} still on board; "
        f"arrived {snap['arrived']} of {len(agent.intents) - snap['gave_up']}"
    )


def call_created_at(r: Run) -> dict[str, float]:
    """request_id -> created_at, from the first HallCallAck that names it."""
    first: dict[str, float] = {}
    for ack in r.of(HallCallAck):
        if ack.accepted and ack.request_id and ack.request_id not in first:
            first[ack.request_id] = ack.t_sim
    return first


def door_state(r: Run, car: str) -> DoorState | None:
    tel = [m for m in r.bus.delivered if isinstance(m, CabinTelemetry) and m.car_id == car]
    return tel[-1].door if tel else None


def alarm(t: float, car: str) -> AlarmPressed:
    return AlarmPressed(t_sim=t, car_id=car)
