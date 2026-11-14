"""SimRunner: drives the Engine in real time for the web UI (implements contracts.runtime).

Every TICK_S of wall time the simulation advances by TICK_S x speed simulated seconds, then
every subscriber gets the new snapshot. This is the only place that reads a wall clock.
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import scenarios
from app.replay import replay
from contracts.enums import RejectReason, SimState
from contracts.messages import Message
from contracts.runtime import SimCommand, SnapshotCallback
from contracts.ui import CommandResult, Snapshot
from simulation.engine import Engine, ModuleSet

TICK_S = 0.1
MIN_SPEED, MAX_SPEED = 0.1, 50.0


def _ok(ident: str | None = None) -> CommandResult:
    return {"ok": True, "id": ident, "reason": None}


def _refused(reason: str) -> CommandResult:
    return {"ok": False, "id": None, "reason": reason}


class SimRunner:
    def __init__(
        self,
        modules: ModuleSet,
        scenario: str = "DEMO",
        seed: int = 0,
        speed: float = 1.0,
        log_path: Path | None = None,
    ) -> None:
        self.modules = modules
        self.speed = min(max(speed, MIN_SPEED), MAX_SPEED)
        self.engine = Engine(modules, scenarios.get(scenario, seed), seed, log_path)
        self._subscribers: list[SnapshotCallback] = []
        self._dirty = True
        self._closed = False

    # --- SimControl ------------------------------------------------------------------

    def submit(self, msg: Message) -> CommandResult:
        if self.engine.state == SimState.STOPPED:
            return _refused(RejectReason.INVALID_STATE.value)
        stamped = self.engine.submit(msg)
        self._dirty = True
        return _ok(stamped.msg_id)

    def control(self, command: SimCommand | str, **args: Any) -> CommandResult:
        try:
            command = SimCommand(command)
        except ValueError:
            return _refused(RejectReason.UNKNOWN_ID.value)
        self._dirty = True
        if command == SimCommand.START:
            self.engine.start()
        elif command == SimCommand.PAUSE:
            self.engine.pause()
        elif command == SimCommand.RESET:
            name = args.get("scenario") or self.engine.scenario.name
            seed = int(args.get("seed", self.engine.seed))
            try:
                scenario = scenarios.get(name, seed)
            except KeyError:
                return _refused(RejectReason.UNKNOWN_ID.value)
            self.engine.reset(scenario, seed)
        elif command == SimCommand.REPLAY:
            try:
                engine, identical = replay(Path(args["trace"]))
            except (KeyError, OSError, ValueError, StopIteration):
                return _refused(RejectReason.UNKNOWN_ID.value)
            self.engine = engine
            return _ok("IDENTICAL" if identical else "DIFFERENT")
        elif command == SimCommand.SPEED:
            self.speed = min(max(float(args.get("factor", 1.0)), MIN_SPEED), MAX_SPEED)
        return _ok()

    def snapshot(self) -> Snapshot:
        return self.engine.snapshot()

    def subscribe(self, callback: SnapshotCallback) -> Callable[[], None]:
        self._subscribers.append(callback)
        self._dirty = True

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def scenarios(self) -> list[str]:
        return scenarios.names()

    # --- real-time loop --------------------------------------------------------------

    def tick(self, wall_dt: float) -> None:
        """Advance by ``wall_dt`` wall seconds x speed (no effect unless RUNNING)."""
        if self.engine.state == SimState.RUNNING:
            self.engine.advance_to(self.engine.now + wall_dt * self.speed)
            self._dirty = True

    async def publish(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        snap = self.engine.snapshot()
        for callback in list(self._subscribers):
            await callback(snap)

    async def run(self) -> None:
        last = time.monotonic()
        while not self._closed:
            await asyncio.sleep(TICK_S)
            now = time.monotonic()
            self.tick(now - last)
            last = now
            await self.publish()

    def close(self) -> None:
        self._closed = True
