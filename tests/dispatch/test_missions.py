"""Fleet selection and drain ETA rules from MED-01..03 and decisions D-20..22."""

from dataclasses import replace

import pytest

from contracts.config import CARS, FREIGHT_MAX_KG, TELEMETRY_STALE_S
from contracts.enums import (
    CabinMode,
    Direction,
    DoorState,
    ExclusionReason,
    MissionType,
    Role,
    StopKind,
)
from contracts.messages import MissionApproval
from dispatch.missions import mission_candidates, mission_route
from tests.dispatch.helpers import destination, pickup, telemetry


def approval(**changes):
    values = dict(
        t_sim=0.0,
        mission_id="MS-0001",
        role=Role.MEDICAL_COORDINATOR,
        mission_type=MissionType.MEDICAL,
        origin=2,
        destination=30,
        allowed_cars=CARS,
    )
    values.update(changes)
    return MissionApproval(**values)


def by_car(candidates):
    return {candidate.car_id: candidate for candidate in candidates}


def test_MED_01_full_car_remains_eligible_after_onboard_drain():
    tel = telemetry(
        28.0,
        mode=CabinMode.FULL,
        load_kg=900.0,
        passengers=12,
        stops=(destination(22, "A"),),
    )

    candidate = by_car(mission_candidates(approval(), {"P1": tel}, 0.0, set()))["P1"]

    assert candidate.excluded is None
    assert candidate.eta_s == 58.0


def test_MED_01_same_floor_drops_share_dwell_and_hall_pickups_are_removed():
    tel = telemetry(
        28.0,
        stops=(pickup(25, "HC-0001"), destination(22, "A"), destination(22, "B")),
    )
    mission = approval()

    candidate = by_car(mission_candidates(mission, {"P1": tel}, 0.0, set()))["P1"]
    route = mission_route(mission, tel)

    assert candidate.eta_s == 58.0
    assert [stop.kind for stop in route] == [
        StopKind.CAR_DEST,
        StopKind.CAR_DEST,
        StopKind.MISSION_PICKUP,
        StopKind.MISSION_DROPOFF,
    ]


def test_MED_01_eta_uses_current_position_of_a_recent_motion_sample():
    tel = telemetry(
        36.0,
        direction=Direction.UP,
        moving=True,
        door_zone=False,
        stops=(destination(40, "A"),),
    )

    candidate = by_car(mission_candidates(approval(), {"P1": tel}, 2.0, set()))["P1"]

    # At t=2 the car has progressed to 37: 6 s to 40, 6 s to unload,
    # then 76 s deadhead to mission origin 2.
    assert candidate.eta_s == 88.0


def test_MED_01_capability_config_and_approval_both_restrict_selection():
    cars = {"P1": telemetry(3.0), "G1": telemetry(3.0, car_id="G1")}
    mission = approval(allowed_cars=("G1",))

    candidates = by_car(mission_candidates(mission, cars, 0.0, set()))

    assert candidates["P1"].excluded == ExclusionReason.NOT_CAPABLE
    assert candidates["G1"].excluded == ExclusionReason.NOT_CAPABLE
    assert tuple(candidates) == CARS


@pytest.mark.parametrize(
    "changes",
    [
        {"sensors_valid": False},
        {"position": float("nan")},
        {"position": float("inf")},
        {"position": 0.0},
        {"load_kg": float("nan")},
        {"load_kg": -1.0},
        {"moving": True, "locked": False},
        {"moving": True, "door": DoorState.OPEN},
    ],
)
def test_MED_01_invalid_sensors_or_measurements_are_explained(changes):
    car = telemetry(**changes)

    candidate = by_car(mission_candidates(approval(), {"P1": car}, 0.0, set()))["P1"]

    assert candidate.eta_s is None
    assert candidate.excluded == ExclusionReason.SENSORS_INVALID


def test_MED_01_freshness_boundary_and_busy_reservation_have_explicit_reasons():
    car = telemetry()

    eligible = by_car(mission_candidates(approval(), {"P1": car}, TELEMETRY_STALE_S, set()))
    stale = by_car(mission_candidates(approval(), {"P1": car}, TELEMETRY_STALE_S + 0.001, set()))
    busy = by_car(mission_candidates(approval(), {"P1": car}, 0.0, {"P1"}))

    assert eligible["P1"].excluded is None
    assert stale["P1"].excluded == ExclusionReason.STALE_TELEMETRY
    assert busy["P1"].excluded == ExclusionReason.MISSION_BUSY
    assert stale["P2"].excluded == ExclusionReason.STALE_TELEMETRY


def test_FRT_01_declared_payload_at_limit_is_valid_above_limit_is_excluded():
    car = telemetry(car_id="G1")
    freight = approval(
        mission_type=MissionType.FREIGHT,
        role=Role.BUILDING_STAFF,
        material="boxes",
        weight_kg=FREIGHT_MAX_KG,
    )

    exact = by_car(mission_candidates(freight, {"G1": car}, 0.0, set()))["G1"]
    above = by_car(
        mission_candidates(
            replace(freight, weight_kg=FREIGHT_MAX_KG + 0.001), {"G1": car}, 0.0, set()
        )
    )["G1"]

    assert exact.excluded is None
    assert above.excluded == ExclusionReason.OVER_PAYLOAD
