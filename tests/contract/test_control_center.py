"""Contract tests for the Control Center (Huy). Written from SRS numbers before the module exists.

The real ``control_center`` package runs on the bus, alone or with FakeCabins. Tests play
the UI (requests, decisions) and the other modules (status updates, alerts). They look only
at emitted messages and ``snapshot()``. Skipped until ``control_center/`` exists.
``pytest --fakes`` runs the ``fake_ok`` ones against fakes/control_center.py.
"""

import json

import pytest

from contracts.config import Policy
from contracts.enums import (
    AlertKind,
    ExclusionReason,
    FaultKind,
    MissionStatus,
    MissionType,
    RejectReason,
    Role,
)
from contracts.messages import (
    AlarmPressed,
    Alert,
    AlertAcknowledged,
    CabinInit,
    Candidate,
    FaultInjected,
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
from contracts.ui import AlertView, ControlCenterSnapshot, MissionView
from fakes import cabin as fake_cabin
from tests.contract.helpers import log_text, make_bus, of_type

MEDICAL, FREIGHT = MissionType.MEDICAL, MissionType.FREIGHT
NEW_POLICY = Policy(version="POL-0001", R=2, W=4)


def cc_bus(cc_impl, *cabin_inits: CabinInit):
    cc = cc_impl.create()
    return make_bus(cc, *[fake_cabin.create(i) for i in cabin_inits]), cc


def request(t, mission_type, role, origin=2, destination=30, **extra) -> MissionRequest:
    if mission_type == FREIGHT:
        extra.setdefault("material", "boxes")
        extra.setdefault("weight_kg", 400.0)
    return MissionRequest(
        t_sim=t,
        role=role,
        mission_type=mission_type,
        origin=origin,
        destination=destination,
        **extra,
    )


def medical(t=0.0, **kw) -> MissionRequest:
    return request(t, MEDICAL, Role.MEDICAL_COORDINATOR, **kw)


def freight(t=0.0, **kw) -> MissionRequest:
    return request(t, FREIGHT, Role.BUILDING_STAFF, **kw)


def updates(bus, mission_id="MS-0001") -> list[MissionStatusUpdate]:
    return of_type(bus.delivered, MissionStatusUpdate, mission_id=mission_id)


def mission_view(cc, mission_id="MS-0001") -> dict:
    return next(m for m in cc.snapshot()["missions"] if m["mission_id"] == mission_id)


def alerts(cc, kind=None) -> list[dict]:
    return [a for a in cc.snapshot()["alerts"] if kind is None or a["kind"] == kind]


# ======================================================================================
# Roles and validation (HY-01)
# ======================================================================================


@pytest.mark.parametrize(
    ("mission_type", "role", "allowed"),
    [
        (MEDICAL, Role.PASSENGER, False),
        (MEDICAL, Role.BUILDING_STAFF, False),
        (FREIGHT, Role.MEDICAL_COORDINATOR, False),
        (MEDICAL, Role.MEDICAL_COORDINATOR, True),
        (FREIGHT, Role.BUILDING_STAFF, True),
    ],
    ids=["med_by_passenger", "med_by_staff", "freight_by_medical", "med_ok", "freight_ok"],
)
def test_CC_01_D_53_role_matrix(cc_impl, mission_type, role, allowed):
    bus, cc = cc_bus(cc_impl)
    bus.inject(request(0.0, mission_type, role))
    bus.run_until(1.0)
    approvals = of_type(bus.delivered, MissionApproval)
    if allowed:
        assert [u.status for u in updates(bus)] == [MissionStatus.APPROVED]
        assert len(approvals) == 1
        return
    (update,) = updates(bus)
    assert (update.status, update.reason) == (
        MissionStatus.REJECTED,
        RejectReason.UNAUTHORIZED_ROLE,
    )
    assert not approvals  # CC-01: a rejected grant changes nothing downstream
    assert mission_view(cc)["status"] == MissionStatus.REJECTED


@pytest.mark.fake_ok
@pytest.mark.parametrize("missing", ["origin", "destination"])
def test_CC_03_missing_floor_rejected(cc_impl, missing):
    bus, cc = cc_bus(cc_impl)
    bus.inject(medical(**{missing: None}))
    bus.run_until(1.0)
    (update,) = updates(bus)
    assert (update.status, update.reason) == (MissionStatus.REJECTED, RejectReason.MISSING_FIELD)
    assert not of_type(bus.delivered, MissionApproval)
    assert mission_view(cc)["reason"] == RejectReason.MISSING_FIELD


@pytest.mark.parametrize(
    ("req", "reason"),
    [
        (medical(origin=0), RejectReason.FLOOR_OUT_OF_RANGE),
        (medical(destination=41), RejectReason.FLOOR_OUT_OF_RANGE),
        (medical(origin=2, destination=2), RejectReason.SAME_ORIGIN_DESTINATION),
        (freight(material=None), RejectReason.INVALID_MATERIAL),
        (freight(material=""), RejectReason.INVALID_MATERIAL),
        (freight(weight_kg=None), RejectReason.MISSING_FIELD),
        (freight(weight_kg=1001.0), RejectReason.OVER_PAYLOAD),
    ],
    ids=[
        "origin_0",
        "destination_41",
        "same_floor",
        "no_material",
        "empty_material",
        "no_weight",
        "1001kg",
    ],
)
def test_CC_03_FRT_01_D_54_invalid_requests_rejected(cc_impl, req, reason):
    bus, _ = cc_bus(cc_impl)
    bus.inject(req)
    bus.run_until(1.0)
    (update,) = updates(bus)
    assert (update.status, update.reason) == (MissionStatus.REJECTED, reason)
    assert not of_type(bus.delivered, MissionApproval)


@pytest.mark.fake_ok
def test_D_54_freight_at_exactly_max_load_is_valid(cc_impl):
    bus, _ = cc_bus(cc_impl)
    bus.inject(freight(weight_kg=1000.0))
    bus.run_until(1.0)
    assert [u.status for u in updates(bus)] == [MissionStatus.APPROVED]
    assert len(of_type(bus.delivered, MissionApproval)) == 1


@pytest.mark.fake_ok
def test_CC_03_D_20_D_21_approval_lists_capable_cars(cc_impl):
    bus, _ = cc_bus(cc_impl)
    bus.inject(medical(0.0))
    bus.inject(freight(0.0, origin=1, destination=30))
    bus.run_until(1.0)
    med, frt = of_type(bus.delivered, MissionApproval)
    assert (med.mission_type, med.origin, med.destination) == (MEDICAL, 2, 30)
    assert med.allowed_cars == ("P1", "P2", "P3", "P4")
    assert set(frt.allowed_cars) == {"P1", "P2", "P3", "P4", "G1"}
    assert (frt.material, frt.weight_kg) == ("boxes", 400.0)


# ======================================================================================
# Mission lifecycle and UNAVAILABLE (HY-02)
# ======================================================================================


@pytest.mark.fake_ok
def test_MED_04_EMG_05_UI_04_unavailable_is_persistent_and_alerted_once(cc_impl):
    bus, cc = cc_bus(cc_impl)
    bus.inject(medical(0.0))
    bus.run_until(1.0)
    for t in (1.0, 2.0):
        bus.inject(
            MissionStatusUpdate(t_sim=t, mission_id="MS-0001", status=MissionStatus.UNAVAILABLE)
        )
    bus.run_until(600.0)
    view = mission_view(cc)
    assert (view["status"], view["car_id"]) == (MissionStatus.UNAVAILABLE, None)
    assert len(alerts(cc, AlertKind.UNAVAILABLE)) == 1


@pytest.mark.fake_ok
def test_HY_02_status_and_candidates_are_tracked(cc_impl):
    bus, cc = cc_bus(cc_impl)
    bus.inject(medical(0.0))
    bus.run_until(1.0)
    candidates = (
        Candidate(car_id="P2", eta_s=20.0),
        Candidate(car_id="P3", excluded=ExclusionReason.MAINTENANCE),
    )
    bus.inject(
        MissionStatusUpdate(
            t_sim=1.0,
            mission_id="MS-0001",
            status=MissionStatus.RESERVED,
            car_id="P2",
            candidates=candidates,
        )
    )
    bus.run_until(1.0)
    view = mission_view(cc)
    assert (view["status"], view["car_id"]) == (MissionStatus.RESERVED, "P2")
    assert view["candidates"] == [
        {"car_id": "P2", "eta_s": 20.0, "excluded": None},
        {"car_id": "P3", "eta_s": None, "excluded": ExclusionReason.MAINTENANCE},
    ]
    for t, status in ((2.0, MissionStatus.IN_PROGRESS), (3.0, MissionStatus.COMPLETED)):
        bus.inject(MissionStatusUpdate(t_sim=t, mission_id="MS-0001", status=status, car_id="P2"))
        bus.run_until(t)
        assert mission_view(cc)["status"] == status


@pytest.mark.fake_ok
def test_D_29_control_center_sends_only_approved_or_rejected(cc_impl):
    bus, _ = cc_bus(cc_impl)
    bus.inject(medical(0.0))
    bus.inject(medical(0.0, origin=None))
    bus.run_until(1.0)
    injected = {
        bus.inject(
            MissionStatusUpdate(t_sim=1.0, mission_id="MS-0001", status=status, car_id="P2")
        ).msg_id
        for status in (MissionStatus.RESERVED, MissionStatus.IN_PROGRESS)
    }
    bus.run_until(5.0)
    own = [u for u in of_type(bus.delivered, MissionStatusUpdate) if u.msg_id not in injected]
    assert own and {u.status for u in own} <= {MissionStatus.APPROVED, MissionStatus.REJECTED}


# ======================================================================================
# Alerts and recovery (HY-03)
# ======================================================================================


@pytest.mark.fake_ok
def test_EMG_01_alarm_gives_one_alarm_alert_and_never_a_mission(cc_impl):
    bus, cc = cc_bus(cc_impl)
    bus.inject(AlarmPressed(t_sim=0.0, car_id="P4"))
    bus.run_until(5.0)
    assert [(a["kind"], a["car_id"]) for a in alerts(cc)] == [(AlertKind.ALARM, "P4")]
    assert not of_type(bus.delivered, MissionApproval)
    assert not of_type(bus.delivered, MissionStatusUpdate)


@pytest.mark.fake_ok
def test_HY_03_module_alert_is_shown_and_can_be_acknowledged(cc_impl):
    bus, cc = cc_bus(cc_impl)
    fault = bus.inject(Alert(t_sim=0.0, kind=AlertKind.FAULT, car_id="P2", details="MOTION"))
    bus.run_until(0.0)
    (shown,) = alerts(cc)
    assert (shown["alert_id"], shown["kind"], shown["acknowledged"]) == (
        fault.msg_id,
        AlertKind.FAULT,
        False,
    )
    bus.inject(AlertAcknowledged(t_sim=1.0, alert_id=fault.msg_id, role=Role.OPERATOR))
    bus.run_until(1.0)
    assert alerts(cc)[0]["acknowledged"] is True


def test_D_53_passenger_cannot_acknowledge_alerts(cc_impl):
    bus, cc = cc_bus(cc_impl)
    fault = bus.inject(Alert(t_sim=0.0, kind=AlertKind.FAULT, car_id="P2"))
    bus.inject(AlertAcknowledged(t_sim=1.0, alert_id=fault.msg_id, role=Role.PASSENGER))
    bus.run_until(1.0)
    assert alerts(cc)[0]["acknowledged"] is False


@pytest.mark.fake_ok
def test_UI_fire_alarm_is_shown(cc_impl):
    bus, cc = cc_bus(cc_impl)
    bus.inject(FireAlarm(t_sim=0.0, active=True))
    bus.run_until(1.0)
    assert [a["kind"] for a in alerts(cc)] == [AlertKind.FIRE]


def test_CC_04_EMG_04_only_a_supervisor_recovers_a_held_car(cc_impl):
    fleet = (CabinInit(car_id="P1", floor=31), CabinInit(car_id="P2", floor=12))
    bus, _ = cc_bus(cc_impl, *fleet)
    bus.inject(FaultInjected(t_sim=0.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS))
    bus.inject(RecoveryRequest(t_sim=1.0, car_id="P2", role=Role.OPERATOR))
    bus.run_until(1.5)
    assert not of_type(bus.delivered, RecoveryApproved)
    bus.inject(RecoveryRequest(t_sim=2.0, car_id="P2", role=Role.SUPERVISOR))
    bus.inject(RecoveryRequest(t_sim=3.0, car_id="P1", role=Role.SUPERVISOR))  # P1 is fine
    bus.run_until(4.0)
    (approved,) = of_type(bus.delivered, RecoveryApproved)
    assert (approved.car_id, approved.approved_by) == ("P2", Role.SUPERVISOR)


# ======================================================================================
# Policy approval (HY-04)
# ======================================================================================


def _recommend(bus):
    bus.inject(
        PolicyRecommended(
            t_sim=0.0,
            run_id="RUN-0001",
            policy=NEW_POLICY,
            objective=12.5,
            scenario_set=("train-1", "train-2"),
        )
    )


@pytest.mark.fake_ok
def test_OPT_06_only_an_approved_known_run_activates(cc_impl):
    bus, _ = cc_bus(cc_impl)
    _recommend(bus)
    bus.inject(PolicyDecision(t_sim=1.0, run_id="RUN-0001", approve=False, role=Role.SUPERVISOR))
    bus.inject(PolicyDecision(t_sim=2.0, run_id="RUN-0099", approve=True, role=Role.SUPERVISOR))
    bus.run_until(2.5)
    assert not of_type(bus.delivered, PolicyActivated)
    bus.inject(PolicyDecision(t_sim=3.0, run_id="RUN-0001", approve=True, role=Role.SUPERVISOR))
    bus.run_until(3.0)
    (activated,) = of_type(bus.delivered, PolicyActivated)
    assert activated.policy == NEW_POLICY


def test_OPT_06_D_53_only_a_supervisor_may_approve(cc_impl):
    bus, _ = cc_bus(cc_impl)
    _recommend(bus)
    bus.inject(PolicyDecision(t_sim=1.0, run_id="RUN-0001", approve=True, role=Role.OPERATOR))
    bus.run_until(2.0)
    assert not of_type(bus.delivered, PolicyActivated)


# ======================================================================================
# Determinism and shape
# ======================================================================================


@pytest.mark.fake_ok
def test_NFR_05_same_inputs_same_log(cc_impl):
    def one_run():
        bus, _ = cc_bus(cc_impl)
        bus.inject(medical(0.0))
        bus.inject(AlarmPressed(t_sim=1.0, car_id="P1"))
        _recommend(bus)
        bus.run_until(10.0)
        return log_text(bus)

    assert one_run() == one_run()


@pytest.mark.fake_ok
def test_F4_snapshot_shape(cc_impl):
    bus, cc = cc_bus(cc_impl)
    bus.inject(medical(0.0))
    bus.inject(AlarmPressed(t_sim=0.0, car_id="P4"))
    bus.run_until(1.0)
    snap = cc.snapshot()
    assert set(snap) == set(ControlCenterSnapshot.__annotations__)
    assert set(snap["missions"][0]) == set(MissionView.__annotations__)
    assert set(snap["alerts"][0]) == set(AlertView.__annotations__)
    json.dumps(snap)
