"""Real-world edge cases for the Cabin (Hai). Documented in tests/cabin/EDGE_CASES.md.

Each test plays one situation that happens in real buildings. Expected times follow the
SRS model: 2 s per floor; door cycle OPENING 1 s, OPEN 4 s, CLOSING 1 s (D-03).
Test names start with the edge-case ID from EDGE_CASES.md, then the SRS ID.
"""

import pytest

from contracts.enums import (
    AlertKind,
    CabinMode,
    DoorState,
    FaultKind,
    MissionAction,
    MissionStatus,
    NackReason,
    RejectReason,
    Role,
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
    MissionLoadConfirmed,
    MissionStatusUpdate,
    Passenger,
    PassengerBoard,
    PassengerExit,
    RecoveryApproved,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
)
from tests.cabin.cabin_helpers import (
    DOWN,
    UP,
    E,
    at,
    board_many,
    bus_with,
    dest,
    doors_open_at_12,
    dropoff,
    hall,
    inject_all,
    pickup,
    propose,
    stops_now,
    tel_between,
    version,
)
from tests.contract.helpers import assert_door_interlocks, events, last_tel, of_type, telemetry


def reply(bus, cmd: str) -> RouteReply:
    return of_type(bus.delivered, RouteReply, command_id=cmd)[-1]


# ======================================================================================
# EC-01 Rush hour: a full car
# ======================================================================================


def test_EC_01a_CB_02_full_car_passes_new_calls_but_keeps_dropping_off():
    bus, _ = doors_open_at_12()
    board_many(bus, 1.0, "P2", 12)  # 12 x 75 kg = 900 kg
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=20, passenger_id="P01"))
    bus.run_until(2.0)
    assert last_tel(bus, "P2").mode == CabinMode.FULL
    propose(bus, 2.0, [hall(15, UP, "HC-2"), dest(20, "P01")], version(bus, "P2"), "RC-2", "P2")
    bus.run_until(23.0)
    assert (reply(bus, "RC-2").accepted, reply(bus, "RC-2").reason) == (
        False,
        NackReason.FULL_LOAD,
    )
    assert at(bus, "P2", E.CAR_ARRIVED) == [22.0]  # 12 -> 20 nonstop: 6 s + 16 s
    assert at(bus, "P2", E.DEST_SERVED, 20) == [22.0]
    # Two people get out; the car is no longer full and takes new calls again.
    bus.inject(PassengerExit(t_sim=23.0, car_id="P2", passenger_id="P01"))
    bus.inject(PassengerExit(t_sim=23.0, car_id="P2", passenger_id="P02"))
    bus.run_until(24.0)
    assert last_tel(bus, "P2").mode == CabinMode.AUTO
    propose(bus, 24.0, [hall(25, UP, "HC-3")], version(bus, "P2"), "RC-3", "P2")
    bus.run_until(24.0)
    assert reply(bus, "RC-3").accepted


def test_EC_01b_SEN_03_CB_05_last_boarder_overloads_the_car():
    bus, _ = doors_open_at_12()
    board_many(bus, 1.0, "P2", 13)  # 975 kg
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="CHILD", weight_kg=30.0))
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=20, passenger_id="P01"))
    bus.run_until(12.0)
    held = tel_between(bus, "P2", 5.0, 12.0)
    assert held and all(t.mode == CabinMode.OVERLOAD_HOLD for t in held)
    assert all(t.door == DoorState.OPEN and not t.moving for t in held)  # CB-05 hold doors
    assert not at(bus, "P2", E.CAR_DEPARTED)
    bus.inject(PassengerExit(t_sim=12.0, car_id="P2", passenger_id="CHILD"))
    bus.run_until(40.0)
    assert at(bus, "P2", E.DOOR_CLOSING, 12) == [12.0]  # closes as soon as the load is legal
    assert at(bus, "P2", E.CAR_DEPARTED) == [13.0]
    assert at(bus, "P2", E.DEST_SERVED, 20) == [29.0]


# ======================================================================================
# EC-02 Someone holds the door
# ======================================================================================


def test_EC_02a_DOOR_04_door_held_by_repeated_obstruction():
    bus, _ = doors_open_at_12()
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="P01"))
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=20, passenger_id="P01"))
    # Each obstruction 0.5 s into CLOSING; every reopen costs 1 s + 4 s again.
    hits = [5.5, 11.0, 16.5, 22.0, 27.5]
    inject_all(bus, [FaultInjected(t_sim=t, car_id="P2", kind=FaultKind.OBSTRUCTION) for t in hits])
    bus.run_until(60.0)
    assert at(bus, "P2", E.DOOR_REOPENED, 12) == hits
    assert at(bus, "P2", E.DOOR_CLOSED_LOCKED, 12) == [33.5]
    assert at(bus, "P2", E.CAR_DEPARTED) == [33.5]
    assert_door_interlocks(bus, "P2")


def test_EC_02b_DOOR_04_obstruction_while_opening_or_open_changes_nothing():
    bus, _ = doors_open_at_12()
    inject_all(
        bus,
        [
            FaultInjected(t_sim=1.0, car_id="P2", kind=FaultKind.OBSTRUCTION),  # just OPEN
            FaultInjected(t_sim=3.0, car_id="P2", kind=FaultKind.OBSTRUCTION),
        ],
    )
    bus.run_until(10.0)
    assert not at(bus, "P2", E.DOOR_REOPENED)
    assert at(bus, "P2", E.DOOR_CLOSED_LOCKED, 12) == [6.0]


def test_EC_02c_D_51_close_button_shortens_the_stop():
    bus, _ = doors_open_at_12()
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="P01"))
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=20, passenger_id="P01"))
    bus.inject(DoorRequest(t_sim=2.0, car_id="P2", open=False))
    bus.run_until(30.0)
    assert at(bus, "P2", E.DOOR_CLOSING, 12) == [2.0]
    assert at(bus, "P2", E.CAR_DEPARTED) == [3.0]
    assert at(bus, "P2", E.DEST_SERVED, 20) == [19.0]


def test_EC_02d_DOOR_03_open_button_held_down_keeps_the_door_open():
    bus, _ = doors_open_at_12()
    inject_all(bus, [DoorRequest(t_sim=t, car_id="P2", open=True) for t in (3.0, 6.0, 9.0)])
    bus.run_until(20.0)
    assert at(bus, "P2", E.DOOR_CLOSING, 12) == [13.0]  # 4 s after the last press
    assert at(bus, "P2", E.DOOR_CLOSED_LOCKED, 12) == [14.0]


def test_EC_02e_D_51_open_button_in_an_idle_car_opens_without_moving():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    bus.inject(DoorRequest(t_sim=1.0, car_id="P2", open=True))
    bus.run_until(10.0)
    door = [(e.t_sim, e.event) for e in events(bus, "P2", E.DOOR_OPENING, E.DOOR_CLOSED_LOCKED)]
    assert door == [(1.0, E.DOOR_OPENING), (7.0, E.DOOR_CLOSED_LOCKED)]
    assert not at(bus, "P2", E.CAR_DEPARTED)


# ======================================================================================
# EC-03 Trapped between floors
# ======================================================================================


def test_EC_03_EMG_02_EMG_04_trapped_between_floors_full_story():
    rider = Passenger(passenger_id="PAX-1", destination=30)
    bus, _ = bus_with(CabinInit(car_id="P1", floor=20, passengers=(rider,)))  # leaves at 0
    inject_all(
        bus,
        [
            FaultInjected(t_sim=5.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS),  # at 22.5
            AlarmPressed(t_sim=6.0, car_id="P1"),  # the passenger presses the alarm
            DoorRequest(t_sim=7.0, car_id="P1", open=True),  # ... and the door button
            PassengerExit(t_sim=8.0, car_id="P1", passenger_id="PAX-1"),  # tries to get out
            RecoveryApproved(t_sim=10.0, car_id="P1", approved_by=Role.SUPERVISOR),  # too soon
            FaultCleared(t_sim=20.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS),
            RecoveryApproved(t_sim=25.0, car_id="P1", approved_by=Role.SUPERVISOR),
        ],
    )
    bus.run_until(50.0)
    held = tel_between(bus, "P1", 5.0, 25.0)
    assert held
    for t in held:
        assert (t.mode, t.moving, t.door, t.locked) == (
            CabinMode.FAULT_HOLD,
            False,
            DoorState.CLOSED,
            True,
        )
        assert t.position == pytest.approx(22.5) and t.passengers == 1
    alerts = [a.kind for a in of_type(bus.delivered, Alert)]
    assert alerts == [AlertKind.FAULT]  # one fault alert; no ALARM from the cabin (D-19)
    assert not [e for e in events(bus, "P1") if 5.0 < e.t_sim < 25.0]  # nothing moves
    assert 25.0 in at(bus, "P1", E.CAR_DEPARTED)  # resumes only after clear + approval
    assert at(bus, "P1", E.DEST_SERVED, 30) == [40.0]  # 7.5 floors from 22.5


# ======================================================================================
# EC-04 Sensor glitches and lost signal
# ======================================================================================


def test_EC_04a_SEN_02_position_sensor_glitch_mid_trip():
    bus, _ = bus_with(CabinInit(car_id="P1", floor=20))
    propose(bus, 0.0, [hall(30, UP, "HC-1")], 0)
    bus.run_until(3.0)
    bus.inject(FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.POSITION_SENSOR))
    bus.run_until(4.0)
    propose(bus, 4.0, [hall(25, UP, "HC-2"), hall(30, UP, "HC-1")], version(bus, "P1"), "RC-2")
    bus.inject(FaultCleared(t_sim=6.0, car_id="P1", kind=FaultKind.POSITION_SENSOR))
    bus.run_until(30.0)
    assert reply(bus, "RC-2").reason == NackReason.POSITION_INVALID
    stopped = tel_between(bus, "P1", 3.0, 6.0)
    assert stopped and all(not t.moving and not t.sensors_valid for t in stopped)
    assert at(bus, "P1", E.CAR_ARRIVED) == [23.0]  # 8.5 floors left from t=6


def test_EC_04b_D_11_heartbeat_lost_for_5_seconds():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    inject_all(
        bus,
        [
            FaultInjected(t_sim=3.5, car_id="P2", kind=FaultKind.HEARTBEAT_LOSS),
            FaultCleared(t_sim=8.5, car_id="P2", kind=FaultKind.HEARTBEAT_LOSS),
        ],
    )
    bus.run_until(5.0)
    propose(bus, 5.0, [hall(20, UP, "HC-1")], 0, car="P2")  # commands still work
    bus.run_until(12.0)
    sent = [t.t_sim for t in telemetry(bus, "P2")]
    assert not [t for t in sent if 3.5 < t < 8.5]
    assert 8.5 in sent  # back as soon as the link returns
    assert reply(bus, "RC-1").accepted


def test_EC_04c_SEN_02_two_faults_need_both_cleared():
    rider = Passenger(passenger_id="PAX-1", destination=30)
    bus, _ = bus_with(CabinInit(car_id="P1", floor=20, passengers=(rider,)))
    inject_all(
        bus,
        [
            FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.SENSOR_CONFLICT),  # at 21.5
            FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.DOOR_LOCK_LOSS),
            FaultCleared(t_sim=5.0, car_id="P1", kind=FaultKind.SENSOR_CONFLICT),
            FaultCleared(t_sim=7.0, car_id="P1", kind=FaultKind.DOOR_LOCK_LOSS),
        ],
    )
    bus.run_until(40.0)
    still = tel_between(bus, "P1", 3.0, 7.0)
    assert still and all(not t.moving and t.position == pytest.approx(21.5) for t in still)
    assert not [t for t in still if t.t_sim > 5.0 and t.locked]  # lock still unproven
    assert at(bus, "P1", E.CAR_DEPARTED) == [0.0, 7.0]
    assert at(bus, "P1", E.DEST_SERVED, 30) == [24.0]  # 8.5 floors from t=7
    assert_door_interlocks(bus, "P1")


# ======================================================================================
# EC-05 A passenger changes their mind or presses the wrong button
# ======================================================================================


def _one_passenger_at_12():
    bus, module = doors_open_at_12()
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="P01"))
    return bus, module


def press(bus, t, floor, pid="P01", car="P2"):
    bus.inject(CabinDestinationPressed(t_sim=t, car_id=car, floor=floor, passenger_id=pid))


def test_EC_05a_CB_03_passenger_changes_destination():
    bus, _ = _one_passenger_at_12()
    press(bus, 1.5, 20)
    press(bus, 2.0, 25)
    bus.run_until(40.0)
    assert at(bus, "P2", E.DEST_ACCEPTED) == [1.5, 2.0]
    assert [s.floor for s in tel_between(bus, "P2", 1.99, 3.0)[-1].stops] == [25]
    assert not at(bus, "P2", E.CAR_ARRIVED, 20)
    assert at(bus, "P2", E.DEST_SERVED, 25) == [32.0]


def test_EC_05b_CB_03_same_button_twice_is_one_destination():
    bus, _ = _one_passenger_at_12()
    press(bus, 1.5, 20)
    press(bus, 2.0, 20)
    bus.run_until(3.0)
    assert at(bus, "P2", E.DEST_ACCEPTED) == [1.5]
    assert [s.floor for s in stops_now(bus, "P2")] == [20]


def test_EC_05c_DOOR_03_pressing_the_current_floor_does_not_start_a_trip():
    bus, _ = _one_passenger_at_12()
    press(bus, 2.0, 12)
    bus.run_until(20.0)
    assert at(bus, "P2", E.DEST_SERVED, 12) == [2.0]  # served on the spot
    assert at(bus, "P2", E.DOOR_OPENING, 12) == [0.0]  # no second door cycle
    assert at(bus, "P2", E.DOOR_CLOSED_LOCKED, 12) == [7.0]  # OPEN period restarted at 2
    assert not at(bus, "P2", E.CAR_DEPARTED)


@pytest.mark.parametrize("floor", [0, 41, -3])
def test_EC_05d_SYS_03_buttons_outside_the_building_are_rejected(floor):
    bus, _ = _one_passenger_at_12()
    press(bus, 2.0, floor)
    bus.run_until(3.0)
    (rejected,) = events(bus, "P2", E.DEST_REJECTED)
    assert rejected.reason == RejectReason.FLOOR_OUT_OF_RANGE
    assert stops_now(bus, "P2") == ()


def test_EC_05e_MOVE_06_destination_pressed_too_late_for_the_next_floor():
    riders = (Passenger(passenger_id="A", destination=30), Passenger(passenger_id="B"))
    bus, _ = bus_with(CabinInit(car_id="P1", floor=20, passengers=riders))  # up from t=0
    press(bus, 1.0, 21, pid="B", car="P1")  # at 20.5: only 0.5 floors left to 21
    bus.run_until(60.0)
    # No emergency-style stop at 21 two seconds in: 30 first, then back down to 21.
    assert at(bus, "P1", E.CAR_ARRIVED) == [20.0, 44.0]
    assert at(bus, "P1", E.DEST_SERVED, 21) == [44.0]


def test_EC_05f_MOVE_06_destination_exactly_one_floor_ahead_is_served_on_the_way():
    riders = (Passenger(passenger_id="A", destination=30), Passenger(passenger_id="B"))
    bus, _ = bus_with(CabinInit(car_id="P1", floor=20, passengers=riders))
    press(bus, 2.0, 22, pid="B", car="P1")  # at 21.0: exactly 1 floor to 22
    bus.run_until(60.0)
    assert at(bus, "P1", E.CAR_ARRIVED) == [4.0, 26.0]  # 22 at 4 s; 30 at 10 + 16


# ======================================================================================
# EC-06 Several people for the same floor
# ======================================================================================


def test_EC_06_REQ_05_three_passengers_and_a_hall_call_share_one_stop():
    bus, module = doors_open_at_12()
    for pid in ("A", "B", "C"):
        bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id=pid))
        press(bus, 1.0, 20, pid=pid)
    bus.run_until(2.0)
    route = list(stops_now(bus, "P2")) + [hall(20, UP, "HC-2")]
    propose(bus, 2.0, route, version(bus, "P2"), "RC-2", "P2")
    bus.run_until(40.0)
    assert reply(bus, "RC-2").accepted
    assert at(bus, "P2", E.CAR_ARRIVED) == [22.0]
    assert at(bus, "P2", E.DOOR_OPENING, 20) == [22.0]  # one door cycle for all four
    served = events(bus, "P2", E.DEST_SERVED)
    assert sorted(e.passenger_id for e in served) == ["A", "B", "C"]
    assert {e.t_sim for e in served} == {22.0}
    assert at(bus, "P2", E.HALL_CALL_SERVED, 20) == [22.0]


# ======================================================================================
# EC-07 Fire alarm
# ======================================================================================


def test_EC_07a_D_24_fire_while_doors_open_still_delivers_the_passenger():
    bus, _ = _one_passenger_at_12()
    press(bus, 1.0, 20)
    bus.inject(FireAlarm(t_sim=2.0, active=True))
    bus.run_until(3.0)
    assert last_tel(bus, "P2").mode == CabinMode.FIRE
    propose(bus, 3.0, [dest(20, "P01"), hall(25, UP, "HC-2")], version(bus, "P2"), "RC-2", "P2")
    bus.run_until(30.0)
    assert reply(bus, "RC-2").reason == NackReason.FIRE
    assert at(bus, "P2", E.DEST_SERVED, 20) == [22.0]


def test_EC_07b_D_24_service_resumes_when_the_fire_alarm_ends():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    bus.inject(FireAlarm(t_sim=1.0, active=True))
    bus.run_until(2.0)
    propose(bus, 2.0, [hall(20, UP, "HC-1")], 0, "RC-1", "P2")
    bus.inject(FireAlarm(t_sim=5.0, active=False))
    bus.run_until(6.0)
    assert reply(bus, "RC-1").reason == NackReason.FIRE
    assert last_tel(bus, "P2").mode == CabinMode.AUTO
    propose(bus, 6.0, [hall(20, UP, "HC-1")], version(bus, "P2"), "RC-2", "P2")
    bus.run_until(6.0)
    assert reply(bus, "RC-2").accepted


# ======================================================================================
# EC-08 Missions that go wrong
# ======================================================================================


def freight(weight_kg: float = 75.0):
    """SRS 4.9 D: P1 at 27 carries PAX-1 to 5; freight MS-1 from 1 to 30 proposed at 0."""
    rider = Passenger(passenger_id="PAX-1", weight_kg=weight_kg, destination=5)
    bus, module = bus_with(CabinInit(car_id="P1", floor=27, passengers=(rider,)))
    route = [dest(5, "PAX-1"), pickup(1), dropoff(30)]
    propose(bus, 0.0, route, 0, mission_id="MS-1", exclusive=True)
    return bus, module


def confirm(t, action, weight_kg=0.0, mid="MS-1"):
    return MissionLoadConfirmed(
        t_sim=t, mission_id=mid, car_id="P1", action=action, weight_kg=weight_kg
    )


def statuses(bus) -> list[tuple[float, MissionStatus]]:
    return [(u.t_sim, u.status) for u in of_type(bus.delivered, MissionStatusUpdate)]


def test_EC_08a_SEN_05_passenger_who_stays_on_blocks_the_mission_until_they_leave():
    bus, _ = freight()
    bus.run_until(100.0)  # PAX-1 never steps out at floor 5
    assert not at(bus, "P1", E.HANDOFF_OK)
    assert statuses(bus) == [(0.0, MissionStatus.DRAINING)]
    assert last_tel(bus, "P1").position == 5.0  # waits; never leaves with someone inside
    assert not of_type(bus.delivered, Alert)  # count and load agree, so no review alert
    bus.inject(DoorRequest(t_sim=100.0, car_id="P1", open=True))  # they ask to get out
    bus.inject(PassengerExit(t_sim=102.0, car_id="P1", passenger_id="PAX-1"))
    bus.run_until(130.0)
    assert at(bus, "P1", E.HANDOFF_OK) == [102.0]
    assert at(bus, "P1", E.CAR_DEPARTED, 5) == [106.0]
    assert at(bus, "P1", E.CAR_ARRIVED, 1) == [114.0]


def test_EC_08b_D_52_loading_never_confirmed_holds_the_car_at_the_pickup():
    bus, _ = freight()
    bus.inject(PassengerExit(t_sim=46.0, car_id="P1", passenger_id="PAX-1"))
    bus.run_until(300.0)
    final = last_tel(bus, "P1")
    assert (final.position, final.door, final.mode) == (
        1.0,
        DoorState.OPEN,
        CabinMode.MISSION_EXCLUSIVE,
    )
    assert not at(bus, "P1", E.CAR_DEPARTED, 1)


def test_EC_08c_D_52_loading_confirmed_too_early_is_ignored():
    bus, _ = freight()
    inject_all(
        bus,
        [
            confirm(10.0, MissionAction.LOADED),  # staff press before the car is there
            PassengerExit(t_sim=46.0, car_id="P1", passenger_id="PAX-1"),
            confirm(60.0, MissionAction.LOADED),
        ],
    )
    bus.run_until(70.0)
    assert at(bus, "P1", E.MISSION_PICKUP_DONE) == [60.0]
    assert at(bus, "P1", E.CAR_DEPARTED, 1) == [64.0]


def test_EC_08d_FRT_03_measured_freight_over_the_rated_load_blocks_the_leg():
    bus, _ = freight()
    inject_all(
        bus,
        [
            PassengerExit(t_sim=46.0, car_id="P1", passenger_id="PAX-1"),
            confirm(60.0, MissionAction.LOADED, weight_kg=1100.0),  # scale says too heavy
            confirm(80.0, MissionAction.LOADED, weight_kg=800.0),  # staff unload some boxes
        ],
    )
    bus.run_until(79.0)
    waiting = tel_between(bus, "P1", 60.0, 79.0)
    assert waiting and all(t.mode == CabinMode.OVERLOAD_HOLD for t in waiting)
    assert all(t.door == DoorState.OPEN and not t.moving for t in waiting)
    assert MissionStatus.IN_PROGRESS not in [s for _, s in statuses(bus)]
    assert [a.kind for a in of_type(bus.delivered, Alert)] == [AlertKind.OVERLOAD]
    bus.run_until(150.0)
    assert at(bus, "P1", E.MISSION_PICKUP_DONE) == [80.0]
    assert at(bus, "P1", E.CAR_DEPARTED, 1) == [81.0]
    assert at(bus, "P1", E.CAR_ARRIVED, 30) == [139.0]


def test_EC_08e_FRT_04_cancelled_while_draining_keeps_the_passenger_trip():
    bus, _ = freight()
    bus.inject(confirm(10.0, MissionAction.LOAD_FAILED))
    bus.run_until(80.0)
    assert statuses(bus) == [(0.0, MissionStatus.DRAINING), (10.0, MissionStatus.FAILED)]
    assert at(bus, "P1", E.DEST_SERVED, 5) == [44.0]
    assert not at(bus, "P1", E.CAR_ARRIVED, 1)
    assert last_tel(bus, "P1").mode == CabinMode.AUTO


def test_EC_08f_D_52_confirmation_for_another_mission_is_ignored():
    bus, _ = freight()
    inject_all(
        bus,
        [
            PassengerExit(t_sim=46.0, car_id="P1", passenger_id="PAX-1"),
            confirm(60.0, MissionAction.LOADED, mid="MS-9"),
            confirm(70.0, MissionAction.LOADED),
        ],
    )
    bus.run_until(80.0)
    assert at(bus, "P1", E.MISSION_PICKUP_DONE) == [70.0]


def test_EC_08g_FRT_02_dispatch_putting_the_pickup_before_a_passenger_stop_is_refused():
    rider = Passenger(passenger_id="PAX-1", destination=5)
    bus, _ = bus_with(CabinInit(car_id="P1", floor=27, passengers=(rider,)))
    route = [pickup(1), dropoff(30), dest(5, "PAX-1")]
    propose(bus, 0.0, route, 0, mission_id="MS-1", exclusive=True)
    bus.run_until(60.0)
    assert reply(bus, "RC-1").reason == NackReason.ONBOARD_COMMITMENT
    assert at(bus, "P1", E.DEST_SERVED, 5) == [44.0]  # the passenger is still delivered


# ======================================================================================
# EC-09 Unreliable messages from Dispatch
# ======================================================================================


def test_EC_09a_SIM_06_same_proposal_delivered_three_times():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    first = propose(bus, 0.0, [hall(20, UP, "HC-1")], 0, car="P2")
    for t in (0.3, 0.6):
        bus.run_until(t)
        bus.inject(type(first)(**{**first.__dict__, "msg_id": "", "t_sim": t, "deliver_at": None}))
    bus.run_until(1.0)
    replies = of_type(bus.delivered, RouteReply, command_id="RC-1")
    assert [(r.accepted, r.committed_version) for r in replies] == [(True, 1)] * 3
    assert version(bus, "P2") == 1  # executed once


def test_EC_09b_CB_01_stale_proposal_arriving_after_a_newer_one():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(20, UP, "HC-1")], 0, "RC-1", "P2")
    propose(bus, 0.5, [hall(25, UP, "HC-2")], 0, "RC-2", "P2")  # built from old telemetry
    bus.run_until(1.0)
    assert reply(bus, "RC-2").reason == NackReason.VERSION_MISMATCH
    assert [s.floor for s in stops_now(bus, "P2")] == [20]


def test_EC_09c_D_17_query_after_a_refusal():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(20, UP, "HC-1")], 7, "RC-1", "P2")
    bus.inject(RouteVersionQuery(t_sim=0.0, command_id="RC-1", car_id="P2"))
    bus.run_until(0.0)
    (report,) = of_type(bus.delivered, RouteVersionReport)
    assert (report.seen, report.accepted, report.route_version) == (True, False, 0)


def test_EC_09d_D_55_dispatch_withdraws_the_only_call_while_moving():
    bus, _ = bus_with(CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(20, UP, "HC-1")], 0, "RC-1", "P2")
    bus.run_until(4.0)  # at 14.0, heading for 20
    propose(bus, 4.0, [], version(bus, "P2"), "RC-2", "P2")
    bus.run_until(30.0)
    assert reply(bus, "RC-2").accepted
    assert at(bus, "P2", E.CAR_ARRIVED) == [6.0]  # stops at the next floor it can: 15
    assert last_tel(bus, "P2").position == 15.0
    assert not at(bus, "P2", E.DOOR_OPENING)  # nothing to serve, so the doors stay shut
    assert not at(bus, "P2", E.HALL_CALL_SERVED)


# ======================================================================================
# EC-10 The ends of the building
# ======================================================================================


@pytest.mark.parametrize(("start", "target", "direction"), [(40, 1, UP), (1, 40, DOWN)])
def test_EC_10a_MOVE_03_full_height_run(start, target, direction):
    bus, _ = bus_with(CabinInit(car_id="P1", floor=start))
    propose(bus, 0.0, [hall(target, direction, "HC-1")], 0)
    bus.run_until(100.0)
    assert at(bus, "P1", E.CAR_ARRIVED) == [78.0]  # 39 floors x 2 s
    assert all(1.0 <= t.position <= 40.0 for t in telemetry(bus, "P1"))


@pytest.mark.parametrize(
    ("t", "floor", "accepted"),
    [(4.0, 27, True), (5.6, 27, False), (5.6, 26, True)],
    ids=["exactly-1-floor", "0.2-floor", "1.2-floor"],
)
def test_EC_10b_MOVE_06_insertion_cutoff_while_moving_down(t, floor, accepted):
    bus, _ = bus_with(CabinInit(car_id="P1", floor=30))
    propose(bus, 0.0, [hall(10, DOWN, "HC-1")], 0)
    bus.run_until(t)
    propose(bus, t, [hall(floor, DOWN, "HC-2"), hall(10, DOWN, "HC-1")], version(bus, "P1"), "RC-2")
    bus.run_until(t)
    assert reply(bus, "RC-2").accepted is accepted
    if not accepted:
        assert reply(bus, "RC-2").reason == NackReason.INSERT_CUTOFF
