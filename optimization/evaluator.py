"""Evaluate a policy theta = (R, W) on fixed traces (AN-06, OPT-04, D-66, D-67).

``Evaluator.evaluate_many(thetas)`` simulates every (theta, seed) pair that is not cached
yet, in parallel processes, and returns one ``Evaluation`` per theta: its runs, J(theta),
and why it was rejected if any run broke a hard invariant. A rejected configuration is
never scored with a penalty (SRS 4.8.2): its objective is None.
"""

import os
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.modules import load
from contracts.config import TRACE_DURATION_S, Policy
from contracts.enums import DispatchAlgorithm
from contracts.messages import Message
from optimization.metrics import RunMetrics, measure, objective
from scenarios.traffic import random_traffic
from simulation.bus import ContractViolation
from simulation.engine import Engine, ModuleSet

Theta = tuple[int, int]
Progress = Callable[[int, int], None]  # (done, total) simulated runs


class TimedDispatch:
    """Wraps Dispatch to time every handle() call (NFR-04, D-67). Behaviour is unchanged."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.times_s: list[float] = []

    def handle(self, msg: Message, now: float) -> list[Message]:
        start = time.perf_counter()
        out = self.inner.handle(msg, now)
        self.times_s.append(time.perf_counter() - start)
        return out

    def snapshot(self) -> dict:
        return self.inner.snapshot()


@dataclass(frozen=True)
class Job:
    seed: int
    R: int
    W: int
    trace_s: float
    algorithm: str
    modules: str  # "auto" | "real" | "fake" (app/modules.py)
    label: str  # TRAIN / HOLDOUT / QUICK


def run_job(job: Job) -> RunMetrics:
    """Simulate one trace for one policy. Top-level so worker processes can import it."""
    dispatch_pkg, _, _ = load("dispatch", job.modules)
    cabin_pkg, _, _ = load("cabin", job.modules)
    cc_pkg, _, _ = load("cc", job.modules)
    timed: list[TimedDispatch] = []

    def make_dispatch(policy: Policy) -> TimedDispatch:
        wrapper = TimedDispatch(dispatch_pkg.create(policy, DispatchAlgorithm(job.algorithm)))
        timed.append(wrapper)
        return wrapper

    modules = ModuleSet(
        dispatch=make_dispatch,
        cabin=cabin_pkg.create,
        cc=cc_pkg.create,
        kinds={},
    )
    policy = Policy(version=f"EVAL-R{job.R}-W{job.W}", R=job.R, W=job.W)
    scenario = random_traffic(
        job.seed, trace_s=job.trace_s, policy=policy, name=f"{job.label}-{job.seed}"
    )
    engine = Engine(modules, scenario, job.seed)
    extra: list[str] = []
    try:
        engine.run_to_end()
    except ContractViolation as err:  # includes a livelock (D-66)
        extra.append(f"CONTRACT_VIOLATION: {err}")
    times = timed[-1].times_s if timed else []
    return measure(engine, job.seed, job.algorithm, times, extra)


@dataclass(frozen=True)
class Evaluation:
    R: int
    W: int
    algorithm: str
    runs: tuple[RunMetrics, ...]
    objective: float | None  # J(theta); None when rejected

    @property
    def theta(self) -> Theta:
        return (self.R, self.W)

    @property
    def rejected(self) -> tuple[str, ...]:
        return tuple(f"seed {r.seed}: {b}" for r in self.runs for b in r.breaches)


class Evaluator:
    def __init__(
        self,
        seeds: Sequence[int],
        trace_s: float = TRACE_DURATION_S,
        algorithm: DispatchAlgorithm = DispatchAlgorithm.COMMITMENT,
        workers: int | None = None,
        modules: str = "auto",
        label: str = "TRAIN",
    ) -> None:
        if not seeds:
            raise ValueError("an evaluation needs at least one trace seed")
        self.seeds = tuple(seeds)
        self.trace_s = trace_s
        self.algorithm = DispatchAlgorithm(algorithm)
        self.workers = workers or os.cpu_count() or 1
        self.modules = modules
        self.label = label
        self.cache: dict[Theta, Evaluation] = {}

    def jobs_for(self, theta: Theta) -> list[Job]:
        return [
            Job(
                seed=s,
                R=theta[0],
                W=theta[1],
                trace_s=self.trace_s,
                algorithm=self.algorithm.value,
                modules=self.modules,
                label=self.label,
            )
            for s in self.seeds
        ]

    def evaluate_many(
        self, thetas: Sequence[Theta], progress: Progress | None = None
    ) -> list[Evaluation]:
        todo = [t for t in dict.fromkeys(thetas) if t not in self.cache]
        jobs = [job for theta in todo for job in self.jobs_for(theta)]
        results = self._run(jobs, progress)
        by_theta: dict[Theta, list[RunMetrics]] = {t: [] for t in todo}
        for job, metrics in zip(jobs, results, strict=True):
            by_theta[(job.R, job.W)].append(metrics)
        for theta, runs in by_theta.items():
            self.cache[theta] = Evaluation(
                R=theta[0],
                W=theta[1],
                algorithm=self.algorithm.value,
                runs=tuple(runs),
                objective=objective(runs),
            )
        return [self.cache[t] for t in thetas]

    def evaluate(self, theta: Theta) -> Evaluation:
        return self.evaluate_many([theta])[0]

    def _run(self, jobs: list[Job], progress: Progress | None) -> list[RunMetrics]:
        if not jobs:
            return []
        if self.workers <= 1 or len(jobs) == 1:
            out = []
            for i, job in enumerate(jobs):
                out.append(run_job(job))
                if progress:
                    progress(i + 1, len(jobs))
            return out
        with ProcessPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
            futures = [pool.submit(run_job, job) for job in jobs]
            out = []
            for i, future in enumerate(futures):
                out.append(future.result())
                if progress:
                    progress(i + 1, len(jobs))
            return out
