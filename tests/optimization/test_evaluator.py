"""Evaluator, J(theta), rejection, inputs and the AT-06 report (OPT-01, OPT-04, AT-06)."""

import dataclasses
import json

import pytest

from optimization import evaluator as ev_module
from optimization.evaluator import Evaluator, Job, run_job
from optimization.metrics import RunMetrics, objective
from optimization.report import GRID, SATSA, Inputs, compare, optimize
from simulation.bus import ContractViolation

SHORT = 120.0  # seconds of traffic: enough to exercise the pipeline quickly


def metrics(**over) -> RunMetrics:
    base = dict(
        scenario="T",
        seed=1,
        R=3,
        W=5,
        algorithm="COMMITMENT",
        calls=10,
        unserved=1,
        mean_wait=20.0,
        median_wait=18.0,
        p95_wait=60.0,
        max_wait=70.0,
        delay_violations=0,
        late_commitments=0,
        max_lateness=None,
        medical_pickup_s=(),
        freight_completion_s=(),
        dispatch_ms_median=None,
        dispatch_ms_max=None,
        breaches=(),
    )
    base.update(over)
    return RunMetrics(**base)


def test_eq_6_objective_is_mean_plus_lambda_p95_plus_mu_unserved_fraction():
    run = metrics()
    assert run.score() == 20.0 + 0.5 * 60.0 + 100.0 * 1 / 10
    assert objective([run, metrics(mean_wait=10.0)]) == (60.0 + 50.0) / 2


def test_D_66_one_invalid_run_rejects_the_configuration():
    assert objective([metrics(), metrics(breaches=("DELAY_CAP x1",))]) is None
    assert objective([]) is None


def test_D_66_a_contract_violation_is_a_breach_not_a_crash(monkeypatch):
    def explode(self):
        raise ContractViolation("livelock: test")

    monkeypatch.setattr(ev_module.Engine, "run_to_end", explode)
    run = run_job(
        Job(seed=1, R=3, W=5, trace_s=SHORT, algorithm="COMMITMENT", modules="fake", label="T")
    )
    assert run.breaches and run.breaches[-1].startswith("CONTRACT_VIOLATION")


def test_OPT_04_evaluator_caches_and_parallel_equals_serial():
    serial = Evaluator((1, 2), SHORT, workers=1, modules="fake")
    first = serial.evaluate((3, 5))
    assert serial.evaluate((3, 5)) is first  # cached
    parallel = Evaluator((1, 2), SHORT, workers=2, modules="fake").evaluate((3, 5))

    def comparable(e):
        return [
            dataclasses.replace(r, dispatch_ms_median=None, dispatch_ms_max=None) for r in e.runs
        ]

    assert comparable(first) == comparable(parallel)
    assert first.objective == parallel.objective is not None


def test_OPT_01_incomplete_or_conflicting_inputs_are_rejected():
    good = Inputs((1,), (101,), SHORT)
    good.validate()
    for bad in (
        Inputs((), (101,), SHORT),
        Inputs((1,), (1,), SHORT),
        Inputs((1,), (101,), 0.0),
        Inputs((1,), (101,), SHORT, lam=-1.0),
        Inputs((1,), (101,), SHORT, reference=(9, 5)),
        Inputs((1,), (101,), SHORT, budget=0),
    ):
        with pytest.raises(ValueError):
            bad.validate()
    with pytest.raises(ValueError):
        optimize("RANDOM", inputs=good)


def test_D_65_optimize_returns_a_recommendation_and_reports_progress():
    seen = []
    inputs = Inputs((1,), (101,), SHORT, budget=5)
    shared = Evaluator((1,), SHORT, workers=1, modules="fake")
    outcome = optimize(SATSA, inputs=inputs, evaluator=shared, progress=lambda f, n: seen.append(n))
    assert outcome.best is not None and outcome.unique_evaluations <= 5
    assert outcome.baseline_objective is not None and seen
    fast = Evaluator((1,), SHORT, workers=4, modules="fake")
    grid = optimize(GRID, inputs=inputs, evaluator=fast)
    assert grid.unique_evaluations == 55


def test_AT_06_compare_writes_the_report_and_the_run_log(tmp_path):
    inputs = Inputs((1,), (101,), SHORT, budget=6)
    summary = compare(
        workers=4, modules="fake", out_dir=tmp_path, inputs=inputs, log=lambda s: None
    )
    text = (tmp_path / "optimization.md").read_text()
    for heading in ("## Inputs (OPT-01)", "## Training", "## Held-out comparison", "J(R, W)"):
        assert heading in text
    for label in ("fixed (3, 5)", "FIRST_IDLE baseline", "SWEEP baseline"):
        assert label in text
    lines = [
        json.loads(line) for line in (tmp_path / "optimization_runs.jsonl").read_text().splitlines()
    ]
    kinds = {line["type"] for line in lines}
    assert {"run", "candidate", "satsa_step", "holdout", "approval"} <= kinds
    assert sum(1 for line in lines if line["type"] == "candidate") == 55  # every pair (LOG-04)
    assert summary["satsa"]["unique"] <= 6
