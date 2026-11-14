"""Who may do what, and when a mission request is valid (HY-01).

Role matrix: DECISIONS.md D-53 (CC-01, SRS 5.3). The API checks the role first
(``contracts.ui.COMMANDS``); Control Center checks again here, because a message can also
come from a scenario or a test, not only from the API.

Request validation: DECISIONS.md D-54 (CC-03, FRT-01). The order of the checks is part of
the behaviour, because a request can break more than one rule and only one reason is sent:

    1. role allowed for this mission type        -> UNAUTHORIZED_ROLE
    2. origin and destination present            -> MISSING_FIELD
    3. both floors in 1..FLOORS                  -> FLOOR_OUT_OF_RANGE
    4. origin != destination                     -> SAME_ORIGIN_DESTINATION
    5. FREIGHT: material is a non-empty string   -> INVALID_MATERIAL
    6. FREIGHT: weight_kg present and > 0        -> MISSING_FIELD
    7. FREIGHT: weight_kg <= FREIGHT_MAX_KG      -> OVER_PAYLOAD  (exactly the limit is valid)

Rule 6 "> 0" is Huy's decision (14/11), not in D-54: a weight of 0 or less is treated as no
usable weight. No RejectReason fits exactly, so MISSING_FIELD is used until An decides.
"""

from contracts.config import CAR_ORDER, FLOORS, FREIGHT_CAPABLE, FREIGHT_MAX_KG, MEDICAL_CAPABLE
from contracts.enums import MissionType, RejectReason, Role
from contracts.messages import MissionRequest

LOWEST_FLOOR = 1

# --- D-53 role matrix ----------------------------------------------------------------

MISSION_ROLES: dict[MissionType, frozenset[Role]] = {
    MissionType.MEDICAL: frozenset({Role.MEDICAL_COORDINATOR}),
    MissionType.FREIGHT: frozenset({Role.BUILDING_STAFF}),
}
ALERT_ACK_ROLES: frozenset[Role] = frozenset({Role.OPERATOR, Role.SUPERVISOR})
RECOVERY_ROLES: frozenset[Role] = frozenset({Role.SUPERVISOR})
POLICY_ROLES: frozenset[Role] = frozenset({Role.SUPERVISOR})

# --- D-20 / D-21 capabilities --------------------------------------------------------

CAPABLE_CARS: dict[MissionType, frozenset[str]] = {
    MissionType.MEDICAL: MEDICAL_CAPABLE,
    MissionType.FREIGHT: FREIGHT_CAPABLE,
}


def may_request(role: Role, mission_type: MissionType) -> bool:
    return role in MISSION_ROLES.get(mission_type, frozenset())


def allowed_cars(mission_type: MissionType) -> tuple[str, ...]:
    """Capable cars for ``mission_type``, in the fleet's fixed order (CAR_ORDER)."""
    capable = CAPABLE_CARS.get(mission_type, frozenset())
    return tuple(car for car in CAR_ORDER if car in capable)


def _floor_ok(floor: int) -> bool:
    return LOWEST_FLOOR <= floor <= FLOORS


def validate_request(req: MissionRequest) -> RejectReason | None:
    """Return the first rule ``req`` breaks (order in the module docstring), or None."""
    if not may_request(req.role, req.mission_type):
        return RejectReason.UNAUTHORIZED_ROLE
    if req.origin is None or req.destination is None:
        return RejectReason.MISSING_FIELD
    if not (_floor_ok(req.origin) and _floor_ok(req.destination)):
        return RejectReason.FLOOR_OUT_OF_RANGE
    if req.origin == req.destination:
        return RejectReason.SAME_ORIGIN_DESTINATION
    if req.mission_type == MissionType.FREIGHT:
        if not req.material or not req.material.strip():
            return RejectReason.INVALID_MATERIAL
        if req.weight_kg is None or req.weight_kg <= 0:
            return RejectReason.MISSING_FIELD
        if req.weight_kg > FREIGHT_MAX_KG:
            return RejectReason.OVER_PAYLOAD
    return None
