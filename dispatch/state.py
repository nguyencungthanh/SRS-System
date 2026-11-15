"""Private state for Dispatch's explicit request and command lifecycles."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from contracts.enums import CallStatus, Direction, MissionStatus
from contracts.messages import Candidate, MissionApproval, RouteProposal


class CommandState(StrEnum):
    WAITING = "WAITING"
    RECONCILING = "RECONCILING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass
class HallCall:
    request_id: str
    floor: int
    direction: Direction
    created_at: float
    status: CallStatus = CallStatus.PENDING
    owner: str | None = None
    rejected: dict[str, Any] = field(default_factory=dict)


@dataclass
class Mission:
    approval: MissionApproval
    status: MissionStatus = MissionStatus.APPROVED
    owner: str | None = None
    rejected: dict[str, Any] = field(default_factory=dict)


@dataclass
class Command:
    proposal: RouteProposal
    calls: tuple[str, ...]
    fingerprint: Any
    candidates: tuple[Candidate, ...] = ()
    state: CommandState = CommandState.WAITING
