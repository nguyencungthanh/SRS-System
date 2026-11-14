"""Continuous motion and stop ordering (MOVE-01..06, D-02). Pure functions.

Position is never stepped tick by tick. While moving the cabin remembers where and when it
started (``pos0``, ``t0``) and derives p(t) = pos0 +/- (t - t0) / SECONDS_PER_FLOOR.
"""

from contracts.config import INSERT_CUTOFF_FLOORS, SECONDS_PER_FLOOR
from contracts.enums import Direction, NackReason
from contracts.messages import Stop

EPS = 1e-9


def sign(direction: Direction) -> int:
    return {Direction.UP: 1, Direction.DOWN: -1}.get(direction, 0)


def position_at(pos0: float, t0: float, direction: Direction, now: float) -> float:
    """Continuous position of a cabin that left ``pos0`` at ``t0`` (MOVE-03)."""
    return pos0 + sign(direction) * (now - t0) / SECONDS_PER_FLOOR


def travel_time(a: float, b: float) -> float:
    """Eq. (1): 2 s per floor."""
    return abs(b - a) * SECONDS_PER_FLOOR


def direction_to(pos: float, floor: float) -> Direction:
    if floor > pos + EPS:
        return Direction.UP
    if floor < pos - EPS:
        return Direction.DOWN
    return Direction.IDLE


def at_floor(pos: float, floor: float) -> bool:
    return abs(pos - floor) < EPS


def is_landing(pos: float) -> bool:
    return at_floor(pos, round(pos))


def check_first_stop(
    pos: float, direction: Direction, first: Stop, is_new: bool
) -> NackReason | None:
    """Local feasibility of a moving cabin's next stop.

    MOVE-05: a stop behind the cabin would need an instant reversal.
    MOVE-06: a newly inserted stop needs at least INSERT_CUTOFF_FLOORS of travel left
    (26.8 -> 27 is rejected; 26.0 -> 27 is accepted).
    """
    ahead = (first.floor - pos) * sign(direction)
    if ahead < -EPS:
        return NackReason.REVERSAL_REQUIRED
    if is_new and ahead < INSERT_CUTOFF_FLOORS - EPS:
        return NackReason.INSERT_CUTOFF
    return None


def insert_in_sweep(route: list[Stop], pos: float, stop: Stop) -> list[Stop]:
    """Insert ``stop`` where the cabin passes its floor on the current path, else append.

    The path is pos -> route[0] -> route[1] ...; the stop goes into the first leg whose
    floor range contains it, after any stop already on that floor (one door cycle).
    Assumption for open question Q8 until DECISIONS.md covers it.
    """
    prev = pos
    for i, existing in enumerate(route):
        low, high = sorted((prev, float(existing.floor)))
        if low - EPS <= stop.floor <= high + EPS:
            j = i
            while j < len(route) and route[j].floor == stop.floor:
                j += 1
            return route[:j] + [stop] + route[j:]
        prev = existing.floor
    return route + [stop]
