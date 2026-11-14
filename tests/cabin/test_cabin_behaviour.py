"""Cabin behaviour on the bus that the contract tests do not cover (Hai)."""

import cabin
from contracts.config import ACK_TIMEOUT_S
from contracts.enums import (
    AlertKind,
    CabinMode,
    Direction,
    DoorState,
    FaultKind,
    MissionAction,
    MissionStatus,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.messages import (
    Alert,
    CabinDestinationPressed,
    CabinInit,
    DoorRequest,
    FaultCleared,
    FaultInjected,
    MaintenanceSet,
    MissionLoadConfirmed,
    MissionStatusUpdate,
    Passenger,
    PassengerBoard,
    PassengerExit,
    RouteProposal,
    Stop,
)
from tests.contract.helpers import events, hall, last_tel, make_bus, of_type, telemetry

E = ServiceEventType
DOWN, UP = Direction.DOWN, Direction.UP


def bus_with(init: CabinInit):
    return make_bus(cabin.create(init))


def propose(bus, t, stops, version, cmd="RC-1", car="P1", **extra):
    bus.inject(
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


def test_D_55_fault_hold_releases_hall_pickups_and_keeps_destinations():
    rider = Passenger(passenger_id="PAX-1", destination=5)
    bus = bus_with(CabinInit(car_id="P1", floor=20, passengers=(rider,)))
    propose(
        bus, 0.0, [hall(15, DOWN, "HC-1"), Stop(floor=5, kind=StopKind.CAR_DEST, ref_id="PAX-1")], 0
    )
    bus.run_until(3.0)
    v_before = last_tel(bus, "P1").route_version
    bus.inject(FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS))
    bus.run_until(4.0)
    after = last_tel(bus, "P1")
    assert [s.kind for s in after.stops] == [StopKind.CAR_DEST]
    assert after.route_version == v_before + 1


def test_D_55_maintenance_releases_hall_pickups():
    bus = bus_with(CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(20, UP, "HC-1")], 0, car="P2")
    bus.inject(MaintenanceSet(t_sim=1.0, car_id="P2", on=True, role=Role.MAINTAINER))
    bus.run_until(2.0)
    assert last_tel(bus, "P2").stops == ()


def test_DOOR_01_obstructed_state_is_reported():
    bus = bus_with(CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(12, UP, "HC-1")], 0, car="P2")  # door cycle 0..6 at floor 12
    bus.run_until(5.5)
    bus.inject(FaultInjected(t_sim=5.5, car_id="P2", kind=FaultKind.OBSTRUCTION))
    bus.run_until(6.0)
    at_obstruction = [t for t in telemetry(bus, "P2") if t.t_sim == 5.5]
    assert any(t.door == DoorState.OBSTRUCTED and t.obstruction for t in at_obstruction)
    assert at_obstruction[-1].door == DoorState.OPENING


def test_DOOR_05_door_button_ignored_in_fault_hold_between_floors():
    bus = bus_with(CabinInit(car_id="P1", floor=20))
    propose(bus, 0.0, [hall(30, UP, "HC-1")], 0)
    bus.run_until(3.0)  # position 21.5
    bus.inject(FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS))
    bus.inject(DoorRequest(t_sim=4.0, car_id="P1", open=True))
    bus.run_until(10.0)
    assert not events(bus, "P1", E.DOOR_OPENING)
    assert last_tel(bus, "P1").door == DoorState.CLOSED


def test_Q3_sensor_fault_stops_the_car_and_clearing_resumes_it():
    bus = bus_with(CabinInit(car_id="P1", floor=20))
    propose(bus, 0.0, [hall(30, UP, "HC-1")], 0)
    bus.run_until(3.0)
    bus.inject(FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.POSITION_SENSOR))
    bus.run_until(6.0)
    held = last_tel(bus, "P1")
    assert not held.moving and held.position == 21.5 and not held.sensors_valid
    assert of_type(bus.delivered, Alert, kind=AlertKind.SENSOR)
    bus.inject(FaultCleared(t_sim=6.0, car_id="P1", kind=FaultKind.POSITION_SENSOR))
    bus.run_until(30.0)
    # 8.5 floors left at 2 s/floor from t=6
    assert [e.t_sim for e in events(bus, "P1", E.CAR_ARRIVED)] == [23.0]


def test_EMG_04_recovery_refused_while_a_fault_is_active():
    bus = bus_with(CabinInit(car_id="P2", floor=12))
    bus.inject(FaultInjected(t_sim=1.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    bus.inject(FaultInjected(t_sim=1.0, car_id="P2", kind=FaultKind.SENSOR_CONFLICT))
    bus.inject(FaultCleared(t_sim=2.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    from contracts.messages import RecoveryApproved

    bus.inject(RecoveryApproved(t_sim=3.0, car_id="P2", approved_by=Role.SUPERVISOR))
    bus.run_until(4.0)
    assert last_tel(bus, "P2").mode == CabinMode.FAULT_HOLD


def test_MED_05_medical_leg_from_an_empty_idle_car():
    """An empty car at the pickup floor: handoff at once, then a direct exclusive leg."""
    bus = bus_with(CabinInit(car_id="P2", floor=2))
    stops = [
        Stop(floor=2, kind=StopKind.MISSION_PICKUP, ref_id="MS-1"),
        Stop(floor=30, kind=StopKind.MISSION_DROPOFF, ref_id="MS-1"),
    ]
    propose(bus, 0.0, stops, 0, car="P2", mission_id="MS-1", exclusive=True)
    bus.inject(
        MissionLoadConfirmed(t_sim=2.0, mission_id="MS-1", car_id="P2", action=MissionAction.LOADED)
    )
    bus.inject(
        MissionLoadConfirmed(
            t_sim=64.0, mission_id="MS-1", car_id="P2", action=MissionAction.UNLOADED
        )
    )
    bus.run_until(70.0)
    assert [e.t_sim for e in events(bus, "P2", E.HANDOFF_OK)] == [0.0]
    assert [e.t_sim for e in events(bus, "P2", E.CAR_DEPARTED)] == [6.0]
    assert [e.t_sim for e in events(bus, "P2", E.CAR_ARRIVED)] == [62.0]  # 28 floors
    statuses = [u.status for u in of_type(bus.delivered, MissionStatusUpdate)]
    assert statuses == [MissionStatus.IN_PROGRESS, MissionStatus.COMPLETED]
    assert last_tel(bus, "P2").mode == CabinMode.AUTO


def test_SEN_04_manual_exit_at_a_landing_cancels_the_trip():
    bus = bus_with(CabinInit(car_id="P2", floor=12))
    propose(bus, 0.0, [hall(12, UP, "HC-1")], 0, car="P2")  # doors open 1..5 at floor 12
    bus.run_until(1.0)
    bus.inject(PassengerBoard(t_sim=1.0, car_id="P2", passenger_id="PAX-1"))
    bus.inject(CabinDestinationPressed(t_sim=1.0, car_id="P2", floor=30, passenger_id="PAX-1"))
    bus.inject(PassengerExit(t_sim=2.0, car_id="P2", passenger_id="PAX-1"))
    bus.run_until(20.0)
    assert last_tel(bus, "P2").stops == ()
    assert last_tel(bus, "P2").passengers == 0
    assert not events(bus, "P2", E.CAR_DEPARTED)


def test_5_5_nobody_is_removed_from_a_moving_cabin():
    rider = Passenger(passenger_id="PAX-1", destination=30)
    bus = bus_with(CabinInit(car_id="P1", floor=12, passengers=(rider,)))
    bus.run_until(1.0)  # moving up since t=0
    bus.inject(PassengerExit(t_sim=1.0, car_id="P1", passenger_id="PAX-1"))
    bus.run_until(2.0)
    assert last_tel(bus, "P1").passengers == 1
    assert not events(bus, "P1", E.PASSENGER_EXITED)
