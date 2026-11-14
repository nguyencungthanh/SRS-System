"""Self-checks for contracts/ itself (framework F1-F5, F10).

These do not test any module. They check that the contract is complete and consistent:
every message is frozen, routed, prioritised and round-trips through JSON, and the
config constants reproduce the SRS worked-example numbers.
"""

import dataclasses
import json

import pytest

from contracts import CONTRACT_VERSION
from contracts import config as cfg
from contracts.codec import MESSAGE_TYPES, from_dict, from_json, to_dict, to_json
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
    Priority,
    RejectReason,
    Role,
    ServiceEventType,
    StopKind,
)
from contracts.ids import IdGen
from contracts.interfaces import CabinModule, DispatchModule, Module
from contracts.messages import (
    ALL_MESSAGES,
    BASE_FIELDS,
    AlarmPressed,
    Alert,
    AlertAcknowledged,
    CabinDestinationPressed,
    CabinTelemetry,
    Candidate,
    DoorRequest,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    HallCallAck,
    HallCallPressed,
    MaintenanceSet,
    Message,
    MissionApproval,
    MissionLoadConfirmed,
    MissionRequest,
    MissionStatusUpdate,
    PassengerBoard,
    PassengerExit,
    PolicyActivated,
    PolicyDecision,
    PolicyRecommended,
    RecoveryApproved,
    RecoveryRequest,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    ServiceEvent,
    Stop,
    Timer,
    payload_key,
)
from contracts.routing import PRIORITY, ROUTES, priority_of, recipients
from contracts.ui import COMMANDS, FleetCarView, Snapshot, fake_snapshot

P27 = Stop(floor=27, kind=StopKind.HALL_PICKUP, ref_id="HC-0001", direction=Direction.DOWN)
D5 = Stop(floor=5, kind=StopKind.CAR_DEST, ref_id="PAX-0001")
POLICY = cfg.Policy(version="POL-0001", R=2, W=4)
CALL = HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN)
ACK = HallCallAck(
    t_sim=0.0,
    request_id="HC-0001",
    floor=27,
    direction=Direction.DOWN,
    accepted=True,
    status=CallStatus.PENDING,
)
PROPOSAL = RouteProposal(
    t_sim=0.0,
    command_id="RC-0001",
    car_id="P1",
    expected_route_version=7,
    ordered_stops=(P27,),
    expiry_ts=0.0 + cfg.ACK_TIMEOUT_S,
)

# One realistic instance of every message type.
SAMPLES: list[Message] = [
    CALL,
    ACK,
    HallCallAck(
        t_sim=0.0,
        request_id=None,
        floor=1,
        direction=Direction.DOWN,
        accepted=False,
        reason=RejectReason.TERMINAL_DIRECTION,
    ),
    CabinDestinationPressed(t_sim=14.0, car_id="P1", floor=5, passenger_id="PAX-0001"),
    PassengerBoard(t_sim=9.0, car_id="P1", passenger_id="PAX-0001"),
    PassengerExit(t_sim=59.0, car_id="P1", passenger_id="PAX-0001"),
    DoorRequest(t_sim=12.0, car_id="P1", open=True),
    AlarmPressed(t_sim=3.0, car_id="P4"),
    MissionRequest(
        t_sim=0.0,
        role=Role.BUILDING_STAFF,
        mission_type=MissionType.FREIGHT,
        origin=1,
        destination=30,
        material="boxes",
        weight_kg=400.0,
    ),
    MissionLoadConfirmed(
        t_sim=60.0,
        mission_id="MS-0001",
        car_id="P1",
        action=MissionAction.LOADED,
        weight_kg=400.0,
    ),
    AlertAcknowledged(t_sim=5.0, alert_id="MSG-000007", role=Role.OPERATOR),
    MaintenanceSet(t_sim=0.0, car_id="P3", on=True, role=Role.MAINTAINER),
    RecoveryRequest(t_sim=50.0, car_id="P3", role=Role.SUPERVISOR),
    PolicyDecision(t_sim=0.0, run_id="RUN-0001", approve=True, role=Role.SUPERVISOR),
    FaultInjected(t_sim=20.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS),
    FaultCleared(t_sim=40.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS),
    FireAlarm(t_sim=100.0, active=True),
    PROPOSAL,
    RouteReply(t_sim=0.0, command_id="RC-0001", car_id="P1", accepted=True, committed_version=8),
    RouteReply(
        t_sim=0.0,
        command_id="RC-0002",
        car_id="P2",
        accepted=False,
        committed_version=3,
        reason=NackReason.INSERT_CUTOFF,
    ),
    RouteVersionQuery(t_sim=2.0, command_id="RC-0001", car_id="P1"),
    RouteVersionReport(
        t_sim=2.0, command_id="RC-0001", car_id="P1", seen=True, accepted=True, route_version=8
    ),
    CabinTelemetry(
        t_sim=1.0,
        car_id="P1",
        position=30.5,
        direction=Direction.DOWN,
        moving=True,
        door=DoorState.CLOSED,
        locked=True,
        door_zone=False,
        obstruction=False,
        load_kg=0.0,
        passengers=0,
        mode=CabinMode.AUTO,
        route_version=8,
        stops=(P27, D5),
        faults=(FaultKind.LOAD_SENSOR,),
        sensors_valid=False,
    ),
    ServiceEvent(
        t_sim=8.0,
        car_id="P1",
        event=ServiceEventType.HALL_CALL_SERVED,
        floor=27,
        ref_id="HC-0001",
    ),
    ServiceEvent(
        t_sim=9.0,
        car_id="P1",
        event=ServiceEventType.DEST_REJECTED,
        floor=27,
        passenger_id="PAX-0009",
        reason=RejectReason.NOT_BOARDED,
    ),
    MissionApproval(
        t_sim=0.0,
        mission_id="MS-0001",
        role=Role.MEDICAL_COORDINATOR,
        mission_type=MissionType.MEDICAL,
        origin=2,
        destination=30,
        allowed_cars=("P1", "P2", "P3", "P4"),
    ),
    MissionStatusUpdate(
        t_sim=0.0,
        mission_id="MS-0001",
        status=MissionStatus.RESERVED,
        car_id="P2",
        candidates=(
            Candidate(car_id="P2", eta_s=20.0),
            Candidate(car_id="P3", excluded=ExclusionReason.MAINTENANCE),
        ),
    ),
    MissionStatusUpdate(
        t_sim=0.0,
        mission_id="MS-0002",
        status=MissionStatus.REJECTED,
        reason=RejectReason.UNAUTHORIZED_ROLE,
    ),
    MissionStatusUpdate(
        t_sim=0.0,
        mission_id="MS-0003",
        status=MissionStatus.FAILED,
        car_id="P1",
        reason=NackReason.FAULT_HOLD,
    ),
    Alert(t_sim=3.0, kind=AlertKind.ALARM, car_id="P4", details="Alarm button pressed"),
    RecoveryApproved(t_sim=51.0, car_id="P3", approved_by=Role.SUPERVISOR),
    PolicyRecommended(
        t_sim=0.0,
        run_id="RUN-0001",
        policy=POLICY,
        objective=42.5,
        scenario_set=("train-1", "train-2"),
    ),
    PolicyActivated(t_sim=0.0, policy=POLICY),
    Timer(t_sim=8.0, deliver_at=9.0, target="cabin:P1", key="door", ref_id="3"),
]


def test_F1_samples_cover_every_message_type():
    assert {type(m) for m in SAMPLES} == set(ALL_MESSAGES)
    assert set(MESSAGE_TYPES) == {cls.__name__ for cls in ALL_MESSAGES}


@pytest.mark.parametrize("cls", ALL_MESSAGES, ids=lambda c: c.__name__)
def test_F1_message_is_frozen_kw_only_with_envelope(cls):
    params = cls.__dataclass_params__
    assert params.frozen
    names = {f.name for f in dataclasses.fields(cls)}
    assert BASE_FIELDS <= names
    assert all(f.kw_only for f in dataclasses.fields(cls))


def test_F1_messages_are_immutable():
    msg = HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.floor = 28  # type: ignore[misc]


@pytest.mark.parametrize("msg", SAMPLES, ids=lambda m: type(m).__name__)
def test_LOG_02_message_round_trips_through_json(msg):
    assert from_dict(json.loads(json.dumps(to_dict(msg)))) == msg
    assert from_json(to_json(msg)) == msg


def test_LOG_02_json_line_is_stable():
    msg = CALL
    assert to_json(msg) == to_json(from_json(to_json(msg)))
    assert "\n" not in to_json(msg)


def test_D_14_payload_key_ignores_envelope():
    first = PROPOSAL
    assert isinstance(first, RouteProposal)
    redelivered = dataclasses.replace(first, msg_id="MSG-000099", t_sim=1.5, deliver_at=1.5)
    assert first != redelivered
    assert payload_key(first) == payload_key(redelivered)


def test_D_14_payload_key_detects_changed_stop():
    first = PROPOSAL
    changed = dataclasses.replace(first, ordered_stops=(P27, D5))
    assert payload_key(first) != payload_key(changed)


@pytest.mark.parametrize("cls", ALL_MESSAGES, ids=lambda c: c.__name__)
def test_SIM_04_every_message_has_route_and_priority(cls):
    assert cls in ROUTES
    assert isinstance(PRIORITY[cls], Priority)


def test_SIM_04_safety_before_physical_before_control_before_input():
    fault = FaultInjected(t_sim=20.0, car_id="P1", kind=FaultKind.OBSTRUCTION)
    timer = Timer(t_sim=19.0, deliver_at=20.0, target="cabin:P1", key="door")
    proposal = PROPOSAL
    call = CALL
    order = sorted([call, proposal, timer, fault], key=priority_of)
    assert order == [fault, timer, proposal, call]


def test_F5_recipients_resolve_placeholders():
    assert recipients(PROPOSAL) == ("cabin:P1",)
    assert recipients(FireAlarm(t_sim=0.0, active=True)) == (
        "dispatch",
        "cabin:P1",
        "cabin:P2",
        "cabin:P3",
        "cabin:P4",
        "cabin:G1",
        "cc",
    )
    assert recipients(AlarmPressed(t_sim=0.0, car_id="P4")) == ("cabin:P4", "cc")
    assert recipients(Timer(t_sim=0.0, target="dispatch", key="ack")) == ("dispatch",)
    assert recipients(ACK) == ("passengers",)  # D-56


def test_EMG_01_alarm_never_routed_to_dispatch():
    assert "dispatch" not in recipients(AlarmPressed(t_sim=0.0, car_id="P1"))


def test_D_03_door_cycle_equals_dwell():
    assert cfg.DOOR_OPENING_S + cfg.DOOR_OPEN_S + cfg.DOOR_CLOSING_S == cfg.DWELL_S


def test_AT_01_config_reproduces_oracle():
    pickup = (31 - 27) * cfg.SECONDS_PER_FLOOR
    arrival = pickup + cfg.DWELL_S + (27 - 5) * cfg.SECONDS_PER_FLOOR
    assert (pickup, arrival, arrival + cfg.DWELL_S) == (8.0, 58.0, 64.0)


def test_AT_02_config_reproduces_oracle():
    first = (31 - 29) * cfg.SECONDS_PER_FLOOR
    second = first + cfg.DWELL_S + (29 - 27) * cfg.SECONDS_PER_FLOOR
    added = second - (31 - 27) * cfg.SECONDS_PER_FLOOR
    assert (first, second, added) == (4.0, 14.0, 6.0)
    assert added <= cfg.DELAY_CAP_S
    assert 29 - 27 <= cfg.DEFAULT_POLICY.R and 2 - 0 <= cfg.DEFAULT_POLICY.W


def test_TC_DELAY_01_cap_uses_fixed_reference():
    reference = 20.0
    assert 26.0 - reference <= cfg.DELAY_CAP_S
    assert 32.0 - reference > cfg.DELAY_CAP_S


def test_AT_03_config_reproduces_medical_etas():
    spf, dwell = cfg.SECONDS_PER_FLOOR, cfg.DWELL_S
    p1 = (40 - 36) * spf + dwell + (40 - 2) * spf
    p2 = (12 - 2) * spf
    p4 = (28 - 22) * spf + dwell + (22 - 2) * spf
    assert (p1, p2, p4) == (90.0, 20.0, 58.0)
    assert "G1" not in cfg.MEDICAL_CAPABLE


def test_AT_04_config_reproduces_freight_timeline():
    spf, dwell = cfg.SECONDS_PER_FLOOR, cfg.DWELL_S
    arrive_5 = (27 - 5) * spf
    exit_done = arrive_5 + dwell
    at_pickup = exit_done + (5 - 1) * spf
    loaded = at_pickup + dwell
    delivered = loaded + (30 - 1) * spf
    assert (arrive_5, exit_done, at_pickup, loaded, delivered) == (44, 50, 58, 64, 122)


def test_D_33_default_policy_inside_search_domain():
    assert cfg.DEFAULT_POLICY.R in cfg.R_DOMAIN
    assert cfg.DEFAULT_POLICY.W in cfg.W_DOMAIN
    assert len(cfg.R_DOMAIN) * len(cfg.W_DOMAIN) == 55


def test_D_15_idgen_is_deterministic():
    gen = IdGen("HC")
    assert [gen.next(), gen.next()] == ["HC-0001", "HC-0002"]
    gen.reset()
    assert gen.next() == "HC-0001"
    assert IdGen("MSG", width=6).next() == "MSG-000001"


def test_F4_protocols_are_structural():
    class Dummy:
        name = "cabin:P1"
        car_id = "P1"

        def handle(self, msg, now):
            return []

        def snapshot(self):
            return {}

    assert isinstance(Dummy(), Module)
    assert isinstance(Dummy(), CabinModule)
    assert isinstance(Dummy(), DispatchModule)  # same shape; name decides the role


def test_F10_fake_snapshot_matches_shape_and_is_json():
    snap = fake_snapshot(4.0)
    assert set(snap) == set(Snapshot.__annotations__)
    for car in snap["cars"]:
        assert set(car) == set(FleetCarView.__annotations__)
    assert [car["car_id"] for car in snap["cars"]] == list(cfg.CARS)
    assert snap["contract_version"] == CONTRACT_VERSION
    json.dumps(snap)


def test_F10_commands_are_unique_and_use_known_messages():
    paths = [c.path for c in COMMANDS]
    assert len(paths) == len(set(paths))
    for command in COMMANDS:
        assert command.message is None or command.message in ALL_MESSAGES
