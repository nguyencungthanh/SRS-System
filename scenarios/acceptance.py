"""Acceptance scenarios AT-01..AT-05 (SRS 6.4, 9.2; PROJECT_PLAN Section 7).

Each function takes the seed (unused here: these scenarios are fully scripted) and returns a
Scenario. Times of scripted LOADED / UNLOADED confirmations are chosen so they fall inside
the door dwell for both the real cabin (door OPEN 1 s after arrival) and the fake.
"""

from contracts.config import CARS
from contracts.enums import CabinMode, FaultKind, MissionAction, MissionType, Role
from contracts.messages import (
    CabinInit,
    FaultInjected,
    MissionLoadConfirmed,
    MissionRequest,
    Passenger,
)
from simulation.scenario import DROP, NetworkRule, PassengerIntent, Scenario


def _at01_fleet() -> tuple[CabinInit, ...]:
    return (CabinInit(car_id="P1", floor=31),) + tuple(
        CabinInit(car_id=c, floor=1) for c in CARS if c != "P1"
    )


def at01(seed: int = 0) -> Scenario:
    return Scenario(
        name="AT-01",
        description="P1 idle at 31; 27 DOWN at t=0; destination 5. Expect 8 / 58 / 64 s.",
        fleet=_at01_fleet(),
        passengers=(PassengerIntent(t=0.0, passenger_id="PAX-0001", origin=27, destination=5),),
        duration_s=80.0,
    )


def at02(seed: int = 0) -> Scenario:
    return Scenario(
        name="AT-02",
        description="27 DOWN at 0, 29 DOWN at 2. Expect 29 picked up at 4 s, 27 at 14 s.",
        fleet=_at01_fleet(),
        passengers=(
            PassengerIntent(t=0.0, passenger_id="PAX-0001", origin=27, destination=5),
            PassengerIntent(t=2.0, passenger_id="PAX-0002", origin=29, destination=3),
        ),
        duration_s=100.0,
    )


def at03(seed: int = 0) -> Scenario:
    return Scenario(
        name="AT-03",
        description="MEDICAL at floor 2. Expect P1=90, P2=20, P4=58; P2 chosen; P3/G1 excluded.",
        fleet=(
            CabinInit(
                car_id="P1", floor=36, passengers=(Passenger(passenger_id="A", destination=40),)
            ),
            CabinInit(car_id="P2", floor=12),
            CabinInit(car_id="P3", floor=5, mode=CabinMode.MAINTENANCE),
            CabinInit(
                car_id="P4", floor=28, passengers=(Passenger(passenger_id="B", destination=22),)
            ),
            CabinInit(car_id="G1", floor=3),
        ),
        inputs=(
            MissionRequest(
                t_sim=0.0,
                role=Role.MEDICAL_COORDINATOR,
                mission_type=MissionType.MEDICAL,
                origin=2,
                destination=30,
            ),
            MissionLoadConfirmed(
                t_sim=22.0, mission_id="MS-0001", car_id="P2", action=MissionAction.LOADED
            ),
            MissionLoadConfirmed(
                t_sim=84.0, mission_id="MS-0001", car_id="P2", action=MissionAction.UNLOADED
            ),
        ),
        duration_s=100.0,
    )


def at04(seed: int = 0) -> Scenario:
    return Scenario(
        name="AT-04",
        description="P1 carries 27->5, then freight 1->30. Expect 44 / 50 / 58 / 64 / 122 s.",
        fleet=(
            CabinInit(
                car_id="P1",
                floor=27,
                passengers=(Passenger(passenger_id="PAX-0001", destination=5),),
            ),
            CabinInit(car_id="G1", floor=3, mode=CabinMode.MAINTENANCE),
        ),
        inputs=(
            MissionRequest(
                t_sim=0.0,
                role=Role.BUILDING_STAFF,
                mission_type=MissionType.FREIGHT,
                origin=1,
                destination=30,
                material="boxes",
                weight_kg=400.0,
            ),
            MissionLoadConfirmed(
                t_sim=60.0,
                mission_id="MS-0001",
                car_id="P1",
                action=MissionAction.LOADED,
                weight_kg=400.0,
            ),
            MissionLoadConfirmed(
                t_sim=124.0, mission_id="MS-0001", car_id="P1", action=MissionAction.UNLOADED
            ),
        ),
        duration_s=130.0,
    )


def _at05_fleet() -> tuple[CabinInit, ...]:
    return (CabinInit(car_id="P1", floor=31), CabinInit(car_id="P2", floor=20))


def at05_ack_lost(seed: int = 0) -> Scenario:
    return Scenario(
        name="AT-05-ack-lost",
        description="P1's first ACK is lost. Expect reconciliation and never two owners.",
        fleet=_at05_fleet(),
        passengers=(PassengerIntent(t=0.0, passenger_id="PAX-0001", origin=27, destination=5),),
        network=(NetworkRule(msg_type="RouteReply", car_id="P1", nth=1, action=DROP),),
        duration_s=80.0,
    )


def at05_unsafe(seed: int = 0) -> Scenario:
    return Scenario(
        name="AT-05-unsafe",
        description="P1 has conflicting sensors. Expect a NACK from P1 and service by P2.",
        fleet=_at05_fleet(),
        passengers=(PassengerIntent(t=0.0, passenger_id="PAX-0001", origin=27, destination=5),),
        inputs=(FaultInjected(t_sim=0.0, car_id="P1", kind=FaultKind.SENSOR_CONFLICT),),
        duration_s=80.0,
    )
