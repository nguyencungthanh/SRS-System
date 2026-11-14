"""RouteProposal validation and the per-command reply cache (CB-01, SIM-06, D-14, D-17).

``validate`` is pure: it reads a ``RouteState`` (what the cabin knows right now) and
returns the NACK reason, or None to ACK. Checks run in a fixed order; the first failure
is the reason. A NACK never changes the route (CB-01).
"""

from dataclasses import dataclass
from typing import Any

from cabin.motion import EPS, check_first_stop
from contracts.config import FULL_LOAD_KG, MAX_FLOOR, MIN_FLOOR
from contracts.enums import CabinMode, Direction, FaultKind, NackReason, StopKind
from contracts.messages import RouteProposal, Stop, payload_key

SENSOR_FAULTS = frozenset(
    {FaultKind.POSITION_SENSOR, FaultKind.LOAD_SENSOR, FaultKind.SENSOR_CONFLICT}
)


@dataclass(frozen=True, kw_only=True)
class RouteState:
    version: int
    route: tuple[Stop, ...]
    pending_dests: frozenset[tuple[str, int]]  # (passenger_id, floor) still to serve
    hold: CabinMode | None  # FAULT_HOLD or MAINTENANCE
    fire: bool
    faults: frozenset[FaultKind]
    mission_id: str | None
    load_kg: float
    moving: bool
    position: float
    direction: Direction


def _stop_key(stop: Stop) -> tuple[Any, ...]:
    return (stop.kind, stop.ref_id, stop.floor)


def validate(msg: RouteProposal, state: RouteState, now: float) -> NackReason | None:
    stops = msg.ordered_stops

    # 1. Delivery and version (D-10, CB-01)
    if now > msg.expiry_ts + EPS:
        return NackReason.EXPIRED
    if msg.expected_route_version != state.version:
        return NackReason.VERSION_MISMATCH

    # 2. Operating mode
    if state.hold == CabinMode.FAULT_HOLD:
        return NackReason.FAULT_HOLD
    if state.hold == CabinMode.MAINTENANCE:
        return NackReason.MAINTENANCE
    if state.fire:
        return NackReason.FIRE

    # 3. Sensor evidence (SEN-02): never treat a bad reading as safe
    if FaultKind.POSITION_SENSOR in state.faults:
        return NackReason.POSITION_INVALID
    if FaultKind.DOOR_LOCK_LOSS in state.faults:
        return NackReason.DOORS_NOT_LOCKED
    if state.faults & SENSOR_FAULTS:
        return NackReason.SENSORS_INVALID

    # 4. Exclusive mission reservation (MED-05, FRT-03)
    if state.mission_id is not None and msg.mission_id != state.mission_id:
        return NackReason.MISSION_EXCLUSIVE

    # 5. Shape of the stop list (D-16)
    if any(not MIN_FLOOR <= s.floor <= MAX_FLOOR for s in stops):
        return NackReason.INVALID_FLOOR
    if _malformed(msg, state):
        return NackReason.INVALID_ROUTE

    # 6. Accepted onboard destinations stay committed (CB-03, 5.5)
    proposed_dests = {(s.ref_id, s.floor) for s in stops if s.kind == StopKind.CAR_DEST}
    if not state.pending_dests <= proposed_dests:
        return NackReason.ONBOARD_COMMITMENT

    # 7. Capacity (CB-02, SEN-03): no new ordinary pickups at FULL_LOAD
    current = {_stop_key(s) for s in state.route}
    new_pickups = [
        s for s in stops if s.kind == StopKind.HALL_PICKUP and _stop_key(s) not in current
    ]
    if new_pickups and state.load_kg >= FULL_LOAD_KG:
        return NackReason.FULL_LOAD

    # 8. Motion feasibility while moving (MOVE-05, MOVE-06)
    if state.moving and stops:
        first = stops[0]
        return check_first_stop(
            state.position, state.direction, first, _stop_key(first) not in current
        )
    return None


def _malformed(msg: RouteProposal, state: RouteState) -> bool:
    keys = [_stop_key(s) for s in msg.ordered_stops]
    if len(keys) != len(set(keys)):
        return True
    for s in msg.ordered_stops:
        if s.kind == StopKind.HALL_PICKUP and (s.ref_id is None or s.direction is None):
            return True
        # The dispatcher may never invent a passenger destination (2.5, 4.7.1).
        if s.kind == StopKind.CAR_DEST and (s.ref_id, s.floor) not in state.pending_dests:
            return True
        if s.kind in (StopKind.MISSION_PICKUP, StopKind.MISSION_DROPOFF) and (
            msg.mission_id is None or s.ref_id != msg.mission_id
        ):
            return True
    return False


@dataclass(frozen=True)
class StoredReply:
    accepted: bool
    committed_version: int
    reason: NackReason | None


class CommandCache:
    """One stored reply per command_id, matched by payload (SIM-06, D-14, D-17)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[Any, ...], StoredReply]] = {}

    def lookup(self, msg: RouteProposal) -> StoredReply | NackReason | None:
        """The stored reply for an identical retry, PAYLOAD_CONFLICT for a changed one,
        or None for a command never seen."""
        entry = self._entries.get(msg.command_id)
        if entry is None:
            return None
        key, reply = entry
        return reply if key == payload_key(msg) else NackReason.PAYLOAD_CONFLICT

    def store(self, msg: RouteProposal, reply: StoredReply) -> None:
        self._entries[msg.command_id] = (payload_key(msg), reply)

    def get(self, command_id: str) -> StoredReply | None:
        entry = self._entries.get(command_id)
        return entry[1] if entry else None
