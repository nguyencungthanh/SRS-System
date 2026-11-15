"""The Control Center module (HY-01..HY-04).

A pure message handler: ``handle(msg, now)`` reacts to one delivered message and returns
what it emits. It never calls another module (PROJECT_PLAN.md Section 3).

What it does
    HY-01  MissionRequest: role + validation (permissions.py). Valid -> APPROVED status and
           MissionApproval with the capable cars. Invalid -> REJECTED + reason, nothing else
           (CC-01: a rejected grant changes no route).
    HY-02  MissionStatusUpdate from Dispatch / Cabin: tracked for the snapshot. The first
           UNAVAILABLE raises exactly one UNAVAILABLE alert and stays visible until a new
           status arrives (MED-04, EMG-05, UI-04).
    HY-03  Alerts from Alert, AlarmPressed (the only source of ALARM alerts, D-19, EMG-01)
           and FireAlarm(active=True). AlertAcknowledged by OPERATOR / SUPERVISOR.
           RecoveryRequest by SUPERVISOR for a car whose telemetry shows FAULT_HOLD or
           MAINTENANCE -> RecoveryApproved. The Cabin still runs its own readiness checks
           (EMG-04, CC-04), so Control Center never decides that a car is safe.
    HY-04  PolicyRecommended is stored. PolicyDecision(approve) by SUPERVISOR for a known
           run -> PolicyActivated (OPT-06, D-37).

Control Center itself only sends APPROVED / REJECTED mission statuses (D-29).
"""

from contracts.enums import AlertKind, CabinMode, MissionStatus, RejectReason, Role
from contracts.ids import PREFIX_MISSION, IdGen
from contracts.messages import (
    AlarmPressed,
    Alert,
    AlertAcknowledged,
    CabinTelemetry,
    FireAlarm,
    Message,
    MissionApproval,
    MissionRequest,
    MissionStatusUpdate,
    PolicyActivated,
    PolicyDecision,
    PolicyRecommended,
    RecoveryApproved,
    RecoveryRequest,
)
from contracts.routing import CONTROL_CENTER
from contracts.ui import AlertView, ControlCenterSnapshot, MissionView
from control_center.audit import AuditAction, AuditEntry, AuditOutcome
from control_center.permissions import (
    ALERT_ACK_ROLES,
    POLICY_ROLES,
    RECOVERY_ROLES,
    allowed_cars,
    validate_request,
)

# Modes from which a supervisor may authorise the return to AUTO (EMG-04).
RECOVERABLE_MODES: frozenset[CabinMode] = frozenset({CabinMode.FAULT_HOLD, CabinMode.MAINTENANCE})

ALARM_DETAILS = "Alarm button pressed"
FIRE_DETAILS = "Fire alarm active"


class ControlCenter:
    name = CONTROL_CENTER

    def __init__(self) -> None:
        self._missions: dict[str, MissionView] = {}
        self._alerts: dict[str, AlertView] = {}
        self._car_modes: dict[str, CabinMode] = {}
        self._recommendations: dict[str, PolicyRecommended] = {}
        self._audit: list[AuditEntry] = []
        self._mission_ids = IdGen(PREFIX_MISSION)

    # --- Module interface ------------------------------------------------------------

    def handle(self, msg: Message, now: float) -> list[Message]:
        out: list[Message] = []
        match msg:
            case MissionRequest():
                self._on_mission_request(msg, now, out)
            case MissionStatusUpdate():
                self._on_status(msg, now)
            case AlarmPressed():
                self._raise_alert(msg.msg_id, AlertKind.ALARM, msg.car_id, ALARM_DETAILS, now)
            case Alert():
                self._raise_alert(msg.msg_id, msg.kind, msg.car_id, msg.details, now)
            case FireAlarm():
                self._on_fire(msg, now)
            case AlertAcknowledged():
                self._on_alert_ack(msg, now)
            case CabinTelemetry():
                self._car_modes[msg.car_id] = msg.mode
            case RecoveryRequest():
                self._on_recovery(msg, now, out)
            case PolicyRecommended():
                self._on_recommended(msg, now)
            case PolicyDecision():
                self._on_policy_decision(msg, now, out)
            case _:
                pass  # Timer(TIMER_START), ServiceEvent, ...: nothing to do
        return out

    def snapshot(self) -> ControlCenterSnapshot:
        """Shape fixed by contracts.ui.ControlCenterSnapshot; copies, so callers cannot
        change module state."""
        return {
            "missions": [
                {**view, "candidates": [dict(c) for c in view["candidates"]]}  # type: ignore[typeddict-item]
                for view in self._missions.values()
            ],
            "alerts": [dict(view) for view in self._alerts.values()],  # type: ignore[misc]
        }

    def audit_log(self) -> tuple[AuditEntry, ...]:
        """Every Control Center decision, oldest first (SRS 5.3). Not part of the snapshot."""
        return tuple(self._audit)

    # --- HY-01 mission requests ------------------------------------------------------

    def _on_mission_request(self, msg: MissionRequest, now: float, out: list[Message]) -> None:
        mission_id = self._mission_ids.next()
        reason = validate_request(msg)
        status = MissionStatus.APPROVED if reason is None else MissionStatus.REJECTED
        self._missions[mission_id] = {
            "mission_id": mission_id,
            "mission_type": msg.mission_type.value,
            "origin": msg.origin,
            "destination": msg.destination,
            "status": status.value,
            "car_id": None,
            "reason": reason.value if reason else None,
            "candidates": [],
        }
        out.append(
            MissionStatusUpdate(t_sim=now, mission_id=mission_id, status=status, reason=reason)
        )
        self._record(
            now,
            AuditAction.MISSION_REQUEST,
            AuditOutcome.APPROVED if reason is None else AuditOutcome.REFUSED,
            mission_id,
            role=msg.role,
            reason=reason,
            details=msg.mission_type.value,
        )
        if reason is not None:
            return  # CC-01: a rejected grant changes nothing downstream
        assert msg.origin is not None and msg.destination is not None  # checked above
        out.append(
            MissionApproval(
                t_sim=now,
                mission_id=mission_id,
                role=msg.role,
                mission_type=msg.mission_type,
                origin=msg.origin,
                destination=msg.destination,
                allowed_cars=allowed_cars(msg.mission_type),
                material=msg.material,
                weight_kg=msg.weight_kg,
            )
        )

    # --- HY-02 mission lifecycle -----------------------------------------------------

    def _on_status(self, msg: MissionStatusUpdate, now: float) -> None:
        view = self._missions.get(msg.mission_id)
        if view is None:
            return  # not a mission this Control Center approved
        was_unavailable = view["status"] == MissionStatus.UNAVAILABLE
        view["status"] = msg.status.value
        if msg.car_id is not None:
            view["car_id"] = msg.car_id
        if msg.reason is not None:
            view["reason"] = msg.reason.value
        if msg.candidates:
            view["candidates"] = [
                {
                    "car_id": c.car_id,
                    "eta_s": c.eta_s,
                    "excluded": c.excluded.value if c.excluded else None,
                }
                for c in msg.candidates
            ]
        if msg.status == MissionStatus.UNAVAILABLE and not was_unavailable:
            self._raise_alert(
                msg.msg_id,
                AlertKind.UNAVAILABLE,
                None,
                f"No eligible car for {msg.mission_id}",
                now,
            )

    # --- HY-03 alerts and recovery ---------------------------------------------------

    def _raise_alert(
        self, alert_id: str, kind: AlertKind, car_id: str | None, details: str, now: float
    ) -> None:
        self._alerts[alert_id] = {
            "alert_id": alert_id,
            "kind": kind.value,
            "car_id": car_id,
            "details": details,
            "t_sim": now,
            "acknowledged": False,
        }
        self._record(
            now, AuditAction.ALERT_RAISED, AuditOutcome.RECORDED, alert_id, details=kind.value
        )

    def _on_fire(self, msg: FireAlarm, now: float) -> None:
        if msg.active:
            self._raise_alert(msg.msg_id, AlertKind.FIRE, None, FIRE_DETAILS, now)
            return
        # The FIRE alert stays visible until someone acknowledges it; only record the clear.
        self._record(now, AuditAction.FIRE_CLEARED, AuditOutcome.RECORDED, msg.msg_id)

    def _on_alert_ack(self, msg: AlertAcknowledged, now: float) -> None:
        reason: RejectReason | None = None
        if msg.role not in ALERT_ACK_ROLES:
            reason = RejectReason.UNAUTHORIZED_ROLE
        elif msg.alert_id not in self._alerts:
            reason = RejectReason.UNKNOWN_ID
        else:
            self._alerts[msg.alert_id]["acknowledged"] = True
        self._record(
            now,
            AuditAction.ALERT_ACK,
            AuditOutcome.APPROVED if reason is None else AuditOutcome.REFUSED,
            msg.alert_id,
            role=msg.role,
            reason=reason,
        )

    def _on_recovery(self, msg: RecoveryRequest, now: float, out: list[Message]) -> None:
        reason: RejectReason | None = None
        mode = self._car_modes.get(msg.car_id)
        if msg.role not in RECOVERY_ROLES:
            reason = RejectReason.UNAUTHORIZED_ROLE
        elif mode is None:
            reason = RejectReason.UNKNOWN_ID  # no telemetry from this car yet
        elif mode not in RECOVERABLE_MODES:
            reason = RejectReason.INVALID_STATE  # the car is not held
        self._record(
            now,
            AuditAction.RECOVERY_REQUEST,
            AuditOutcome.APPROVED if reason is None else AuditOutcome.REFUSED,
            msg.car_id,
            role=msg.role,
            reason=reason,
            details=mode.value if mode else "",
        )
        if reason is None:
            out.append(RecoveryApproved(t_sim=now, car_id=msg.car_id, approved_by=msg.role))

    # --- HY-04 policy registry -------------------------------------------------------

    def _on_recommended(self, msg: PolicyRecommended, now: float) -> None:
        self._recommendations[msg.run_id] = msg
        self._record(
            now,
            AuditAction.POLICY_RECOMMENDED,
            AuditOutcome.RECORDED,
            msg.run_id,
            details=f"{msg.policy.version} R={msg.policy.R} W={msg.policy.W}",
        )

    def _on_policy_decision(self, msg: PolicyDecision, now: float, out: list[Message]) -> None:
        reason: RejectReason | None = None
        if msg.role not in POLICY_ROLES:
            reason = RejectReason.UNAUTHORIZED_ROLE
        elif msg.run_id not in self._recommendations:
            reason = RejectReason.UNKNOWN_ID
        self._record(
            now,
            AuditAction.POLICY_DECISION,
            AuditOutcome.REFUSED if reason is not None else AuditOutcome.APPROVED,
            msg.run_id,
            role=msg.role,
            reason=reason,
            details="approve" if msg.approve else "reject",
        )
        if reason is None and msg.approve:
            out.append(PolicyActivated(t_sim=now, policy=self._recommendations[msg.run_id].policy))

    # --- helpers ---------------------------------------------------------------------

    def _record(
        self,
        now: float,
        action: AuditAction,
        outcome: AuditOutcome,
        subject: str,
        *,
        role: Role | None = None,
        reason: RejectReason | None = None,
        details: str = "",
    ) -> None:
        self._audit.append(
            AuditEntry(
                t_sim=now,
                action=action,
                outcome=outcome,
                subject=subject,
                role=role,
                reason=reason,
                details=details,
            )
        )


def create() -> ControlCenter:
    """Factory used by the engine (contracts.interfaces.ControlCenterFactory)."""
    return ControlCenter()
