"""Door state machine (DOOR-01..05, SAF-02, D-03, D-04, D-51). Pure: no messages, no clock.

The cabin feeds a ``DoorInput`` and gets the next ``DoorState`` (or ``None`` = ignore).
Guards that need cabin state (stationary, landing door zone, fault hold) are checked by the
cabin before it sends OPEN_CMD / OPEN_BUTTON.

    CLOSED --open--> OPENING --1 s--> OPEN --4 s--> CLOSING --1 s--> CLOSED (+ locked)
                                                       |
                         obstruction / open button     v
                     OPENING <--0 s-- OBSTRUCTED <-----+

OBSTRUCTED lasts 0 s: it is reported once (DOOR-01 exposes it), then the door reopens
(assumption for open question Q4, until DECISIONS.md says otherwise).
"""

from enum import StrEnum

from contracts.config import DOOR_CLOSING_S, DOOR_OPEN_S, DOOR_OPENING_S
from contracts.enums import DoorState, ServiceEventType


class DoorInput(StrEnum):
    OPEN_CMD = "OPEN_CMD"  # the cabin serves a landing
    TIMER = "TIMER"  # the current timed phase is over
    OBSTRUCTION = "OBSTRUCTION"  # FaultKind.OBSTRUCTION (momentary)
    OPEN_BUTTON = "OPEN_BUTTON"  # DoorRequest(open=True)
    CLOSE_BUTTON = "CLOSE_BUTTON"  # DoorRequest(open=False)


S, In = DoorState, DoorInput

# (state, input) -> next state. A missing pair means the input is ignored.
TRANSITIONS: dict[tuple[DoorState, DoorInput], DoorState] = {
    (S.CLOSED, In.OPEN_CMD): S.OPENING,
    (S.CLOSED, In.OPEN_BUTTON): S.OPENING,  # D-51: stationary at a landing, start a cycle
    (S.OPENING, In.TIMER): S.OPEN,
    (S.OPEN, In.TIMER): S.CLOSING,
    (S.OPEN, In.OPEN_BUTTON): S.OPEN,  # D-51 / DOOR-03: restart the OPEN period
    (S.OPEN, In.CLOSE_BUTTON): S.CLOSING,  # D-51: close now
    (S.CLOSING, In.TIMER): S.CLOSED,
    (S.CLOSING, In.OBSTRUCTION): S.OBSTRUCTED,  # DOOR-04
    (S.CLOSING, In.OPEN_BUTTON): S.OBSTRUCTED,  # D-51: reopen like an obstruction
    (S.OBSTRUCTED, In.TIMER): S.OPENING,  # D-04: full OPENING + OPEN + CLOSING again
}

# How long each state lasts before a TIMER input; None = until something else happens.
DURATION: dict[DoorState, float | None] = {
    S.CLOSED: None,
    S.OPENING: DOOR_OPENING_S,
    S.OPEN: DOOR_OPEN_S,
    S.CLOSING: DOOR_CLOSING_S,
    S.OBSTRUCTED: 0.0,
}

# The ServiceEvent reported when the door ENTERS a state.
ENTRY_EVENT: dict[DoorState, ServiceEventType] = {
    S.OPENING: ServiceEventType.DOOR_OPENING,
    S.OPEN: ServiceEventType.DOOR_OPENED,
    S.CLOSING: ServiceEventType.DOOR_CLOSING,
    S.CLOSED: ServiceEventType.DOOR_CLOSED_LOCKED,
    S.OBSTRUCTED: ServiceEventType.DOOR_REOPENED,
}


def next_state(state: DoorState, event: DoorInput) -> DoorState | None:
    """The state after ``event``, or None if the door ignores it in ``state``."""
    return TRANSITIONS.get((state, event))
