"""SimRunner: drives the Engine in real time for the web UI (implements contracts.runtime).

Every TICK_S of wall time the simulation advances by TICK_S x speed simulated seconds, then
every subscriber gets the new snapshot. This is the only place that reads a wall clock.

OPTIMIZE (D-65, D-68) trains GRID or SATSA in a background thread on the quick traces. The
thread never touches the engine: when it finishes, the next tick submits PolicyRecommended
to Control Center, where a supervisor approves or rejects it (OPT-06).
"""

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import scenarios
from app.replay import replay
from contracts.config import Policy
from contracts.enums import RejectReason, Role, SimState
from contracts.ids import PREFIX_POLICY, PREFIX_RUN, IdGen
from contracts.messages import Message, PolicyActivated, PolicyDecision, PolicyRecommended
from contracts.runtime import SimCommand, SnapshotCallback
from contracts.ui import CommandResult, OptimizationRunView, Snapshot
from optimization.report import METHODS, Inputs, Outcome
from simulation.engine import Engine, ModuleSet

TICK_S = 0.1
MIN_SPEED, MAX_SPEED = 0.1, 50.0


def _ok(ident: str | None = None) -> CommandResult:
    return {"ok": True, "id": ident, "reason": None}


def _refused(reason: str) -> CommandResult:
    return {"ok": False, "id": None, "reason": reason}


Optimizer = Callable[[str, bool, Callable[[float, int], None]], Outcome]


def default_optimizer(method: str, quick: bool, progress: Callable[[float, int], None]) -> Outcome:
    from optimization.report import optimize

    return optimize(method, quick, progress=progress)


@dataclass
class _Run:
    run_id: str
    method: str
    quick: bool
    status: str = "RUNNING"
    progress: float = 0.0
    evaluated: int = 0
    outcome: Outcome | None = None
    policy: Policy | None = None
    decision: str = "PENDING"
    detail: str | None = None
    submitted: bool = False

    def view(self) -> OptimizationRunView:
        best = None
        if self.policy is not None:
            best = {"version": self.policy.version, "R": self.policy.R, "W": self.policy.W}
        return {
            "run_id": self.run_id,
            "method": self.method,
            "quick": self.quick,
            "status": self.status,
            "progress": round(self.progress, 3),
            "evaluated": self.evaluated,
            "best": best,
            "objective": self.outcome.objective if self.outcome else None,
            "baseline_objective": self.outcome.baseline_objective if self.outcome else None,
            "decision": self.decision,
            "detail": self.detail,
        }


class SimRunner:
    def __init__(
        self,
        modules: ModuleSet,
        scenario: str = "DEMO",
        seed: int = 0,
        speed: float = 1.0,
        log_path: Path | None = None,
        optimizer: Optimizer | None = None,
    ) -> None:
        self.modules = modules
        self.speed = min(max(speed, MIN_SPEED), MAX_SPEED)
        self.engine = Engine(modules, scenarios.get(scenario, seed), seed, log_path)
        self._subscribers: list[SnapshotCallback] = []
        self._dirty = True
        self._closed = False
        self.optimizer = optimizer or default_optimizer
        self.runs: dict[str, _Run] = {}
        self._run_ids = IdGen(PREFIX_RUN)
        self._policy_ids = IdGen(PREFIX_POLICY)
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._scanned = 0  # how far engine.bus.delivered was read for policy decisions

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
            self._new_engine()
        elif command == SimCommand.REPLAY:
            try:
                engine, identical = replay(Path(args["trace"]))
            except (KeyError, OSError, ValueError, StopIteration):
                return _refused(RejectReason.UNKNOWN_ID.value)
            self.engine = engine
            self._new_engine()
            return _ok("IDENTICAL" if identical else "DIFFERENT")
        elif command == SimCommand.SPEED:
            self.speed = min(max(float(args.get("factor", 1.0)), MIN_SPEED), MAX_SPEED)
        elif command == SimCommand.OPTIMIZE:
            return self._start_optimization(args)
        return _ok()

    def snapshot(self) -> Snapshot:
        self._sync_runs()
        snap = self.engine.snapshot()
        with self._lock:
            snap["optimization"] = {"runs": [run.view() for run in self.runs.values()]}
        return snap

    # --- optimization (D-65, D-68, OPT-06) ---------------------------------------------

    def _start_optimization(self, args: dict[str, Any]) -> CommandResult:
        method = str(args.get("method") or "SATSA").upper()
        if method not in METHODS:
            return _refused(RejectReason.UNKNOWN_ID.value)
        with self._lock:
            if any(run.status == "RUNNING" for run in self.runs.values()):
                return _refused(RejectReason.INVALID_STATE.value)  # one run at a time
            run = _Run(self._run_ids.next(), method, bool(args.get("quick", True)))
            self.runs[run.run_id] = run
        thread = threading.Thread(target=self._optimize, args=(run,), daemon=True)
        self._threads.append(thread)
        thread.start()
        return _ok(run.run_id)

    def _optimize(self, run: _Run) -> None:
        def progress(fraction: float, evaluated: int) -> None:
            with self._lock:
                run.progress, run.evaluated = fraction, evaluated
            self._dirty = True

        try:
            outcome = self.optimizer(run.method, run.quick, progress)
        except Exception as err:  # reported in the snapshot, never crashes the server
            with self._lock:
                run.status, run.detail = "FAILED", f"{type(err).__name__}: {err}"
        else:
            with self._lock:
                run.outcome = outcome
                run.evaluated = outcome.unique_evaluations
                run.progress = 1.0
                if outcome.best is None:
                    run.status, run.detail = "FAILED", "every configuration was rejected (D-66)"
                else:
                    r, w = outcome.best
                    run.policy = Policy(version=self._policy_ids.next(), R=r, W=w)
                    run.status = "DONE"
        self._dirty = True

    def wait_for_optimizations(self, timeout: float | None = None) -> None:
        """Block until background runs end (tests and the CLI only)."""
        for thread in list(self._threads):
            thread.join(timeout)
        self._sync_runs()

    def _new_engine(self) -> None:
        """A reset or replay loses Control Center's memory: offer pending runs again."""
        self._scanned = 0
        with self._lock:
            for run in self.runs.values():
                if run.decision == "PENDING":
                    run.submitted = False
        self._sync_runs()

    def _sync_runs(self) -> None:
        """Submit finished recommendations; read decisions from the log (incrementally)."""
        with self._lock:
            ready = [r for r in self.runs.values() if r.status == "DONE" and not r.submitted]
        if self.engine.state != SimState.STOPPED:
            for run in ready:
                assert run.policy is not None and run.outcome is not None
                seeds = Inputs.standard(run.quick).train_seeds
                prefix = "QUICK" if run.quick else "TRAIN"
                self.engine.submit(
                    PolicyRecommended(
                        t_sim=0.0,
                        run_id=run.run_id,
                        policy=run.policy,
                        objective=run.outcome.objective or 0.0,
                        scenario_set=tuple(f"{prefix}-{s}" for s in seeds),
                    )
                )
                run.submitted = True
                self._dirty = True
        delivered = self.engine.bus.delivered
        new, self._scanned = delivered[self._scanned :], len(delivered)
        if not new:
            return
        with self._lock:
            by_version = {r.policy.version: r for r in self.runs.values() if r.policy}
            for msg in new:
                if isinstance(msg, PolicyActivated) and msg.policy.version in by_version:
                    by_version[msg.policy.version].decision = "APPROVED"
                elif (
                    isinstance(msg, PolicyDecision)
                    and not msg.approve
                    and msg.role == Role.SUPERVISOR
                    and msg.run_id in self.runs
                    and self.runs[msg.run_id].decision == "PENDING"
                ):
                    self.runs[msg.run_id].decision = "REJECTED"

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
        self._sync_runs()
        if self.engine.state == SimState.RUNNING:
            self.engine.advance_to(self.engine.now + wall_dt * self.speed)
            self._dirty = True

    async def publish(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        snap = self.snapshot()
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
