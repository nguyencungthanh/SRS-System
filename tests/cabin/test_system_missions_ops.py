"""Whole-system scenarios for missions, failures and operator actions (Hai).
Documented in tests/cabin/SYSTEM_EDGE_CASES.md (SY-21 to SY-31).

Same rules as test_system_scenarios.py: all modules real, system invariants checked, and a
failure in another owner's module is kept as a strict xfail with its finding ID.
"""

import random

import pytest

from contracts.config import DEFAULT_POLICY, Policy
from contracts.enums import (
    CabinMode,
    ExclusionReason,
    FaultKind,
    MissionStatus,
    MissionType,
    Role,
)
from contracts.messages import (
    AlarmPressed,
    AlertAcknowledged,
    CabinInit,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    MaintenanceSet,
    MissionRequest,
    MissionStatusUpdate,
    PolicyActivated,
    PolicyDecision,
    PolicyRecommended,
    RecoveryRequest,
)
from simulation.scenario import Scenario
from tests.cabin.system_helpers import (
    E,
    check_everyone_arrived,
    check_system,
    fleet,
    run,
    traffic,
)

STILL_OPEN = {MissionStatus.APPROVED, MissionStatus.RESERVED, MissionStatus.DRAINING}


def scenario(name, *, passengers=(), inputs=(), duration=600.0, cars=None):
    return Scenario(
        name=name,
        fleet=cars if cars is not None else fleet(),
        passengers=tuple(passengers),
        inputs=tuple(sorted(inputs, key=lambda m: m.t_sim)),
        duration_s=duration,
    )


def medical(t, origin=2, dest=30):
    return MissionRequest(
        t_sim=t,
        role=Role.MEDICAL_COORDINATOR,
        mission_type=MissionType.MEDICAL,
        origin=origin,
        destination=dest,
    )


def freight(t, origin=1, dest=30, weight=400.0):
    return MissionRequest(
        t_sim=t,
        role=Role.BUILDING_STAFF,
        mission_type=MissionType.FREIGHT,
        origin=origin,
        destination=dest,
        material="boxes",
        weight_kg=weight,
    )


def reserved_car(r, mid: str) -> list[str]:
    return [
        u.car_id
        for u in r.of(MissionStatusUpdate, mission_id=mid)
        if u.status == MissionStatus.RESERVED
    ]


# ======================================================================================
# SY-21..23 Choosing cars for missions
# ======================================================================================


def test_SY_21_MED_02_medical_goes_to_the_lowest_eta_during_traffic():
    people = traffic(random.Random(17), 15, 20.0, "mixed")
    r = run(scenario("SY-21", passengers=people, inputs=[medical(15.0, origin=18, dest=3)]))
    check_system(r)
    update = [u for u in r.of(MissionStatusUpdate, status=MissionStatus.RESERVED) if u.candidates][
        0
    ]
    etas = {c.car_id: c.eta_s for c in update.candidates if c.excluded is None}
    assert update.car_id == min(etas, key=lambda car: (etas[car], car)), (update.car_id, etas)
    g1 = [c for c in update.candidates if c.car_id == "G1"]
    assert g1 and g1[0].excluded == ExclusionReason.NOT_CAPABLE  # D-20
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED


def test_SY_22_MED_01_two_medical_emergencies_at_once_get_two_cars():
    r = run(
        scenario("SY-22", inputs=[medical(0.0, origin=5, dest=35), medical(0.0, origin=33, dest=2)])
    )
    check_system(r)
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED
    assert r.final_status("MS-0002") == MissionStatus.COMPLETED
    assert reserved_car(r, "MS-0001") != reserved_car(r, "MS-0002")


def test_SY_23_FRT_02_medical_and_freight_at_the_same_time():
    r = run(
        scenario("SY-23", inputs=[freight(0.0, origin=5, dest=25), medical(0.0, origin=8, dest=30)])
    )
    check_system(r)
    assert reserved_car(r, "MS-0001") == ["G1"]  # freight on the goods car
    assert reserved_car(r, "MS-0002") and reserved_car(r, "MS-0002")[0] != "G1"
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED
    assert r.final_status("MS-0002") == MissionStatus.COMPLETED


# ======================================================================================
# SY-24..26 Cars failing during missions and service
# ======================================================================================


BROKEN_P2 = dict(P1=40, P2=10, P3=25, P4=35)  # P2 is clearly the best car for floor 2


def _medical_car_breaks_down():
    inputs = [medical(0.0), FaultInjected(t_sim=3.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS)]
    return run(scenario("SY-24", inputs=inputs, duration=400.0, cars=fleet(**BROKEN_P2)))


def test_SY_24a_EMG_05_medical_car_breaks_down_on_the_way_the_operator_is_told():
    r = _medical_car_breaks_down()
    check_system(r)
    assert reserved_car(r, "MS-0001")[:1] == ["P2"]
    # EMG-05: the emergency must not silently stay with a car that will never arrive.
    statuses = r.statuses("MS-0001")
    assert MissionStatus.FAILED in statuses, f"no explicit failure reported: {statuses}"
    view = r.engine.snapshot()["missions"][0]
    assert view["status"] != "RESERVED", view


def test_SY_24b_MED_04_patient_still_picked_up_by_another_car():
    # F-1 fixed by D-62 (Dispatch re-selects a mission released by a held car).
    r = _medical_car_breaks_down()
    pickups = [e for e in r.events(E.MISSION_PICKUP_DONE) if e.ref_id == "MS-0001"]
    assert pickups and pickups[0].car_id != "P2", r.statuses("MS-0001")


def _freight_breakdown(repaired: bool):
    inputs = [
        freight(0.0, origin=1, dest=30),
        FaultInjected(t_sim=30.0, car_id="G1", kind=FaultKind.MOTION_SERIOUS),  # mid-leg
    ]
    if repaired:
        inputs += [
            FaultCleared(t_sim=60.0, car_id="G1", kind=FaultKind.MOTION_SERIOUS),
            RecoveryRequest(t_sim=61.0, car_id="G1", role=Role.SUPERVISOR),
        ]
    return run(scenario("SY-25", inputs=inputs, duration=300.0))


def test_SY_25a_FRT_04_freight_car_breaks_down_with_the_load_on_board():
    r = _freight_breakdown(repaired=False)
    check_system(r)
    statuses = r.statuses("MS-0001")
    assert MissionStatus.IN_PROGRESS in statuses
    # FRT-04: "failed loading or fault produces an explicit held/failed mission"
    assert statuses[-1] == MissionStatus.HOLD_PENDING_REVIEW, statuses
    assert r.engine.snapshot()["missions"][0]["status"] == "HOLD_PENDING_REVIEW"


def test_SY_25b_FRT_04_freight_resumes_after_authorised_recovery():
    r = _freight_breakdown(repaired=True)
    check_system(r)
    statuses = r.statuses("MS-0001")
    held = statuses.index(MissionStatus.HOLD_PENDING_REVIEW)
    assert statuses[held + 1 :] == [MissionStatus.IN_PROGRESS, MissionStatus.COMPLETED], statuses


def test_SY_26_SIM_05_EMG_03_every_passenger_car_out_of_service():
    cars = tuple(
        CabinInit(car_id=c, floor=f, mode=CabinMode.FAULT_HOLD, faults=(FaultKind.MOTION_SERIOUS,))
        for c, f in (("P1", 1), ("P2", 10), ("P3", 20), ("P4", 30))
    ) + (CabinInit(car_id="G1", floor=1),)
    people = traffic(random.Random(9), 8, 30.0, "inter")
    inputs = []
    for car in ("P1", "P2", "P3", "P4"):
        inputs += [
            FaultCleared(t_sim=100.0, car_id=car, kind=FaultKind.MOTION_SERIOUS),
            RecoveryRequest(t_sim=101.0, car_id=car, role=Role.SUPERVISOR),
        ]
    r = run(scenario("SY-26", passengers=people, inputs=inputs, cars=cars), snapshot_every_s=5.0)
    check_system(r)
    check_everyone_arrived(r)
    early = [e for e in r.events(E.HALL_CALL_SERVED) if e.t_sim < 101.0]
    assert not early, "a call was served while no passenger car was in service"
    waiting = [c for s in r.snapshots if 40.0 < s["t_sim"] < 100.0 for c in s["calls"]]
    assert waiting and all(c["status"] == "PENDING" for c in waiting)  # SIM-05: shown PENDING


# ======================================================================================
# SY-27..29 Operator actions
# ======================================================================================


def test_SY_27_CC_02_alert_acknowledgement_needs_an_operator():
    acks = []

    def operator(engine):
        alerts = engine.snapshot()["alerts"]
        if alerts and not acks:
            alert_id = alerts[0]["alert_id"]
            acks.append(alert_id)
            engine.submit(AlertAcknowledged(t_sim=0.0, alert_id=alert_id, role=Role.PASSENGER))
        elif acks and len(acks) == 1 and engine.now >= 5.0:
            acks.append(acks[0])
            engine.submit(AlertAcknowledged(t_sim=0.0, alert_id=acks[0], role=Role.OPERATOR))

    states = []
    r = run(
        scenario("SY-27", inputs=[AlarmPressed(t_sim=1.0, car_id="P1")], duration=10.0),
        during=lambda eng: (operator(eng), states.append((eng.now, eng.snapshot()["alerts"]))),
    )
    check_system(r)
    before = [a for t, alerts in states if 2.0 <= t < 5.0 for a in alerts]
    after = r.engine.snapshot()["alerts"]
    assert before and not any(a["acknowledged"] for a in before)  # a passenger cannot ack
    assert after and all(a["acknowledged"] for a in after)


@pytest.mark.parametrize(
    ("decisions", "activated"),
    [
        ([], False),
        ([(Role.OPERATOR, True)], False),
        ([(Role.SUPERVISOR, False)], False),
        ([(Role.SUPERVISOR, True)], True),
    ],
    ids=["no-decision", "operator-approves", "supervisor-rejects", "supervisor-approves"],
)
def test_SY_28_OPT_06_only_an_approved_policy_reaches_dispatch(decisions, activated):
    policy = Policy(version="POL-1", R=2, W=3)
    inputs = [
        PolicyRecommended(
            t_sim=1.0, run_id="RUN-0001", policy=policy, objective=12.5, scenario_set=("train",)
        )
    ]
    inputs += [
        PolicyDecision(t_sim=2.0 + i, run_id="RUN-0001", approve=ok, role=role)
        for i, (role, ok) in enumerate(decisions)
    ]
    people = traffic(random.Random(4), 6, 20.0, "inter")
    r = run(scenario("SY-28", passengers=people, inputs=inputs, duration=200.0))
    check_system(r)
    check_everyone_arrived(r)
    assert bool(r.of(PolicyActivated)) is activated
    expected = policy.version if activated else DEFAULT_POLICY.version
    assert r.engine.snapshot()["policy"]["version"] == expected


def test_SY_29_SIM_02_pause_freezes_the_whole_building():
    from app.modules import module_set
    from simulation.engine import Engine

    sc = scenario("SY-29", passengers=traffic(random.Random(2), 10, 20.0, "inter"), duration=300.0)
    engine = Engine(module_set("real", "real", "real")[0], sc)
    engine.start()
    engine.advance_to(15.0)
    before = engine.snapshot()
    engine.pause()
    engine.advance_to(120.0)  # wall time passes, simulation must not
    frozen = engine.snapshot()
    assert frozen["t_sim"] == before["t_sim"] == 15.0
    assert [c["position"] for c in frozen["cars"]] == [c["position"] for c in before["cars"]]
    assert [c["door"] for c in frozen["cars"]] == [c["door"] for c in before["cars"]]
    engine.start()
    engine.advance_to(300.0)
    assert engine.passengers.snapshot()["arrived"] == 10


# ======================================================================================
# SY-30..31 Emergencies that overlap
# ======================================================================================


def test_SY_30_D_24_MED_04_medical_request_during_a_fire_alarm():
    inputs = [
        FireAlarm(t_sim=0.0, active=True),
        medical(5.0),
        FireAlarm(t_sim=60.0, active=False),
    ]
    r = run(scenario("SY-30", inputs=inputs, duration=300.0))
    check_system(r)
    statuses = r.statuses("MS-0001")
    assert statuses and statuses[0] == MissionStatus.APPROVED
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED, statuses


def test_SY_31_EMG_04_maintenance_ordered_on_the_car_running_a_medical_mission():
    inputs = [
        medical(0.0),
        MaintenanceSet(t_sim=2.0, car_id="P2", on=True, role=Role.MAINTAINER),
    ]
    r = run(scenario("SY-31", inputs=inputs, duration=300.0))
    check_system(r)
    final = r.final_status("MS-0001")
    # The patient must still be picked up, by P2 or another car, or the operator told why.
    assert final not in STILL_OPEN, r.statuses("MS-0001")
