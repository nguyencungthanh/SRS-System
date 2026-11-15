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
from control_center.audit import AuditAction, AuditOutcome, PolicyRunStatus

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
        AuditOutcome.REJECTED,  # was APPROVED: the earlier test had locked in bug F12
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


# --- verification findings F2, F16 (15/11) -----------------------------------------------


@pytest.mark.parametrize(
    ("weight", "reason"),
    [(float("nan"), RejectReason.MISSING_FIELD), (float("inf"), RejectReason.OVER_PAYLOAD)],
    ids=["nan", "infinity"],
)
def test_FRT_01_non_finite_weight_fails_closed(cc, weight, reason):
    out = cc.handle(freight(weight_kg=weight), 0.0)
    assert status_of(out) == (MissionStatus.REJECTED, reason)


@pytest.mark.parametrize(
    "material", ["\u200b", "\u200b \t", "\u00a0"], ids=["zwsp", "zwsp_ws", "nbsp"]
)
def test_FRT_01_invisible_material_is_invalid(cc, material):
    out = cc.handle(freight(material=material), 0.0)
    assert status_of(out) == (MissionStatus.REJECTED, RejectReason.INVALID_MATERIAL)


def test_FRT_01_material_is_stripped_in_the_approval(cc):
    out = cc.handle(freight(material="  boxes  "), 0.0)
    (approval,) = [m for m in out if isinstance(m, MissionApproval)]
    assert approval.material == "boxes"


# --- verification finding F11 (15/11) ----------------------------------------------------


def approved_mission(cc) -> str:
    cc.handle(req(), 0.0)
    return cc.snapshot()["missions"][0]["mission_id"]


def update(mission_id, status, **kw):
    return MissionStatusUpdate(t_sim=0.0, mission_id=mission_id, status=status, **kw)


def test_CC_02_unavailable_after_reserved_shows_no_car_and_fresh_fields(cc):
    mid = approved_mission(cc)
    cc.handle(update(mid, MissionStatus.RESERVED, car_id="P2"), 1.0)
    cc.handle(update(mid, MissionStatus.UNAVAILABLE, reason=RejectReason.INVALID_STATE), 2.0)
    view = cc.snapshot()["missions"][0]
    assert (view["status"], view["car_id"], view["reason"]) == (
        "UNAVAILABLE",
        None,
        "INVALID_STATE",
    )
    cc.handle(update(mid, MissionStatus.RESERVED, car_id="P3"), 3.0)
    view = cc.snapshot()["missions"][0]
    assert (view["car_id"], view["reason"], view["candidates"]) == ("P3", None, [])


@pytest.mark.parametrize("final", [MissionStatus.COMPLETED, MissionStatus.FAILED])
def test_CC_02_finished_mission_never_changes(cc, final):
    mid = approved_mission(cc)
    cc.handle(update(mid, final, car_id="P1"), 1.0)
    cc.handle(update(mid, MissionStatus.IN_PROGRESS, car_id="P4"), 2.0)
    view = cc.snapshot()["missions"][0]
    assert (view["status"], view["car_id"]) == (final.value, "P1")


def test_CC_01_rejected_mission_cannot_be_moved_on(cc):
    cc.handle(req(role=Role.PASSENGER), 0.0)
    mid = cc.snapshot()["missions"][0]["mission_id"]
    cc.handle(update(mid, MissionStatus.IN_PROGRESS, car_id="P1"), 1.0)
    assert cc.snapshot()["missions"][0]["status"] == "REJECTED"


# --- verification finding F12 (15/11): approval status per run ---------------------------


def decide(cc, run_id, approve, t=1.0, role=Role.SUPERVISOR):
    return cc.handle(PolicyDecision(t_sim=t, run_id=run_id, approve=approve, role=role), t)


def test_OPT_06_run_status_follows_the_decisions(cc):
    recommend(cc, "RUN-0001")
    recommend(cc, "RUN-0002")
    assert cc.policy_runs() == {
        "RUN-0001": PolicyRunStatus.PENDING,
        "RUN-0002": PolicyRunStatus.PENDING,
    }
    decide(cc, "RUN-0001", approve=True)
    decide(cc, "RUN-0002", approve=True, t=2.0)
    assert cc.policy_runs() == {
        "RUN-0001": PolicyRunStatus.SUPERSEDED,
        "RUN-0002": PolicyRunStatus.ACTIVE,
    }


def test_OPT_06_active_run_is_not_activated_twice(cc):
    recommend(cc)
    assert [type(m) for m in decide(cc, "RUN-0001", approve=True)] == [PolicyActivated]
    assert decide(cc, "RUN-0001", approve=True, t=2.0) == []
    entry = cc.audit_log()[-1]
    assert (entry.outcome, entry.reason) == (AuditOutcome.REFUSED, RejectReason.INVALID_STATE)


def test_OPT_06_only_a_pending_run_can_be_rejected(cc):
    recommend(cc)
    decide(cc, "RUN-0001", approve=True)
    assert decide(cc, "RUN-0001", approve=False, t=2.0) == []
    assert cc.policy_runs()["RUN-0001"] == PolicyRunStatus.ACTIVE


def test_OPT_06_repeated_run_id_does_not_overwrite_the_recommendation(cc):
    recommend(cc)
    other = Policy(version="POL-0099", R=9, W=9)
    cc.handle(
        PolicyRecommended(
            t_sim=1.0, run_id="RUN-0001", policy=other, objective=0.0, scenario_set=()
        ),
        1.0,
    )
    (activated,) = decide(cc, "RUN-0001", approve=True, t=2.0)
    assert activated.policy == POLICY


# --- verification finding F13 (15/11): bounded snapshot ----------------------------------


def test_UI_04_finished_missions_are_capped_but_open_ones_are_kept(cc):
    from control_center.center import MAX_FINISHED_MISSIONS

    open_id = approved_mission(cc)  # stays APPROVED (not finished)
    for i in range(MAX_FINISHED_MISSIONS + 50):
        cc.handle(req(role=Role.PASSENGER), float(i))  # REJECTED = finished
    missions = cc.snapshot()["missions"]
    assert missions[0]["mission_id"] == open_id
    assert sum(m["status"] == "REJECTED" for m in missions) == MAX_FINISHED_MISSIONS
    assert missions[-1]["mission_id"] == f"MS-{MAX_FINISHED_MISSIONS + 51:04d}"  # newest kept


def test_EMG_05_unacknowledged_alerts_are_never_dropped(cc):
    from control_center.center import MAX_ACKED_ALERTS

    for i in range(MAX_ACKED_ALERTS + 30):
        cc.handle(Alert(t_sim=0.0, msg_id=f"MSG-{i}", kind=AlertKind.FAULT, car_id="P1"), 0.0)
        if i >= 10:
            cc.handle(AlertAcknowledged(t_sim=0.0, alert_id=f"MSG-{i}", role=Role.OPERATOR), 0.0)
    alerts = cc.snapshot()["alerts"]
    assert [a["alert_id"] for a in alerts if not a["acknowledged"]] == [
        f"MSG-{i}" for i in range(10)
    ]
    assert sum(a["acknowledged"] for a in alerts) == MAX_ACKED_ALERTS


# --- D-29 / D-62 (finalize, 15/11): status order and re-dispatch after a failure ---------


def test_D_29_late_reserved_does_not_hide_draining(cc):
    mid = approved_mission(cc)
    cc.handle(update(mid, MissionStatus.DRAINING, car_id="P1"), 0.0)
    cc.handle(update(mid, MissionStatus.RESERVED, car_id="P1"), 0.0)  # Dispatch's, after the ACK
    assert cc.snapshot()["missions"][0]["status"] == "DRAINING"


def test_D_29_hold_can_resume_in_progress(cc):
    mid = approved_mission(cc)
    cc.handle(update(mid, MissionStatus.IN_PROGRESS, car_id="P1"), 1.0)
    cc.handle(update(mid, MissionStatus.HOLD_PENDING_REVIEW, car_id="P1"), 2.0)
    cc.handle(update(mid, MissionStatus.IN_PROGRESS, car_id="P1"), 3.0)
    assert cc.snapshot()["missions"][0]["status"] == "IN_PROGRESS"


@pytest.mark.parametrize("next_status", [MissionStatus.RESERVED, MissionStatus.UNAVAILABLE])
def test_D_62_failed_car_then_dispatch_starts_a_new_attempt(cc, next_status):
    mid = approved_mission(cc)
    cc.handle(update(mid, MissionStatus.RESERVED, car_id="P2"), 0.0)
    cc.handle(update(mid, MissionStatus.FAILED, car_id="P2"), 3.0)
    new_car = "P3" if next_status == MissionStatus.RESERVED else None
    cc.handle(update(mid, next_status, car_id=new_car), 3.0)
    view = cc.snapshot()["missions"][0]
    assert (view["status"], view["car_id"]) == (next_status.value, new_car)


def test_D_62_failed_stays_failed_for_the_same_car(cc):
    mid = approved_mission(cc)
    cc.handle(update(mid, MissionStatus.FAILED, car_id="P2"), 3.0)
    cc.handle(update(mid, MissionStatus.RESERVED, car_id="P2"), 4.0)
    assert cc.snapshot()["missions"][0]["status"] == "FAILED"
