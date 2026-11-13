"""Contract tests for Dispatch (Thanh). Written from SRS numbers before the module exists.

The real ``dispatch`` package runs with FakeCabin and FakeControlCenter on the bus, or is
fed messages directly. Tests look only at emitted messages and ``snapshot()``.
Skipped until ``dispatch/`` exists. ``pytest --fakes`` runs the ``fake_ok`` ones on fakes/.
"""

import json

import pytest

from contracts.config import DEFAULT_POLICY, Policy
from contracts.enums import (
    CabinMode,
    CallStatus,
    Direction,
    DispatchAlgorithm,
    ExclusionReason,
    FaultKind,
    MissionStatus,
    MissionType,
    RejectReason,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.messages import (
    CabinInit,
    FaultInjected,
    FireAlarm,
    HallCallAck,
    HallCallPressed,
    MissionRequest,
    MissionStatusUpdate,
    Passenger,
    PassengerExit,
    PolicyActivated,
    RecoveryRequest,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
)
from contracts.ui import CallView, DispatchSnapshot
from fakes import cabin as fake_cabin
from fakes import control_center as fake_cc
from tests.contract.helpers import (
    EPS,
    ScriptedCabin,
    assert_command_ids_consistent,
    assert_single_owner,
    at01_fleet,
    at03_fleet,
    at04_fleet,
    calls,
    dest,
    events,
    feed,
    hall,
    log_text,
    make_bus,
    of_type,
    proposals,
    proposals_with,
    start,
    tel,
)

DOWN, UP = Direction.DOWN, Direction.UP
SERVED = ServiceEventType.HALL_CALL_SERVED


def system(dispatch_impl, fleet, policy=DEFAULT_POLICY, algorithm=None, scripted=None):
    """Real (or fake) dispatch + FakeControlCenter + one FakeCabin per CabinInit."""
    scripted = scripted or {}
    algorithm = algorithm or DispatchAlgorithm.COMMITMENT
    dispatch = dispatch_impl.create(policy, algorithm)
    cabins = [scripted.get(i.car_id) or fake_cabin.create(i) for i in fleet]
    return make_bus(dispatch, fake_cc.create(), *cabins), dispatch


def call(t: float, floor: int, direction: Direction) -> HallCallPressed:
    return HallCallPressed(t_sim=t, floor=floor, direction=direction)


def served(bus, request_id: str):
    hits = [
        e
        for car in ("P1", "P2", "P3", "P4", "G1")
        for e in events(bus, car, SERVED)
        if e.ref_id == request_id
    ]
    return hits[0] if hits else None


def mission(t, mission_type=MissionType.MEDICAL, origin=2, destination=30, **extra):
    role = Role.MEDICAL_COORDINATOR if mission_type == MissionType.MEDICAL else Role.BUILDING_STAFF
    return MissionRequest(
        t_sim=t,
        role=role,
        mission_type=mission_type,
        origin=origin,
        destination=destination,
        **extra,
    )


def freight(t, origin=1, destination=30):
    return mission(t, MissionType.FREIGHT, origin, destination, material="boxes", weight_kg=400.0)


def statuses(bus, mission_id="MS-0001"):
    out = []
    for u in of_type(bus.delivered, MissionStatusUpdate, mission_id=mission_id):
        if not out or out[-1] != u.status:
            out.append(u.status)
    return out


def stop_keys(stops):
    return [(s.floor, s.kind, s.ref_id) for s in stops]


def full_car(car_id: str, floor: int, destination: int) -> CabinInit:
    """A car at FULL_LOAD (2 x 450 kg) that therefore takes no new hall pickups (CB-02)."""
    riders = (
        Passenger(passenger_id="A", weight_kg=450.0, destination=destination),
        Passenger(passenger_id="B", weight_kg=450.0, destination=destination),
    )
    return CabinInit(car_id=car_id, floor=floor, passengers=riders)


# ======================================================================================
# Hall Call repository (TH-01)
# ======================================================================================


@pytest.mark.fake_ok
def test_DC_01_SYS_03_invalid_hall_calls_rejected_with_reason(dispatch_impl):
    d = dispatch_impl.create()
    cases = [
        (1, DOWN, RejectReason.TERMINAL_DIRECTION),
        (40, UP, RejectReason.TERMINAL_DIRECTION),
        (0, UP, RejectReason.FLOOR_OUT_OF_RANGE),
        (41, DOWN, RejectReason.FLOOR_OUT_OF_RANGE),
        (10, Direction.IDLE, RejectReason.INVALID_DIRECTION),
    ]
    out = feed(d, start(d), tel("P1", 20), *[call(0.0, f, dr) for f, dr, _ in cases])
    acks = of_type(out, HallCallAck)
    assert [(a.accepted, a.request_id, a.reason) for a in acks] == [
        (False, None, reason) for *_, reason in cases
    ]
    assert not of_type(out, RouteProposal)
    assert d.snapshot()["calls"] == []


@pytest.mark.fake_ok
def test_REQ_02_NFR_01_duplicate_returns_same_request_and_keeps_created_at(dispatch_impl):
    bus, d = system(dispatch_impl, at01_fleet())
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(3.0)
    bus.inject(call(3.0, 27, DOWN))
    bus.run_until(3.0)
    first, second = of_type(bus.delivered, HallCallAck)
    assert (first.t_sim, second.t_sim) == (0.0, 3.0)  # NFR-01: acknowledged immediately
    assert first.request_id == second.request_id == "HC-0001"
    assert (first.duplicate, second.duplicate) == (False, True)
    view = calls(d)["HC-0001"]
    assert (view["floor"], view["direction"], view["created_at"]) == (27, DOWN, 0.0)
    assert len(calls(d)) == 1


@pytest.mark.fake_ok
def test_REQ_03_reserved_until_ack_then_assigned(dispatch_impl):
    d = dispatch_impl.create()
    out = feed(d, start(d), tel("P1", 31), call(0.0, 27, DOWN))
    (proposal,) = of_type(out, RouteProposal)
    assert proposal.car_id == "P1"
    view = calls(d)["HC-0001"]
    assert (view["status"], view["owner"]) == (CallStatus.RESERVED, "P1")
    feed(
        d,
        RouteReply(
            t_sim=0.0,
            command_id=proposal.command_id,
            car_id="P1",
            accepted=True,
            committed_version=1,
        ),
    )
    view = calls(d)["HC-0001"]
    assert (view["status"], view["owner"]) == (CallStatus.ASSIGNED, "P1")


# ======================================================================================
# Eligibility, ETA, insertion, DELAY_CAP (TH-02)
# ======================================================================================


@pytest.mark.fake_ok
def test_AT_01_proposes_to_P1_at_once_and_pickup_at_8s(dispatch_impl):
    bus, _ = system(dispatch_impl, at01_fleet())
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(20.0)
    first = proposals_with(bus, "HC-0001")[0]
    assert (first.t_sim, first.car_id) == (0.0, "P1")
    assert first.ordered_stops == (hall(27, DOWN, "HC-0001"),)
    pickup = served(bus, "HC-0001")
    assert (pickup.car_id, pickup.t_sim) == ("P1", 8.0)


@pytest.mark.fake_ok
def test_DC_02_ineligible_cars_get_nothing(dispatch_impl):
    d = dispatch_impl.create()
    out = feed(
        d,
        start(d),
        tel("P3", 26, t=0.0),  # stale by t=5 (> TELEMETRY_STALE_S)
        tel("P1", 26, t=5.0, mode=CabinMode.FAULT_HOLD),
        tel("P2", 26, t=5.0, mode=CabinMode.MAINTENANCE),
        tel("P4", 26, t=5.0, mode=CabinMode.FULL, load_kg=900.0, passengers=12),
        tel("G1", 27, t=5.0),  # goods car: no ordinary hall calls
        call(5.0, 27, DOWN),
    )
    assert not of_type(out, RouteProposal)
    assert calls(d)["HC-0001"]["status"] == CallStatus.PENDING
    out = feed(d, tel("P4", 20, t=6.0))  # P4 becomes a normal AUTO car
    (proposal,) = of_type(out, RouteProposal)
    assert proposal.car_id == "P4"


@pytest.mark.fake_ok
def test_DC_05_equal_eta_ties_break_by_car_id(dispatch_impl):
    d = dispatch_impl.create()
    out = feed(d, start(d), tel("P2", 20), tel("P1", 30), call(0.0, 25, UP))
    (proposal,) = of_type(out, RouteProposal)
    assert proposal.car_id == "P1"  # both 10 s away; D-13 -> P1 before P2


def test_MOVE_06_no_insertion_less_than_one_floor_ahead(dispatch_impl):
    d = dispatch_impl.create()
    moving = {
        "direction": UP,
        "moving": True,
        "door_zone": False,
        "route_version": 4,
        "stops": (dest(35, "PAX-1"),),
        "passengers": 1,
        "load_kg": 75.0,
    }
    out = feed(d, start(d), tel("P1", 26.8, **moving), call(0.0, 27, UP))
    assert not of_type(out, RouteProposal)
    assert calls(d)["HC-0001"]["status"] == CallStatus.PENDING


def test_MOVE_06_insertion_exactly_one_floor_ahead_allowed(dispatch_impl):
    d = dispatch_impl.create()
    moving = {
        "direction": UP,
        "moving": True,
        "door_zone": False,
        "route_version": 4,
        "stops": (dest(35, "PAX-1"),),
        "passengers": 1,
        "load_kg": 75.0,
    }
    out = feed(d, start(d), tel("P1", 26.0, **moving), call(0.0, 27, UP))
    (proposal,) = of_type(out, RouteProposal)
    assert proposal.car_id == "P1"
    assert proposal.expected_route_version == 4
    assert proposal.ordered_stops == (hall(27, UP, "HC-0001"), dest(35, "PAX-1"))


def test_MOVE_04_opposite_direction_call_not_inserted(dispatch_impl):
    d = dispatch_impl.create()
    out = feed(
        d,
        start(d),
        tel(
            "P1",
            20,
            direction=UP,
            moving=True,
            door_zone=False,
            stops=(dest(35, "PAX-1"),),
            passengers=1,
            load_kg=75.0,
        ),
        call(0.0, 25, DOWN),
    )
    assert not of_type(out, RouteProposal)


def test_TC_DELAY_01_cap_measured_against_the_fixed_reference(dispatch_impl):
    # P1 at 40 carries a passenger to 30: committed reference ETA 20 s.
    fleet = [
        CabinInit(car_id="P1", floor=40, passengers=(Passenger(passenger_id="A", destination=30),))
    ]
    bus, _ = system(dispatch_impl, fleet)
    bus.inject(call(0.0, 35, DOWN))  # dest ETA 20 -> 26 (+6): accepted
    bus.inject(call(0.0, 33, DOWN))  # dest ETA -> 32: +12 vs 20 (not +6 vs 26): rejected
    bus.run_until(30.0)
    first = proposals_with(bus, "HC-0001")[0]
    assert first.car_id == "P1"
    assert first.ordered_stops == (hall(35, DOWN, "HC-0001"), dest(30, "A"))
    assert served(bus, "HC-0001").t_sim == 10.0
    (dropped,) = events(bus, "P1", ServiceEventType.DEST_SERVED)
    assert dropped.t_sim == 26.0
    assert not [p for p in proposals_with(bus, "HC-0002") if p.t_sim < dropped.t_sim]


# ======================================================================================
# Grouping, AGE_LIMIT, pending calls (TH-04)
# ======================================================================================


def test_AT_02_groups_29_then_27_without_waiting(dispatch_impl):
    bus, _ = system(dispatch_impl, at01_fleet())
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(2.0)
    bus.inject(call(2.0, 29, DOWN))
    bus.run_until(30.0)
    assert proposals_with(bus, "HC-0001")[0].t_sim == 0.0  # DC-03: no deliberate wait
    assert (served(bus, "HC-0002").car_id, served(bus, "HC-0002").t_sim) == ("P1", 4.0)
    assert (served(bus, "HC-0001").car_id, served(bus, "HC-0001").t_sim) == ("P1", 14.0)


@pytest.mark.parametrize(
    ("second_floor", "second_t", "grouped"),
    [(33, 1.0, True), (34, 1.0, False), (33, 5.0, True), (33, 6.0, False)],
    ids=["R_exact", "R_plus_1", "W_exact", "W_plus_1"],
)
def test_DC_03_grouping_bounds_R_and_W(dispatch_impl, second_floor, second_t, grouped):
    # Only P1 at 40; anchor call 30 DOWN at t=0. D-50: span <= R=3 and age span <= W=5.
    bus, _ = system(dispatch_impl, [CabinInit(car_id="P1", floor=40)])
    bus.inject(call(0.0, 30, DOWN))
    bus.run_until(second_t)
    bus.inject(call(second_t, second_floor, DOWN))
    bus.run_until(40.0)
    anchor_done = served(bus, "HC-0001").t_sim
    early = [p for p in proposals_with(bus, "HC-0002") if p.t_sim < anchor_done]
    assert bool(early) is grouped


def test_TH_04_dispatch_uses_the_policy_it_was_given(dispatch_impl):
    narrow = Policy(version="POL-T1", R=1, W=5)
    bus, d = system(dispatch_impl, [CabinInit(car_id="P1", floor=40)], policy=narrow)
    bus.inject(call(0.0, 30, DOWN))
    bus.run_until(1.0)
    bus.inject(call(1.0, 32, DOWN))  # span 2 > R=1
    bus.run_until(40.0)
    anchor_done = served(bus, "HC-0001").t_sim
    assert not [p for p in proposals_with(bus, "HC-0002") if p.t_sim < anchor_done]
    assert d.snapshot()["policy"] == {"version": "POL-T1", "R": 1, "W": 5}


@pytest.mark.fake_ok
def test_DC_06_call_older_than_age_limit_goes_first(dispatch_impl):
    # P1 is FULL (900 kg) from 10 up to 40, so it takes no pickups until the riders leave.
    bus, _ = system(dispatch_impl, [full_car("P1", 10, 40)])
    bus.inject(call(0.0, 5, UP))  # X: far, will be 62 s old
    bus.inject(call(10.0, 38, DOWN))  # Y: near, 52 s old
    bus.run_until(62.0)
    bus.inject(PassengerExit(t_sim=62.0, car_id="P1", passenger_id="A"))
    bus.inject(PassengerExit(t_sim=62.0, car_id="P1", passenger_id="B"))
    bus.run_until(90.0)
    first = next(
        p
        for p in proposals(bus)
        if any(s.ref_id in ("HC-0001", "HC-0002") for s in p.ordered_stops)
    )
    assert first.t_sim >= 62.0 - EPS
    assert "HC-0001" in [s.ref_id for s in first.ordered_stops]


@pytest.mark.fake_ok
def test_SIM_05_new_call_stays_pending_while_no_car_can_take_it(dispatch_impl):
    bus, d = system(dispatch_impl, [full_car("P1", 10, 40)])
    bus.inject(call(0.0, 20, DOWN))
    bus.run_until(5.0)
    (ack,) = of_type(bus.delivered, HallCallAck)
    assert ack.accepted
    view = calls(d)["HC-0001"]
    assert (view["status"], view["owner"]) == (CallStatus.PENDING, None)
    assert not proposals_with(bus, "HC-0001")


# ======================================================================================
# Ownership, ACK / NACK, reconciliation (TH-03)
# ======================================================================================


@pytest.mark.fake_ok
def test_AT_05_lost_ack_never_creates_a_second_owner(dispatch_impl):
    scripted = {"P1": ScriptedCabin(CabinInit(car_id="P1", floor=31), mode="ack_lost")}
    fleet = [CabinInit(car_id="P1", floor=31), CabinInit(car_id="P2", floor=20)]
    bus, _ = system(dispatch_impl, fleet, scripted=scripted)
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(30.0)
    assert {p.car_id for p in proposals_with(bus, "HC-0001")} == {"P1"}
    assert served(bus, "HC-0001").car_id == "P1"
    assert_single_owner(bus)
    assert_command_ids_consistent(bus)


def test_REQ_04_SIM_07_reconcile_before_offering_another_car(dispatch_impl):
    scripted = {"P1": ScriptedCabin(CabinInit(car_id="P1", floor=31), mode="drop_first")}
    fleet = [CabinInit(car_id="P1", floor=31), CabinInit(car_id="P2", floor=20)]
    bus, _ = system(dispatch_impl, fleet, scripted=scripted)
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(60.0)
    first = proposals_with(bus, "HC-0001")[0]
    queries = of_type(bus.delivered, RouteVersionQuery, command_id=first.command_id)
    assert queries, "no RouteVersionQuery after the missing ACK (D-10)"
    assert queries[0].t_sim >= first.expiry_ts - EPS
    (report,) = of_type(bus.delivered, RouteVersionReport, command_id=first.command_id)
    elsewhere = [p for p in proposals_with(bus, "HC-0001") if p.car_id != first.car_id]
    assert all(p.t_sim >= report.t_sim for p in elsewhere)
    assert served(bus, "HC-0001") is not None
    assert_single_owner(bus)
    assert_command_ids_consistent(bus)


@pytest.mark.fake_ok
def test_REQ_04_nack_moves_the_call_to_another_car(dispatch_impl):
    scripted = {"P1": ScriptedCabin(CabinInit(car_id="P1", floor=31), mode="nack")}
    fleet = [CabinInit(car_id="P1", floor=31), CabinInit(car_id="P2", floor=20)]
    bus, _ = system(dispatch_impl, fleet, scripted=scripted)
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(40.0)
    assert proposals_with(bus, "HC-0001")[0].car_id == "P1"
    assert served(bus, "HC-0001").car_id == "P2"
    assert_single_owner(bus)


@pytest.mark.fake_ok
def test_EMG_03_faulted_car_releases_calls_with_original_age(dispatch_impl):
    bus, d = system(dispatch_impl, at01_fleet())
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(1.0)
    bus.inject(FaultInjected(t_sim=1.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS))
    bus.run_until(5.0)
    view = calls(d)["HC-0001"]
    assert view["created_at"] == 0.0 and view["owner"] != "P1"
    bus.run_until(80.0)
    assert served(bus, "HC-0001").car_id != "P1"
    assert not [p for p in proposals(bus, "P1") if p.t_sim > 1.0]
    assert_single_owner(bus)


@pytest.mark.fake_ok
def test_D_24_fire_alarm_stops_new_ordinary_assignments(dispatch_impl):
    bus, d = system(dispatch_impl, at01_fleet())
    bus.inject(FireAlarm(t_sim=0.0, active=True))
    bus.inject(call(1.0, 27, DOWN))
    bus.run_until(10.0)
    assert not proposals_with(bus, "HC-0001")
    assert calls(d)["HC-0001"]["status"] == CallStatus.PENDING


# ======================================================================================
# MEDICAL and FREIGHT selection (TH-05)
# ======================================================================================


def test_AT_03_medical_picks_P2_by_eta_and_reports_every_car(dispatch_impl):
    bus, _ = system(dispatch_impl, at03_fleet())
    bus.inject(mission(0.0))
    bus.run_until(5.0)
    (reserved,) = [
        u
        for u in of_type(bus.delivered, MissionStatusUpdate, mission_id="MS-0001")
        if u.status == MissionStatus.RESERVED
    ]
    assert reserved.car_id == "P2"
    cands = {c.car_id: c for c in reserved.candidates}
    assert cands["P1"].eta_s == pytest.approx(90.0)
    assert cands["P2"].eta_s == pytest.approx(20.0)
    assert cands["P4"].eta_s == pytest.approx(58.0)
    assert cands["P3"].excluded == ExclusionReason.MAINTENANCE
    assert cands["G1"].excluded == ExclusionReason.NOT_CAPABLE
    (proposal,) = [p for p in proposals(bus, "P2") if p.mission_id == "MS-0001"]
    assert proposal.exclusive
    assert stop_keys(proposal.ordered_stops)[0] == (2, StopKind.MISSION_PICKUP, "MS-0001")
    assert (30, StopKind.MISSION_DROPOFF, "MS-0001") in stop_keys(proposal.ordered_stops)
    ack = next(
        m for m in of_type(bus.delivered, RouteReply, command_id=proposal.command_id) if m.accepted
    )
    assert bus.delivered.index(ack) < bus.delivered.index(reserved)  # MED-02: after ACK


def test_MED_02_nack_from_best_car_moves_to_next_and_keeps_onboard_drop(dispatch_impl):
    scripted = {"P2": ScriptedCabin(CabinInit(car_id="P2", floor=12), mode="nack")}
    bus, _ = system(dispatch_impl, at03_fleet(), scripted=scripted)
    bus.inject(mission(0.0))
    bus.run_until(5.0)
    mission_props = [p for p in proposals(bus) if p.mission_id == "MS-0001"]
    assert [p.car_id for p in mission_props][:2] == ["P2", "P4"]
    p4 = mission_props[1]
    assert p4.exclusive
    keys = stop_keys(p4.ordered_stops)
    assert keys[0] == (22, StopKind.CAR_DEST, "B")  # onboard passenger first
    assert keys.index((2, StopKind.MISSION_PICKUP, "MS-0001")) > 0
    reserved = [
        u
        for u in of_type(bus.delivered, MissionStatusUpdate, mission_id="MS-0001")
        if u.status == MissionStatus.RESERVED
    ]
    assert reserved and reserved[-1].car_id == "P4"


def test_MED_03_selected_car_returns_unpicked_calls_with_original_age(dispatch_impl):
    fleet = [CabinInit(car_id="P1", floor=40), CabinInit(car_id="P2", floor=12)]
    bus, d = system(dispatch_impl, fleet)
    bus.inject(call(0.0, 10, DOWN))  # P2 is closest and heads down to 10
    bus.run_until(1.0)
    bus.inject(mission(1.0))  # MEDICAL 2 -> 30: P2 (~20 s) beats P1 (76 s)
    bus.run_until(10.0)
    (p2_mission,) = [p for p in proposals(bus, "P2") if p.mission_id == "MS-0001"]
    assert not [s for s in p2_mission.ordered_stops if s.kind == StopKind.HALL_PICKUP]
    view = calls(d)["HC-0001"]
    assert view["created_at"] == 0.0 and view["owner"] != "P2"
    bus.run_until(120.0)
    assert served(bus, "HC-0001").car_id == "P1"


@pytest.mark.fake_ok
def test_MED_04_unavailable_then_reevaluated_when_fleet_changes(dispatch_impl):
    fleet = [
        CabinInit(car_id="P3", floor=5, mode=CabinMode.MAINTENANCE),
        CabinInit(car_id="G1", floor=3),
    ]
    bus, _ = system(dispatch_impl, fleet)
    bus.inject(mission(0.0))
    bus.run_until(2.0)
    unavailable = [
        u
        for u in of_type(bus.delivered, MissionStatusUpdate, mission_id="MS-0001")
        if u.status == MissionStatus.UNAVAILABLE
    ]
    cands = {c.car_id: c for c in unavailable[0].candidates}
    assert cands["P3"].excluded == ExclusionReason.MAINTENANCE
    assert cands["G1"].excluded == ExclusionReason.NOT_CAPABLE
    assert all(cands[c].excluded == ExclusionReason.STALE_TELEMETRY for c in ("P1", "P2", "P4"))
    bus.inject(RecoveryRequest(t_sim=2.0, car_id="P3", role=Role.SUPERVISOR))
    bus.run_until(10.0)
    assert statuses(bus)[:3] == [
        MissionStatus.APPROVED,
        MissionStatus.UNAVAILABLE,
        MissionStatus.RESERVED,
    ]


def test_AT_04_freight_waits_for_onboard_drop_then_direct_leg(dispatch_impl):
    bus, _ = system(dispatch_impl, at04_fleet())
    bus.inject(freight(0.0))
    bus.run_until(5.0)
    reserved = [
        u
        for u in of_type(bus.delivered, MissionStatusUpdate, mission_id="MS-0001")
        if u.status == MissionStatus.RESERVED
    ]
    assert reserved and reserved[-1].car_id == "P1"
    cands = {c.car_id: c for c in reserved[-1].candidates}
    assert cands["P1"].eta_s == pytest.approx(58.0)  # 44 + 6 + 8 (SRS 4.9 D)
    assert cands["G1"].excluded == ExclusionReason.MAINTENANCE
    (proposal,) = [p for p in proposals(bus, "P1") if p.mission_id == "MS-0001"]
    assert proposal.exclusive
    assert stop_keys(proposal.ordered_stops) == [
        (5, StopKind.CAR_DEST, "PAX-0001"),
        (1, StopKind.MISSION_PICKUP, "MS-0001"),
        (30, StopKind.MISSION_DROPOFF, "MS-0001"),
    ]


def test_D_21_freight_prefers_G1_when_it_is_eligible(dispatch_impl):
    fleet = [CabinInit(car_id="G1", floor=10), CabinInit(car_id="P2", floor=2)]
    bus, _ = system(dispatch_impl, fleet)
    bus.inject(freight(0.0))
    bus.run_until(5.0)
    first = next(p for p in proposals(bus) if p.mission_id == "MS-0001")
    assert first.car_id == "G1"


# ======================================================================================
# Policy, baselines, determinism, shape (TH-06, OPT-06, NFR-05)
# ======================================================================================


@pytest.mark.fake_ok
def test_OPT_06_policy_activation_changes_no_route_by_itself(dispatch_impl):
    d = dispatch_impl.create()
    new = Policy(version="POL-0001", R=2, W=4)
    out = feed(d, start(d), tel("P1", 31), PolicyActivated(t_sim=1.0, policy=new))
    assert not of_type(out, RouteProposal)
    assert d.snapshot()["policy"] == {"version": "POL-0001", "R": 2, "W": 4}


@pytest.mark.fake_ok
@pytest.mark.parametrize("algorithm", [DispatchAlgorithm.FIRST_IDLE, DispatchAlgorithm.SWEEP])
def test_TH_06_baseline_policies_serve_calls(dispatch_impl, algorithm):
    bus, _ = system(dispatch_impl, at01_fleet(), algorithm=algorithm)
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(60.0)
    assert served(bus, "HC-0001") is not None
    assert_single_owner(bus)


@pytest.mark.fake_ok
def test_NFR_05_same_inputs_same_log(dispatch_impl):
    def one_run():
        bus, _ = system(dispatch_impl, at03_fleet())
        bus.inject(mission(0.0))
        bus.inject(call(1.0, 15, UP))
        bus.inject(call(2.0, 9, DOWN))
        bus.run_until(90.0)
        return log_text(bus)

    assert one_run() == one_run()


@pytest.mark.fake_ok
def test_F4_snapshot_shape(dispatch_impl):
    bus, d = system(dispatch_impl, at01_fleet())
    bus.inject(call(0.0, 27, DOWN))
    bus.run_until(3.0)
    snap = d.snapshot()
    assert set(snap) == set(DispatchSnapshot.__annotations__)
    assert snap["calls"] and all(set(c) == set(CallView.__annotations__) for c in snap["calls"])
    json.dumps(snap)
