"""Exclusive MEDICAL / FREIGHT mission phases on the cabin side (D-52, SEN-05, SEN-06).

NONE --exclusive route ACKed--> DRAINING --empty handoff--> TO_PICKUP --arrive--> AT_PICKUP
DRAINING --count/load conflict--> REVIEW (HOLD_PENDING_REVIEW) --empty handoff--> TO_PICKUP
AT_PICKUP --LOADED--> EXCLUSIVE --arrive--> AT_DROPOFF --UNLOADED--> NONE (COMPLETED)
any active phase --LOAD_FAILED--> NONE (FAILED)
"""

from enum import StrEnum

from contracts.enums import CabinMode, StopKind
from contracts.messages import Stop


class MissionPhase(StrEnum):
    NONE = "NONE"
    DRAINING = "DRAINING"  # onboard passengers still being dropped off
    REVIEW = "REVIEW"  # count and load disagree: HOLD_PENDING_REVIEW (SEN-06)
    TO_PICKUP = "TO_PICKUP"  # empty handoff verified, running to the mission pickup
    AT_PICKUP = "AT_PICKUP"  # doors open at the pickup, waiting for LOADED
    EXCLUSIVE = "EXCLUSIVE"  # direct leg to the drop-off (MED-05, FRT-03)
    AT_DROPOFF = "AT_DROPOFF"  # doors open at the drop-off, waiting for UNLOADED


MISSION_STOPS = (StopKind.MISSION_PICKUP, StopKind.MISSION_DROPOFF)

MODE: dict[MissionPhase, CabinMode | None] = {
    MissionPhase.NONE: None,
    MissionPhase.DRAINING: CabinMode.MISSION_DRAINING,
    MissionPhase.REVIEW: CabinMode.MISSION_DRAINING,
    MissionPhase.TO_PICKUP: CabinMode.MISSION_EXCLUSIVE,
    MissionPhase.AT_PICKUP: CabinMode.MISSION_EXCLUSIVE,
    MissionPhase.EXCLUSIVE: CabinMode.MISSION_EXCLUSIVE,
    MissionPhase.AT_DROPOFF: CabinMode.MISSION_EXCLUSIVE,
}

# Phases in which the doors wait open for a staff confirmation (D-52).
WAITING_FOR_CONFIRMATION = (MissionPhase.AT_PICKUP, MissionPhase.AT_DROPOFF)


def blocks_departure(phase: MissionPhase, next_stop: Stop) -> bool:
    """Whether the mission forbids leaving for ``next_stop`` now.

    While draining, the cabin may only run to its passengers' destinations (FRT-02); it
    leaves a mission stop only after the staff confirmation.
    """
    if phase in (MissionPhase.DRAINING, MissionPhase.REVIEW):
        return next_stop.kind in MISSION_STOPS
    return phase in WAITING_FOR_CONFIRMATION


def load_conflicts(passenger_count: int, load_kg: float, load_sensor_ok: bool) -> bool:
    """SEN-06: the count and the load reading disagree, so emptiness cannot be proven.

    Assumption for open question Q7: people on board with a zero load reading, or nobody
    on board with a non-zero reading, or a failed load sensor.
    """
    if not load_sensor_ok:
        return True
    if passenger_count > 0:
        return load_kg <= 0.0
    return load_kg > 0.0
