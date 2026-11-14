"""Every message exchanged between modules (framework F1).

Rules for every message:

* Frozen dataclass, keyword-only. Collections are ``tuple`` so messages are immutable
  and comparable with ``==``.
* ``msg_id``: leave ``""``. The engine stamps it (``MSG-000001``) when it queues the message.
* ``t_sim``: the ``now`` you received in ``handle()``.
* ``deliver_at``: leave ``None`` (= ``t_sim`` + default delay, D-06). Only ``Timer`` and
  scenario scripts set a future time.
* Who receives a message is decided by ``contracts.routing``, never by the sender.

Field names follow SRS 3.3 / 3.4 / Appendix D where those name a field.
"""

from dataclasses import dataclass, fields
from typing import Any

from contracts.config import DEFAULT_PASSENGER_KG, Policy
from contracts.enums import (
    AlertKind,
    CabinMode,
    CallStatus,
    Direction,
    DoorState,
    ExclusionReason,
    FaultKind,
    MissionAction,
    MissionStatus,
    MissionType,
    NackReason,
    Reason,
    RejectReason,
    Role,
    ServiceEventType,
    StopKind,
)

# ======================================================================================
# Value types (not messages)
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class Stop:
    """One stop in a cabin route (D-16).

    ``kind`` tells the Cabin which ServiceEvent to emit when the stop is served:
    HALL_PICKUP -> HALL_CALL_SERVED(ref_id=request_id), CAR_DEST -> DEST_SERVED,
    MISSION_PICKUP / MISSION_DROPOFF -> MISSION_PICKUP_DONE / MISSION_DELIVERY_DONE.
    """

    floor: int
    kind: StopKind
    ref_id: str | None = None  # request_id, passenger_id or mission_id
    direction: Direction | None = None  # set for HALL_PICKUP: the call's direction


@dataclass(frozen=True, kw_only=True)
class Passenger:
    """A virtual passenger already inside a cabin (used by CabinInit)."""

    passenger_id: str
    weight_kg: float = DEFAULT_PASSENGER_KG
    destination: int | None = None  # accepted Cabin Destination, if any


@dataclass(frozen=True, kw_only=True)
class CabinInit:
    """Initial state of one cabin, passed to ``cabin.create()`` (D-09).

    Scenarios use it for setups such as AT-03 (P3 in MAINTENANCE, P1 carrying a passenger
    to floor 40). Onboard destinations become CAR_DEST stops in the initial route.
    """

    car_id: str
    floor: int
    mode: CabinMode = CabinMode.AUTO
    passengers: tuple[Passenger, ...] = ()
    faults: tuple[FaultKind, ...] = ()
    route_version: int = 0


@dataclass(frozen=True, kw_only=True)
class Candidate:
    """One car considered for a mission, with its ETA or why it was excluded (MED-01, AT-03)."""

    car_id: str
    eta_s: float | None = None  # MedicalETA / freight ETA, eq. (5); None if excluded
    excluded: ExclusionReason | None = None


# ======================================================================================
# Base
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class Message:
    """Common envelope. See the module docstring for how to fill these three fields."""

    msg_id: str = ""
    t_sim: float
    deliver_at: float | None = None


BASE_FIELDS = frozenset({"msg_id", "t_sim", "deliver_at"})


def payload_key(msg: Message) -> tuple[Any, ...]:
    """The message without its envelope (msg_id, t_sim, deliver_at).

    Two deliveries of the "same" command have equal payload keys. Used for idempotency:
    same ``command_id`` + equal ``payload_key`` -> return the stored reply; same
    ``command_id`` + different ``payload_key`` -> NACK PAYLOAD_CONFLICT (D-14).
    """
    return (type(msg).__name__,) + tuple(
        (f.name, getattr(msg, f.name)) for f in fields(msg) if f.name not in BASE_FIELDS
    )


# ======================================================================================
# Passenger and operator input
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class HallCallPressed(Message):
    """UP/DOWN pressed at a landing. UI / scenario -> Dispatch. No personal data (5.3)."""

    floor: int
    direction: Direction


@dataclass(frozen=True, kw_only=True)
class HallCallAck(Message):
    """Dispatch's answer to every HallCallPressed (NFR-01). Logged; UI reads the snapshot.

    Accepted new call: accepted=True, duplicate=False, request_id set, status=PENDING.
    Duplicate: accepted=True, duplicate=True, the existing request_id and status (REQ-02).
    Invalid: accepted=False, request_id=None, reason set (SYS-03, D-18).
    """

    request_id: str | None
    floor: int
    direction: Direction
    accepted: bool
    duplicate: bool = False
    status: CallStatus | None = None
    reason: RejectReason | None = None


@dataclass(frozen=True, kw_only=True)
class CabinDestinationPressed(Message):
    """Floor button inside a cabin. UI / scenario -> Cabin.

    Valid only for a passenger already on board (DOOR-06); otherwise the Cabin emits
    ServiceEvent(DEST_REJECTED, reason=NOT_BOARDED).
    """

    car_id: str
    floor: int
    passenger_id: str


@dataclass(frozen=True, kw_only=True)
class PassengerBoard(Message):
    """A virtual passenger enters the cabin at its current landing. Scenario / UI -> Cabin."""

    car_id: str
    passenger_id: str
    weight_kg: float = DEFAULT_PASSENGER_KG


@dataclass(frozen=True, kw_only=True)
class PassengerExit(Message):
    """A virtual passenger leaves the cabin (also manual exit on overload, SEN-04)."""

    car_id: str
    passenger_id: str


@dataclass(frozen=True, kw_only=True)
class DoorRequest(Message):
    """Door open / close button inside the cabin. UI -> Cabin (DOOR-03, DOOR-05, D-51).

    open=True while OPEN restarts the OPEN period; while CLOSING reopens like an obstruction;
    while stationary at a landing with the door CLOSED starts a normal door cycle.
    open=False while OPEN starts CLOSING now. Every other case (e.g. moving) is ignored.
    """

    car_id: str
    open: bool


@dataclass(frozen=True, kw_only=True)
class AlarmPressed(Message):
    """Cabin alarm button. -> Cabin and Control Center. Never becomes MEDICAL (EMG-01)."""

    car_id: str


@dataclass(frozen=True, kw_only=True)
class MissionRequest(Message):
    """Privileged mission request from the UI. -> Control Center (CC-01, CC-03, FRT-01).

    origin / destination may be missing so Control Center can reject with MISSING_FIELD.
    """

    role: Role
    mission_type: MissionType
    origin: int | None
    destination: int | None
    material: str | None = None  # FREIGHT material category
    weight_kg: float | None = None  # FREIGHT declared load


@dataclass(frozen=True, kw_only=True)
class MissionLoadConfirmed(Message):
    """Staff / medical team confirms loading or unloading at a mission stop. -> Cabin (D-25).

    LOADED at the pickup starts the exclusive leg (FRT-03, MED-05); UNLOADED at the
    destination completes the mission (FRT-04); LOAD_FAILED makes the mission FAILED/HELD.
    """

    mission_id: str
    car_id: str
    action: MissionAction
    weight_kg: float = 0.0


@dataclass(frozen=True, kw_only=True)
class AlertAcknowledged(Message):
    """Operator acknowledges an alert. UI -> Control Center. alert_id is the Alert's msg_id."""

    alert_id: str
    role: Role


@dataclass(frozen=True, kw_only=True)
class MaintenanceSet(Message):
    """Maintainer puts a car into maintenance (on=True) or records the work as finished.

    on=False does NOT return the car to AUTO: that still needs RecoveryApproved (EMG-04).
    """

    car_id: str
    on: bool
    role: Role


@dataclass(frozen=True, kw_only=True)
class RecoveryRequest(Message):
    """Supervisor asks to return a held car to service. UI -> Control Center (CC-04)."""

    car_id: str
    role: Role


@dataclass(frozen=True, kw_only=True)
class PolicyDecision(Message):
    """Supervisor approves or rejects a recommended policy. UI -> Control Center (OPT-06)."""

    run_id: str
    approve: bool
    role: Role


# ======================================================================================
# Safety inputs (delivered first at equal timestamps, D-05)
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class FaultInjected(Message):
    """Simulated fault or obstruction. Scenario / UI -> Cabin. See FaultKind for persistence."""

    car_id: str
    kind: FaultKind


@dataclass(frozen=True, kw_only=True)
class FaultCleared(Message):
    """The simulated fault is physically gone. Does not by itself return a car to AUTO."""

    car_id: str
    kind: FaultKind


@dataclass(frozen=True, kw_only=True)
class FireAlarm(Message):
    """Building fire-recall input. -> Dispatch, every Cabin, Control Center (D-24)."""

    active: bool


# ======================================================================================
# Dispatch <-> Cabin
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class RouteProposal(Message):
    """Dispatch proposes the full new route for one car. -> that Cabin (CB-01, SIM-06).

    ``ordered_stops`` is the complete route, not a delta. ``expiry_ts`` = t_sim +
    ACK_TIMEOUT_S (D-10). Idempotent by ``command_id`` (see ``payload_key``).
    """

    command_id: str
    car_id: str
    expected_route_version: int
    ordered_stops: tuple[Stop, ...]
    expiry_ts: float
    mission_id: str | None = None
    exclusive: bool = False  # exclusive mission leg: no ordinary pickups (MED-05, FRT-03)


@dataclass(frozen=True, kw_only=True)
class RouteReply(Message):
    """Cabin ACK / NACK for a RouteProposal. -> Dispatch.

    ACK: accepted=True, committed_version = new version.
    NACK: accepted=False, committed_version = unchanged current version, reason set.
    """

    command_id: str
    car_id: str
    accepted: bool
    committed_version: int
    reason: NackReason | None = None


@dataclass(frozen=True, kw_only=True)
class RouteVersionQuery(Message):
    """After a missing ACK, Dispatch asks what happened to ``command_id`` (REQ-04, SIM-07)."""

    command_id: str
    car_id: str


@dataclass(frozen=True, kw_only=True)
class RouteVersionReport(Message):
    """Cabin's authoritative answer to a RouteVersionQuery. -> Dispatch.

    seen=False: the Cabin never received that command. seen=True: ``accepted`` is the
    stored result. ``route_version`` is always the Cabin's current version.
    """

    command_id: str
    car_id: str
    seen: bool
    accepted: bool | None
    route_version: int


# ======================================================================================
# Cabin -> others
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class CabinTelemetry(Message):
    """Authoritative cabin state (SYS-02, MOVE-01, SEN-01). Sent on every change and at least
    every TELEMETRY_HEARTBEAT_S (D-11). -> Dispatch, Control Center. ``t_sim`` is the sample time.
    """

    car_id: str
    position: float  # continuous, floor 1 = 1.0 (D-02)
    direction: Direction
    moving: bool
    door: DoorState
    locked: bool
    door_zone: bool  # at a valid landing door zone
    obstruction: bool
    load_kg: float
    passengers: int
    mode: CabinMode
    route_version: int
    stops: tuple[Stop, ...] = ()
    faults: tuple[FaultKind, ...] = ()
    sensors_valid: bool = True  # False if any reading is stale or conflicting (SEN-02)
    mission_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class ServiceEvent(Message):
    """Something observable happened at a cabin (7.3, 7.4, CB-04, REQ-05). -> Dispatch, CC."""

    car_id: str
    event: ServiceEventType
    floor: int
    ref_id: str | None = None  # request_id / mission_id, per ServiceEventType
    passenger_id: str | None = None
    reason: RejectReason | None = None  # only for DEST_REJECTED


# ======================================================================================
# Missions, alerts, recovery, policy
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class MissionApproval(Message):
    """Control Center grants a mission. -> Dispatch, which selects the car (MED-01, FRT-02)."""

    mission_id: str
    role: Role
    mission_type: MissionType
    origin: int
    destination: int
    allowed_cars: tuple[str, ...]  # capable cars from config (D-20, D-21)
    material: str | None = None
    weight_kg: float | None = None


@dataclass(frozen=True, kw_only=True)
class MissionStatusUpdate(Message):
    """Mission lifecycle change. Dispatch / Cabin / CC -> Control Center (MED-04, UI-04).

    Dispatch fills ``candidates`` when it selects (or fails to select) a car, so every ETA
    and every exclusion reason is visible (AT-03).
    """

    mission_id: str
    status: MissionStatus
    car_id: str | None = None
    reason: Reason | None = None
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Alert(Message):
    """Operator-visible alert. Any module -> Control Center. alert_id = this msg_id."""

    kind: AlertKind
    car_id: str | None = None
    ref_id: str | None = None  # mission_id, command_id, ...
    details: str = ""


@dataclass(frozen=True, kw_only=True)
class RecoveryApproved(Message):
    """Control Center authorises return to AUTO. -> Cabin, which still runs its own readiness
    checks before leaving FAULT_HOLD or MAINTENANCE (EMG-04, CC-04)."""

    car_id: str
    approved_by: Role


@dataclass(frozen=True, kw_only=True)
class PolicyRecommended(Message):
    """Offline optimizer recommends a policy. Optimization -> Control Center (OPT-06, D-36)."""

    run_id: str
    policy: Policy
    objective: float  # J(theta) on the training traces
    scenario_set: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class PolicyActivated(Message):
    """Control Center activates an approved policy. -> Dispatch, which applies it at the next
    decision boundary without touching accepted routes (OPT-06)."""

    policy: Policy


# ======================================================================================
# Timers
# ======================================================================================


@dataclass(frozen=True, kw_only=True)
class Timer(Message):
    """A module's wake-up call to itself (D-08).

    Return ``Timer(t_sim=now, deliver_at=now + delay, target=self.name, key="door")`` from
    ``handle()``; the engine delivers it back at ``deliver_at``. A timer cannot be cancelled:
    ignore stale ones, for example by putting a counter in ``key`` or ``ref_id``.
    """

    target: str  # the module's name / address, e.g. "cabin:P1", "dispatch"
    key: str
    ref_id: str | None = None


# At t=0 and after every reset the engine sends Timer(target=<name>, key=TIMER_START) to
# every module, so modules can send their first telemetry and start their own timers (D-08).
TIMER_START = "start"


ALL_MESSAGES: tuple[type[Message], ...] = (
    HallCallPressed,
    HallCallAck,
    CabinDestinationPressed,
    PassengerBoard,
    PassengerExit,
    DoorRequest,
    AlarmPressed,
    MissionRequest,
    MissionLoadConfirmed,
    AlertAcknowledged,
    MaintenanceSet,
    RecoveryRequest,
    PolicyDecision,
    FaultInjected,
    FaultCleared,
    FireAlarm,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    CabinTelemetry,
    ServiceEvent,
    MissionApproval,
    MissionStatusUpdate,
    Alert,
    RecoveryApproved,
    PolicyRecommended,
    PolicyActivated,
    Timer,
)
