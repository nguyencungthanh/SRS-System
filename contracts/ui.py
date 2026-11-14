"""Engine <-> UI contract (framework F10, PROJECT_PLAN.md Section 4).

The UI never talks to a module. It sends REST commands (``COMMANDS``) that the API turns
into messages, and it reads state only from ``Snapshot`` dicts pushed over ``/ws``.

Snapshot assembly (engine):
    cars     <- every cabin.snapshot()["car"]  + telemetry_age_s added by the engine
    dests    <- every cabin.snapshot()["dests"], concatenated in CAR_ORDER
    calls    <- dispatch.snapshot()["calls"];  policy <- dispatch.snapshot()["policy"]
    missions <- cc.snapshot()["missions"];     alerts <- cc.snapshot()["alerts"]
    metrics, last_events, t_sim, sim_state, seed, contract_version <- engine

REST body rule: the JSON body of a command is the message's own fields WITHOUT
``msg_id``, ``t_sim`` and ``deliver_at`` (the engine sets those), plus ``"role"`` when the
message has no role field of its own. Every endpoint answers ``CommandResult``.
"""

from dataclasses import dataclass
from typing import TypedDict

from contracts import CONTRACT_VERSION
from contracts.config import CARS, DEFAULT_POLICY
from contracts.enums import (
    AlertKind,
    CabinMode,
    CallStatus,
    DestStatus,
    Direction,
    DoorState,
    ExclusionReason,
    MissionStatus,
    MissionType,
    Role,
    SimState,
    StopKind,
)
from contracts.messages import (
    AlarmPressed,
    AlertAcknowledged,
    CabinDestinationPressed,
    DoorRequest,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    HallCallPressed,
    MaintenanceSet,
    Message,
    MissionLoadConfirmed,
    MissionRequest,
    PassengerBoard,
    PassengerExit,
    PolicyDecision,
    RecoveryRequest,
)

# ======================================================================================
# Snapshot shapes. Enum-typed values are sent as their string value.
# ======================================================================================


class PolicyView(TypedDict):
    version: str
    R: int
    W: int


class StopView(TypedDict):
    floor: int
    kind: str  # StopKind
    ref_id: str | None
    direction: str | None  # Direction


class CarView(TypedDict):
    car_id: str
    position: float
    floor_display: int  # nearest floor, display only (D-02)
    direction: str  # Direction
    door: str  # DoorState
    locked: bool
    load_kg: float
    passengers: int
    mode: str  # CabinMode
    faults: list[str]  # FaultKind
    route_version: int
    stops: list[StopView]
    mission_id: str | None


class FleetCarView(CarView):
    telemetry_age_s: float | None  # added by the engine; None before the first telemetry


class CallView(TypedDict):
    request_id: str
    floor: int
    direction: str  # Direction
    status: str  # CallStatus
    owner: str | None  # car_id, confirmed or provisional
    created_at: float


class DestView(TypedDict):
    car_id: str
    floor: int
    passenger_id: str
    status: str  # DestStatus


class CandidateView(TypedDict):
    car_id: str
    eta_s: float | None
    excluded: str | None  # ExclusionReason


class MissionView(TypedDict):
    mission_id: str
    mission_type: str  # MissionType
    origin: int | None
    destination: int | None
    status: str  # MissionStatus
    car_id: str | None
    reason: str | None
    candidates: list[CandidateView]


class AlertView(TypedDict):
    alert_id: str
    kind: str  # AlertKind
    car_id: str | None
    details: str
    t_sim: float
    acknowledged: bool


class MetricsView(TypedDict):
    mean_wait: float | None
    median_wait: float | None
    p95_wait: float | None
    max_wait: float | None
    unserved: int
    delay_violations: int


class Snapshot(TypedDict):
    t_sim: float
    sim_state: str  # SimState
    seed: int
    contract_version: int
    policy: PolicyView
    cars: list[FleetCarView]
    calls: list[CallView]
    dests: list[DestView]
    missions: list[MissionView]
    alerts: list[AlertView]
    metrics: MetricsView
    last_events: list[str]  # latest ~50 log lines, oldest first


# Per-module snapshot() return shapes (contracts.interfaces).


class DispatchSnapshot(TypedDict):
    calls: list[CallView]
    policy: PolicyView


class CabinSnapshot(TypedDict):
    car: CarView
    dests: list[DestView]


class ControlCenterSnapshot(TypedDict):
    missions: list[MissionView]
    alerts: list[AlertView]


# ======================================================================================
# REST commands
# ======================================================================================


class CommandResult(TypedDict):
    ok: bool
    id: str | None  # e.g. the queued msg_id, or the run_id for /optimization/run
    reason: str | None  # RejectReason value when ok is False


@dataclass(frozen=True)
class Command:
    path: str  # all commands are POST
    message: type[Message] | None  # None: engine / optimizer control, not a module message
    roles: frozenset[Role] | None  # None: anyone may call
    note: str = ""


_ANYONE = None
_OPERATOR = frozenset({Role.OPERATOR})

COMMANDS: tuple[Command, ...] = (
    Command("/hall-call", HallCallPressed, _ANYONE),
    Command("/destination", CabinDestinationPressed, frozenset({Role.PASSENGER})),
    Command("/passenger/board", PassengerBoard, _OPERATOR),
    Command("/passenger/exit", PassengerExit, _OPERATOR),
    Command("/door", DoorRequest, frozenset({Role.PASSENGER})),
    Command("/alarm", AlarmPressed, _ANYONE),
    Command("/mission", MissionRequest, frozenset({Role.MEDICAL_COORDINATOR, Role.BUILDING_STAFF})),
    Command(
        "/mission/confirm",
        MissionLoadConfirmed,
        frozenset({Role.MEDICAL_COORDINATOR, Role.BUILDING_STAFF}),
    ),
    Command("/fault", FaultInjected, _OPERATOR),
    Command("/fault/clear", FaultCleared, frozenset({Role.OPERATOR, Role.MAINTAINER})),
    Command("/fire", FireAlarm, _OPERATOR),
    Command("/maintenance", MaintenanceSet, frozenset({Role.MAINTAINER})),
    Command("/recovery", RecoveryRequest, frozenset({Role.SUPERVISOR})),
    Command("/alert/ack", AlertAcknowledged, frozenset({Role.OPERATOR, Role.SUPERVISOR})),
    Command("/policy/approve", PolicyDecision, frozenset({Role.SUPERVISOR})),
    Command("/sim/start", None, _OPERATOR, "RUNNING; also resumes from PAUSED"),
    Command("/sim/pause", None, _OPERATOR),
    Command("/sim/reset", None, _OPERATOR, "body: {scenario, seed}"),
    Command("/sim/replay", None, _OPERATOR, "body: {trace, seed}"),
    Command("/optimization/run", None, _OPERATOR, "body: {method: GRID | SATSA}"),
)

SNAPSHOT_GET = "/snapshot"  # GET: current Snapshot once
SNAPSHOT_WS = "/ws"  # WebSocket: Snapshot after every processed timestamp and on connect


# ======================================================================================
# Fake data so the UI can be built before the engine runs
# ======================================================================================


def fake_snapshot(t_sim: float = 0.0) -> Snapshot:
    """A deterministic, realistic Snapshot. P1 moves down from 31 as ``t_sim`` grows."""
    p1_pos = max(27.0, 31.0 - t_sim / 2.0)

    def car(car_id: str, position: float, **over: object) -> FleetCarView:
        base: FleetCarView = {
            "car_id": car_id,
            "position": position,
            "floor_display": round(position),
            "direction": Direction.IDLE,
            "door": DoorState.CLOSED,
            "locked": True,
            "load_kg": 0.0,
            "passengers": 0,
            "mode": CabinMode.AUTO,
            "faults": [],
            "route_version": 0,
            "stops": [],
            "mission_id": None,
            "telemetry_age_s": 0.0,
        }
        base.update(over)  # type: ignore[typeddict-item]
        return base

    cars = [
        car(
            "P1",
            p1_pos,
            direction=Direction.DOWN if p1_pos > 27.0 else Direction.IDLE,
            route_version=8,
            stops=[
                {
                    "floor": 27,
                    "kind": StopKind.HALL_PICKUP,
                    "ref_id": "HC-0001",
                    "direction": Direction.DOWN,
                }
            ],
        ),
        car("P2", 12.0),
        car("P3", 5.0, mode=CabinMode.MAINTENANCE),
        car(
            "P4",
            22.0,
            door=DoorState.OPEN,
            locked=False,
            load_kg=150.0,
            passengers=2,
            route_version=3,
        ),
        car("G1", 3.0),
    ]
    assert [c["car_id"] for c in cars] == list(CARS)

    return {
        "t_sim": t_sim,
        "sim_state": SimState.RUNNING,
        "seed": 1,
        "contract_version": CONTRACT_VERSION,
        "policy": {
            "version": DEFAULT_POLICY.version,
            "R": DEFAULT_POLICY.R,
            "W": DEFAULT_POLICY.W,
        },
        "cars": cars,
        "calls": [
            {
                "request_id": "HC-0001",
                "floor": 27,
                "direction": Direction.DOWN,
                "status": CallStatus.ASSIGNED,
                "owner": "P1",
                "created_at": 0.0,
            },
            {
                "request_id": "HC-0002",
                "floor": 10,
                "direction": Direction.UP,
                "status": CallStatus.PENDING,
                "owner": None,
                "created_at": 1.0,
            },
        ],
        "dests": [
            {"car_id": "P4", "floor": 1, "passenger_id": "PAX-0001", "status": DestStatus.PENDING},
            {"car_id": "P4", "floor": 2, "passenger_id": "PAX-0002", "status": DestStatus.PENDING},
        ],
        "missions": [
            {
                "mission_id": "MS-0001",
                "mission_type": MissionType.MEDICAL,
                "origin": 2,
                "destination": 30,
                "status": MissionStatus.RESERVED,
                "car_id": "P2",
                "reason": None,
                "candidates": [
                    {"car_id": "P1", "eta_s": 90.0, "excluded": None},
                    {"car_id": "P2", "eta_s": 20.0, "excluded": None},
                    {"car_id": "P3", "eta_s": None, "excluded": ExclusionReason.MAINTENANCE},
                    {"car_id": "P4", "eta_s": 58.0, "excluded": None},
                    {"car_id": "G1", "eta_s": None, "excluded": ExclusionReason.NOT_CAPABLE},
                ],
            }
        ],
        "alerts": [
            {
                "alert_id": "MSG-000007",
                "kind": AlertKind.ALARM,
                "car_id": "P4",
                "details": "Alarm button pressed",
                "t_sim": 0.0,
                "acknowledged": False,
            }
        ],
        "metrics": {
            "mean_wait": None,
            "median_wait": None,
            "p95_wait": None,
            "max_wait": None,
            "unserved": 0,
            "delay_violations": 0,
        },
        "last_events": [
            "00:00:00 HALL_CALL_ACCEPTED call=HC-0001 origin=27 dir=DOWN",
            "00:00:00 ROUTE_PROPOSED car=P1 cmd=RC-0001 expected_version=7",
            "00:00:00 ROUTE_ACK car=P1 cmd=RC-0001 committed_version=8",
        ],
    }
