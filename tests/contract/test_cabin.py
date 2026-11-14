"""Contract tests for the Cabin (Hai). Written from SRS numbers before the module exists.

One real cabin runs alone on the bus; the tests play Dispatch by injecting RouteProposals,
and play passengers / staff / scenario by injecting the other inputs. Tests look only at
emitted messages and ``snapshot()``. Skipped until ``cabin/`` exists.
``pytest --fakes`` runs the ``fake_ok`` ones against fakes/cabin.py.
"""

import dataclasses
import json

import pytest

from contracts.config import ACK_TIMEOUT_S, SECONDS_PER_FLOOR
from contracts.enums import (
    AlertKind,
    CabinMode,
    Direction,
    DoorState,
    FaultKind,
    MissionAction,
    MissionStatus,
    NackReason,
    RejectReason,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.messages import (
    AlarmPressed,
    Alert,
    CabinDestinationPressed,
    CabinInit,
    DoorRequest,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    MaintenanceSet,
    Message,
    MissionLoadConfirmed,
    MissionStatusUpdate,
    Passenger,
    PassengerBoard,
    PassengerExit,
    RecoveryApproved,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    Stop,
)
from contracts.ui import CabinSnapshot, CarView
from tests.contract.helpers import (
    EPS,
    assert_door_interlocks,
    dest,
    events,
    floors,
    hall,
    last_tel,
    log_text,
    make_bus,
    of_type,
    telemetry,
    times,
)

DOWN, UP = Direction.DOWN, Direction.UP
E = ServiceEventType
DOOR_EVENTS = (E.DOOR_OPENING, E.DOOR_OPENED, E.DOOR_CLOSING, E.DOOR_CLOSED_LOCKED)


def cabin_bus(cabin_impl, init: CabinInit):
    cabin = cabin_impl.create(init)
    return make_bus(cabin), cabin


def propose(bus, t, stops, version, cmd="RC-0001", car="P1", **extra) -> RouteProposal:
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


def reply(bus, cmd: str) -> RouteReply:
    return of_type(bus.delivered, RouteReply, command_id=cmd)[-1]


def version(bus, car="P1") -> int:
    return last_tel(bus, car).route_version


def at(bus, car, kind, floor=None) -> list[float]:
    return [e.t_sim for e in events(bus, car, kind) if floor is None or e.floor == floor]


def run_script(bus, script: list[tuple[float, Message]], until: float) -> None:
    for t, msg in sorted(script, key=lambda item: item[0]):
        bus.run_until(t)
        bus.inject(msg)
    bus.run_until(until)


def at01(cabin_impl, until=70.0, extra=()):
    """SRS 4.9 A: P1 idle at 31, pickup at 27 DOWN, passenger boards and chooses 5."""
    bus, cabin = cabin_bus(cabin_impl, CabinInit(car_id="P1", floor=31))
    propose(bus, 0.0, [hall(27, DOWN, "HC-0001")], 0)
    script = [
        (9.0, PassengerBoard(t_sim=9.0, car_id="P1", passenger_id="PAX-0001")),
        (
            9.0,
            CabinDestinationPressed(t_sim=9.0, car_id="P1", floor=5, passenger_id="PAX-0001"),
        ),
        *extra,
    ]
    run_script(bus, script, until)
    return bus, cabin


def mission_stops(mid="MS-0001", pickup=1, dropoff=30) -> list[Stop]:
    return [
        Stop(floor=pickup, kind=StopKind.MISSION_PICKUP, ref_id=mid),
        Stop(floor=dropoff, kind=StopKind.MISSION_DROPOFF, ref_id=mid),
    ]


def freight_bus(cabin_impl, weight_kg=75.0):
    """SRS 4.9 D: P1 at 27 carries PAX-0001 to 5; exclusive freight 1 -> 30 proposed at 0."""
    rider = Passenger(passenger_id="PAX-0001", weight_kg=weight_kg, destination=5)
    bus, cabin = cabin_bus(cabin_impl, CabinInit(car_id="P1", floor=27, passengers=(rider,)))
    propose(
        bus,
        0.0,
        [dest(5, "PAX-0001"), *mission_stops()],
        0,
        mission_id="MS-0001",
        exclusive=True,
    )
    return bus, cabin


def confirm(t, action) -> MissionLoadConfirmed:
    return MissionLoadConfirmed(t_sim=t, mission_id="MS-0001", car_id="P1", action=action)


def mission_updates(bus, status=None):
    return [
        u
        for u in of_type(bus.delivered, MissionStatusUpdate, mission_id="MS-0001")
        if status is None or u.status == status
    ]


# ======================================================================================
# Telemetry and motion (HA-01, HA-02)
# ======================================================================================


@pytest.mark.fake_ok
def test_D_08_D_11_telemetry_on_start_heartbeat_and_loss(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    bus.run_until(10.0)
    ts = times(telemetry(bus, "P2"))
    assert ts[0] == 0.0
    assert all(b - a <= 1.0 + EPS for a, b in zip(ts, ts[1:], strict=False))
    assert ts[-1] >= 9.0
    bus.inject(FaultInjected(t_sim=10.0, car_id="P2", kind=FaultKind.HEARTBEAT_LOSS))
    bus.run_until(15.0)
    assert not [t for t in times(telemetry(bus, "P2")) if t > 10.0]


@pytest.mark.fake_ok
def test_SYS_02_MOVE_01_initial_telemetry(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P1", floor=31))
    bus.run_until(0.0)
    first = telemetry(bus, "P1")[0]
    assert (first.position, first.direction, first.moving) == (31.0, Direction.IDLE, False)
    assert (first.door, first.locked, first.mode) == (DoorState.CLOSED, True, CabinMode.AUTO)
    assert (first.route_version, first.stops, first.passengers) == (0, (), 0)
    assert first.sensors_valid


@pytest.mark.fake_ok
def test_AT_01_cabin_timeline(cabin_impl):
    bus, _ = at01(cabin_impl)
    ack = reply(bus, "RC-0001")
    assert (ack.accepted, ack.committed_version) == (True, 1)
    (pickup,) = events(bus, "P1", E.HALL_CALL_SERVED)
    assert (pickup.t_sim, pickup.floor, pickup.ref_id) == (8.0, 27, "HC-0001")
    assert at(bus, "P1", E.DEST_ACCEPTED, 5) == [9.0]
    (dropoff,) = events(bus, "P1", E.DEST_SERVED)
    assert (dropoff.t_sim, dropoff.floor, dropoff.passenger_id) == (58.0, 5, "PAX-0001")
    assert at(bus, "P1", E.DOOR_CLOSED_LOCKED, 5)[-1] == 64.0


def test_D_03_door_cycle_inside_the_6s_dwell(cabin_impl):
    bus, _ = at01(cabin_impl, until=20.0)
    door = [(e.t_sim, e.event) for e in events(bus, "P1", *DOOR_EVENTS) if e.floor == 27]
    assert door == [
        (8.0, E.DOOR_OPENING),
        (9.0, E.DOOR_OPENED),
        (13.0, E.DOOR_CLOSING),
        (14.0, E.DOOR_CLOSED_LOCKED),
    ]
    assert at(bus, "P1", E.CAR_DEPARTED, 27) == [14.0]


def test_MOVE_03_position_is_continuous(cabin_impl):
    bus, _ = at01(cabin_impl, until=20.0)
    samples = telemetry(bus, "P1")
    at_one = [s for s in samples if s.t_sim == 1.0]
    assert at_one and at_one[-1].position == pytest.approx(30.5)
    for a, b in zip(samples, samples[1:], strict=False):
        assert abs(b.position - a.position) <= (b.t_sim - a.t_sim) / SECONDS_PER_FLOOR + EPS


@pytest.mark.fake_ok
def test_SAF_01_SAF_02_MOVE_02_DOOR_02_door_interlocks(cabin_impl):
    bus, _ = at01(cabin_impl)
    assert_door_interlocks(bus, "P1")


@pytest.mark.fake_ok
def test_CB_03_CB_04_REQ_05_destination_committed_and_separate_events(cabin_impl):
    bus, _ = at01(cabin_impl)
    after_press = [t for t in telemetry(bus, "P1") if 9.0 < t.t_sim < 58.0]
    assert after_press and all(dest(5, "PAX-0001") in t.stops for t in after_press)
    (pickup,) = events(bus, "P1", E.HALL_CALL_SERVED)
    (dropoff,) = events(bus, "P1", E.DEST_SERVED)
    assert pickup is not dropoff and pickup.t_sim < dropoff.t_sim


@pytest.mark.fake_ok
def test_DOOR_06_destination_before_boarding_rejected(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=5, passenger_id="NOBODY"))
    bus.run_until(2.0)
    (rejected,) = events(bus, "P2", E.DEST_REJECTED)
    assert rejected.reason == RejectReason.NOT_BOARDED
    assert not events(bus, "P2", E.DEST_ACCEPTED)
    assert last_tel(bus, "P2").stops == ()


# ======================================================================================
# Doors (HA-03)
# ======================================================================================


def test_DOOR_04_D_04_obstruction_while_closing_reopens(cabin_impl):
    obstruction = FaultInjected(t_sim=13.5, car_id="P1", kind=FaultKind.OBSTRUCTION)
    bus, _ = at01(cabin_impl, extra=[(13.5, obstruction)])
    assert at(bus, "P1", E.DOOR_REOPENED, 27) == [13.5]
    assert at(bus, "P1", E.DOOR_CLOSED_LOCKED, 27)[-1] == 19.5  # 1 s + 4 s + 1 s
    assert at(bus, "P1", E.CAR_DEPARTED, 27) == [19.5]
    assert at(bus, "P1", E.DEST_SERVED) == [63.5]


def test_SIM_04_obstruction_beats_door_close_at_the_same_instant(cabin_impl):
    obstruction = FaultInjected(t_sim=14.0, car_id="P1", kind=FaultKind.OBSTRUCTION)
    bus, _ = at01(cabin_impl, until=30.0, extra=[(14.0, obstruction)])
    assert at(bus, "P1", E.DOOR_REOPENED, 27) == [14.0]
    assert at(bus, "P1", E.CAR_DEPARTED, 27) == [20.0]


def test_DOOR_03_open_button_restarts_the_open_period(cabin_impl):
    press = DoorRequest(t_sim=12.0, car_id="P1", open=True)
    bus, _ = at01(cabin_impl, until=30.0, extra=[(12.0, press)])
    assert at(bus, "P1", E.DOOR_CLOSED_LOCKED, 27)[-1] == 17.0  # OPEN until 16, CLOSING 1 s
    assert at(bus, "P1", E.CAR_DEPARTED, 27) == [17.0]


@pytest.mark.fake_ok
def test_DOOR_05_open_button_ignored_while_moving(cabin_impl):
    press = DoorRequest(t_sim=20.0, car_id="P1", open=True)
    bus, _ = at01(cabin_impl, extra=[(20.0, press)])
    assert not [e for e in events(bus, "P1", *DOOR_EVENTS) if 14.0 < e.t_sim < 58.0]
    assert at(bus, "P1", E.DEST_SERVED) == [58.0]


# ======================================================================================
# RouteProposal handling (HA-04)
# ======================================================================================


def test_CB_01_wrong_version_is_nacked_and_route_kept(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(20, UP, "HC-0001")], 5, car="P2")
    bus.run_until(1.0)
    nack = reply(bus, "RC-0001")
    assert (nack.accepted, nack.reason, nack.committed_version) == (
        False,
        NackReason.VERSION_MISMATCH,
        0,
    )
    assert (last_tel(bus, "P2").stops, last_tel(bus, "P2").route_version) == ((), 0)


@pytest.mark.fake_ok
def test_SIM_06_D_14_repeated_command_returns_stored_result(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    first = propose(bus, 0.0, [hall(20, UP, "HC-0001")], 0, car="P2")
    bus.run_until(0.5)
    again = dataclasses.replace(first, msg_id="", t_sim=0.5, deliver_at=None)
    bus.inject(again)  # the identical command delivered a second time
    bus.inject(dataclasses.replace(again, ordered_stops=(hall(21, UP, "HC-0001"),)))
    bus.run_until(1.0)
    a, b, conflict = of_type(bus.delivered, RouteReply, command_id=first.command_id)
    assert a.accepted and b.accepted and a.committed_version == b.committed_version
    assert (conflict.accepted, conflict.reason) == (False, NackReason.PAYLOAD_CONFLICT)
    assert floors(last_tel(bus, "P2").stops) == [20]


@pytest.mark.fake_ok
def test_D_17_version_query_reports_what_happened(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(20, UP, "HC-0001")], 0, car="P2")
    bus.inject(RouteVersionQuery(t_sim=0.0, command_id="RC-0001", car_id="P2"))
    bus.inject(RouteVersionQuery(t_sim=0.0, command_id="RC-0099", car_id="P2"))
    bus.run_until(0.0)
    known, unknown = of_type(bus.delivered, RouteVersionReport)
    assert (known.command_id, known.seen, known.accepted) == ("RC-0001", True, True)
    assert (unknown.command_id, unknown.seen, unknown.accepted) == ("RC-0099", False, None)
    assert known.route_version == version(bus, "P2")


def test_D_10_expired_proposal_is_nacked(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    bus.inject(
        RouteProposal(
            t_sim=0.0,
            deliver_at=3.0,  # arrives after expiry_ts
            command_id="RC-0001",
            car_id="P2",
            expected_route_version=0,
            ordered_stops=(hall(20, UP, "HC-0001"),),
            expiry_ts=2.0,
        )
    )
    bus.run_until(4.0)
    nack = reply(bus, "RC-0001")
    assert (nack.accepted, nack.reason) == (False, NackReason.EXPIRED)
    assert last_tel(bus, "P2").stops == ()


def _moving_up_from_20(cabin_impl):
    bus, cabin = cabin_bus(cabin_impl, CabinInit(car_id="P1", floor=20))
    propose(bus, 0.0, [hall(30, UP, "HC-0001")], 0)
    return bus, cabin


def test_MOVE_06_late_insertion_under_one_floor_rejected(cabin_impl):
    bus, _ = _moving_up_from_20(cabin_impl)
    bus.run_until(13.6)  # position 26.8: floor 27 is 0.2 floors ahead
    propose(bus, 13.6, [hall(27, UP, "HC-0002"), hall(30, UP, "HC-0001")], version(bus), "RC-2")
    bus.run_until(14.0)
    nack = reply(bus, "RC-2")
    assert (nack.accepted, nack.reason) == (False, NackReason.INSERT_CUTOFF)
    assert floors(last_tel(bus, "P1").stops) == [30]


def test_MOVE_06_insertion_exactly_one_floor_ahead_accepted(cabin_impl):
    bus, _ = _moving_up_from_20(cabin_impl)
    bus.run_until(12.0)  # position 26.0: floor 27 is exactly 1 floor ahead
    propose(bus, 12.0, [hall(27, UP, "HC-0002"), hall(30, UP, "HC-0001")], version(bus), "RC-2")
    bus.run_until(20.0)
    assert reply(bus, "RC-2").accepted
    assert at(bus, "P1", E.CAR_ARRIVED, 27) == [14.0]


def test_MOVE_05_no_reversal_while_committed(cabin_impl):
    bus, _ = _moving_up_from_20(cabin_impl)
    bus.run_until(4.0)  # position 22, committed UP to 30
    propose(bus, 4.0, [hall(21, UP, "HC-0002"), hall(30, UP, "HC-0001")], version(bus), "RC-2")
    bus.run_until(5.0)
    nack = reply(bus, "RC-2")
    assert (nack.accepted, nack.reason) == (False, NackReason.REVERSAL_REQUIRED)


def test_CB_03_route_that_drops_an_onboard_destination_is_nacked(cabin_impl):
    rider = Passenger(passenger_id="PAX-0001", destination=5)
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P1", floor=27, passengers=(rider,)))
    propose(bus, 0.0, [hall(20, DOWN, "HC-0001")], 0)  # dest 5 missing
    bus.run_until(1.0)
    nack = reply(bus, "RC-0001")
    assert (nack.accepted, nack.reason) == (False, NackReason.ONBOARD_COMMITMENT)
    assert dest(5, "PAX-0001") in last_tel(bus, "P1").stops


def test_SEN_02_conflicting_sensors_are_never_treated_as_safe(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    bus.inject(FaultInjected(t_sim=0.5, car_id="P2", kind=FaultKind.SENSOR_CONFLICT))
    bus.run_until(1.0)
    assert last_tel(bus, "P2").sensors_valid is False
    propose(bus, 1.0, [hall(20, UP, "HC-0001")], version(bus, "P2"), car="P2")
    bus.run_until(2.0)
    nack = reply(bus, "RC-0001")
    assert (nack.accepted, nack.reason) == (False, NackReason.SENSORS_INVALID)


# ======================================================================================
# Load and boarding (HA-05)
# ======================================================================================


def _door_open_at_12(cabin_impl):
    """P2 serves a call at its own floor: door opening 0-1 s, OPEN 1-5 s."""
    bus, cabin = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(12, UP, "HC-0001")], 0, car="P2")
    bus.run_until(1.0)
    return bus, cabin


@pytest.mark.parametrize(
    ("load_kg", "mode", "accepted"),
    [(899.0, CabinMode.AUTO, True), (900.0, CabinMode.FULL, False)],
    ids=["899kg", "900kg"],
)
def test_CB_02_SEN_03_full_load_bypasses_new_pickups(cabin_impl, load_kg, mode, accepted):
    bus, _ = _door_open_at_12(cabin_impl)
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="PAX-1", weight_kg=load_kg))
    bus.run_until(2.0)
    assert last_tel(bus, "P2").mode == mode
    propose(bus, 2.0, [hall(20, UP, "HC-0002")], version(bus, "P2"), "RC-2", car="P2")
    bus.run_until(2.0)
    answer = reply(bus, "RC-2")
    assert answer.accepted is accepted
    if not accepted:
        assert answer.reason == NackReason.FULL_LOAD


@pytest.mark.fake_ok
@pytest.mark.parametrize(
    ("heavy_kg", "moves"), [(925.0, True), (926.0, False)], ids=["1000kg", "1001kg"]
)
def test_SEN_03_SEN_04_CB_05_overload_holds_until_someone_exits(cabin_impl, heavy_kg, moves):
    bus, _ = _door_open_at_12(cabin_impl)
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="PAX-1", weight_kg=75.0))
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="PAX-2", weight_kg=heavy_kg))
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=20, passenger_id="PAX-1"))
    bus.run_until(20.0)
    departed = at(bus, "P2", E.CAR_DEPARTED, 12)
    if moves:
        assert departed and departed[0] <= 7.0
        return
    assert not departed
    assert last_tel(bus, "P2").mode == CabinMode.OVERLOAD_HOLD
    bus.inject(PassengerExit(t_sim=20.0, car_id="P2", passenger_id="PAX-2"))
    bus.run_until(40.0)
    assert [t for t in at(bus, "P2", E.CAR_DEPARTED, 12) if t >= 20.0]


# ======================================================================================
# Faults, alarm, fire, maintenance, recovery (HA-06)
# ======================================================================================


def test_EMG_02_SAF_03_serious_fault_between_floors(cabin_impl):
    fault = FaultInjected(t_sim=21.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS)
    bus, _ = at01(cabin_impl, until=25.0, extra=[(21.0, fault)])  # position 23.5 at 21 s
    propose(bus, 25.0, [dest(5, "PAX-0001")], version(bus), "RC-2")
    bus.run_until(40.0)
    held = [t for t in telemetry(bus, "P1") if t.t_sim > 21.0]
    assert held
    for t in held:
        assert t.mode == CabinMode.FAULT_HOLD and not t.moving
        assert t.position == pytest.approx(23.5)
        assert t.door == DoorState.CLOSED and t.locked
        assert t.passengers == 1
    assert [a for a in of_type(bus.delivered, Alert, car_id="P1") if a.kind == AlertKind.FAULT]
    assert not [e for e in events(bus, "P1", E.DOOR_OPENING, E.DOOR_OPENED) if e.t_sim > 21.0]
    nack = reply(bus, "RC-2")
    assert (nack.accepted, nack.reason) == (False, NackReason.FAULT_HOLD)


@pytest.mark.fake_ok
def test_EMG_04_recovery_needs_cleared_fault_and_approval(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    bus.inject(FaultInjected(t_sim=1.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    bus.inject(RecoveryApproved(t_sim=2.0, car_id="P2", approved_by=Role.SUPERVISOR))
    bus.run_until(2.5)
    assert last_tel(bus, "P2").mode == CabinMode.FAULT_HOLD  # fault still active
    bus.inject(FaultCleared(t_sim=3.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    bus.run_until(3.5)
    assert last_tel(bus, "P2").mode == CabinMode.FAULT_HOLD  # no approval yet
    bus.inject(RecoveryApproved(t_sim=4.0, car_id="P2", approved_by=Role.SUPERVISOR))
    bus.run_until(5.0)
    assert last_tel(bus, "P2").mode == CabinMode.AUTO


@pytest.mark.fake_ok
def test_EMG_01_D_19_alarm_raises_no_alert_from_the_cabin(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P1", floor=31))
    bus.inject(AlarmPressed(t_sim=1.0, car_id="P1"))
    bus.run_until(3.0)
    assert not [a for a in of_type(bus.delivered, Alert) if a.kind == AlertKind.ALARM]
    assert last_tel(bus, "P1").mode == CabinMode.AUTO


def test_D_24_fire_finishes_the_leg_and_refuses_new_pickups(cabin_impl):
    fire = FireAlarm(t_sim=20.0, active=True)
    bus, _ = at01(cabin_impl, until=25.0, extra=[(20.0, fire)])
    assert last_tel(bus, "P1").mode == CabinMode.FIRE
    propose(bus, 25.0, [hall(10, DOWN, "HC-0009"), dest(5, "PAX-0001")], version(bus), "RC-2")
    bus.run_until(70.0)
    nack = reply(bus, "RC-2")
    assert (nack.accepted, nack.reason) == (False, NackReason.FIRE)
    assert at(bus, "P1", E.DEST_SERVED) == [58.0]


def test_EMG_04_maintenance_refuses_routes_until_recovery(cabin_impl):
    bus, _ = cabin_bus(cabin_impl, CabinInit(car_id="P2", floor=12))
    bus.inject(MaintenanceSet(t_sim=0.0, car_id="P2", on=True, role=Role.MAINTAINER))
    bus.run_until(0.5)
    assert last_tel(bus, "P2").mode == CabinMode.MAINTENANCE
    propose(bus, 1.0, [hall(20, UP, "HC-0001")], version(bus, "P2"), car="P2")
    bus.run_until(1.5)
    nack = reply(bus, "RC-0001")
    assert (nack.accepted, nack.reason) == (False, NackReason.MAINTENANCE)
    bus.inject(RecoveryApproved(t_sim=2.0, car_id="P2", approved_by=Role.SUPERVISOR))
    bus.run_until(3.0)
    assert last_tel(bus, "P2").mode == CabinMode.AUTO


# ======================================================================================
# Missions (HA-07)
# ======================================================================================


def test_AT_04_freight_timeline_with_drain_and_exclusive_leg(cabin_impl):
    bus, _ = freight_bus(cabin_impl)
    script = [
        (45.0, PassengerExit(t_sim=45.0, car_id="P1", passenger_id="PAX-0001")),
        (60.0, confirm(60.0, MissionAction.LOADED)),
        (124.0, confirm(124.0, MissionAction.UNLOADED)),
    ]
    run_script(bus, script[:2], until=80.0)
    propose(bus, 80.0, [hall(20, UP, "HC-0009"), mission_stops()[1]], version(bus), "RC-2")
    run_script(bus, script[2:], until=130.0)

    assert reply(bus, "RC-0001").accepted
    draining = [u.t_sim for u in mission_updates(bus, MissionStatus.DRAINING)]
    assert draining and draining[0] <= 44.0
    assert at(bus, "P1", E.CAR_ARRIVED, 5) == [44.0]
    assert at(bus, "P1", E.DEST_SERVED, 5) == [44.0]
    assert [t for t in at(bus, "P1", E.HANDOFF_OK) if 45.0 <= t <= 50.0]
    assert at(bus, "P1", E.CAR_DEPARTED, 5) == [50.0]
    assert at(bus, "P1", E.CAR_ARRIVED, 1) == [58.0]
    assert at(bus, "P1", E.MISSION_PICKUP_DONE) == [60.0]
    assert [u.t_sim for u in mission_updates(bus, MissionStatus.IN_PROGRESS)] == [60.0]
    assert at(bus, "P1", E.CAR_DEPARTED, 1) == [64.0]
    assert at(bus, "P1", E.CAR_ARRIVED, 30) == [122.0]
    assert at(bus, "P1", E.MISSION_DELIVERY_DONE) == [122.0]
    assert [u.t_sim for u in mission_updates(bus, MissionStatus.COMPLETED)] == [124.0]
    nack = reply(bus, "RC-2")
    assert (nack.accepted, nack.reason) == (False, NackReason.MISSION_EXCLUSIVE)
    leg = [t for t in telemetry(bus, "P1") if 64.0 < t.t_sim < 122.0]
    assert leg and all(floors(t.stops) == [30] for t in leg)
    assert not [e for e in events(bus, "P1", E.CAR_ARRIVED) if 64.0 < e.t_sim < 122.0]
    assert last_tel(bus, "P1").mode == CabinMode.AUTO


def test_SEN_05_SEN_06_low_load_with_a_passenger_blocks_the_handoff(cabin_impl):
    bus, _ = freight_bus(cabin_impl, weight_kg=0.0)  # load reads 0 kg, 1 person on board
    bus.run_until(100.0)  # the passenger never leaves
    assert not at(bus, "P1", E.CAR_ARRIVED, 1)
    assert not at(bus, "P1", E.HANDOFF_OK)
    assert not mission_updates(bus, MissionStatus.IN_PROGRESS)
    assert mission_updates(bus)[-1].status in (
        MissionStatus.DRAINING,
        MissionStatus.HOLD_PENDING_REVIEW,
    )
    assert [a for a in of_type(bus.delivered, Alert) if a.kind == AlertKind.HANDOFF_REVIEW]


@pytest.mark.fake_ok
def test_FRT_04_failed_loading_ends_the_mission(cabin_impl):
    bus, _ = freight_bus(cabin_impl)
    script = [
        (45.0, PassengerExit(t_sim=45.0, car_id="P1", passenger_id="PAX-0001")),
        (60.0, confirm(60.0, MissionAction.LOAD_FAILED)),
    ]
    run_script(bus, script, until=75.0)
    assert [u.t_sim for u in mission_updates(bus, MissionStatus.FAILED)] == [60.0]
    final = last_tel(bus, "P1")
    assert final.mode == CabinMode.AUTO and final.mission_id is None
    assert not [s for s in final.stops if s.ref_id == "MS-0001"]


# ======================================================================================
# Determinism and shape
# ======================================================================================


@pytest.mark.fake_ok
def test_NFR_05_same_inputs_same_log(cabin_impl):
    assert log_text(at01(cabin_impl)[0]) == log_text(at01(cabin_impl)[0])


@pytest.mark.fake_ok
def test_F4_snapshot_shape(cabin_impl):
    _, cabin = at01(cabin_impl, until=20.0)
    snap = cabin.snapshot()
    assert set(snap) == set(CabinSnapshot.__annotations__)
    assert set(snap["car"]) == set(CarView.__annotations__)
    assert snap["car"]["car_id"] == "P1" and snap["dests"]
    json.dumps(snap)
