"""Huy's own Control Center tests (HY-01..HY-04).

The contract tests in tests/contract/test_control_center.py are An's exam. These cover what
the exam does not: which reason wins when a request breaks several rules, the audit trail
(SRS 5.3), unknown ids, refused recovery, fire clear, and the message envelope rules.

They call ``handle()`` directly, without the bus, and import only contracts and
control_center (AI_CONTEXT.md).
"""

import pytest

import control_center
from contracts.config import FLOORS, FREIGHT_MAX_KG, Policy
from contracts.enums import (
    AlertKind,
    CabinMode,
    Direction,
    DoorState,
    MissionStatus,
    MissionType,
    RejectReason,
    Role,
)
from contracts.messages import (
    AlarmPressed,
    Alert,
    AlertAcknowledged,
    CabinTelemetry,
    FireAlarm,
    MissionApproval,
    MissionRequest,
    MissionStatusUpdate,
    PolicyActivated,
    PolicyDecision,
    PolicyRecommended,
    RecoveryApproved,
    RecoveryRequest,
)
from control_center.audit import AuditAction, AuditOutcome

MEDICAL, FREIGHT = MissionType.MEDICAL, MissionType.FREIGHT
POLICY = Policy(version="POL-0001", R=2, W=4)


@pytest.fixture
def cc() -> control_center.ControlCenter:
    return control_center.create()


def req(role=Role.MEDICAL_COORDINATOR, mission_type=MEDICAL, origin=2, destination=30, **kw):
    return MissionRequest(
        t_sim=0.0,
        role=role,
        mission_type=mission_type,
        origin=origin,
        destination=destination,
        **kw,
    )


def freight(**kw):
    kw.setdefault("material", "boxes")
    kw.setdefault("weight_kg", 400.0)
    return req(role=Role.BUILDING_STAFF, mission_type=FREIGHT, **kw)


def telemetry(car_id: str, mode: CabinMode) -> CabinTelemetry:
    return CabinTelemetry(
        t_sim=0.0,
        car_id=car_id,
        position=12.0,
        direction=Direction.IDLE,
        moving=False,
        door=DoorState.CLOSED,
        locked=True,
        door_zone=True,
        obstruction=False,
        load_kg=0.0,
        passengers=0,
        mode=mode,
        route_version=0,
    )


def status_of(out) -> tuple[MissionStatus, RejectReason | None]:
    (update,) = [m for m in out if isinstance(m, MissionStatusUpdate)]
    return update.status, update.reason


# --- HY-01: which reason wins ------------------------------------------------------------


@pytest.mark.parametrize(
    ("request_", "reason"),
    [
        (req(role=Role.PASSENGER, origin=None), RejectReason.UNAUTHORIZED_ROLE),
        (req(origin=None, destination=41), RejectReason.MISSING_FIELD),
        (req(origin=0, destination=0), RejectReason.FLOOR_OUT_OF_RANGE),
        (freight(material="", weight_kg=None), RejectReason.INVALID_MATERIAL),
        (freight(material="   "), RejectReason.INVALID_MATERIAL),
        (freight(weight_kg=None, origin=5, destination=5), RejectReason.SAME_ORIGIN_DESTINATION),
    ],
    ids=[
        "role_first",
        "missing_before_range",
        "range_before_same",
        "material_before_weight",
        "blank_material",
        "same_floor_before_freight_fields",
    ],
)
def test_CC_03_D_54_first_broken_rule_is_the_reason(cc, request_, reason):
    out = cc.handle(request_, 0.0)
    assert status_of(out) == (MissionStatus.REJECTED, reason)
    assert not [m for m in out if isinstance(m, MissionApproval)]


def test_CC_03_terminal_floors_are_valid(cc):
    out = cc.handle(req(origin=1, destination=FLOORS), 0.0)
    assert status_of(out) == (MissionStatus.APPROVED, None)


@pytest.mark.parametrize("weight", [0.0, -5.0], ids=["zero", "negative"])
def test_FRT_01_weight_of_zero_or_less_is_rejected(cc, weight):
    # Huy's decision 14/11, not in D-54; reason is provisional until An decides.
    out = cc.handle(freight(weight_kg=weight), 0.0)
    assert status_of(out) == (MissionStatus.REJECTED, RejectReason.MISSING_FIELD)


def test_FRT_01_exact_payload_limit_is_valid_and_carried_in_approval(cc):
    out = cc.handle(freight(weight_kg=FREIGHT_MAX_KG), 0.0)
    (approval,) = [m for m in out if isinstance(m, MissionApproval)]
    assert (approval.material, approval.weight_kg) == ("boxes", FREIGHT_MAX_KG)


def test_CC_01_rejected_request_is_still_listed_with_its_reason(cc):
    cc.handle(req(role=Role.OPERATOR), 0.0)
    (view,) = cc.snapshot()["missions"]
    assert (view["status"], view["reason"]) == ("REJECTED", "UNAUTHORIZED_ROLE")


# --- SRS 5.3 audit trail -----------------------------------------------------------------


def test_5_3_audit_records_approvals_and_refusals_with_role_and_time(cc):
    cc.handle(req(), 1.0)
    cc.handle(req(role=Role.PASSENGER), 2.0)
    first, second = cc.audit_log()
    assert (first.t_sim, first.action, first.outcome, first.role) == (
        1.0,
        AuditAction.MISSION_REQUEST,
        AuditOutcome.APPROVED,
        Role.MEDICAL_COORDINATOR,
    )
    assert (second.t_sim, second.outcome, second.reason) == (
        2.0,
        AuditOutcome.REFUSED,
        RejectReason.UNAUTHORIZED_ROLE,
    )


def test_5_3_audit_records_emergency_alerts(cc):
    cc.handle(AlarmPressed(t_sim=0.0, msg_id="MSG-1", car_id="P1"), 0.0)
    cc.handle(FireAlarm(t_sim=1.0, msg_id="MSG-2", active=True), 1.0)
    cc.handle(Alert(t_sim=2.0, msg_id="MSG-3", kind=AlertKind.FAULT, car_id="P2"), 2.0)
    raised = [
        (e.subject, e.details) for e in cc.audit_log() if e.action == AuditAction.ALERT_RAISED
    ]
    assert raised == [("MSG-1", "ALARM"), ("MSG-2", "FIRE"), ("MSG-3", "FAULT")]


# --- HY-03 alerts ------------------------------------------------------------------------


def test_D_53_ack_of_unknown_alert_is_refused_and_audited(cc):
    assert (
        cc.handle(AlertAcknowledged(t_sim=0.0, alert_id="MSG-404", role=Role.OPERATOR), 0.0) == []
    )
    (entry,) = cc.audit_log()
    assert (entry.action, entry.outcome, entry.reason) == (
        AuditAction.ALERT_ACK,
        AuditOutcome.REFUSED,
        RejectReason.UNKNOWN_ID,
    )


def test_D_53_supervisor_may_acknowledge(cc):
    cc.handle(Alert(t_sim=0.0, msg_id="MSG-1", kind=AlertKind.FAULT, car_id="P2"), 0.0)
    cc.handle(AlertAcknowledged(t_sim=1.0, alert_id="MSG-1", role=Role.SUPERVISOR), 1.0)
    assert cc.snapshot()["alerts"][0]["acknowledged"] is True


def test_D_24_fire_clear_keeps_the_fire_alert_visible(cc):
    cc.handle(FireAlarm(t_sim=0.0, msg_id="MSG-1", active=True), 0.0)
    cc.handle(FireAlarm(t_sim=5.0, msg_id="MSG-2", active=False), 5.0)
    assert [a["kind"] for a in cc.snapshot()["alerts"]] == ["FIRE"]
    assert cc.audit_log()[-1].action == AuditAction.FIRE_CLEARED


def test_MED_04_status_for_unknown_mission_is_ignored(cc):
    cc.handle(
        MissionStatusUpdate(t_sim=0.0, mission_id="MS-9999", status=MissionStatus.UNAVAILABLE), 0.0
    )
    assert cc.snapshot() == {"missions": [], "alerts": []}


# --- HY-03 recovery (CC-04, EMG-04) ------------------------------------------------------


def test_CC_04_recovery_without_telemetry_is_refused(cc):
    assert cc.handle(RecoveryRequest(t_sim=0.0, car_id="P2", role=Role.SUPERVISOR), 0.0) == []
    assert cc.audit_log()[-1].reason == RejectReason.UNKNOWN_ID


def test_EMG_04_recovery_from_maintenance_is_approved(cc):
    cc.handle(telemetry("P3", CabinMode.MAINTENANCE), 0.0)
    (approved,) = cc.handle(RecoveryRequest(t_sim=1.0, car_id="P3", role=Role.SUPERVISOR), 1.0)
    assert isinstance(approved, RecoveryApproved)
    assert (approved.car_id, approved.approved_by) == ("P3", Role.SUPERVISOR)


def test_EMG_04_recovery_for_a_car_back_in_auto_is_refused(cc):
    cc.handle(telemetry("P2", CabinMode.FAULT_HOLD), 0.0)
    cc.handle(telemetry("P2", CabinMode.AUTO), 1.0)
    assert cc.handle(RecoveryRequest(t_sim=2.0, car_id="P2", role=Role.SUPERVISOR), 2.0) == []
    assert cc.audit_log()[-1].reason == RejectReason.INVALID_STATE


# --- HY-04 policy registry (OPT-06) ------------------------------------------------------


def recommend(cc, run_id="RUN-0001"):
    cc.handle(
        PolicyRecommended(t_sim=0.0, run_id=run_id, policy=POLICY, objective=1.0, scenario_set=()),
        0.0,
    )


def test_OPT_06_supervisor_approval_activates_the_recommended_policy(cc):
    recommend(cc)
    decide = PolicyDecision(t_sim=1.0, run_id="RUN-0001", approve=True, role=Role.SUPERVISOR)
    (activated,) = cc.handle(decide, 1.0)
    assert isinstance(activated, PolicyActivated) and activated.policy == POLICY


def test_OPT_06_rejection_is_audited_and_activates_nothing(cc):
    recommend(cc)
    out = cc.handle(
        PolicyDecision(t_sim=1.0, run_id="RUN-0001", approve=False, role=Role.SUPERVISOR), 1.0
    )
    assert out == []
    entry = cc.audit_log()[-1]
    assert (entry.action, entry.outcome, entry.details) == (
        AuditAction.POLICY_DECISION,
        AuditOutcome.APPROVED,
        "reject",
    )


# --- Module rules (AI_CONTEXT.md) --------------------------------------------------------


def test_F4_emitted_messages_follow_the_envelope_rules(cc):
    out = cc.handle(req(), 7.5)
    assert out and all((m.t_sim, m.msg_id, m.deliver_at) == (7.5, "", None) for m in out)


def test_F4_snapshot_is_a_copy(cc):
    cc.handle(req(), 0.0)
    snap = cc.snapshot()
    snap["missions"][0]["status"] = "HACKED"
    snap["missions"][0]["candidates"].append({"car_id": "X"})
    assert cc.snapshot()["missions"][0]["status"] == "APPROVED"
    assert cc.snapshot()["missions"][0]["candidates"] == []
