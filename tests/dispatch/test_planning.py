"""Dispatch safety boundaries derived from SRS 4.7 and MOVE-04..06."""

from dataclasses import replace

import pytest

from contracts.config import DEFAULT_POLICY, DELAY_CAP_S, TELEMETRY_STALE_S
from contracts.enums import (
    CabinMode,
    Direction,
    DispatchAlgorithm,
    DoorState,
    ExclusionReason,
)
from dispatch.planning import hall_route, ordinary_exclusion, route_etas
from tests.dispatch.helpers import destination, pickup, telemetry


def test_DC_04_eta_charges_one_dwell_for_coincident_physical_stops():
    shared_pickup = pickup(35)
    first_drop = destination(35, "A")
    second_drop = destination(30, "B")
    stops = (shared_pickup, first_drop, second_drop)

    etas = route_etas(telemetry(), stops, 0.0)

    assert etas[shared_pickup] == etas[first_drop] == 10.0
    assert etas[second_drop] == 26.0


@pytest.mark.parametrize(("elapsed", "allowed"), [(4.0, True), (4.001, False)])
def test_DC_04_fixed_reference_delay_cap_is_inclusive(elapsed, allowed):
    committed = destination(30, "A")
    tel = telemetry(t_sim=elapsed, stops=(committed,), passengers=1, load_kg=75.0)
    # The reference was accepted at t=0: 40 -> 30 in 20 s. A hold has since
    # consumed four seconds; an insertion adds six more, exactly DELAY_CAP.
    route = hall_route(
        tel,
        pickup(35),
        elapsed,
        {committed: 20.0},
        DEFAULT_POLICY,
        {"HC-new": elapsed},
        DispatchAlgorithm.COMMITMENT,
    )

    assert (route is not None) is allowed
    if route is not None:
        assert route_etas(tel, route, elapsed)[committed] - 20.0 == DELAY_CAP_S


def test_DC_02_residual_dwell_is_an_absolute_departure_deadline():
    committed = destination(30, "A")
    tel = telemetry(35.0, door=DoorState.OPEN, locked=False, stops=(committed,))

    first = route_etas(tel, tel.stops, 1.0, ready_at=6.0)
    heartbeat = route_etas(replace(tel, t_sim=2.0), tel.stops, 2.0, ready_at=6.0)

    assert first[committed] == heartbeat[committed] == 16.0


def test_DC_02_telemetry_at_freshness_boundary_is_eligible():
    tel = telemetry()

    assert ordinary_exclusion(tel, TELEMETRY_STALE_S) is None
    assert ordinary_exclusion(tel, TELEMETRY_STALE_S + 0.001) == ExclusionReason.STALE_TELEMETRY


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"sensors_valid": False}, ExclusionReason.SENSORS_INVALID),
        ({"mode": CabinMode.OVERLOAD_HOLD}, ExclusionReason.OVERLOAD),
        ({"load_kg": 900.0}, ExclusionReason.FULL_LOAD),
        ({"mission_id": "MS-active"}, ExclusionReason.MISSION_BUSY),
        ({"mode": CabinMode.MISSION_DRAINING}, ExclusionReason.MISSION_BUSY),
        ({"mode": CabinMode.FIRE}, ExclusionReason.FIRE),
    ],
)
def test_DC_02_ordinary_safety_exclusions(changes, reason):
    assert ordinary_exclusion(telemetry(**changes), 0.0) == reason


def test_TH_06_first_idle_and_sweep_have_distinct_assignment_rules():
    committed = destination(35, "A")
    tel = telemetry(
        20.0,
        direction=Direction.UP,
        moving=True,
        door_zone=False,
        stops=(committed,),
    )
    new = pickup(25, direction=Direction.UP)
    args = (tel, new, 0.0, {committed: 30.0}, DEFAULT_POLICY, {new.ref_id: 0.0})

    assert hall_route(*args, DispatchAlgorithm.FIRST_IDLE) is None
    assert hall_route(*args, DispatchAlgorithm.SWEEP) == (new, committed)


def test_MOVE_06_moving_car_without_stops_still_obeys_continuous_cutoff():
    tel = telemetry(26.8, direction=Direction.UP, moving=True, door_zone=False)
    new = pickup(27, direction=Direction.UP)

    assert (
        hall_route(
            tel,
            new,
            0.0,
            {},
            DEFAULT_POLICY,
            {new.ref_id: 0.0},
            DispatchAlgorithm.COMMITMENT,
        )
        is None
    )
