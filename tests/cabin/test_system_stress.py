"""Whole-system random stress test SY-20 (Hai). Documented in SYSTEM_EDGE_CASES.md.

For each seed the REAL Dispatch, Cabin and Control Center run 10 minutes of random traffic
with random breakdowns, sensor faults, lost telemetry, maintenance, fire, alarms, medical
and freight missions and an unreliable network. Every disruption is repaired before the
end. Then a 40-minute calm-down must bring every passenger home and finish every mission.

Inputs are fixed by the seed, so every failure replays exactly: run the seed again.
"""

import random
from collections import Counter

import pytest

from contracts.config import CARS, MAX_FLOOR, MIN_FLOOR, PASSENGER_CARS
from contracts.enums import FaultKind, MissionStatus, MissionType, Role
from contracts.messages import (
    AlarmPressed,
    CabinInit,
    CabinTelemetry,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    MaintenanceSet,
    MissionRequest,
    MissionStatusUpdate,
    RecoveryRequest,
)
from simulation.scenario import DELAY, DROP, NetworkRule, Scenario
from tests.cabin.system_helpers import (
    FINAL_MISSION,
    StaffBot,
    check_everyone_arrived,
    check_system,
    run,
    traffic,
)

SEEDS = range(1, 21)
BUSY_S = 600.0
CALM_S = 2400.0
FAULT_KINDS = [
    FaultKind.MOTION_SERIOUS,
    FaultKind.DOOR_LOCK_LOSS,
    FaultKind.POSITION_SENSOR,
    FaultKind.SENSOR_CONFLICT,
    FaultKind.HEARTBEAT_LOSS,
    FaultKind.LOAD_SENSOR,
]


def build(seed: int) -> tuple[Scenario, StaffBot, dict]:
    rng = random.Random(seed)
    fleet = tuple(CabinInit(car_id=c, floor=rng.randint(MIN_FLOOR, MAX_FLOOR)) for c in CARS)
    passengers = traffic(rng, 60, BUSY_S - 60.0, "mixed")
    inputs: list = []
    plan = Counter()

    def at(lo, hi):
        return round(rng.uniform(lo, hi), 1)

    for _ in range(rng.randint(3, 6)):  # faults, each repaired later
        car, kind, t = rng.choice(CARS), rng.choice(FAULT_KINDS), at(10, BUSY_S - 120)
        fixed = round(t + rng.uniform(5, 60), 1)
        inputs += [
            FaultInjected(t_sim=t, car_id=car, kind=kind),
            FaultCleared(t_sim=fixed, car_id=car, kind=kind),
            RecoveryRequest(t_sim=fixed + 1.0, car_id=car, role=Role.SUPERVISOR),
        ]
        plan[f"fault:{kind}"] += 1
    for _ in range(rng.randint(0, 6)):  # bags in the door
        inputs.append(
            FaultInjected(t_sim=at(5, BUSY_S), car_id=rng.choice(CARS), kind=FaultKind.OBSTRUCTION)
        )
    if rng.random() < 0.5:  # a maintenance window
        car, t = rng.choice(PASSENGER_CARS), at(20, BUSY_S - 200)
        inputs += [
            MaintenanceSet(t_sim=t, car_id=car, on=True, role=Role.MAINTAINER),
            MaintenanceSet(t_sim=t + 60.0, car_id=car, on=False, role=Role.MAINTAINER),
            RecoveryRequest(t_sim=t + 61.0, car_id=car, role=Role.SUPERVISOR),
        ]
        plan["maintenance"] += 1
    if rng.random() < 0.3:  # a fire alarm
        t = at(30, BUSY_S - 150)
        inputs += [
            FireAlarm(t_sim=t, active=True),
            FireAlarm(t_sim=round(t + rng.uniform(20, 60), 1), active=False),
        ]
        plan["fire"] += 1
    for _ in range(rng.randint(0, 3)):  # occupant alarms
        inputs.append(AlarmPressed(t_sim=at(5, BUSY_S), car_id=rng.choice(CARS)))
    staff = StaffBot()
    for _ in range(rng.randint(0, 2)):  # missions
        kind = rng.choice([MissionType.MEDICAL, MissionType.FREIGHT])
        origin, dest = rng.sample(range(MIN_FLOOR, MAX_FLOOR + 1), 2)
        weight = round(rng.uniform(50, 800), 0) if kind == MissionType.FREIGHT else None
        inputs.append(
            MissionRequest(
                t_sim=at(10, BUSY_S - 100),
                role=Role.MEDICAL_COORDINATOR
                if kind == MissionType.MEDICAL
                else Role.BUILDING_STAFF,
                mission_type=kind,
                origin=origin,
                destination=dest,
                material="crates" if kind == MissionType.FREIGHT else None,
                weight_kg=weight,
            )
        )
        plan[f"mission:{kind}"] += 1
    network = []
    for _ in range(rng.randint(0, 4)):  # unreliable links
        msg_type = rng.choice(["RouteReply", "RouteProposal", "CabinTelemetry", "RouteReply"])
        action = DROP if msg_type != "RouteProposal" and rng.random() < 0.6 else DELAY
        network.append(
            NetworkRule(
                msg_type=msg_type,
                car_id=rng.choice(PASSENGER_CARS),
                nth=rng.randint(1, 6),
                action=action,
                delay_s=round(rng.uniform(0.5, 4.0), 1),
            )
        )
        plan[f"network:{msg_type}:{action}"] += 1
    sc = Scenario(
        name=f"SY-20-seed-{seed}",
        fleet=fleet,
        passengers=passengers,
        inputs=tuple(sorted(inputs, key=lambda m: m.t_sim)),
        network=tuple(network),
        duration_s=BUSY_S + CALM_S,
    )
    return sc, staff, plan


def run_seed(seed: int):
    sc, staff, plan = build(seed)
    return run(sc, staff=staff, snapshot_every_s=5.0), plan


def check_stress(r, seed: int) -> None:
    check_system(r)
    check_everyone_arrived(r)
    # Calls keep their original creation time through faults and reassignment (EMG-03)
    created: dict[str, float] = {}
    for snap in r.snapshots:
        for call in snap["calls"]:
            first = created.setdefault(call["request_id"], call["created_at"])
            assert call["created_at"] == first, f"seed {seed}: {call['request_id']} age reset"
    # Every approved mission ends; none stays stuck after the fleet is healthy again
    for mid in sorted({u.mission_id for u in r.of(MissionStatusUpdate)}):
        final = r.final_status(mid)
        assert final in FINAL_MISSION, f"seed {seed}: {mid} ended as {final}: {r.statuses(mid)}"
        # Never two owners at once. D-62: a car that is held gives the mission back
        # (FAILED) and Dispatch may then reserve another car, one after the other.
        owner = None
        for u in r.of(MissionStatusUpdate, mission_id=mid):
            if u.status == MissionStatus.RESERVED:
                assert owner in (None, u.car_id), (
                    f"seed {seed}: {mid} reserved by {u.car_id} while {owner} still held it"
                )
                owner = u.car_id
            elif u.status == MissionStatus.FAILED and u.car_id == owner:
                owner = None
    # The fleet is back in normal service at the end
    last = {}
    for t in r.of(CabinTelemetry):
        last[t.car_id] = t
    stuck = {c: t.mode.value for c, t in last.items() if t.mode.value not in ("AUTO",)}
    assert not stuck, f"seed {seed}: cars not back in service: {stuck}"


# Seeds whose failure was traced to another owner's module. They are documented in
# SYSTEM_EDGE_CASES.md and kept strict: an XPASS means the owner fixed it.
KNOWN: dict[int, str] = {}


@pytest.mark.parametrize("seed", SEEDS)
def test_SY_20_whole_system_survives_random_disruption(seed):
    if seed in KNOWN:
        pytest.xfail(KNOWN[seed])
    r, _ = run_seed(seed)
    check_stress(r, seed)


def test_SY_20_the_random_runs_reach_the_hard_cases():
    """Guard against a weak generator: the seeds must include every kind of disruption."""
    plan: Counter = Counter()
    for seed in SEEDS:
        plan += build(seed)[2]
    for key in [
        f"fault:{FaultKind.MOTION_SERIOUS}",
        f"fault:{FaultKind.HEARTBEAT_LOSS}",
        f"fault:{FaultKind.SENSOR_CONFLICT}",
        "maintenance",
        "fire",
        f"mission:{MissionType.MEDICAL}",
        f"mission:{MissionType.FREIGHT}",
    ]:
        assert plan[key] > 0, f"no seed exercises {key}"
    assert any(k.startswith("network:") for k in plan)


def test_SY_20_NFR_05_whole_system_replay_is_identical():
    for seed in (1, 2, 3):
        sc, _, _ = build(seed)
        a = run(sc, staff=build(seed)[1]).engine.log_sha256()
        b = run(sc, staff=build(seed)[1]).engine.log_sha256()
        assert a == b, f"seed {seed}: two identical runs produced different logs"
