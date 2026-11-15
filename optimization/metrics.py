"""Measurements of one simulated run and the fitness J(theta) (AN-03, SRS 4.8.2 eq. 6)."""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from contracts.config import LAMBDA, MU_S
from contracts.enums import MissionStatus, MissionType, ServiceEventType
from simulation.engine import Engine


@dataclass(frozen=True)
class RunMetrics:
    scenario: str
    seed: int
    R: int
    W: int
    algorithm: str
    calls: int
    unserved: int
    mean_wait: float | None
    median_wait: float | None
    p95_wait: float | None
    max_wait: float | None
    delay_violations: int  # Dispatch decisions breaking DELAY_CAP (D-63)
    late_commitments: int  # stops served more than DELAY_CAP after their reference
    max_lateness: float | None
    medical_pickup_s: tuple[float, ...]  # approval -> MISSION_PICKUP_DONE, MEDICAL
    freight_completion_s: tuple[float, ...]  # approval -> COMPLETED, FREIGHT
    dispatch_ms_median: float | None  # NFR-04, wall time of Dispatch.handle (D-67)
    dispatch_ms_max: float | None
    breaches: tuple[str, ...]  # hard-invariant breaches (D-66); empty = valid run

    @property
    def valid(self) -> bool:
        return not self.breaches

    def score(self) -> float:
        """One scenario's term of eq. (6): mean + lambda * P95 + mu * unserved / total."""
        if self.calls == 0:
            return 0.0
        mean = self.mean_wait or 0.0
        p95 = self.p95_wait or 0.0
        return mean + LAMBDA * p95 + MU_S * self.unserved / self.calls


def objective(runs: Sequence[RunMetrics]) -> float | None:
    """J(theta): the average score over the scenarios, or None if any run is invalid."""
    if not runs or any(not r.valid for r in runs):
        return None
    return sum(r.score() for r in runs) / len(runs)


def measure(
    engine: Engine,
    seed: int,
    algorithm: str,
    dispatch_times_s: Sequence[float] = (),
    extra_breaches: Sequence[str] = (),
) -> RunMetrics:
    obs = engine.observer
    waits = obs.wait_stats()
    approved: dict[str, tuple[float, str]] = {}
    medical: list[float] = []
    freight: list[float] = []
    for entry in engine.bus.log:
        kind = entry.get("msg_type")
        if kind == "MissionApproval":
            approved[entry["mission_id"]] = (entry["t_sim"], entry["mission_type"])
        elif (
            kind == "ServiceEvent"
            and entry["event"] == ServiceEventType.MISSION_PICKUP_DONE
            and entry["ref_id"] in approved
        ):
            t0, mtype = approved[entry["ref_id"]]
            if mtype == MissionType.MEDICAL:
                medical.append(entry["t_sim"] - t0)
        elif (
            kind == "MissionStatusUpdate"
            and entry["status"] == MissionStatus.COMPLETED
            and entry["mission_id"] in approved
        ):
            t0, mtype = approved[entry["mission_id"]]
            if mtype == MissionType.FREIGHT:
                freight.append(entry["t_sim"] - t0)
    times_ms = [t * 1000.0 for t in dispatch_times_s]
    policy = engine.scenario.policy
    return RunMetrics(
        scenario=engine.scenario.name,
        seed=seed,
        R=policy.R,
        W=policy.W,
        algorithm=algorithm,
        calls=obs.calls,
        unserved=obs.unserved,
        mean_wait=waits["mean_wait"],
        median_wait=waits["median_wait"],
        p95_wait=waits["p95_wait"],
        max_wait=waits["max_wait"],
        delay_violations=obs.delay_violations,
        late_commitments=obs.late_commitments,
        max_lateness=max(obs.delays) if obs.delays else None,
        medical_pickup_s=tuple(medical),
        freight_completion_s=tuple(freight),
        dispatch_ms_median=statistics.median(times_ms) if times_ms else None,
        dispatch_ms_max=max(times_ms) if times_ms else None,
        breaches=tuple(obs.breaches()) + tuple(extra_breaches),
    )
