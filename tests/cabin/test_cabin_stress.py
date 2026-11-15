"""EC-11 Random stress test for the Cabin (Hai). Documented in tests/cabin/EDGE_CASES.md.

For each seed, one car receives 10 minutes of random but reproducible inputs: proposals
(good, stale, late, repeated, conflicting), boarding, exits, buttons, obstructions,
faults, recovery, fire and maintenance. A wrapper checks the safety invariants after
EVERY message. Then a calm-down phase clears every fault and checks that the car still
finishes all of its work (no deadlock).

White-box: the checker reads the cabin's internal state, because some faults stop
telemetry on purpose (HEARTBEAT_LOSS) and the invariants must hold even then.
"""

import dataclasses
import random
from collections import Counter

import pytest

import cabin
from cabin.cabin import Cabin
from contracts.config import (
    ACK_TIMEOUT_S,
    MAX_FLOOR,
    MAX_LOAD_KG,
    MIN_FLOOR,
    SECONDS_PER_FLOOR,
)
from contracts.enums import (
    AlertKind,
    CabinMode,
    Direction,
    DoorState,
    FaultKind,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.messages import (
    AlarmPressed,
    Alert,
    CabinDestinationPressed,
    CabinInit,
    CabinTelemetry,
    DoorRequest,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    MaintenanceSet,
    Message,
    PassengerBoard,
    PassengerExit,
    RecoveryApproved,
    RouteProposal,
    RouteReply,
    ServiceEvent,
    Stop,
)
from tests.contract.helpers import make_bus

E = ServiceEventType
SEEDS = range(1, 31)
DURATION_S = 600.0
CALM_S = 3000.0
EPS = 1e-6
SERVED = (E.HALL_CALL_SERVED, E.DEST_SERVED)


class InvariantViolation(AssertionError):
    pass


class WatchedCabin:
    """The real cabin plus a safety check after every handled message."""

    def __init__(self, inner: Cabin, seed: int) -> None:
        self.inner = inner
        self.name = inner.name
        self.car_id = inner.car_id
        self.seed = seed
        self.prev: tuple[float, CabinTelemetry] | None = None
        self.seen_commands: set[str] = set()
        self.stats: Counter[str] = Counter()

    def snapshot(self) -> dict:
        return self.inner.snapshot()

    def handle(self, msg: Message, now: float) -> list[Message]:
        before = self.inner._telemetry(now)
        fresh = isinstance(msg, RouteProposal) and msg.command_id not in self.seen_commands
        if isinstance(msg, RouteProposal):
            self.seen_commands.add(msg.command_id)
        out = self.inner.handle(msg, now)
        after = self.inner._telemetry(now)
        self._check(msg, now, before, after, out, fresh)
        self.prev = (now, after)
        return out

    def _fail(self, now: float, msg: Message, text: str) -> None:
        raise InvariantViolation(f"seed {self.seed}, t={now:.2f}, {type(msg).__name__}: {text}")

    def _check(self, msg, now, before, after, out, fresh) -> None:
        fail = lambda text: self._fail(now, msg, text)  # noqa: E731
        c = self.inner
        for state in (before, after):
            # I1 SAF-01, MOVE-02: never move with the door open or unlocked
            if state.moving and not (state.door == DoorState.CLOSED and state.locked):
                fail(f"moving with door {state.door}, locked={state.locked}")
            # I2 SAF-02, DOOR-02: doors open only stationary in a landing zone
            if state.door != DoorState.CLOSED and (state.moving or not state.door_zone):
                fail(f"door {state.door} while moving or between floors at {state.position}")
            # I3 SYS-01: stay inside the building
            if not MIN_FLOOR - EPS <= state.position <= MAX_FLOOR + EPS:
                fail(f"position {state.position} outside the shaft")
            # I7 EMG-02 and I8 SEN-03: no motion in fault hold or overload
            if state.moving and (state.mode == CabinMode.FAULT_HOLD or state.load_kg > MAX_LOAD_KG):
                fail(f"moving in mode {state.mode} with {state.load_kg} kg")
        # I4 D-16: the route version never goes back
        if after.route_version < before.route_version:
            fail("route version decreased")
        # I5 MOVE-03: no teleporting, within one message or between messages
        if abs(after.position - before.position) > EPS:
            fail(f"position jumped {before.position} -> {after.position} within one instant")
        if self.prev is not None:
            prev_t, prev_state = self.prev
            limit = (now - prev_t) / SECONDS_PER_FLOOR + EPS
            if abs(before.position - prev_state.position) > limit:
                fail(f"moved {before.position - prev_state.position} floors in {now - prev_t} s")
        # I6 5.5, SEN-04: people get on or off only at a landing with the doors open
        if after.passengers != before.passengers and not (
            not before.moving and before.door == DoorState.OPEN and before.door_zone
        ):
            fail(f"passenger count changed with door {before.door}, moving={before.moving}")
        # I9 2.5, DOOR-06: every destination belongs to someone on board
        owners = {s.ref_id for s in c.route if s.kind == StopKind.CAR_DEST}
        if not owners <= set(c.onboard):
            fail(f"destination for someone not on board: {owners - set(c.onboard)}")
        for m in out:
            if isinstance(m, RouteReply) and fresh:
                self.stats["ack" if m.accepted else f"nack:{m.reason}"] += 1
                # I10 CB-01: ACK commits exactly one new version; NACK keeps the route
                if m.accepted and m.committed_version != before.route_version + 1:
                    fail("ACK did not commit exactly one new version")
                if not m.accepted:
                    if m.reason is None:
                        fail("NACK without a reason")
                    if after.stops != before.stops:
                        fail("route changed on NACK")
            elif isinstance(m, ServiceEvent):
                self.stats[m.event.value] += 1
                # I11 CB-04: a stop is served only stationary at its own floor
                if m.event in SERVED and (after.moving or abs(after.position - m.floor) > EPS):
                    fail(f"{m.event} at floor {m.floor} while at {after.position}")
            elif isinstance(m, CabinTelemetry) and FaultKind.HEARTBEAT_LOSS in c.faults:
                fail("telemetry sent while the heartbeat is lost")  # I12 D-11
            elif isinstance(m, Alert):
                self.stats[f"alert:{m.kind}"] += 1
                if m.kind == AlertKind.ALARM:
                    fail("cabin raised an ALARM alert")  # I13 D-19
        self.stats[f"mode:{after.mode}"] += 1


# --- the random input generator -------------------------------------------------------

FAULTS = [
    (FaultKind.MOTION_SERIOUS, 1),
    (FaultKind.DOOR_LOCK_LOSS, 2),
    (FaultKind.POSITION_SENSOR, 2),
    (FaultKind.LOAD_SENSOR, 2),
    (FaultKind.SENSOR_CONFLICT, 2),
    (FaultKind.HEARTBEAT_LOSS, 2),
]
WEIGHTS_KG = [55.0, 70.0, 75.0, 75.0, 90.0, 110.0, 150.0, 250.0]


class RandomInputs:
    def __init__(self, rng: random.Random, watched: WatchedCabin) -> None:
        self.rng = rng
        self.c = watched.inner
        self.car = watched.car_id
        self.n = 0
        self.last: RouteProposal | None = None

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}-{self.n:04d}"

    def _hall(self) -> Stop:
        floor = self.rng.randint(MIN_FLOOR, MAX_FLOOR)
        if floor == MIN_FLOOR:
            direction = Direction.UP
        elif floor == MAX_FLOOR:
            direction = Direction.DOWN
        else:
            direction = self.rng.choice([Direction.UP, Direction.DOWN])
        return Stop(
            floor=floor, kind=StopKind.HALL_PICKUP, ref_id=self._id("HC"), direction=direction
        )

    def proposal(self, t: float) -> RouteProposal | None:
        rng, c = self.rng, self.c
        if c.mission_id is not None:
            return None
        if self.last is not None and rng.random() < 0.10:  # retry: identical payload
            return dataclasses.replace(self.last, msg_id="", t_sim=t, deliver_at=None)
        kept = [s for s in c.route if s.kind in (StopKind.CAR_DEST, StopKind.HALL_PICKUP)]
        if kept and rng.random() < 0.10:
            kept.pop(rng.randrange(len(kept)))  # sometimes drop a stop (maybe a destination)
        stops = kept + [self._hall() for _ in range(rng.randint(0, 2))]
        pos = c._pos(t)
        if rng.random() < 0.3:
            rng.shuffle(stops)
        else:
            stops.sort(key=lambda s: abs(s.floor - pos))
        version = c.version + rng.choice([0, 0, 0, 0, 0, 0, 0, 0, -1, 1])
        conflict = self.last is not None and rng.random() < 0.05
        cmd = self.last.command_id if conflict else self._id("RC")
        late = rng.random() < 0.10
        msg = RouteProposal(
            t_sim=t,
            deliver_at=t + ACK_TIMEOUT_S + 0.5 if late else None,
            command_id=cmd,
            car_id=self.car,
            expected_route_version=version,
            ordered_stops=tuple(stops),
            expiry_ts=t + ACK_TIMEOUT_S,
        )
        if not conflict:
            self.last = msg
        return msg

    def next(self, t: float) -> list[Message]:
        rng, c, car = self.rng, self.c, self.car
        onboard = sorted(c.onboard)
        roll = rng.random()
        if roll < 0.35:
            msg = self.proposal(t)
            return [msg] if msg else []
        if roll < 0.50:
            pid = self._id("PX")
            return [
                PassengerBoard(
                    t_sim=t, car_id=car, passenger_id=pid, weight_kg=rng.choice(WEIGHTS_KG)
                ),
                CabinDestinationPressed(
                    t_sim=t,
                    car_id=car,
                    floor=rng.randint(MIN_FLOOR - 1, MAX_FLOOR + 1),
                    passenger_id=pid,
                ),
            ]
        if roll < 0.58 and onboard:
            floor = rng.randint(MIN_FLOOR - 1, MAX_FLOOR + 1)
            pid = rng.choice(onboard)
            return [CabinDestinationPressed(t_sim=t, car_id=car, floor=floor, passenger_id=pid)]
        if roll < 0.66 and onboard:
            return [PassengerExit(t_sim=t, car_id=car, passenger_id=rng.choice(onboard))]
        if roll < 0.74:
            return [DoorRequest(t_sim=t, car_id=car, open=rng.random() < 0.6)]
        if roll < 0.80:
            return [FaultInjected(t_sim=t, car_id=car, kind=FaultKind.OBSTRUCTION)]
        if roll < 0.85:
            kinds, weights = zip(*FAULTS, strict=True)
            kind = rng.choices(kinds, weights=weights)[0]
            return [FaultInjected(t_sim=t, car_id=car, kind=kind)]
        if roll < 0.91 and c.faults:
            kind = rng.choice(sorted(c.faults))
            return [FaultCleared(t_sim=t, car_id=car, kind=kind)]
        if roll < 0.95:
            return [RecoveryApproved(t_sim=t, car_id=car, approved_by=Role.SUPERVISOR)]
        if roll < 0.97:
            return [FireAlarm(t_sim=t, active=not c.fire)]
        if roll < 0.98:
            return [MaintenanceSet(t_sim=t, car_id=car, on=True, role=Role.MAINTAINER)]
        return [AlarmPressed(t_sim=t, car_id=car)]


def run_random(seed: int) -> WatchedCabin:
    rng = random.Random(seed)
    init = CabinInit(car_id="P1", floor=rng.randint(MIN_FLOOR, MAX_FLOOR))
    watched = WatchedCabin(cabin.create(init), seed)
    bus = make_bus(watched)
    inputs = RandomInputs(rng, watched)
    t = 0.0
    while t < DURATION_S:
        t += round(rng.uniform(0.1, 3.0), 2)
        bus.run_until(t)
        for msg in inputs.next(t):
            bus.inject(msg)

    # Calm-down: the faults are repaired, the fire is over, a supervisor approves recovery,
    # and anyone over the load limit steps out. Then the car must finish all its work.
    c = watched.inner
    t += 1.0
    bus.run_until(t)
    for kind in sorted(c.faults):
        bus.inject(FaultCleared(t_sim=t, car_id="P1", kind=kind))
    bus.inject(FireAlarm(t_sim=t, active=False))
    bus.inject(RecoveryApproved(t_sim=t + 0.1, car_id="P1", approved_by=Role.SUPERVISOR))
    bus.run_until(t + 0.5)
    for pid in sorted(c.onboard, key=lambda p: -c.onboard[p]):
        if c.load_kg <= MAX_LOAD_KG:
            break
        bus.inject(PassengerExit(t_sim=t + 0.5, car_id="P1", passenger_id=pid))
        bus.run_until(t + 0.5)
    bus.run_until(t + CALM_S)

    assert not c.faults and c.hold is None and not c.fire, f"seed {seed}: still held"
    assert c.route == [], f"seed {seed}: unfinished route {c.route} (deadlock)"
    assert not c.moving and c.door == DoorState.CLOSED, f"seed {seed}: not at rest"
    return watched


@pytest.mark.parametrize("seed", SEEDS)
def test_EC_11_random_inputs_never_break_safety_and_never_deadlock(seed):
    run_random(seed)


def test_EC_11_the_random_inputs_reach_the_hard_cases():
    """Guard against a weak generator: the runs must actually hit the dangerous states."""
    total: Counter[str] = Counter()
    for seed in SEEDS:
        total += run_random(seed).stats
    expected = [
        "ack",
        "nack:VERSION_MISMATCH",
        "nack:EXPIRED",
        "nack:FAULT_HOLD",
        "nack:SENSORS_INVALID",
        "nack:REVERSAL_REQUIRED",
        "nack:INSERT_CUTOFF",
        "nack:ONBOARD_COMMITMENT",
        "DOOR_REOPENED",
        "DEST_SERVED",
        "HALL_CALL_SERVED",
        "PASSENGER_EXITED",
        "alert:FAULT",
        f"mode:{CabinMode.FAULT_HOLD}",
        f"mode:{CabinMode.OVERLOAD_HOLD}",
        f"mode:{CabinMode.FULL}",
        f"mode:{CabinMode.FIRE}",
        f"mode:{CabinMode.MAINTENANCE}",
    ]
    missing = [k for k in expected if total[k] == 0]
    assert not missing, f"stress runs never reached: {missing}"
