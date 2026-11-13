"""Shared enumerations (framework F2).

Every enum is a ``StrEnum`` so it serialises to its name in JSON logs and snapshots.
Only ``Priority`` is an ``IntEnum`` because the engine sorts on it.

Adding a new value nobody uses yet is always allowed (CONTRIBUTING.md, Section 4).
Renaming or removing a value needs a contract change.
"""

from enum import IntEnum, StrEnum


class Direction(StrEnum):
    """Travel direction of a cabin, or requested direction of a Hall Call (MOVE-01)."""

    UP = "UP"
    DOWN = "DOWN"
    IDLE = "IDLE"  # cabins only; a Hall Call is always UP or DOWN


class DoorState(StrEnum):
    """Door model states (DOOR-01, SRS Figure 8)."""

    CLOSED = "CLOSED"
    OPENING = "OPENING"
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    OBSTRUCTED = "OBSTRUCTED"


class CabinMode(StrEnum):
    """Operating mode of a cabin (SRS Figure 7, 7.5, 7.6)."""

    AUTO = "AUTO"
    FULL = "FULL"  # load >= FULL_LOAD_KG: bypass new ordinary pickups (CB-02)
    OVERLOAD_HOLD = "OVERLOAD_HOLD"  # load > MAX_LOAD_KG: no motion (SEN-03, SEN-04)
    FAULT_HOLD = "FAULT_HOLD"  # serious fault: motion and normal door opening inhibited (EMG-02)
    MAINTENANCE = "MAINTENANCE"
    MISSION_DRAINING = "MISSION_DRAINING"  # reserved for a mission, dropping onboard passengers
    MISSION_EXCLUSIVE = "MISSION_EXCLUSIVE"  # running the exclusive mission leg
    FIRE = "FIRE"


class CallStatus(StrEnum):
    """Hall Call lifecycle (REQ-01, SRS Figure 6)."""

    PENDING = "PENDING"
    RESERVED = "RESERVED"  # provisional owner, waiting for Cabin ACK (REQ-03)
    ASSIGNED = "ASSIGNED"  # confirmed owner after ACK
    SERVED = "SERVED"


class MissionStatus(StrEnum):
    """Privileged mission lifecycle (7.2, 7.8)."""

    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"  # no eligible car; kept and re-evaluated (MED-04)
    RESERVED = "RESERVED"
    DRAINING = "DRAINING"  # selected car is dropping its onboard passengers
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    HOLD_PENDING_REVIEW = "HOLD_PENDING_REVIEW"  # SEN-06, FRT-04 held mission


class MissionType(StrEnum):
    MEDICAL = "MEDICAL"
    FREIGHT = "FREIGHT"


class Role(StrEnum):
    """User roles chosen in the UI (DECISIONS.md D-23)."""

    PASSENGER = "PASSENGER"
    MEDICAL_COORDINATOR = "MEDICAL_COORDINATOR"
    BUILDING_STAFF = "BUILDING_STAFF"
    OPERATOR = "OPERATOR"  # also the "simulator operator" in the REST table
    SUPERVISOR = "SUPERVISOR"
    MAINTAINER = "MAINTAINER"


class NackReason(StrEnum):
    """Why a Cabin rejected a RouteProposal (CB-01). Every NACK carries one."""

    VERSION_MISMATCH = "VERSION_MISMATCH"  # expected_route_version != current version
    PAYLOAD_CONFLICT = "PAYLOAD_CONFLICT"  # same command_id, different payload (D-14)
    EXPIRED = "EXPIRED"  # received after expiry_ts
    INVALID_FLOOR = "INVALID_FLOOR"
    INVALID_ROUTE = "INVALID_ROUTE"  # empty, duplicated or malformed stop list
    ONBOARD_COMMITMENT = "ONBOARD_COMMITMENT"  # route drops an accepted onboard destination
    DOORS_NOT_LOCKED = "DOORS_NOT_LOCKED"
    POSITION_INVALID = "POSITION_INVALID"
    OVERLOAD = "OVERLOAD"
    FULL_LOAD = "FULL_LOAD"  # new ordinary pickup while at or above FULL_LOAD_KG
    INSERT_CUTOFF = "INSERT_CUTOFF"  # less than INSERT_CUTOFF_FLOORS to the new stop (MOVE-06)
    REVERSAL_REQUIRED = "REVERSAL_REQUIRED"  # would reverse while committed (MOVE-05)
    FAULT_HOLD = "FAULT_HOLD"
    MAINTENANCE = "MAINTENANCE"
    FIRE = "FIRE"
    SENSORS_INVALID = "SENSORS_INVALID"  # stale or conflicting readings (SEN-02)
    MISSION_EXCLUSIVE = "MISSION_EXCLUSIVE"  # car is reserved for a mission
    NOT_CAPABLE = "NOT_CAPABLE"


class RejectReason(StrEnum):
    """Why an input (Hall Call, destination, mission, command) was refused."""

    FLOOR_OUT_OF_RANGE = "FLOOR_OUT_OF_RANGE"  # SYS-03
    TERMINAL_DIRECTION = "TERMINAL_DIRECTION"  # DOWN at floor 1 or UP at floor 40
    INVALID_DIRECTION = "INVALID_DIRECTION"  # a Hall Call must be UP or DOWN, never IDLE
    NOT_BOARDED = "NOT_BOARDED"  # destination pressed before boarding (DOOR-06)
    UNAUTHORIZED_ROLE = "UNAUTHORIZED_ROLE"  # CC-01
    MISSING_FIELD = "MISSING_FIELD"  # CC-03
    SAME_ORIGIN_DESTINATION = "SAME_ORIGIN_DESTINATION"
    INVALID_MATERIAL = "INVALID_MATERIAL"  # FRT-01
    OVER_PAYLOAD = "OVER_PAYLOAD"  # FRT-01 rated payload
    UNKNOWN_ID = "UNKNOWN_ID"  # unknown car, mission, alert or run id
    INVALID_STATE = "INVALID_STATE"  # e.g. recovery for a car that is not held


class ExclusionReason(StrEnum):
    """Why a car was not a candidate for a call or mission (MED-01, AT-03)."""

    MAINTENANCE = "MAINTENANCE"
    FAULT_HOLD = "FAULT_HOLD"
    STALE_TELEMETRY = "STALE_TELEMETRY"  # older than TELEMETRY_STALE_S (D-11)
    SENSORS_INVALID = "SENSORS_INVALID"
    NOT_CAPABLE = "NOT_CAPABLE"  # capability config (D-20, D-21)
    FIRE = "FIRE"
    OVERLOAD = "OVERLOAD"
    FULL_LOAD = "FULL_LOAD"
    MISSION_BUSY = "MISSION_BUSY"  # already reserved for another mission
    OVER_PAYLOAD = "OVER_PAYLOAD"  # freight weight above the car's limit


# Any reason value carried by a mission status update.
Reason = NackReason | RejectReason | ExclusionReason


class ServiceEventType(StrEnum):
    """Observable cabin events (7.3, 7.4, 7.9). Pickup and drop-off are distinct (CB-04, REQ-05)."""

    CAR_ARRIVED = "CAR_ARRIVED"
    CAR_DEPARTED = "CAR_DEPARTED"
    CAR_IDLE = "CAR_IDLE"
    DOOR_OPENING = "DOOR_OPENING"
    DOOR_OPENED = "DOOR_OPENED"
    DOOR_CLOSING = "DOOR_CLOSING"
    DOOR_CLOSED_LOCKED = "DOOR_CLOSED_LOCKED"
    DOOR_REOPENED = "DOOR_REOPENED"  # obstruction during closing (DOOR-04)
    HALL_CALL_SERVED = "HALL_CALL_SERVED"  # ref_id = request_id
    PASSENGER_BOARDED = "PASSENGER_BOARDED"
    PASSENGER_EXITED = "PASSENGER_EXITED"
    DEST_ACCEPTED = "DEST_ACCEPTED"  # floor = the destination
    DEST_REJECTED = "DEST_REJECTED"  # floor = the requested destination; reason is set
    DEST_SERVED = "DEST_SERVED"  # floor = the destination; passenger_id set
    DRAIN_COMPLETE = "DRAIN_COMPLETE"  # no onboard passengers left before a mission
    HANDOFF_OK = "HANDOFF_OK"  # empty handoff verified (SEN-05)
    MISSION_PICKUP_DONE = "MISSION_PICKUP_DONE"  # ref_id = mission_id
    MISSION_DELIVERY_DONE = "MISSION_DELIVERY_DONE"  # ref_id = mission_id


class AlertKind(StrEnum):
    """Operator alerts. ALARM is never mapped to MEDICAL (EMG-01)."""

    ALARM = "ALARM"
    MEDICAL = "MEDICAL"
    FIRE = "FIRE"
    FAULT = "FAULT"
    NO_ELIGIBLE_CAR = "NO_ELIGIBLE_CAR"
    UNAVAILABLE = "UNAVAILABLE"
    OVERLOAD = "OVERLOAD"
    OBSTRUCTION = "OBSTRUCTION"
    SENSOR = "SENSOR"
    HANDOFF_REVIEW = "HANDOFF_REVIEW"  # SEN-06
    PROTOCOL_ERROR = "PROTOCOL_ERROR"  # e.g. PAYLOAD_CONFLICT (D-14)


class FaultKind(StrEnum):
    """Injected faults. OBSTRUCTION is momentary (one input, one reopen).
    All other kinds stay active until a matching ``FaultCleared``."""

    MOTION_SERIOUS = "MOTION_SERIOUS"  # between-floor serious fault -> FAULT_HOLD (EMG-02)
    DOOR_LOCK_LOSS = "DOOR_LOCK_LOSS"
    OBSTRUCTION = "OBSTRUCTION"
    POSITION_SENSOR = "POSITION_SENSOR"
    LOAD_SENSOR = "LOAD_SENSOR"
    SENSOR_CONFLICT = "SENSOR_CONFLICT"
    HEARTBEAT_LOSS = "HEARTBEAT_LOSS"  # cabin stops sending telemetry


class StopKind(StrEnum):
    """What a stop in a route is for (DECISIONS.md D-16)."""

    HALL_PICKUP = "HALL_PICKUP"  # ref_id = request_id, direction set
    CAR_DEST = "CAR_DEST"  # ref_id = passenger_id
    MISSION_PICKUP = "MISSION_PICKUP"  # ref_id = mission_id
    MISSION_DROPOFF = "MISSION_DROPOFF"  # ref_id = mission_id


class MissionAction(StrEnum):
    """Staff / medical confirmation at a mission stop (FRT-03, FRT-04, MED-05)."""

    LOADED = "LOADED"
    UNLOADED = "UNLOADED"
    LOAD_FAILED = "LOAD_FAILED"


class DestStatus(StrEnum):
    """Cabin Destination state shown in the UI (UI-03)."""

    PENDING = "PENDING"
    SERVED = "SERVED"


class DispatchAlgorithm(StrEnum):
    """Dispatch policies behind the same interface (TH-06, SRS 6.5)."""

    COMMITMENT = "COMMITMENT"  # the SRS commitment-aware dispatcher (default)
    FIRST_IDLE = "FIRST_IDLE"  # baseline 1
    SWEEP = "SWEEP"  # baseline 2: directional sweep


class SimState(StrEnum):
    """Simulation session state (SIM-01)."""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class Priority(IntEnum):
    """Same-timestamp delivery order, lower first (SRS 7.7, DECISIONS.md D-05)."""

    SAFETY = 0
    PHYSICAL = 1
    CONTROL = 2
    INPUT = 3
    REPORTING = 4


class EventType(StrEnum):
    """Engine-level log entries that are not module messages."""

    MESSAGE = "MESSAGE"  # a message was delivered
    MESSAGE_DROPPED = "MESSAGE_DROPPED"  # scenario dropped a message (AT-05)
    SIM_STARTED = "SIM_STARTED"
    SIM_PAUSED = "SIM_PAUSED"
    SIM_RESUMED = "SIM_RESUMED"
    SIM_STOPPED = "SIM_STOPPED"
    SIM_RESET = "SIM_RESET"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    METRICS = "METRICS"
