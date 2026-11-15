"""Optimization runs, held-out comparison and the results files (OPT-01, OPT-05, LOG-04, NFR-06).

``optimize(method, quick)`` trains GRID or SATSA and returns the recommendation; the UI's
runner uses it. ``compare(quick)`` is AT-06: grid and SATSA on the training traces, then
(3, 5), both winners and the two baseline dispatchers on the held-out traces, written to
``results/optimization.md`` and ``results/optimization_runs.jsonl``.
"""

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from contracts.config import (
    DEFAULT_POLICY,
    HOLDOUT_SEEDS,
    LAMBDA,
    MU_S,
    QUICK_HOLDOUT_SEEDS,
    QUICK_SEEDS,
    QUICK_TRACE_S,
    R_DOMAIN,
    SATSA_MAX_EVALS,
    TRACE_DURATION_S,
    TRAIN_SEEDS,
    W_DOMAIN,
)
from contracts.enums import DispatchAlgorithm
from optimization.evaluator import Evaluation, Evaluator, Theta
from optimization.grid import grid_search
from optimization.satsa import satsa

GRID, SATSA = "GRID", "SATSA"
METHODS = (GRID, SATSA)
BASELINE: Theta = (DEFAULT_POLICY.R, DEFAULT_POLICY.W)
Progress = Callable[[float, int], None]  # (fraction done, unique (R, W) scored)


@dataclass(frozen=True)
class Inputs:
    """The input package of an optimization request (OPT-01)."""

    train_seeds: tuple[int, ...]
    holdout_seeds: tuple[int, ...]
    trace_s: float
    lam: float = LAMBDA
    mu: float = MU_S
    reference: Theta = BASELINE
    budget: int = SATSA_MAX_EVALS
    satsa_seed: int = 0

    @classmethod
    def standard(cls, quick: bool) -> "Inputs":
        if quick:
            return cls(QUICK_SEEDS, QUICK_HOLDOUT_SEEDS, QUICK_TRACE_S)
        return cls(TRAIN_SEEDS, HOLDOUT_SEEDS, TRACE_DURATION_S)

    def validate(self) -> None:
        """Reject an incomplete or conflicting request (OPT-01)."""
        problems = []
        if not self.train_seeds or not self.holdout_seeds:
            problems.append("training and held-out seeds are both required")
        if set(self.train_seeds) & set(self.holdout_seeds):
            problems.append("training and held-out seeds must not overlap")
        if self.trace_s <= 0:
            problems.append("the trace length must be positive")
        if self.lam < 0 or self.mu < 0:
            problems.append("lambda and mu must not be negative")
        if self.reference[0] not in R_DOMAIN or self.reference[1] not in W_DOMAIN:
            problems.append("the reference policy must lie in the search domain")
        if not 1 <= self.budget <= len(R_DOMAIN) * len(W_DOMAIN):
            problems.append("the SATSA budget must be between 1 and the domain size")
        if problems:
            raise ValueError("; ".join(problems))


@dataclass
class Outcome:
    method: str
    best: Theta | None
    objective: float | None
    baseline_objective: float | None
    unique_evaluations: int
    evaluations: dict[Theta, Evaluation] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)


def _batch(evaluator: Evaluator, on_runs: Callable[[], None] | None = None):
    def evaluate_batch(thetas: Sequence[Theta]) -> dict[Theta, float | None]:
        results = evaluator.evaluate_many(thetas)
        if on_runs:
            on_runs()
        return {e.theta: e.objective for e in results}

    return evaluate_batch


def optimize(
    method: str,
    quick: bool = True,
    workers: int | None = None,
    modules: str = "auto",
    progress: Progress | None = None,
    inputs: Inputs | None = None,
    evaluator: Evaluator | None = None,
) -> Outcome:
    """Train one method on the training traces and return its recommendation."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    inputs = inputs or Inputs.standard(quick)
    inputs.validate()
    ev = evaluator or Evaluator(
        inputs.train_seeds, inputs.trace_s, workers=workers, modules=modules, label="TRAIN"
    )
    total = len(R_DOMAIN) * len(W_DOMAIN) if method == GRID else inputs.budget
    steps: list[dict[str, Any]] = []

    def report() -> None:
        if progress:
            progress(min(1.0, len(ev.cache) / max(1, total)), len(ev.cache))

    batch = _batch(ev, report)
    if method == GRID:
        result = grid_search(batch)
        best, unique = result.best, len(result.scores)
    else:
        result = satsa(batch, budget=inputs.budget, seed=inputs.satsa_seed)
        best, unique = result.best, result.unique_evaluations
        steps = [asdict(s) for s in result.history]
    baseline = ev.evaluate(inputs.reference)
    return Outcome(
        method=method,
        best=best,
        objective=ev.cache[best].objective if best else None,
        baseline_objective=baseline.objective,
        unique_evaluations=unique,
        evaluations=dict(ev.cache),
        steps=steps,
    )


# --- AT-06 comparison ----------------------------------------------------------------


def compare(
    quick: bool = False,
    workers: int | None = None,
    modules: str = "auto",
    out_dir: Path = Path("results"),
    inputs: Inputs | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    inputs = inputs or Inputs.standard(quick)
    inputs.validate()
    started = time.perf_counter()
    train = Evaluator(inputs.train_seeds, inputs.trace_s, workers=workers, modules=modules)
    log(f"grid search on {len(inputs.train_seeds)} training traces ...")
    grid = optimize(GRID, inputs=inputs, evaluator=train)
    log(f"SATSA (budget {inputs.budget}, sharing the grid's cache) ...")
    sat = optimize(SATSA, inputs=inputs, evaluator=train)

    robustness = _satsa_robustness(train, grid.best, inputs)

    log("held-out traces ...")
    rows: list[tuple[str, Evaluation]] = []
    held = Evaluator(
        inputs.holdout_seeds, inputs.trace_s, workers=workers, modules=modules, label="HOLDOUT"
    )
    configs = {f"fixed {BASELINE}": BASELINE}
    if grid.best:
        configs[f"grid best {grid.best}"] = grid.best
    if sat.best:
        configs[f"SATSA best {sat.best}"] = sat.best
    for label, theta in configs.items():
        rows.append((label, held.evaluate(theta)))
    for algorithm in (DispatchAlgorithm.FIRST_IDLE, DispatchAlgorithm.SWEEP):
        baseline = Evaluator(
            inputs.holdout_seeds,
            inputs.trace_s,
            algorithm=algorithm,
            workers=workers,
            modules=modules,
            label="HOLDOUT",
        )
        rows.append((f"{algorithm.value} baseline {BASELINE}", baseline.evaluate(BASELINE)))

    summary = {
        "inputs": asdict(inputs),
        "quick": quick,
        "grid": {"best": grid.best, "objective": grid.objective, "unique": grid.unique_evaluations},
        "satsa": {"best": sat.best, "objective": sat.objective, "unique": sat.unique_evaluations},
        "baseline_train_objective": grid.baseline_objective,
        "rejected": sorted(t for t, e in train.cache.items() if e.objective is None),
        "satsa_robustness": robustness,
        "wall_s": round(time.perf_counter() - started, 1),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "optimization_runs.jsonl", inputs, train, sat, rows, summary)
    (out_dir / "optimization.md").write_text(_markdown(inputs, train, grid, sat, rows, summary))
    log(f"wrote {out_dir / 'optimization.md'} in {summary['wall_s']} s")
    return summary


ROBUSTNESS_SEEDS = tuple(range(20))


def _satsa_robustness(train: Evaluator, grid_best: Theta | None, inputs: Inputs) -> dict:
    """SATSA with 20 seeds on the training table the grid already filled (no new runs).

    One SATSA run says little; this shows how often the search finds the grid's optimum.
    """
    table = {t: e.objective for t, e in train.cache.items()}

    def lookup(thetas: Sequence[Theta]) -> dict[Theta, float | None]:
        return {t: table[t] for t in thetas}

    best_j = table.get(grid_best) if grid_best else None
    found, regrets, picks = 0, [], []
    for s in ROBUSTNESS_SEEDS:
        result = satsa(lookup, budget=inputs.budget, seed=s)
        picks.append(result.best)
        if result.best_objective is not None and best_j is not None:
            regrets.append(result.best_objective - best_j)
            found += result.best_objective <= best_j + 1e-9
    return {
        "seeds": len(ROBUSTNESS_SEEDS),
        "found_grid_optimum": found,
        "median_regret": sorted(regrets)[len(regrets) // 2] if regrets else None,
        "worst_regret": max(regrets) if regrets else None,
        "distinct_picks": len(set(picks)),
    }


def _mean(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def _fmt(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _row(label: str, e: Evaluation) -> str:
    runs = e.runs
    calls = sum(r.calls for r in runs)
    unserved = sum(r.unserved for r in runs)
    cells = [
        label,
        _fmt(e.objective) if e.objective is not None else "rejected",
        _fmt(_mean([r.mean_wait for r in runs])),
        _fmt(_mean([r.p95_wait for r in runs])),
        _fmt(max((r.max_wait or 0.0) for r in runs)),
        f"{unserved}/{calls}",
        str(sum(r.delay_violations for r in runs)),
        str(sum(r.late_commitments for r in runs)),
        _fmt(_mean([r.dispatch_ms_median for r in runs]), 3),
        _fmt(max((r.dispatch_ms_max or 0.0) for r in runs), 2),
    ]
    return "| " + " | ".join(cells) + " |"


def _markdown(inputs, train, grid, sat, rows, summary) -> str:
    lines = [
        "# Offline tuning of (R, W) - AT-06",
        "",
        "Generated by `python -m optimization --method compare`"
        + (" --quick" if summary["quick"] else "")
        + f" in {summary['wall_s']} s. Simulated with the real modules (auto). SRS 4.8, 6.5.",
        "",
        "## Inputs (OPT-01)",
        "",
        f"- Training seeds {list(inputs.train_seeds)}, held-out seeds "
        f"{list(inputs.holdout_seeds)}; each trace {inputs.trace_s:g} s of traffic (D-34, D-64)",
        f"- Domain R in {list(R_DOMAIN)}, W in {list(W_DOMAIN)} (D-33); "
        f"reference {inputs.reference}",
        f"- J(theta) = mean wait + {inputs.lam} x P95 wait + {inputs.mu} x unserved fraction"
        " (eq. 6)",
        f"- SATSA budget {inputs.budget} unique pairs, seed {inputs.satsa_seed} (D-35)",
        "",
        "## Training (choose theta)",
        "",
        "| Method | Best (R, W) | J on training | Unique pairs simulated |",
        "|---|---|---|---|",
        f"| Grid search | {grid.best} | {_fmt(grid.objective)} | {grid.unique_evaluations} |",
        f"| SATSA | {sat.best} | {_fmt(sat.objective)} | {sat.unique_evaluations} |",
        f"| Fixed reference | {inputs.reference} | {_fmt(grid.baseline_objective)} | 1 |",
        "",
        f"Rejected pairs (hard invariant broken, D-66): {summary['rejected'] or 'none'}.",
        "",
        "SATSA robustness (same training table, no new simulations): with "
        f"{summary['satsa_robustness']['seeds']} different SATSA seeds it found the grid's "
        f"optimum {summary['satsa_robustness']['found_grid_optimum']} times; median gap to it "
        f"{_fmt(summary['satsa_robustness']['median_regret'])}, worst "
        f"{_fmt(summary['satsa_robustness']['worst_regret'])} "
        f"({summary['satsa_robustness']['distinct_picks']} different pairs chosen).",
        "",
        "## Held-out comparison (report these, not the training numbers)",
        "",
        "| Configuration | J | Mean wait (s) | P95 wait (s) | Max wait (s) | Unserved | "
        "Dispatch DELAY_CAP violations | Late commitments (observed) | Dispatch ms median | "
        "Dispatch ms max |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *[_row(label, e) for label, e in rows],
        "",
        "## J(R, W) on the training traces",
        "",
        "| R \\ W | " + " | ".join(str(w) for w in W_DOMAIN) + " |",
        "|---" * (len(W_DOMAIN) + 1) + "|",
    ]
    for r in R_DOMAIN:
        cells = []
        for w in W_DOMAIN:
            e = train.cache.get((r, w))
            cells.append("?" if e is None else ("X" if e.objective is None else _fmt(e.objective)))
        lines.append(f"| {r} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## How to read this",
        "",
        "- Lower J is better. Only the held-out table says whether a tuned policy generalises;",
        "  no improvement is claimed unless its held-out J is lower than the fixed (3, 5).",
        "- *Dispatch DELAY_CAP violations* count ACKed Dispatch routes that push a committed",
        "  stop more than 10 s past its reference (D-63); any violation rejects a configuration.",
        "- *Late commitments* also include delays nobody decided (destinations pressed by",
        "  passengers after a stop was committed, door re-openings); they are reported only.",
        "- Dispatch time is wall-clock time of one `Dispatch.handle` call (NFR-04, D-67).",
        "- SATSA's settings were chosen on synthetic surfaces (see optimization/satsa.py).",
        "- A recommendation changes nothing until a supervisor approves it (OPT-06).",
        "",
    ]
    return "\n".join(lines)


def _write_jsonl(path: Path, inputs, train, sat, rows, summary) -> None:
    def theta_runs(e: Evaluation) -> list[dict[str, Any]]:
        return [asdict(r) for r in e.runs]

    with path.open("w") as f:

        def put(obj: dict[str, Any]) -> None:
            f.write(json.dumps(obj, sort_keys=True, default=list) + "\n")

        put({"type": "run", "inputs": asdict(inputs), "summary": summary})
        for theta, e in sorted(train.cache.items()):
            put(
                {
                    "type": "candidate",
                    "theta": theta,
                    "objective": e.objective,
                    "rejected": e.rejected,
                    "runs": theta_runs(e),
                }
            )
        for step in sat.steps:
            put({"type": "satsa_step", **step})
        for label, e in rows:
            put(
                {
                    "type": "holdout",
                    "label": label,
                    "theta": e.theta,
                    "algorithm": e.algorithm,
                    "objective": e.objective,
                    "runs": theta_runs(e),
                }
            )
        put({"type": "approval", "status": "PENDING", "note": "decided in Control Center (OPT-06)"})
