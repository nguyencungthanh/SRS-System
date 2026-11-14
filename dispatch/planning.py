"""Pure route feasibility and ETA calculations for Thanh's dispatcher.

The stored commitment references are absolute simulation times.  In particular, none
of these functions replace an old reference with the ETA after the latest insertion.
"""

from math import isfinite

from contracts.config import (
    DELAY_CAP_S,
    DOOR_CLOSING_S,
    DOOR_OPEN_S,
    DWELL_S,
    FULL_LOAD_KG,
    INSERT_CUTOFF_FLOORS,
    MAX_FLOOR,
    MAX_LOAD_KG,
    MIN_FLOOR,
    PASSENGER_CARS,
    SECONDS_PER_FLOOR,
    TELEMETRY_STALE_S,
    Policy,
)
from contracts.enums import (
    CabinMode,
    Direction,
    DispatchAlgorithm,
    DoorState,
    ExclusionReason,
    StopKind,
)
from contracts.messages import CabinTelemetry, Stop


def ordinary_exclusion(telemetry: CabinTelemetry | None, now: float) -> ExclusionReason | None:
    """Return the reason a car cannot receive a new ordinary hall pickup (DC-02)."""
    if telemetry is None:
        return ExclusionReason.STALE_TELEMETRY
    if telemetry.car_id not in PASSENGER_CARS:
        return ExclusionReason.NOT_CAPABLE
    if now - telemetry.t_sim > TELEMETRY_STALE_S:
        return ExclusionReason.STALE_TELEMETRY
    mode_reasons = {
        CabinMode.MAINTENANCE: ExclusionReason.MAINTENANCE,
        CabinMode.FAULT_HOLD: ExclusionReason.FAULT_HOLD,
        CabinMode.FIRE: ExclusionReason.FIRE,
        CabinMode.OVERLOAD_HOLD: ExclusionReason.OVERLOAD,
        CabinMode.MISSION_DRAINING: ExclusionReason.MISSION_BUSY,
        CabinMode.MISSION_EXCLUSIVE: ExclusionReason.MISSION_BUSY,
    }
    if telemetry.mode in mode_reasons:
        return mode_reasons[telemetry.mode]
    if (
        not telemetry.sensors_valid
        or not isfinite(telemetry.position)
        or not MIN_FLOOR <= telemetry.position <= MAX_FLOOR
        or not isfinite(telemetry.load_kg)
        or telemetry.load_kg < 0
        or (telemetry.moving and (telemetry.door != DoorState.CLOSED or not telemetry.locked))
    ):
        return ExclusionReason.SENSORS_INVALID
    if telemetry.load_kg > MAX_LOAD_KG:
        return ExclusionReason.OVERLOAD
    if telemetry.mission_id is not None:
        return ExclusionReason.MISSION_BUSY
    if telemetry.mode == CabinMode.FULL or telemetry.load_kg >= FULL_LOAD_KG:
        return ExclusionReason.FULL_LOAD
    return None


def car_fingerprint(telemetry: CabinTelemetry) -> tuple[object, ...]:
    """Identify meaningful state changes, excluding heartbeat/envelope timestamps."""
    return (
        telemetry.car_id,
        telemetry.position,
        telemetry.direction,
        telemetry.moving,
        telemetry.door,
        telemetry.locked,
        telemetry.door_zone,
        telemetry.obstruction,
        telemetry.load_kg,
        telemetry.passengers,
        telemetry.mode,
        telemetry.route_version,
        telemetry.stops,
        telemetry.faults,
        telemetry.sensors_valid,
        telemetry.mission_id,
    )


def position_at(telemetry: CabinTelemetry, now: float) -> float:
    """Project a recent motion sample to ``now``, bounded by its next known stop."""
    if not telemetry.moving or telemetry.direction == Direction.IDLE:
        return telemetry.position
    sign = 1 if telemetry.direction == Direction.UP else -1
    projected = telemetry.position + sign * max(0.0, now - telemetry.t_sim) / SECONDS_PER_FLOOR
    if telemetry.stops:
        target = telemetry.stops[0].floor
        if sign * (target - telemetry.position) >= 0:
            return min(projected, target) if sign > 0 else max(projected, target)
    return min(MAX_FLOOR, max(MIN_FLOOR, projected))


def departure_time(
    telemetry: CabinTelemetry, now: float, *, ready_at: float | None = None
) -> float:
    """Earliest departure after the observed door cycle, including residual dwell.

    The controller supplies the tracked cycle end when it has seen its transitions.
    Without that history, use the remaining duration of the observed door phase.
    """
    if telemetry.moving or telemetry.door == DoorState.CLOSED:
        return now
    if ready_at is not None:
        return max(now, ready_at)
    remaining = {
        DoorState.OPENING: DWELL_S,
        DoorState.OPEN: DOOR_OPEN_S + DOOR_CLOSING_S,
        DoorState.CLOSING: DOOR_CLOSING_S,
        DoorState.OBSTRUCTED: DWELL_S,
    }
    return now + remaining[telemetry.door]


def route_etas(
    telemetry: CabinTelemetry,
    stops: tuple[Stop, ...],
    now: float,
    *,
    ready_at: float | None = None,
) -> dict[Stop, float]:
    """Calculate absolute arrivals, sharing one physical dwell at coincident stops."""
    position = position_at(telemetry, now)
    departure = departure_time(telemetry, now, ready_at=ready_at)
    previous_floor: int | None = None
    arrival = departure
    etas: dict[Stop, float] = {}
    for stop in stops:
        if stop.floor != previous_floor:
            arrival = departure + abs(stop.floor - position) * SECONDS_PER_FLOOR
            departure = arrival + DWELL_S
            position = stop.floor
            previous_floor = stop.floor
        etas[stop] = arrival
    return etas


def _sweep_direction(telemetry: CabinTelemetry, position: float) -> Direction:
    if telemetry.moving:
        return telemetry.direction
    for stop in telemetry.stops:
        if stop.floor != position:
            return Direction.UP if stop.floor > position else Direction.DOWN
    return Direction.IDLE


def _group_fits(
    stops: tuple[Stop, ...], pickup: Stop, policy: Policy, created_at: dict[str, float]
) -> bool:
    group = [
        stop
        for stop in stops
        if stop.kind == StopKind.HALL_PICKUP and stop.direction == pickup.direction
    ]
    if not group:
        return True
    group.append(pickup)
    floors = [stop.floor for stop in group]
    if max(floors) - min(floors) > policy.R:
        return False
    # Unknown external hall references cannot establish a safe fixed anchor window.
    if any(stop.ref_id not in created_at for stop in group):
        return False
    times = [created_at[stop.ref_id] for stop in group]
    return max(times) - min(times) <= policy.W


def hall_route(
    telemetry: CabinTelemetry,
    pickup: Stop,
    now: float,
    references: dict[Stop, float],
    policy: Policy,
    created_at: dict[str, float],
    algorithm: DispatchAlgorithm,
    *,
    ready_at: float | None = None,
) -> tuple[Stop, ...] | None:
    """Return the earliest safe copied route, preserving every existing commitment.

    FIRST_IDLE accepts an empty stationary car. SWEEP inserts in its current sweep.
    COMMITMENT also enforces the active R/W grouping policy. All three retain the
    physical insertion cutoff and fixed commitment delay guard.
    """
    if ordinary_exclusion(telemetry, now) is not None:
        return None
    if pickup.kind != StopKind.HALL_PICKUP or pickup.direction not in (
        Direction.UP,
        Direction.DOWN,
    ):
        return None
    if not MIN_FLOOR <= pickup.floor <= MAX_FLOOR:
        return None
    stops = telemetry.stops
    if pickup in stops:
        return stops
    if algorithm == DispatchAlgorithm.FIRST_IDLE and (telemetry.moving or stops):
        return None
    if algorithm == DispatchAlgorithm.COMMITMENT and not _group_fits(
        stops, pickup, policy, created_at
    ):
        return None

    position = position_at(telemetry, now)
    direction = _sweep_direction(telemetry, position)
    if telemetry.moving and direction == Direction.IDLE:
        return None
    if (stops or telemetry.moving) and direction != Direction.IDLE:
        if pickup.direction != direction:
            return None
        sign = 1 if direction == Direction.UP else -1
        distance = sign * (pickup.floor - position)
        if distance < (INSERT_CUTOFF_FLOORS if telemetry.moving else 0):
            return None
    else:
        sign = 1 if pickup.floor >= position else -1

    # Insert only within (or at the end of) the existing first directional sweep.
    # Existing later reverse legs remain untouched.
    sweep_end = len(stops)
    previous = position
    for index, stop in enumerate(stops):
        if sign * (stop.floor - previous) < 0:
            sweep_end = index
            break
        previous = stop.floor

    baseline = route_etas(telemetry, stops, now, ready_at=ready_at)
    candidates: list[tuple[float, int, tuple[Stop, ...]]] = []
    for index in range(sweep_end + 1):
        previous = position if index == 0 else stops[index - 1].floor
        if stops and sign * (pickup.floor - previous) < 0:
            continue
        if index < sweep_end and sign * (stops[index].floor - pickup.floor) < 0:
            continue
        route = stops[:index] + (pickup,) + stops[index:]
        etas = route_etas(telemetry, route, now, ready_at=ready_at)
        if any(etas[stop] - references.get(stop, baseline[stop]) > DELAY_CAP_S for stop in stops):
            continue
        candidates.append((etas[pickup], index, route))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]
