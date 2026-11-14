"""Who receives each message, and in which order equal-time messages run (framework F5).

Addresses are module names: ``"dispatch"``, ``"cc"``, ``"cabin:P1"`` ... ``"cabin:G1"``, and
``"passengers"`` (the engine's virtual passengers, D-56).
The engine logs EVERY message, whether or not it has recipients (e.g. HallCallAck).

Priority (SRS 7.7, D-05): at equal ``deliver_at`` the engine delivers lower priority values
first, then by insertion sequence number.
"""

from contracts.config import CARS
from contracts.enums import Priority
from contracts.messages import (
    AlarmPressed,
    Alert,
    AlertAcknowledged,
    CabinDestinationPressed,
    CabinTelemetry,
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
    Timer,
)

DISPATCH = "dispatch"
CONTROL_CENTER = "cc"
PASSENGERS = "passengers"
CABIN_PREFIX = "cabin:"

# Placeholders resolved by recipients():
BY_CAR = "<cabin given by msg.car_id>"
ALL_CABINS = "<every cabin>"
TARGET = "<module given by msg.target>"


def cabin_address(car_id: str) -> str:
    return f"{CABIN_PREFIX}{car_id}"


ROUTES: dict[type[Message], tuple[str, ...]] = {
    # passenger and operator input
    HallCallPressed: (DISPATCH,),
    HallCallAck: (PASSENGERS,),  # the UI reads call state from the snapshot
    CabinDestinationPressed: (BY_CAR,),
    PassengerBoard: (BY_CAR,),
    PassengerExit: (BY_CAR,),
    DoorRequest: (BY_CAR,),
    AlarmPressed: (BY_CAR, CONTROL_CENTER),
    MissionRequest: (CONTROL_CENTER,),
    MissionLoadConfirmed: (BY_CAR,),
    AlertAcknowledged: (CONTROL_CENTER,),
    MaintenanceSet: (BY_CAR,),
    RecoveryRequest: (CONTROL_CENTER,),
    PolicyDecision: (CONTROL_CENTER,),
    # safety inputs
    FaultInjected: (BY_CAR,),
    FaultCleared: (BY_CAR,),
    FireAlarm: (DISPATCH, ALL_CABINS, CONTROL_CENTER),
    # dispatch <-> cabin
    RouteProposal: (BY_CAR,),
    RouteReply: (DISPATCH,),
    RouteVersionQuery: (BY_CAR,),
    RouteVersionReport: (DISPATCH,),
    # cabin -> others
    CabinTelemetry: (DISPATCH, CONTROL_CENTER),
    ServiceEvent: (DISPATCH, CONTROL_CENTER, PASSENGERS),
    # missions, alerts, recovery, policy
    MissionApproval: (DISPATCH,),
    MissionStatusUpdate: (CONTROL_CENTER,),
    Alert: (CONTROL_CENTER,),
    RecoveryApproved: (BY_CAR,),
    PolicyRecommended: (CONTROL_CENTER,),
    PolicyActivated: (DISPATCH,),
    # timers
    Timer: (TARGET,),
}

PRIORITY: dict[type[Message], Priority] = {
    # 0 - safety input: before any same-time movement or route commitment
    FaultInjected: Priority.SAFETY,
    FaultCleared: Priority.SAFETY,
    FireAlarm: Priority.SAFETY,
    # 1 - cabin / physical-model result: update cabin state before new routing
    CabinTelemetry: Priority.PHYSICAL,
    ServiceEvent: Priority.PHYSICAL,
    PassengerBoard: Priority.PHYSICAL,
    PassengerExit: Priority.PHYSICAL,
    Timer: Priority.PHYSICAL,  # D-08: motion and door timers are physical progress
    # 2 - control messages
    HallCallAck: Priority.CONTROL,
    RouteProposal: Priority.CONTROL,
    RouteReply: Priority.CONTROL,
    RouteVersionQuery: Priority.CONTROL,
    RouteVersionReport: Priority.CONTROL,
    MissionApproval: Priority.CONTROL,
    MissionStatusUpdate: Priority.CONTROL,
    Alert: Priority.CONTROL,
    RecoveryApproved: Priority.CONTROL,
    PolicyRecommended: Priority.CONTROL,
    PolicyActivated: Priority.CONTROL,
    # 3 - passenger and operator input
    HallCallPressed: Priority.INPUT,
    CabinDestinationPressed: Priority.INPUT,
    DoorRequest: Priority.INPUT,
    AlarmPressed: Priority.INPUT,  # an alarm is an occupant event, not a safety input
    MissionRequest: Priority.INPUT,
    MissionLoadConfirmed: Priority.INPUT,
    AlertAcknowledged: Priority.INPUT,
    MaintenanceSet: Priority.INPUT,
    RecoveryRequest: Priority.INPUT,
    PolicyDecision: Priority.INPUT,
}


def recipients(msg: Message, cars: tuple[str, ...] = CARS) -> tuple[str, ...]:
    """Addresses that receive ``msg``, in a fixed order."""
    out: list[str] = []
    for route in ROUTES[type(msg)]:
        if route == BY_CAR:
            out.append(cabin_address(msg.car_id))  # type: ignore[attr-defined]
        elif route == ALL_CABINS:
            out.extend(cabin_address(car) for car in cars)
        elif route == TARGET:
            out.append(msg.target)  # type: ignore[attr-defined]
        else:
            out.append(route)
    return tuple(out)


def priority_of(msg: Message) -> Priority:
    return PRIORITY[type(msg)]
