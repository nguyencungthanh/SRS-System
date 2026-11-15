"""Message-level regressions for ownership, mission handoff and policy boundaries."""

from dataclasses import replace

import pytest

from contracts.config import CARS, DEFAULT_POLICY, Policy
from contracts.enums import (
    CabinMode,
    CallStatus,
    Direction,
    MissionStatus,
    MissionType,
    NackReason,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.messages import (
    HallCallAck,
    HallCallPressed,
    MissionApproval,
    MissionStatusUpdate,
    PolicyActivated,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    ServiceEvent,
    Timer,
)
from dispatch import create
from tests.dispatch.helpers import telemetry


def deliver(module, *messages):
    output = []
    for message in messages:
        output.extend(module.handle(message, message.t_sim))
    return output


def emitted(messages, kind):
    return [message for message in messages if isinstance(message, kind)]


def press(t=0.0, floor=25, direction=Direction.DOWN):
    return HallCallPressed(t_sim=t, floor=floor, direction=direction)


def acknowledge(proposal, *, accepted=True, car_id=None, t=None):
    return RouteReply(
        t_sim=proposal.t_sim if t is None else t,
        command_id=proposal.command_id,
        car_id=car_id or proposal.car_id,
        accepted=accepted,
        committed_version=proposal.expected_route_version + int(accepted),
        reason=None if accepted else NackReason.FAULT_HOLD,
    )


def call_view(module, request_id="HC-0001"):
    return next(call for call in module.snapshot()["calls"] if call["request_id"] == request_id)


def approval(t=0.0, mission_id="MS-0001"):
    return MissionApproval(
        t_sim=t,
        mission_id=mission_id,
        role=Role.MEDICAL_COORDINATOR,
        mission_type=MissionType.MEDICAL,
        origin=2,
        destination=30,
        allowed_cars=tuple(car for car in CARS if car != "G1"),
    )


def test_REQ_02_served_floor_direction_can_start_a_new_canonical_call():
    module = create()
    output = deliver(module, telemetry(30.0), press())
    (proposal,) = emitted(output, RouteProposal)
    deliver(
        module,
        acknowledge(proposal),
        ServiceEvent(
            t_sim=10.0,
            car_id="P1",
            event=ServiceEventType.HALL_CALL_SERVED,
            floor=25,
            ref_id="HC-0001",
        ),
    )

    (ack,) = emitted(deliver(module, press(11.0)), HallCallAck)

    assert (ack.request_id, ack.duplicate) == ("HC-0002", False)
    assert call_view(module)["status"] == CallStatus.SERVED
    assert call_view(module, "HC-0002")["created_at"] == 11.0


def test_REQ_02_duplicate_old_pickup_does_not_remove_a_new_active_call():
    module = create()
    (proposal,) = emitted(deliver(module, telemetry(30.0), press()), RouteProposal)
    pickup_event = ServiceEvent(
        t_sim=10.0,
        car_id="P1",
        event=ServiceEventType.HALL_CALL_SERVED,
        floor=25,
        ref_id="HC-0001",
    )
    deliver(module, acknowledge(proposal), pickup_event, press(11.0))

    deliver(module, replace(pickup_event, t_sim=12.0))
    (ack,) = emitted(deliver(module, press(13.0)), HallCallAck)

    assert (ack.request_id, ack.duplicate) == ("HC-0002", True)
    assert len(module.snapshot()["calls"]) == 2


def test_REQ_04_nack_suppression_survives_unchanged_heartbeats():
    module = create()
    tel = telemetry(30.0)
    (proposal,) = emitted(deliver(module, tel, press()), RouteProposal)

    output = deliver(module, acknowledge(proposal, accepted=False))
    output += deliver(module, replace(tel, t_sim=1.0), replace(tel, t_sim=2.0))

    assert not emitted(output, RouteProposal)
    assert call_view(module)["status"] == CallStatus.PENDING
    # A new route version is an actual state change and permits reconsideration.
    assert emitted(deliver(module, replace(tel, t_sim=3.0, route_version=1)), RouteProposal)


def test_REQ_03_wrong_car_ack_cannot_confirm_or_release_a_reservation():
    module = create()
    (proposal,) = emitted(deliver(module, telemetry(30.0), press()), RouteProposal)

    output = deliver(
        module,
        acknowledge(proposal, car_id="P2"),
        acknowledge(proposal, accepted=False, car_id="P2"),
    )

    assert not emitted(output, RouteProposal)
    assert (call_view(module)["status"], call_view(module)["owner"]) == (
        CallStatus.RESERVED,
        "P1",
    )


def test_REQ_04_timeout_keeps_owner_until_matching_authoritative_report():
    module = create()
    output = deliver(module, telemetry(30.0), telemetry(1.0, car_id="P2"), press())
    (proposal,) = emitted(output, RouteProposal)
    timeout = next(timer for timer in emitted(output, Timer) if timer.ref_id == proposal.command_id)

    output = module.handle(timeout, proposal.expiry_ts)
    assert len(emitted(output, RouteVersionQuery)) == 1
    assert not emitted(output, RouteProposal)
    assert call_view(module)["status"] == CallStatus.RESERVED
    wrong = RouteVersionReport(
        t_sim=proposal.expiry_ts,
        command_id=proposal.command_id,
        car_id="P2",
        seen=False,
        accepted=None,
        route_version=0,
    )
    assert not emitted(deliver(module, wrong), RouteProposal)
    assert call_view(module)["owner"] == "P1"

    output = deliver(module, replace(wrong, car_id="P1", seen=True, accepted=True, route_version=1))

    assert not emitted(output, RouteProposal)
    assert (call_view(module)["status"], call_view(module)["owner"]) == (
        CallStatus.ASSIGNED,
        "P1",
    )


def test_REQ_04_late_ack_from_rejected_command_cannot_steal_new_reservation():
    module = create()
    (first,) = emitted(
        deliver(module, telemetry(30.0), telemetry(1.0, car_id="P2"), press()), RouteProposal
    )
    (second,) = emitted(deliver(module, acknowledge(first, accepted=False)), RouteProposal)
    assert second.car_id == "P2"

    output = deliver(module, acknowledge(first, t=0.1))

    assert not emitted(output, RouteProposal)
    assert (call_view(module)["status"], call_view(module)["owner"]) == (
        CallStatus.RESERVED,
        "P2",
    )


def test_REQ_03_unknown_command_reply_has_no_effect():
    module = create()
    (proposal,) = emitted(deliver(module, telemetry(30.0), press()), RouteProposal)

    output = deliver(module, replace(acknowledge(proposal), command_id="RC-unknown"))

    assert not emitted(output, RouteProposal)
    assert call_view(module)["status"] == CallStatus.RESERVED


@pytest.mark.parametrize("version", [0, 2], ids=["unchanged_version", "skipped_version"])
def test_REQ_03_ack_must_confirm_the_exact_proposed_route_version(version):
    module = create()
    (proposal,) = emitted(deliver(module, telemetry(30.0), press()), RouteProposal)

    output = deliver(module, replace(acknowledge(proposal), committed_version=version))

    assert not emitted(output, RouteProposal)
    assert call_view(module)["status"] == CallStatus.RESERVED
    deliver(module, acknowledge(proposal))
    assert call_view(module)["status"] == CallStatus.ASSIGNED


def test_MOVE_06_ack_does_not_fabricate_current_position_before_accepted_telemetry():
    module = create()
    initial = telemetry(40.0)
    (proposal,) = emitted(deliver(module, initial, press(floor=37)), RouteProposal)
    deliver(module, acknowledge(proposal, t=1.5))

    output = deliver(module, press(1.6, floor=39))

    # Acceptance at t=1.5 starts motion from 40. The actual position is 39.95
    # at t=1.6, leaving only 0.95 floors to 39. Reusing the pre-ACK idle
    # position would incorrectly permit this insertion at exactly one floor.
    assert not emitted(output, RouteProposal)
    assert call_view(module, "HC-0002")["status"] == CallStatus.PENDING
    output = deliver(
        module,
        replace(
            initial,
            t_sim=1.6,
            position=39.95,
            direction=Direction.DOWN,
            moving=True,
            door_zone=False,
            route_version=1,
            stops=proposal.ordered_stops,
        ),
    )
    assert not emitted(output, RouteProposal)


def test_REQ_04_accepted_report_cannot_confirm_an_uncommitted_route_version():
    module = create()
    output = deliver(module, telemetry(30.0), press())
    (proposal,) = emitted(output, RouteProposal)
    timeout = next(timer for timer in emitted(output, Timer) if timer.ref_id == proposal.command_id)
    module.handle(timeout, proposal.expiry_ts)
    report = RouteVersionReport(
        t_sim=proposal.expiry_ts,
        command_id=proposal.command_id,
        car_id=proposal.car_id,
        seen=True,
        accepted=True,
        route_version=proposal.expected_route_version,
    )

    output = deliver(module, report)

    assert not emitted(output, RouteProposal)
    assert call_view(module)["status"] == CallStatus.RESERVED
    deliver(module, replace(report, route_version=proposal.expected_route_version + 1))
    assert call_view(module)["status"] == CallStatus.ASSIGNED


def test_REQ_01_served_pickup_is_not_resurrected_before_stop_removal_telemetry():
    module = create()
    initial = telemetry(30.0)
    (proposal,) = emitted(deliver(module, initial, press(floor=29)), RouteProposal)
    deliver(
        module,
        acknowledge(proposal),
        replace(initial, t_sim=2.0, position=29.0, route_version=1, stops=proposal.ordered_stops),
        ServiceEvent(
            t_sim=2.0,
            car_id="P1",
            event=ServiceEventType.HALL_CALL_SERVED,
            floor=29,
            ref_id="HC-0001",
        ),
    )

    output = deliver(module, press(2.0, floor=27))

    assert not emitted(output, RouteProposal)
    output = deliver(module, replace(initial, t_sim=2.0, position=29.0, route_version=2))
    (next_route,) = emitted(output, RouteProposal)
    assert next_route.expected_route_version == 2
    assert [stop.ref_id for stop in next_route.ordered_stops] == ["HC-0002"]


def test_REQ_01_pickup_completion_requires_the_owner_and_origin():
    module = create()
    (proposal,) = emitted(deliver(module, telemetry(30.0), press()), RouteProposal)
    deliver(module, acknowledge(proposal))
    pickup_event = ServiceEvent(
        t_sim=10.0,
        car_id="P1",
        event=ServiceEventType.HALL_CALL_SERVED,
        floor=25,
        ref_id="HC-0001",
    )

    deliver(module, replace(pickup_event, car_id="P2"), replace(pickup_event, floor=24))

    assert call_view(module)["status"] == CallStatus.ASSIGNED
    deliver(module, pickup_event)
    assert call_view(module)["status"] == CallStatus.SERVED


def test_EMG_03_old_telemetry_cannot_release_a_confirmed_call():
    module = create()
    initial = telemetry(30.0)
    (proposal,) = emitted(deliver(module, initial, press()), RouteProposal)
    committed = replace(initial, t_sim=1.0, route_version=1, stops=proposal.ordered_stops)
    deliver(module, acknowledge(proposal), committed)

    output = module.handle(replace(initial, t_sim=0.5, mode=CabinMode.FAULT_HOLD), 1.5)

    assert not emitted(output, RouteProposal)
    assert (call_view(module)["status"], call_view(module)["owner"]) == (
        CallStatus.ASSIGNED,
        "P1",
    )


@pytest.mark.parametrize("reconcile", [False, True], ids=["delayed_ack", "accepted_report"])
def test_EMG_03_fault_release_is_applied_when_delayed_acceptance_is_reconciled(reconcile):
    module = create()
    initial = telemetry(30.0)
    output = deliver(module, initial, telemetry(1.0, car_id="P2"), press())
    (proposal,) = emitted(output, RouteProposal)
    deliver(
        module,
        replace(initial, t_sim=1.0, route_version=2, mode=CabinMode.FAULT_HOLD),
        telemetry(1.0, car_id="P2", t_sim=1.0),
    )
    assert call_view(module)["owner"] == "P1"
    if reconcile:
        timeout = next(
            timer for timer in emitted(output, Timer) if timer.ref_id == proposal.command_id
        )
        module.handle(timeout, proposal.expiry_ts)
        acceptance = RouteVersionReport(
            t_sim=2.0,
            command_id=proposal.command_id,
            car_id="P1",
            seen=True,
            accepted=True,
            route_version=2,
        )
    else:
        acceptance = acknowledge(proposal, t=1.0)

    output = deliver(module, acceptance)

    (replacement,) = emitted(output, RouteProposal)
    assert replacement.car_id == "P2"
    assert call_view(module)["owner"] == "P2"
    assert call_view(module)["created_at"] == 0.0


def test_MED_03_hall_ownership_is_retained_until_mission_ack():
    module = create()
    initial = telemetry(12.0)
    (hall_proposal,) = emitted(deliver(module, initial, press(floor=10)), RouteProposal)
    deliver(
        module,
        acknowledge(hall_proposal),
        replace(initial, route_version=1, stops=hall_proposal.ordered_stops),
    )
    output = deliver(module, approval())
    (mission_proposal,) = emitted(output, RouteProposal)
    assert mission_proposal.mission_id == "MS-0001"
    assert not emitted(output, MissionStatusUpdate)
    assert call_view(module)["owner"] == "P1"

    # The cabin's telemetry can precede delivery of the ACK. It is not permission
    # to allocate the pickup to another car before ownership is reconciled.
    deliver(
        module,
        replace(
            initial,
            route_version=2,
            stops=mission_proposal.ordered_stops,
            mission_id="MS-0001",
            mode=CabinMode.MISSION_EXCLUSIVE,
        ),
    )
    assert call_view(module)["owner"] == "P1"

    output = deliver(module, acknowledge(mission_proposal))

    (reserved,) = emitted(output, MissionStatusUpdate)
    assert reserved.status == MissionStatus.RESERVED
    assert call_view(module)["status"] == CallStatus.PENDING
    assert call_view(module)["owner"] is None
    assert call_view(module)["created_at"] == 0.0


def test_MED_02_pending_mission_blocks_another_mission_on_same_car():
    module = create()
    deliver(module, telemetry(12.0))
    (first,) = emitted(deliver(module, approval()), RouteProposal)

    output = deliver(module, approval(mission_id="MS-0002"))

    assert not emitted(output, RouteProposal)
    assert first.car_id == "P1"
    assert all(
        update.status != MissionStatus.RESERVED for update in emitted(output, MissionStatusUpdate)
    )


def test_MED_01_concurrent_unavailable_missions_prioritize_medical_new_work():
    module = create()
    freight = replace(
        approval(mission_id="MS-freight"),
        mission_type=MissionType.FREIGHT,
        role=Role.BUILDING_STAFF,
        material="boxes",
        weight_kg=400.0,
    )
    deliver(module, freight, approval(mission_id="MS-medical"))

    output = deliver(module, telemetry(12.0, t_sim=1.0))

    (proposal,) = emitted(output, RouteProposal)
    assert proposal.mission_id == "MS-medical"


def test_MED_01_medical_new_work_does_not_replace_an_unresolved_freight_command():
    module = create()
    freight = replace(
        approval(mission_id="MS-freight"),
        mission_type=MissionType.FREIGHT,
        role=Role.BUILDING_STAFF,
        material="boxes",
        weight_kg=400.0,
    )
    (freight_proposal,) = emitted(deliver(module, telemetry(12.0), freight), RouteProposal)

    output = deliver(module, approval(mission_id="MS-medical"))

    assert freight_proposal.mission_id == "MS-freight"
    assert not emitted(output, RouteProposal)


def test_OPT_06_wider_policy_applies_to_next_call_without_replanning_existing_route():
    module = create(Policy(version="narrow", R=1, W=5))
    initial = telemetry(40.0)
    (first,) = emitted(deliver(module, initial, press(floor=30)), RouteProposal)
    deliver(
        module,
        acknowledge(first),
        replace(initial, route_version=1, stops=first.ordered_stops),
    )

    output = deliver(module, PolicyActivated(t_sim=1.0, policy=DEFAULT_POLICY))
    assert not emitted(output, RouteProposal)
    output = deliver(
        module, replace(initial, t_sim=1.0, route_version=1, stops=first.ordered_stops)
    )
    assert not emitted(output, RouteProposal)
    (grouped,) = emitted(deliver(module, press(1.0, floor=32)), RouteProposal)

    assert [(stop.floor, stop.kind) for stop in grouped.ordered_stops] == [
        (32, StopKind.HALL_PICKUP),
        (30, StopKind.HALL_PICKUP),
    ]


def test_F4_snapshot_does_not_expose_mutable_controller_state():
    module = create()
    deliver(module, press())
    snapshot = module.snapshot()
    snapshot["calls"][0]["created_at"] = -1.0
    snapshot["policy"]["R"] = -1
    snapshot["calls"].clear()

    assert call_view(module)["created_at"] == 0.0
    assert module.snapshot()["policy"]["R"] == DEFAULT_POLICY.R
