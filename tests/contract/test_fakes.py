"""Tests for fakes/: they satisfy the contract and reproduce the SRS timing (framework F6).

Every test runs the fakes together on the deterministic bus, the same way the engine will.
"""

import dataclasses
import json

from contracts.codec import to_json
from contracts.config import CARS, DEFAULT_POLICY, Policy
from contracts.enums import (
    AlertKind,
    CabinMode,
    CallStatus,
    Direction,
    ExclusionReason,
    FaultKind,
    MissionAction,
    MissionStatus,
    MissionType,
    NackReason,
    RejectReason,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.interfaces import CabinModule, ControlCenterModule, DispatchModule
from contracts.messages import (
    AlarmPressed,
    Alert,
    CabinDestinationPressed,
    CabinInit,
    CabinTelemetry,
    Candidate,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    HallCallAck,
    HallCallPressed,
    MissionApproval,
    MissionLoadConfirmed,
    MissionRequest,
    MissionStatusUpdate,
    Passenger,
    PassengerBoard,
    PolicyDecision,
    PolicyRecommended,
    RecoveryApproved,
    RecoveryRequest,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    ServiceEvent,
    Stop,
)
from contracts.ui import (
    CabinSnapshot,
    CallView,
    CarView,
    ControlCenterSnapshot,
    DestView,
    DispatchSnapshot,
    MissionView,
)
from fakes import cabin as fake_cabin
from fakes import control_center as fake_cc
from fakes import dispatch as fake_dispatch
from simulation.bus import Bus


def run_system(inits: list[CabinInit], policy: Policy = DEFAULT_POLICY):
    dispatch = fake_dispatch.create(policy)
    cc = fake_cc.create()
    cabins = {i.car_id: fake_cabin.create(i) for i in inits}
    bus = Bus([dispatch, cc, *cabins.values()])
    bus.start()
    return bus, dispatch, cc, cabins


def at01_fleet() -> list[CabinInit]:
    return [CabinInit(car_id="P1", floor=31)] + [
        CabinInit(car_id=car, floor=1) for car in CARS if car != "P1"
    ]


def at03_fleet() -> list[CabinInit]:
    return [
        CabinInit(car_id="P1", floor=36, passengers=(Passenger(passenger_id="A", destination=40),)),
        CabinInit(car_id="P2", floor=12),
        CabinInit(car_id="P3", floor=5, mode=CabinMode.MAINTENANCE),
        CabinInit(car_id="P4", floor=28, passengers=(Passenger(passenger_id="B", destination=22),)),
        CabinInit(car_id="G1", floor=3),
    ]


def events(bus: Bus, car_id: str, event: ServiceEventType) -> list[ServiceEvent]:
    return bus.select(ServiceEvent, car_id=car_id, event=event)


def mission_request(t: float, origin: int | None = 2, destination: int | None = 30):
    return MissionRequest(
        t_sim=t,
        role=Role.MEDICAL_COORDINATOR,
        mission_type=MissionType.MEDICAL,
        origin=origin,
        destination=destination,
    )


# --- shape -----------------------------------------------------------------------------


def test_F6_fakes_satisfy_module_protocols():
    assert isinstance(fake_dispatch.create(), DispatchModule)
    assert isinstance(fake_cc.create(), ControlCenterModule)
    cabin = fake_cabin.create(CabinInit(car_id="P1", floor=1))
    assert isinstance(cabin, CabinModule)
    assert cabin.name == "cabin:P1"


def test_F10_fake_snapshots_match_ui_shapes():
    bus, dispatch, cc, cabins = run_system(at01_fleet())
    bus.inject(HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN))
    bus.inject(mission_request(0.0))
    bus.inject(AlarmPressed(t_sim=0.0, car_id="P4"))
    bus.run_until(9.0)
    bus.inject(PassengerBoard(t_sim=9.0, car_id="P1", passenger_id="PAX-0001"))
    bus.inject(CabinDestinationPressed(t_sim=9.0, car_id="P1", floor=5, passenger_id="PAX-0001"))
    bus.inject(HallCallPressed(t_sim=9.0, floor=20, direction=Direction.UP))
    bus.run_until(10.0)

    d = dispatch.snapshot()
    assert set(d) == set(DispatchSnapshot.__annotations__)
    assert d["calls"] and all(set(c) == set(CallView.__annotations__) for c in d["calls"])

    c = cabins["P1"].snapshot()
    assert set(c) == set(CabinSnapshot.__annotations__)
    assert set(c["car"]) == set(CarView.__annotations__)
    assert c["dests"] and all(set(x) == set(DestView.__annotations__) for x in c["dests"])

    s = cc.snapshot()
    assert set(s) == set(ControlCenterSnapshot.__annotations__)
    assert s["missions"] and set(s["missions"][0]) == set(MissionView.__annotations__)
    assert s["alerts"]

    json.dumps([d, c, s])


# --- ordinary trips --------------------------------------------------------------------


def test_AT_01_timeline_with_all_fakes():
    bus, *_ = run_system(at01_fleet())
    bus.inject(HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN))
    bus.run_until(9.0)
    bus.inject(PassengerBoard(t_sim=9.0, car_id="P1", passenger_id="PAX-0001"))
    bus.inject(CabinDestinationPressed(t_sim=9.0, car_id="P1", floor=5, passenger_id="PAX-0001"))
    bus.run_until(70.0)

    (pickup,) = events(bus, "P1", ServiceEventType.HALL_CALL_SERVED)
    (dropoff,) = events(bus, "P1", ServiceEventType.DEST_SERVED)
    closes = events(bus, "P1", ServiceEventType.DOOR_CLOSED_LOCKED)
    assert (pickup.t_sim, pickup.floor, pickup.ref_id) == (8.0, 27, "HC-0001")
    assert (dropoff.t_sim, dropoff.floor, dropoff.passenger_id) == (58.0, 5, "PAX-0001")
    assert (closes[-1].t_sim, closes[-1].floor) == (64.0, 5)
    assert pickup is not dropoff  # CB-04, REQ-05: distinct lifecycle events


def test_REQ_02_duplicate_hall_call_returns_existing_request():
    bus, *_ = run_system(at01_fleet())
    bus.inject(HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN))
    bus.run_until(1.0)
    bus.inject(HallCallPressed(t_sim=1.0, floor=27, direction=Direction.DOWN))
    bus.run_until(1.0)
    first, second = bus.select(HallCallAck)
    assert first.request_id == second.request_id == "HC-0001"
    assert (first.duplicate, second.duplicate) == (False, True)
    assert second.status == CallStatus.ASSIGNED


def test_SYS_03_invalid_hall_calls_are_rejected_with_reason():
    bus, dispatch, *_ = run_system(at01_fleet())
    cases = [
        (1, Direction.DOWN, RejectReason.TERMINAL_DIRECTION),
        (40, Direction.UP, RejectReason.TERMINAL_DIRECTION),
        (0, Direction.UP, RejectReason.FLOOR_OUT_OF_RANGE),
        (41, Direction.DOWN, RejectReason.FLOOR_OUT_OF_RANGE),
        (10, Direction.IDLE, RejectReason.INVALID_DIRECTION),
    ]
    for floor, direction, _ in cases:
        bus.inject(HallCallPressed(t_sim=0.0, floor=floor, direction=direction))
    bus.run_until(0.0)
    acks = bus.select(HallCallAck)
    assert [(a.accepted, a.request_id, a.reason) for a in acks] == [
        (False, None, reason) for _, _, reason in cases
    ]
    assert dispatch.snapshot()["calls"] == []


def test_SIM_05_call_stays_pending_while_the_only_car_is_busy():
    bus, dispatch, *_ = run_system([CabinInit(car_id="P1", floor=31)])
    bus.inject(HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN))
    bus.inject(HallCallPressed(t_sim=1.0, floor=10, direction=Direction.UP))
    bus.run_until(5.0)
    second = next(c for c in dispatch.snapshot()["calls"] if c["request_id"] == "HC-0002")
    assert (second["status"], second["owner"]) == (CallStatus.PENDING, None)
    bus.run_until(60.0)
    served = events(bus, "P1", ServiceEventType.HALL_CALL_SERVED)
    # 27 at 8 s; door closes at 14 s; 27 -> 10 is 17 floors = 34 s
    assert [(e.ref_id, e.t_sim) for e in served] == [("HC-0001", 8.0), ("HC-0002", 48.0)]


def test_DOOR_06_destination_before_boarding_is_rejected():
    bus, *_ = run_system([CabinInit(car_id="P2", floor=12)])
    bus.inject(CabinDestinationPressed(t_sim=0.0, car_id="P2", floor=5, passenger_id="NOBODY"))
    bus.run_until(1.0)
    (rejected,) = events(bus, "P2", ServiceEventType.DEST_REJECTED)
    assert rejected.reason == RejectReason.NOT_BOARDED
    assert not events(bus, "P2", ServiceEventType.DEST_ACCEPTED)


def test_D_24_fire_alarm_stops_new_assignments():
    bus, dispatch, cc, _ = run_system(at01_fleet())
    bus.inject(FireAlarm(t_sim=0.0, active=True))
    bus.inject(HallCallPressed(t_sim=1.0, floor=27, direction=Direction.DOWN))
    bus.run_until(10.0)
    assert dispatch.snapshot()["calls"][0]["status"] == CallStatus.PENDING
    assert not bus.select(RouteProposal)
    assert [a["kind"] for a in cc.snapshot()["alerts"]] == [AlertKind.FIRE]


# --- missions --------------------------------------------------------------------------


def test_AT_03_mission_flow_with_fakes():
    bus, _, cc, _ = run_system(at03_fleet())
    bus.inject(mission_request(0.0))
    bus.run_until(21.0)
    loaded = MissionLoadConfirmed
    bus.inject(loaded(t_sim=21.0, mission_id="MS-0001", car_id="P2", action=MissionAction.LOADED))
    bus.run_until(83.0)
    bus.inject(loaded(t_sim=83.0, mission_id="MS-0001", car_id="P2", action=MissionAction.UNLOADED))
    bus.run_until(100.0)

    updates = bus.select(MissionStatusUpdate, mission_id="MS-0001")
    assert [(u.status, u.t_sim) for u in updates] == [
        (MissionStatus.APPROVED, 0.0),
        (MissionStatus.RESERVED, 0.0),
        (MissionStatus.IN_PROGRESS, 21.0),  # arrives at 2 at 20 s; LOADED at 21 s (D-52)
        (MissionStatus.COMPLETED, 83.0),  # leaves 26 s, arrives at 30 at 82 s; UNLOADED 83 s
    ]
    (delivered,) = events(bus, "P2", ServiceEventType.MISSION_DELIVERY_DONE)
    assert (delivered.t_sim, delivered.floor) == (82.0, 30)
    (approval,) = bus.select(MissionApproval)
    assert approval.allowed_cars == ("P1", "P2", "P3", "P4")
    reserved = updates[1]
    assert reserved.car_id == "P2"
    assert reserved.candidates == (
        Candidate(car_id="P1", excluded=ExclusionReason.MISSION_BUSY),
        Candidate(car_id="P2", eta_s=20.0),
        Candidate(car_id="P3", excluded=ExclusionReason.MAINTENANCE),
        Candidate(car_id="P4", excluded=ExclusionReason.MISSION_BUSY),
        Candidate(car_id="G1", excluded=ExclusionReason.NOT_CAPABLE),
    )
    (proposal,) = [p for p in bus.select(RouteProposal) if p.mission_id == "MS-0001"]
    assert proposal.exclusive
    assert [s.kind for s in proposal.ordered_stops] == [
        StopKind.MISSION_PICKUP,
        StopKind.MISSION_DROPOFF,
    ]
    view = cc.snapshot()["missions"][0]
    assert (view["status"], view["car_id"]) == (MissionStatus.COMPLETED, "P2")


def test_MED_04_no_eligible_car_gives_one_unavailable_and_an_alert():
    bus, _, cc, _ = run_system([CabinInit(car_id="G1", floor=3)])
    bus.inject(mission_request(0.0))
    bus.run_until(30.0)
    statuses = [u.status for u in bus.select(MissionStatusUpdate)]
    assert statuses == [MissionStatus.APPROVED, MissionStatus.UNAVAILABLE]
    snap = cc.snapshot()
    assert snap["missions"][0]["status"] == MissionStatus.UNAVAILABLE
    assert [a["kind"] for a in snap["alerts"]] == [AlertKind.UNAVAILABLE]


def test_CC_03_mission_without_destination_is_rejected():
    bus, _, cc, _ = run_system(at01_fleet())
    bus.inject(mission_request(0.0, destination=None))
    bus.run_until(1.0)
    (update,) = bus.select(MissionStatusUpdate)
    assert (update.status, update.reason) == (MissionStatus.REJECTED, RejectReason.MISSING_FIELD)
    assert not bus.select(MissionApproval)
    assert cc.snapshot()["missions"][0]["reason"] == RejectReason.MISSING_FIELD


# --- safety, alerts, recovery -----------------------------------------------------------


def test_EMG_01_alarm_gives_exactly_one_alarm_alert_and_no_mission():
    bus, _, cc, _ = run_system(at01_fleet())
    bus.inject(AlarmPressed(t_sim=0.0, car_id="P4"))
    bus.run_until(5.0)
    alerts = cc.snapshot()["alerts"]
    assert [(a["kind"], a["car_id"]) for a in alerts] == [(AlertKind.ALARM, "P4")]
    assert not [a for a in bus.select(Alert) if a.kind == AlertKind.ALARM]
    assert not bus.select(MissionRequest) and not bus.select(MissionApproval)


def test_EMG_04_recovery_needs_fault_cleared_first():
    bus, _, cc, cabins = run_system([CabinInit(car_id="P2", floor=12)])
    bus.inject(FaultInjected(t_sim=1.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    bus.inject(RecoveryRequest(t_sim=2.0, car_id="P2", role=Role.SUPERVISOR))
    bus.run_until(2.0)
    assert bus.select(RecoveryApproved)
    assert cabins["P2"].snapshot()["car"]["mode"] == CabinMode.FAULT_HOLD
    assert [a["kind"] for a in cc.snapshot()["alerts"]] == [AlertKind.FAULT]

    bus.inject(FaultCleared(t_sim=3.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    bus.inject(RecoveryRequest(t_sim=4.0, car_id="P2", role=Role.SUPERVISOR))
    bus.run_until(4.0)
    assert cabins["P2"].snapshot()["car"]["mode"] == CabinMode.AUTO


def test_D_11_heartbeat_and_stale_telemetry():
    bus, dispatch, *_ = run_system([CabinInit(car_id="P2", floor=12)])
    bus.run_until(10.0)
    assert len(bus.select(CabinTelemetry, car_id="P2")) >= 10

    bus.inject(FaultInjected(t_sim=10.0, car_id="P2", kind=FaultKind.HEARTBEAT_LOSS))
    bus.run_until(14.0)  # last telemetry at 10.0 -> stale after 13.0
    bus.inject(HallCallPressed(t_sim=14.0, floor=20, direction=Direction.UP))
    bus.run_until(20.0)
    assert dispatch.snapshot()["calls"][0]["status"] == CallStatus.PENDING


# --- idempotency, policy, determinism -------------------------------------------------


def test_D_14_fake_cabin_is_idempotent_by_command_id():
    bus, *_ = run_system([CabinInit(car_id="P2", floor=12)])
    stop = Stop(floor=20, kind=StopKind.HALL_PICKUP, ref_id="HC-0042", direction=Direction.UP)
    proposal = RouteProposal(
        t_sim=0.0,
        command_id="RC-0042",
        car_id="P2",
        expected_route_version=0,
        ordered_stops=(stop,),
        expiry_ts=2.0,
    )
    bus.inject(proposal)
    bus.run_until(0.0)
    bus.inject(proposal)  # redelivered: same payload
    bus.inject(dataclasses.replace(proposal, ordered_stops=(stop, stop)))  # changed payload
    bus.inject(RouteVersionQuery(t_sim=0.0, command_id="RC-0042", car_id="P2"))
    bus.run_until(0.0)

    first, again, conflict = bus.select(RouteReply, command_id="RC-0042")
    assert first.accepted and again.accepted
    assert first.committed_version == again.committed_version == 1
    assert (conflict.accepted, conflict.reason) == (False, NackReason.PAYLOAD_CONFLICT)
    (report,) = bus.select(RouteVersionReport)
    assert (report.seen, report.accepted) == (True, True)
    assert [a.kind for a in bus.select(Alert)] == [AlertKind.PROTOCOL_ERROR]


def test_OPT_06_only_an_approved_policy_reaches_dispatch():
    bus, dispatch, *_ = run_system(at01_fleet())
    new = Policy(version="POL-0001", R=2, W=4)
    bus.inject(
        PolicyRecommended(
            t_sim=0.0, run_id="RUN-0001", policy=new, objective=12.5, scenario_set=("t1",)
        )
    )
    bus.inject(PolicyDecision(t_sim=1.0, run_id="RUN-0001", approve=False, role=Role.SUPERVISOR))
    bus.run_until(1.0)
    assert dispatch.snapshot()["policy"]["version"] == DEFAULT_POLICY.version

    bus.inject(PolicyDecision(t_sim=2.0, run_id="RUN-0001", approve=True, role=Role.SUPERVISOR))
    bus.run_until(2.0)
    assert dispatch.snapshot()["policy"] == {"version": "POL-0001", "R": 2, "W": 4}


def test_NFR_05_same_inputs_give_identical_logs():
    def one_run() -> list[str]:
        bus, *_ = run_system(at03_fleet())
        bus.inject(mission_request(0.0))
        bus.inject(HallCallPressed(t_sim=3.0, floor=15, direction=Direction.UP))
        bus.inject(AlarmPressed(t_sim=4.0, car_id="P4"))
        bus.run_until(120.0)
        return [to_json(m) for m in bus.delivered]

    first, second = one_run(), one_run()
    assert len(first) > 100
    assert first == second
