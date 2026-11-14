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
