"""Audit trail of Control Center decisions (HY-01, SRS 5.3).

SRS 5.3: "The Control Center logs approvals, refusals, emergency alerts and route-version
decisions with timestamps." Route-version decisions belong to Dispatch and Cabin and are in
the engine's message log. This module records the Control Center's own part.

The engine already logs every message (including roles and reject reasons), so that log is
the primary audit evidence. This list exists so Control Center decisions that send NO
message (an ignored acknowledgement, a refused recovery) are still recorded and testable.
It is not part of ``snapshot()``: the snapshot shape is fixed by contracts.ui and checked by
``tests/contract/test_control_center.py::test_F4_snapshot_shape``.

Ordinary Hall Calls never reach Control Center, so no passenger identity is recorded (5.3).
"""

from dataclasses import dataclass
from enum import StrEnum

from contracts.enums import RejectReason, Role


class AuditAction(StrEnum):
    MISSION_REQUEST = "MISSION_REQUEST"
    ALERT_RAISED = "ALERT_RAISED"  # emergency and operator alerts (ALARM, FIRE, FAULT, ...)
    ALERT_ACK = "ALERT_ACK"
    FIRE_CLEARED = "FIRE_CLEARED"
    RECOVERY_REQUEST = "RECOVERY_REQUEST"
    POLICY_RECOMMENDED = "POLICY_RECOMMENDED"
    POLICY_DECISION = "POLICY_DECISION"


class AuditOutcome(StrEnum):
    APPROVED = "APPROVED"  # the privileged action was granted
    REFUSED = "REFUSED"  # the action was refused; ``reason`` says why
    RECORDED = "RECORDED"  # an event to remember, nothing to grant (alerts, fire cleared)


@dataclass(frozen=True, kw_only=True)
class AuditEntry:
    t_sim: float
    action: AuditAction
    outcome: AuditOutcome
    subject: str  # mission_id, alert_id, car_id or run_id
    role: Role | None = None
    reason: RejectReason | None = None
    details: str = ""
