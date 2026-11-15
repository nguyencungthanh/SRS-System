"""app/: headless CLI, replay CLI, web entry point, SimRunner (D-58..D-60)."""

import asyncio
import importlib.util

import pytest

import app.__main__ as web
from app import headless
from app.modules import load, module_set
from app.runner import MAX_SPEED, SimRunner
from contracts.enums import Direction, SimState
from contracts.messages import HallCallPressed
from contracts.runtime import SimCommand, SimControl

FAKE_FLAGS = ["--dispatch", "fake", "--cabin", "fake", "--cc", "fake"]
FAKES, _ = module_set("fake", "fake", "fake")


def test_AT_01_headless_prints_the_oracle_times(capsys):
    assert headless.main(["--scenario", "AT-01", *FAKE_FLAGS]) == 0
    out = capsys.readouterr().out
    assert "dispatch: fake" in out
    assert "8.0  P1  HALL_CALL_SERVED" in out
    assert "58.0  P1  DEST_SERVED" in out
    assert "64.0  P1  DOOR_CLOSED_LOCKED     floor 5" in out


def test_D_59_headless_trace_then_replay_is_identical(tmp_path, capsys):
    trace = tmp_path / "trace.jsonl"
    log = tmp_path / "log.jsonl"
    assert (
        headless.main(
            ["--scenario", "AT-03", *FAKE_FLAGS, "--trace", str(trace), "--log", str(log)]
        )
        == 0
    )
    assert trace.exists() and log.read_text().count("\n") > 100
    capsys.readouterr()
    assert headless.main(["--replay", str(trace)]) == 0
    assert "replay: IDENTICAL" in capsys.readouterr().out


def test_D_60_auto_falls_back_to_fakes_and_says_so():
    for kind, real in (("dispatch", "dispatch"), ("cabin", "cabin"), ("cc", "control_center")):
        if importlib.util.find_spec(real) is not None:
            continue
        _, chosen, label = load(kind, "auto")
        assert chosen == "fake" and "not found" in label


@pytest.mark.skipif(importlib.util.find_spec("api") is not None, reason="api/ exists now")
def test_web_entry_without_api_exits_with_a_hint(capsys):
    assert web.main(FAKE_FLAGS) == 2
    assert "python -m app.headless" in capsys.readouterr().err


def test_D_58_runner_is_a_SimControl():
    assert isinstance(SimRunner(FAKES, "AT-01"), SimControl)


def _drive(speed: float) -> str:
    runner = SimRunner(FAKES, "AT-01", speed=speed)
    runner.control(SimCommand.START)
    wall_step = 1.0 / speed  # one simulated second per tick, whatever the speed
    while runner.engine.now < 3.0:
        runner.tick(wall_step)
    runner.submit(HallCallPressed(t_sim=0.0, floor=10, direction=Direction.UP))
    while runner.engine.state != SimState.STOPPED:
        runner.tick(wall_step)
    return runner.engine.log_sha256()


def test_NFR_05_runner_speed_does_not_change_decisions():
    assert _drive(1.0) == _drive(5.0)


def test_D_58_runner_controls_and_pushes_snapshots(tmp_path):
    runner = SimRunner(FAKES, "AT-01")
    seen = []

    async def collect(snapshot):
        seen.append(snapshot)

    unsubscribe = runner.subscribe(collect)
    assert runner.control(SimCommand.RESET, scenario="AT-04", seed=0)["ok"]
    assert runner.control("START")["ok"]
    runner.tick(1.0)
    asyncio.run(runner.publish())
    assert seen and seen[-1]["sim_state"] == SimState.RUNNING and seen[-1]["t_sim"] == 1.0
    assert runner.snapshot()["cars"][0]["car_id"] == "P1"

    assert not runner.control("BOGUS")["ok"]
    assert not runner.control(SimCommand.RESET, scenario="NOPE")["ok"]
    runner.control(SimCommand.SPEED, factor=1_000)
    assert runner.speed == MAX_SPEED
    assert "AT-01" in runner.scenarios()

    unsubscribe()
    runner.tick(1.0)
    asyncio.run(runner.publish())
    assert seen[-1]["t_sim"] == 1.0  # no longer subscribed

    runner.engine.run_to_end()
    refused = runner.submit(HallCallPressed(t_sim=0.0, floor=10, direction=Direction.UP))
    assert not refused["ok"]


def test_D_58_runner_replay_command(tmp_path):
    runner = SimRunner(FAKES, "AT-01")
    runner.control(SimCommand.START)
    runner.engine.advance_to(80.0)
    path = tmp_path / "trace.jsonl"
    runner.engine.save_trace(path)
    assert runner.control(SimCommand.REPLAY, trace=str(path))["id"] == "IDENTICAL"
    assert not runner.control(SimCommand.REPLAY, trace=str(tmp_path / "missing.jsonl"))["ok"]


# --- OPTIMIZE from the UI (D-65, D-68, OPT-06) -------------------------------------------

import threading  # noqa: E402

from contracts.enums import Role  # noqa: E402
from contracts.messages import PolicyActivated, PolicyDecision, PolicyRecommended  # noqa: E402
from optimization.report import Outcome  # noqa: E402


def fake_optimizer(best=(2, 3), gate: threading.Event | None = None, fail=False):
    def run(method, quick, progress):
        progress(0.5, 3)
        if gate is not None:
            gate.wait(5)
        if fail:
            raise RuntimeError("boom")
        return Outcome(method, best, 40.0 if best else None, 45.0, 6)

    return run


def _view(runner, run_id):
    return next(r for r in runner.snapshot()["optimization"]["runs"] if r["run_id"] == run_id)


def test_OPT_06_D_68_ui_run_recommend_approve_activate():
    runner = SimRunner(FAKES, "AT-01", optimizer=fake_optimizer())
    runner.control(SimCommand.START)
    result = runner.control(SimCommand.OPTIMIZE, method="SATSA", quick=True)
    assert result["ok"] and result["id"] == "RUN-0001"
    runner.wait_for_optimizations()
    view = _view(runner, "RUN-0001")
    assert (view["status"], view["best"], view["decision"]) == (
        "DONE",
        {"version": "POL-0001", "R": 2, "W": 3},
        "PENDING",
    )
    assert (view["objective"], view["baseline_objective"], view["evaluated"]) == (40.0, 45.0, 6)
    runner.tick(0.1)
    (rec,) = runner.engine.bus.select(PolicyRecommended)
    assert rec.run_id == "RUN-0001" and (rec.policy.R, rec.policy.W) == (2, 3)
    assert rec.scenario_set == ("QUICK-1",)
    runner.submit(PolicyDecision(t_sim=0.0, run_id="RUN-0001", approve=True, role=Role.SUPERVISOR))
    runner.tick(0.1)
    assert runner.engine.bus.select(PolicyActivated)
    assert _view(runner, "RUN-0001")["decision"] == "APPROVED"
    assert runner.snapshot()["policy"] == {"version": "POL-0001", "R": 2, "W": 3}


def test_D_68_one_run_at_a_time_and_bad_method_refused():
    gate = threading.Event()
    runner = SimRunner(FAKES, "AT-01", optimizer=fake_optimizer(gate=gate))
    assert runner.control(SimCommand.OPTIMIZE, method="GRID")["ok"]
    assert not runner.control(SimCommand.OPTIMIZE, method="GRID")["ok"]  # still running
    assert _view(runner, "RUN-0001")["status"] == "RUNNING"
    gate.set()
    runner.wait_for_optimizations()
    assert not runner.control(SimCommand.OPTIMIZE, method="RANDOM")["ok"]


def test_D_68_failures_and_rejections_are_shown_not_raised():
    runner = SimRunner(FAKES, "AT-01", optimizer=fake_optimizer(fail=True))
    runner.control(SimCommand.OPTIMIZE, method="GRID")
    runner.wait_for_optimizations()
    view = _view(runner, "RUN-0001")
    assert view["status"] == "FAILED" and "boom" in view["detail"]
    runner.optimizer = fake_optimizer(best=None)
    runner.control(SimCommand.OPTIMIZE, method="GRID")
    runner.wait_for_optimizations()
    assert _view(runner, "RUN-0002")["status"] == "FAILED"
    assert not runner.engine.bus.select(PolicyRecommended)


def test_D_68_reset_offers_a_pending_recommendation_again():
    runner = SimRunner(FAKES, "AT-01", optimizer=fake_optimizer())
    runner.control(SimCommand.OPTIMIZE, method="SATSA")
    runner.wait_for_optimizations()
    runner.tick(0.1)
    runner.control(SimCommand.RESET, scenario="AT-02")  # a fresh, paused engine
    runner.control(SimCommand.START)
    runner.tick(0.1)
    assert len(runner.engine.bus.select(PolicyRecommended)) == 1  # offered again
    assert _view(runner, "RUN-0001")["decision"] == "PENDING"
