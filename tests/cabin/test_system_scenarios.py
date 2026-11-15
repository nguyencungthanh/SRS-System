"""Whole-system real-world scenarios (Hai). Documented in tests/cabin/SYSTEM_EDGE_CASES.md.

Every test runs the REAL Dispatch (Thanh), Cabin (Hai) and Control Center (Huy) plus the
virtual passengers through An's engine. Each one ends with the system-wide invariants
(``check_system``). A test that fails because of another person's module is marked
``xfail(strict=True)`` with the finding ID: it documents the defect without hiding it,
and it turns red again (XPASS) the moment the owner fixes it.
"""

import random

import pytest

from contracts.enums import (
    AlertKind,
    CabinMode,
    Direction,
    FaultKind,
    MissionStatus,
    MissionType,
    RejectReason,
    Role,
)
from contracts.messages import (
    AlarmPressed,
    CabinInit,
    CabinTelemetry,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    HallCallAck,
    HallCallPressed,
    MaintenanceSet,
    MissionRequest,
    MissionStatusUpdate,
    RecoveryApproved,
    RecoveryRequest,
    RouteProposal,
)
from simulation.scenario import DELAY, DROP, NetworkRule, PassengerIntent, Scenario
from tests.cabin.system_helpers import (
    E,
    call_created_at,
    check_everyone_arrived,
    check_system,
    fleet,
    run,
    traffic,
)


def scenario(name, *, passengers=(), inputs=(), duration=900.0, cars=None, network=()):
    return Scenario(
        name=name,
        fleet=cars if cars is not None else fleet(),
        passengers=tuple(passengers),
        inputs=tuple(inputs),
        duration_s=duration,
        network=tuple(network),
    )


def medical(t, origin=2, dest=30, role=Role.MEDICAL_COORDINATOR):
    return MissionRequest(
        t_sim=t, role=role, mission_type=MissionType.MEDICAL, origin=origin, destination=dest
    )


def freight(t, origin=1, dest=30, weight=400.0, role=Role.BUILDING_STAFF, material="boxes"):
    return MissionRequest(
        t_sim=t,
        role=role,
        mission_type=MissionType.FREIGHT,
        origin=origin,
        destination=dest,
        material=material,
        weight_kg=weight,
    )


# ======================================================================================
# SY-01..03 Daily traffic peaks
# ======================================================================================


@pytest.mark.parametrize(
    ("pattern", "n", "span"),
    [("up", 30, 60.0), ("down", 30, 60.0), ("inter", 40, 120.0)],
    ids=["SY-01-morning-up-peak", "SY-02-evening-down-peak", "SY-03-lunch-inter-floor"],
)
def test_SY_01_03_NFR_03_traffic_peaks_serve_everyone(pattern, n, span):
    r = run(scenario(pattern, passengers=traffic(random.Random(7), n, span, pattern)))
    check_system(r)
    check_everyone_arrived(r)
    m = r.engine.metrics()
    assert m["unserved"] == 0
    assert m["max_wait"] is not None and m["max_wait"] < 600.0  # no starvation


# ======================================================================================
# SY-04 Crowd bigger than one car
# ======================================================================================


def test_SY_04_SEN_04_crowd_bigger_than_one_car_at_the_lobby():
    crowd = [
        PassengerIntent(t=0.0, passenger_id=f"PAX-{i:04d}", origin=1, destination=20 + i)
        for i in range(1, 17)
    ]  # 16 x 75 kg = 1200 kg at floor 1, going up
    r = run(scenario("SY-04", passengers=crowd, cars=fleet(P1=1, P2=30, P3=35, P4=40)))
    check_system(r)
    check_everyone_arrived(r)
    assert len({a.request_id for a in r.of(HallCallAck) if a.accepted}) >= 1
    loads = [t.load_kg for t in r.of(CabinTelemetry)]
    assert max(loads) <= 1000.0  # passengers never board past the limit


# ======================================================================================
# SY-05 A car breaks down in traffic
# ======================================================================================


def test_SY_05_SAF_03_EMG_03_breakdown_with_a_passenger_inside_and_a_call_assigned():
    """P1 picks A up at 12, is assigned B at 14, then faults between 12 and 13."""
    people = [
        PassengerIntent(t=0.0, passenger_id="A", origin=12, destination=30),
        PassengerIntent(t=1.0, passenger_id="B", origin=14, destination=35),
    ]
    inputs = [
        FaultInjected(t_sim=11.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS),
        FaultCleared(t_sim=60.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS),
        RecoveryRequest(t_sim=61.0, car_id="P1", role=Role.SUPERVISOR),
    ]
    r = run(
        scenario("SY-05", passengers=people, inputs=inputs, cars=fleet(P1=10, P2=1, P3=1, P4=1))
    )
    check_system(r)
    check_everyone_arrived(r)
    a_served = [e for e in r.events(E.HALL_CALL_SERVED) if e.floor == 12]
    b_served = [e for e in r.events(E.HALL_CALL_SERVED) if e.floor == 14]
    assert a_served and a_served[0].car_id == "P1"
    assert b_served and b_served[0].car_id != "P1"  # B reassigned to a healthy car
    # A stays inside the stuck car until the recovery, then is delivered by P1
    arrivals = [e for e in r.events(E.DEST_SERVED, "P1") if e.passenger_id == "A"]
    assert arrivals and arrivals[0].t_sim > 61.0
    assert r.of(RecoveryApproved, car_id="P1")
    assert len(r.alerts(AlertKind.FAULT)) == 1


def test_SY_05b_EMG_03_reassigned_call_keeps_its_original_age():
    people = [
        PassengerIntent(t=0.0, passenger_id="A", origin=12, destination=30),
        PassengerIntent(t=1.0, passenger_id="B", origin=14, destination=35),
    ]
    inputs = [FaultInjected(t_sim=11.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS)]
    r = run(
        scenario("SY-05b", passengers=people, inputs=inputs, cars=fleet(P1=10, P2=1, P3=1, P4=1)),
        snapshot_every_s=1.0,
    )
    created = {}
    for snap in r.snapshots:
        for call in snap["calls"]:
            created.setdefault(call["request_id"], call["created_at"])
            assert call["created_at"] == created[call["request_id"]], "created_at was reset"
    b_req = [c for c in r.snapshots[3]["calls"] if c["floor"] == 14]
    assert b_req and b_req[0]["created_at"] == 1.0


# ======================================================================================
# SY-06 Maintenance in the middle of the day
# ======================================================================================


def test_SY_06_D_55_car_taken_for_maintenance_hands_over_its_calls():
    people = traffic(random.Random(3), 12, 40.0, "inter")
    inputs = [MaintenanceSet(t_sim=10.0, car_id="P2", on=True, role=Role.MAINTAINER)]
    r = run(scenario("SY-06", passengers=people, inputs=inputs))
    check_system(r)
    check_everyone_arrived(r)
    late = [e for e in r.events(E.HALL_CALL_SERVED, "P2") if e.t_sim > 10.0]
    assert not late, "a car in maintenance kept serving hall calls"


# ======================================================================================
# SY-07..08 Medical emergencies
# ======================================================================================


def test_SY_07_MED_01_MED_03_medical_emergency_during_busy_traffic():
    people = traffic(random.Random(11), 20, 30.0, "mixed")
    r = run(scenario("SY-07", passengers=people, inputs=[medical(10.0)]))
    check_system(r)
    check_everyone_arrived(r)  # MED-03: ordinary calls are not lost
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED
    reserved = [u.car_id for u in r.of(MissionStatusUpdate, status=MissionStatus.RESERVED)]
    assert len(reserved) == 1 and reserved[0] != "G1"  # one capable car (D-20)


def test_SY_08_MED_04_EMG_05_no_medical_car_then_one_recovers():
    cars = tuple(
        CabinInit(car_id=c, floor=f, mode=CabinMode.MAINTENANCE)
        for c, f in (("P1", 1), ("P2", 10), ("P3", 20), ("P4", 30))
    ) + (CabinInit(car_id="G1", floor=1),)
    inputs = [medical(0.0), RecoveryRequest(t_sim=30.0, car_id="P2", role=Role.SUPERVISOR)]
    r = run(scenario("SY-08", inputs=inputs, cars=cars, duration=300.0))
    check_system(r)
    statuses = r.statuses("MS-0001")
    assert statuses[:2] == [MissionStatus.APPROVED, MissionStatus.UNAVAILABLE]
    assert len(r.alerts(AlertKind.UNAVAILABLE)) == 1  # one urgent alert, not a flood
    # MED-04: re-evaluated when P2 comes back, then served by P2
    assert MissionStatus.RESERVED in statuses
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED


# ======================================================================================
# SY-09 Freight requests, good and bad
# ======================================================================================


def test_SY_09a_FRT_02_freight_prefers_the_goods_car():
    r = run(scenario("SY-09a", inputs=[freight(0.0, origin=5, dest=25)], duration=200.0))
    check_system(r)
    reserved = [u.car_id for u in r.of(MissionStatusUpdate, status=MissionStatus.RESERVED)]
    assert reserved == ["G1"]  # D-21: the goods car first when it is eligible
    assert r.final_status("MS-0001") == MissionStatus.COMPLETED


@pytest.mark.parametrize(
    ("request_msg", "reason"),
    [
        (freight(0.0, weight=1200.0), RejectReason.OVER_PAYLOAD),
        (freight(0.0, material=""), RejectReason.INVALID_MATERIAL),
        (freight(0.0, role=Role.PASSENGER), RejectReason.UNAUTHORIZED_ROLE),
        (medical(0.0, role=Role.BUILDING_STAFF), RejectReason.UNAUTHORIZED_ROLE),
        (medical(0.0, origin=7, dest=7), RejectReason.SAME_ORIGIN_DESTINATION),
        (medical(0.0, origin=0, dest=7), RejectReason.FLOOR_OUT_OF_RANGE),
    ],
    ids=[
        "overweight",
        "no-material",
        "passenger-asks-freight",
        "staff-asks-medical",
        "same-floor",
        "floor-0",
    ],
)
def test_SY_09b_CC_01_FRT_01_bad_mission_requests_change_nothing(request_msg, reason):
    r = run(scenario("SY-09b", inputs=[request_msg], duration=30.0))
    check_system(r)
    assert r.statuses("MS-0001") == [MissionStatus.REJECTED]
    assert r.of(MissionStatusUpdate)[0].reason == reason
    assert not [p for p in r.of(RouteProposal) if p.mission_id is not None]


# ======================================================================================
# SY-10 Fire alarm during traffic
# ======================================================================================


def test_SY_10_D_24_fire_alarm_suspends_new_work_then_service_resumes():
    people = traffic(random.Random(5), 16, 80.0, "inter")
    inputs = [FireAlarm(t_sim=20.0, active=True), FireAlarm(t_sim=90.0, active=False)]
    r = run(scenario("SY-10", passengers=people, inputs=inputs))
    check_system(r)
    check_everyone_arrived(r)  # nobody is lost because of the fire
    created = call_created_at(r)
    during = [
        p
        for p in r.of(RouteProposal)
        if 20.0 < p.t_sim < 90.0
        and any(
            s.kind.value == "HALL_PICKUP" and created.get(s.ref_id, 0.0) > 20.0
            for s in p.ordered_stops
        )
    ]
    assert not during, "new ordinary calls were dispatched during the fire"
    assert r.alerts(AlertKind.FIRE)


# ======================================================================================
# SY-11 Unreliable network
# ======================================================================================


def test_SY_11_SAF_04_SIM_07_lost_and_delayed_messages_never_create_two_owners():
    people = traffic(random.Random(13), 16, 40.0, "inter")
    rules = [
        NetworkRule(msg_type="RouteReply", car_id="P1", nth=1, action=DROP),
        NetworkRule(msg_type="RouteReply", car_id="P2", nth=2, action=DROP),
        NetworkRule(msg_type="RouteProposal", car_id="P3", nth=1, action=DELAY, delay_s=3.0),
        NetworkRule(msg_type="RouteReply", car_id="P4", nth=1, action=DELAY, delay_s=5.0),
        NetworkRule(msg_type="CabinTelemetry", car_id="P2", nth=10, action=DROP),
    ]
    r = run(scenario("SY-11", passengers=people, network=rules))
    check_system(r)
    check_everyone_arrived(r)


# ======================================================================================
# SY-12..16 Operator and passenger actions
# ======================================================================================


def test_SY_12_EMG_01_alarm_in_a_moving_car_is_an_alarm_not_a_medical_mission():
    people = [PassengerIntent(t=0.0, passenger_id="A", origin=27, destination=5)]
    r = run(scenario("SY-12", passengers=people, inputs=[AlarmPressed(t_sim=20.0, car_id="P4")]))
    check_system(r)
    check_everyone_arrived(r)
    assert len(r.alerts(AlertKind.ALARM)) == 1
    assert not r.of(MissionRequest) and not r.alerts(AlertKind.MEDICAL)


def test_SY_13_REQ_02_many_people_press_the_same_button():
    people = [
        PassengerIntent(t=i * 0.4, passenger_id=f"PAX-{i:04d}", origin=15, destination=2 + i)
        for i in range(1, 9)
    ]  # eight people at 15 DOWN within 3 s
    r = run(scenario("SY-13", passengers=people))
    check_system(r)
    check_everyone_arrived(r)
    ids = {a.request_id for a in r.of(HallCallAck) if a.accepted}
    assert len(ids) == 1, f"one canonical 15/DOWN call expected, got {ids}"
    assert len(r.events(E.HALL_CALL_SERVED)) == 1


def test_SY_14_DC_01_up_and_down_at_the_same_floor_are_two_calls():
    people = [
        PassengerIntent(t=0.0, passenger_id="UP", origin=20, destination=35),
        PassengerIntent(t=0.0, passenger_id="DOWN", origin=20, destination=3),
    ]
    r = run(scenario("SY-14", passengers=people))
    check_system(r)
    check_everyone_arrived(r)
    acks = [a for a in r.of(HallCallAck) if a.accepted]
    assert {a.direction for a in acks} == {Direction.UP, Direction.DOWN}


def test_SY_15_SYS_03_invalid_hall_buttons_are_rejected_with_a_reason():
    presses = [
        HallCallPressed(t_sim=0.0, floor=1, direction=Direction.DOWN),
        HallCallPressed(t_sim=0.0, floor=40, direction=Direction.UP),
        HallCallPressed(t_sim=0.0, floor=41, direction=Direction.DOWN),
        HallCallPressed(t_sim=0.0, floor=0, direction=Direction.UP),
    ]
    r = run(scenario("SY-15", inputs=presses, duration=10.0))
    check_system(r)
    acks = r.of(HallCallAck)
    assert [a.accepted for a in acks] == [False] * 4
    assert [a.reason for a in acks] == [
        RejectReason.TERMINAL_DIRECTION,
        RejectReason.TERMINAL_DIRECTION,
        RejectReason.FLOOR_OUT_OF_RANGE,
        RejectReason.FLOOR_OUT_OF_RANGE,
    ]
    assert not r.of(RouteProposal)


def test_SY_16a_D_11_car_that_stops_reporting_gets_no_new_calls():
    people = [PassengerIntent(t=6.0, passenger_id="A", origin=11, destination=30)]
    inputs = [FaultInjected(t_sim=0.5, car_id="P2", kind=FaultKind.HEARTBEAT_LOSS)]
    r = run(scenario("SY-16a", passengers=people, inputs=inputs))  # P2 idle at 10, closest
    check_system(r)
    check_everyone_arrived(r)
    (pickup,) = r.events(E.HALL_CALL_SERVED)
    assert pickup.car_id != "P2", "a car with stale telemetry was given the call"


def test_SY_16b_CC_04_recovery_for_a_healthy_car_is_refused():
    r = run(
        scenario(
            "SY-16b",
            inputs=[RecoveryRequest(t_sim=1.0, car_id="P3", role=Role.SUPERVISOR)],
            duration=10.0,
        )
    )
    check_system(r)
    assert not r.of(RecoveryApproved)


def test_SY_16c_CC_04_recovery_by_an_operator_is_refused():
    inputs = [
        FaultInjected(t_sim=0.0, car_id="P3", kind=FaultKind.MOTION_SERIOUS),
        FaultCleared(t_sim=1.0, car_id="P3", kind=FaultKind.MOTION_SERIOUS),
        RecoveryRequest(t_sim=2.0, car_id="P3", role=Role.OPERATOR),
    ]
    r = run(scenario("SY-16c", inputs=inputs, duration=10.0))
    check_system(r)
    assert not r.of(RecoveryApproved)
    assert r.of(CabinTelemetry, car_id="P3")[-1].mode == CabinMode.FAULT_HOLD


# ======================================================================================
# SY-17 Determinism of the whole system
# ======================================================================================


def test_SY_17_NFR_05_same_traffic_same_log_for_the_whole_system():
    def once():
        inputs = [
            medical(15.0),
            FaultInjected(t_sim=40.0, car_id="P3", kind=FaultKind.SENSOR_CONFLICT),
        ]
        sc = scenario(
            "SY-17",
            passengers=traffic(random.Random(21), 25, 60.0, "mixed"),
            inputs=inputs,
            duration=400.0,
        )
        return run(sc).engine.log_sha256()

    assert once() == once()
