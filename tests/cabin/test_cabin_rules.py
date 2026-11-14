"""Unit tests for the pure cabin rules (Hai): doors, motion, proposal checks, missions.

Names follow the SRS / DECISIONS id they check (CONTRIBUTING.md, Section 8).
"""

import pytest

from cabin.doors import DURATION, DoorInput, next_state
from cabin.mission import MissionPhase, blocks_departure, load_conflicts
from cabin.motion import check_first_stop, insert_in_sweep, position_at
from cabin.proposal import CommandCache, RouteState, StoredReply, validate
from contracts.config import ACK_TIMEOUT_S, DWELL_S, FULL_LOAD_KG
from contracts.enums import (
    CabinMode,
    Direction,
    DoorState,
    FaultKind,
    NackReason,
    StopKind,
)
from contracts.messages import RouteProposal, Stop

D, In = DoorState, DoorInput
UP, DOWN = Direction.UP, Direction.DOWN


def hall(floor: int, ref: str = "HC-1", direction: Direction = UP) -> Stop:
    return Stop(floor=floor, kind=StopKind.HALL_PICKUP, ref_id=ref, direction=direction)


def dest(floor: int, pid: str = "PAX-1") -> Stop:
    return Stop(floor=floor, kind=StopKind.CAR_DEST, ref_id=pid)


# --- doors ---------------------------------------------------------------------------


def test_D_03_normal_cycle_lasts_one_dwell():
    state, total = D.CLOSED, 0.0
    state = next_state(state, In.OPEN_CMD)
    while state != D.CLOSED:
        total += DURATION[state]
        state = next_state(state, In.TIMER)
    assert total == DWELL_S


def test_DOOR_04_obstruction_only_while_closing():
    assert next_state(D.CLOSING, In.OBSTRUCTION) == D.OBSTRUCTED
    assert next_state(D.OBSTRUCTED, In.TIMER) == D.OPENING
    for state in (D.CLOSED, D.OPENING, D.OPEN):
        assert next_state(state, In.OBSTRUCTION) is None


def test_DOOR_01_obstructed_is_a_reachable_state():
    assert D.OBSTRUCTED in {next_state(D.CLOSING, i) for i in In}
    assert DURATION[D.OBSTRUCTED] == 0.0


def test_D_51_door_buttons():
    assert next_state(D.OPEN, In.OPEN_BUTTON) == D.OPEN  # restart the OPEN period
    assert next_state(D.OPEN, In.CLOSE_BUTTON) == D.CLOSING
    assert next_state(D.CLOSING, In.OPEN_BUTTON) == D.OBSTRUCTED
    assert next_state(D.CLOSED, In.CLOSE_BUTTON) is None


# --- motion --------------------------------------------------------------------------


def test_MOVE_03_continuous_position():
    assert position_at(31.0, 0.0, DOWN, 1.0) == pytest.approx(30.5)
    assert position_at(20.0, 0.0, UP, 13.6) == pytest.approx(26.8)
    assert position_at(12.0, 5.0, Direction.IDLE, 9.0) == 12.0


@pytest.mark.parametrize(
    ("pos", "floor", "is_new", "reason"),
    [
        (26.8, 27, True, NackReason.INSERT_CUTOFF),  # 0.2 floors ahead (SRS 7.3 example)
        (26.0, 27, True, None),  # exactly one floor: allowed
        (26.8, 27, False, None),  # the stop was already committed
        (22.0, 21, True, NackReason.REVERSAL_REQUIRED),
        (22.0, 21, False, NackReason.REVERSAL_REQUIRED),
    ],
)
def test_MOVE_05_MOVE_06_first_stop_checks(pos, floor, is_new, reason):
    assert check_first_stop(pos, UP, hall(floor), is_new) == reason


def test_MOVE_06_cutoff_mirrors_for_down():
    assert check_first_stop(27.2, DOWN, hall(27), True) == NackReason.INSERT_CUTOFF
    assert check_first_stop(28.0, DOWN, hall(27), True) is None


def test_CB_03_destination_inserted_in_sweep_order():
    route = [dest(20, "A"), dest(10, "B")]
    assert [s.floor for s in insert_in_sweep(route, 30.0, dest(25, "C"))] == [25, 20, 10]
    assert [s.floor for s in insert_in_sweep(route, 30.0, dest(15, "C"))] == [20, 15, 10]
    assert [s.floor for s in insert_in_sweep(route, 30.0, dest(35, "C"))] == [20, 10, 35]


def test_Q9_same_floor_stops_stay_together():
    route = [hall(20, "HC-1"), dest(10, "B")]
    out = insert_in_sweep(route, 30.0, dest(20, "C"))
    assert [(s.floor, s.ref_id) for s in out] == [(20, "HC-1"), (20, "C"), (10, "B")]


# --- route proposals -----------------------------------------------------------------


def state(**over) -> RouteState:
    fields = dict(
        version=3,
        route=(),
        pending_dests=frozenset(),
        hold=None,
        fire=False,
        faults=frozenset(),
        mission_id=None,
        load_kg=0.0,
        moving=False,
        position=12.0,
        direction=Direction.IDLE,
    )
    fields.update(over)
    return RouteState(**fields)


def proposal(stops=None, version=3, t=0.0, cmd="RC-1", **extra) -> RouteProposal:
    return RouteProposal(
        t_sim=t,
        command_id=cmd,
        car_id="P1",
        expected_route_version=version,
        ordered_stops=tuple(stops if stops is not None else [hall(20)]),
        expiry_ts=t + ACK_TIMEOUT_S,
        **extra,
    )


def test_CB_01_valid_proposal_is_accepted():
    assert validate(proposal(), state(), 0.0) is None


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"hold": CabinMode.FAULT_HOLD}, NackReason.FAULT_HOLD),
        ({"hold": CabinMode.MAINTENANCE}, NackReason.MAINTENANCE),
        ({"fire": True}, NackReason.FIRE),
        ({"faults": frozenset({FaultKind.POSITION_SENSOR})}, NackReason.POSITION_INVALID),
        ({"faults": frozenset({FaultKind.DOOR_LOCK_LOSS})}, NackReason.DOORS_NOT_LOCKED),
        ({"faults": frozenset({FaultKind.SENSOR_CONFLICT})}, NackReason.SENSORS_INVALID),
        ({"mission_id": "MS-1"}, NackReason.MISSION_EXCLUSIVE),
        ({"load_kg": FULL_LOAD_KG}, NackReason.FULL_LOAD),
    ],
)
def test_CB_01_every_nack_has_a_reason(over, reason):
    assert validate(proposal(), state(**over), 0.0) == reason


def test_D_10_expiry_is_inclusive():
    assert validate(proposal(), state(), ACK_TIMEOUT_S) is None
    assert validate(proposal(), state(), ACK_TIMEOUT_S + 0.1) == NackReason.EXPIRED


def test_CB_01_version_checked_before_mode():
    nack = validate(proposal(version=2), state(hold=CabinMode.FAULT_HOLD), 0.0)
    assert nack == NackReason.VERSION_MISMATCH


def test_SEN_03_full_load_still_accepts_existing_pickups():
    committed = state(load_kg=950.0, route=(hall(20),))
    assert validate(proposal([hall(20)]), committed, 0.0) is None
    assert validate(proposal([hall(20), hall(22, "HC-2")]), committed, 0.0) == (
        NackReason.FULL_LOAD
    )


def test_DC_02_dispatch_cannot_invent_a_destination():
    nack = validate(proposal([dest(5, "STRANGER")]), state(), 0.0)
    assert nack == NackReason.INVALID_ROUTE


def test_CB_03_dropping_a_hall_pickup_is_allowed_but_not_a_destination():
    onboard = state(route=(hall(20), dest(5)), pending_dests=frozenset({("PAX-1", 5)}))
    assert validate(proposal([dest(5)]), onboard, 0.0) is None
    assert validate(proposal([hall(20)]), onboard, 0.0) == NackReason.ONBOARD_COMMITMENT


@pytest.mark.parametrize(
    "stops",
    [
        [hall(41)],
        [hall(0)],
    ],
)
def test_SYS_03_floor_out_of_range(stops):
    assert validate(proposal(stops), state(), 0.0) == NackReason.INVALID_FLOOR


def test_D_16_duplicate_or_incomplete_stops_are_invalid():
    assert validate(proposal([hall(20), hall(20)]), state(), 0.0) == NackReason.INVALID_ROUTE
    no_dir = Stop(floor=20, kind=StopKind.HALL_PICKUP, ref_id="HC-1")
    assert validate(proposal([no_dir]), state(), 0.0) == NackReason.INVALID_ROUTE
    stray = Stop(floor=1, kind=StopKind.MISSION_PICKUP, ref_id="MS-9")
    assert validate(proposal([stray]), state(), 0.0) == NackReason.INVALID_ROUTE


def test_SIM_06_D_14_command_cache():
    cache = CommandCache()
    first = proposal()
    reply = StoredReply(True, 4, None)
    assert cache.lookup(first) is None
    cache.store(first, reply)
    retry = proposal(t=0.5)  # same payload, other envelope... but expiry moved
    same = RouteProposal(**{**first.__dict__, "t_sim": 0.5, "msg_id": "MSG-9"})
    assert cache.lookup(same) == reply
    assert cache.lookup(retry) == NackReason.PAYLOAD_CONFLICT  # expiry_ts is payload (D-17)
    assert cache.lookup(proposal([hall(21)])) == NackReason.PAYLOAD_CONFLICT


# --- missions ------------------------------------------------------------------------


def test_FRT_02_draining_car_may_not_leave_for_a_mission_stop():
    pickup = Stop(floor=1, kind=StopKind.MISSION_PICKUP, ref_id="MS-1")
    assert blocks_departure(MissionPhase.DRAINING, pickup)
    assert not blocks_departure(MissionPhase.DRAINING, dest(5))
    assert not blocks_departure(MissionPhase.TO_PICKUP, pickup)
    assert blocks_departure(MissionPhase.AT_PICKUP, pickup)


@pytest.mark.parametrize(
    ("count", "load", "sensor_ok", "conflict"),
    [
        (1, 0.0, True, True),  # SEN-06: low load does not prove an empty cabin
        (1, 75.0, True, False),
        (0, 0.0, True, False),
        (0, 40.0, True, True),
        (0, 0.0, False, True),
    ],
)
def test_SEN_06_count_and_load_must_agree(count, load, sensor_ok, conflict):
    assert load_conflicts(count, load, sensor_ok) is conflict
