"""How the API (Huy) drives the running simulation (An) - DECISIONS.md D-58.

``app/runner.py`` implements ``SimControl``. Huy's ``api/`` package exports::

    def create_app(control: SimControl) -> fastapi.FastAPI

and translates REST commands (``contracts.ui.COMMANDS``) and the WebSocket stream into these
calls. The API builds messages with ``t_sim=0.0``; the runner replaces ``t_sim`` with the
current simulation time, so the API never needs to know the clock.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from contracts.messages import Message
from contracts.ui import CommandResult, Snapshot


class SimCommand(StrEnum):
    """Engine control commands (``/sim/*`` endpoints)."""

    START = "START"  # RUNNING; also resumes from PAUSED
    PAUSE = "PAUSE"
    RESET = "RESET"  # args: scenario: str, seed: int
    REPLAY = "REPLAY"  # args: trace: str (path of a trace file); answers IDENTICAL / DIFFERENT
    SPEED = "SPEED"  # args: factor: float (simulated seconds per wall second, 0.1..50)


SnapshotCallback = Callable[[Snapshot], Awaitable[None]]


@runtime_checkable
class SimControl(Protocol):
    def submit(self, msg: Message) -> CommandResult:
        """Queue a UI input (HallCallPressed, MissionRequest, ...) at the current sim time."""
        ...

    def control(self, command: SimCommand, **args: Any) -> CommandResult: ...

    def snapshot(self) -> Snapshot: ...

    def subscribe(self, callback: SnapshotCallback) -> Callable[[], None]:
        """Call ``callback`` with every new snapshot. Returns a function that unsubscribes."""
        ...

    def scenarios(self) -> list[str]:
        """Scenario names accepted by RESET."""
        ...
