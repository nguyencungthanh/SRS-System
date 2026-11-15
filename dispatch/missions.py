"""Authorized mission routes and fleet-wide pickup estimates (TH-05, SRS eq. 5)."""

from math import isfinite

from contracts.config import (
    CAR_ORDER,
    DWELL_S,
    FREIGHT_CAPABLE,
    FREIGHT_MAX_KG,
    MAX_FLOOR,
    MAX_LOAD_KG,
    MEDICAL_CAPABLE,
    MIN_FLOOR,
    SECONDS_PER_FLOOR,
    TELEMETRY_STALE_S,
)
from contracts.enums import CabinMode, DoorState, ExclusionReason, MissionType, StopKind
from contracts.messages import CabinTelemetry, Candidate, MissionApproval, Stop

from .planning import departure_time, position_at


def mission_route(approval: MissionApproval, telemetry: CabinTelemetry) -> tuple[Stop, ...]:
    """Preserve onboard drop-offs, then run pickup and the exclusive delivery leg."""
    return tuple(stop for stop in telemetry.stops if stop.kind == StopKind.CAR_DEST) + (
        Stop(floor=approval.origin, kind=StopKind.MISSION_PICKUP, ref_id=approval.mission_id),
        Stop(floor=approval.destination, kind=StopKind.MISSION_DROPOFF, ref_id=approval.mission_id),
    )


def _exclusion(
    approval: MissionApproval,
    car_id: str,
    telemetry: CabinTelemetry | None,
    now: float,
    busy: set[str],
) -> ExclusionReason | None:
    capable = MEDICAL_CAPABLE if approval.mission_type == MissionType.MEDICAL else FREIGHT_CAPABLE
    if car_id not in capable or car_id not in approval.allowed_cars:
        return ExclusionReason.NOT_CAPABLE
    if telemetry is None or now - telemetry.t_sim > TELEMETRY_STALE_S:
        return ExclusionReason.STALE_TELEMETRY
    if not telemetry.sensors_valid or not (
        isfinite(telemetry.position)
        and MIN_FLOOR <= telemetry.position <= MAX_FLOOR
        and isfinite(telemetry.load_kg)
        and telemetry.load_kg >= 0
        and not (telemetry.moving and (telemetry.door != DoorState.CLOSED or not telemetry.locked))
    ):
        return ExclusionReason.SENSORS_INVALID
    held = {
        CabinMode.MAINTENANCE: ExclusionReason.MAINTENANCE,
        CabinMode.FAULT_HOLD: ExclusionReason.FAULT_HOLD,
        CabinMode.FIRE: ExclusionReason.FIRE,
        CabinMode.OVERLOAD_HOLD: ExclusionReason.OVERLOAD,
        CabinMode.MISSION_DRAINING: ExclusionReason.MISSION_BUSY,
        CabinMode.MISSION_EXCLUSIVE: ExclusionReason.MISSION_BUSY,
    }
    if telemetry.mode in held:
        return held[telemetry.mode]
    if telemetry.load_kg > MAX_LOAD_KG:
        return ExclusionReason.OVERLOAD
    if car_id in busy or telemetry.mission_id is not None:
        return ExclusionReason.MISSION_BUSY
    if (
        approval.mission_type == MissionType.FREIGHT
        and approval.weight_kg is not None
        and approval.weight_kg > FREIGHT_MAX_KG
    ):
        return ExclusionReason.OVER_PAYLOAD
    return None


def mission_candidates(
    approval: MissionApproval,
    telemetry: dict[str, CabinTelemetry],
    now: float,
    busy: set[str],
    *,
    ready_at: dict[str, float] | None = None,
) -> tuple[Candidate, ...]:
    """Explain every configured car, including absent, incapable and held cars.

    FULL cars can drain their existing passengers before a mission; ordinary pickup
    eligibility is deliberately stricter. Estimates are seconds from ``now``.
    """
    candidates: list[Candidate] = []
    for car_id in CAR_ORDER:
        car = telemetry.get(car_id)
        reason = _exclusion(approval, car_id, car, now, busy)
        if reason is not None:
            candidates.append(Candidate(car_id=car_id, excluded=reason))
            continue
        assert car is not None
        eta = departure_time(car, now, ready_at=(ready_at or {}).get(car_id)) - now
        position = position_at(car, now)
        last_drop: int | None = None
        for stop in car.stops:
            if stop.kind != StopKind.CAR_DEST:
                continue
            if stop.floor == last_drop:
                continue
            eta += abs(stop.floor - position) * SECONDS_PER_FLOOR + DWELL_S
            position = stop.floor
            last_drop = stop.floor
        eta += abs(approval.origin - position) * SECONDS_PER_FLOOR
        candidates.append(Candidate(car_id=car_id, eta_s=eta))
    return tuple(candidates)
