"""Engine: states, pause, reset, log, network rules, replay (SIM-01..03, LOG-01/02, NFR-05)."""

import dataclasses
import json

import pytest

import scenarios
from app.modules import module_set
from app.replay import read_trace, replay
from contracts.enums import (
    Direction,
    EventType,
    MissionStatus,
    ServiceEventType,
    SimState,
)
from contracts.messages import (
    AlarmPressed,
    HallCallAck,
    HallCallPressed,
    MissionStatusUpdate,
    PassengerExit,
    RouteReply,
    ServiceEvent,
)
from contracts.ui import FleetCarView, Snapshot
from simulation.engine import Engine
from simulation.scenario import DELAY, NetworkRule

FAKES, _ = module_set("fake", "fake", "fake")


def engine(name="AT-01", **kw) -> Engine:
    return Engine(FAKES, scenarios.get(name), **kw)


def events(e: Engine, car: str, kind: ServiceEventType) -> list[ServiceEvent]:
    return [m for m in e.bus.select(ServiceEvent, car_id=car) if m.event == kind]


def test_SIM_01_states_paused_running_stopped():
    e = engine()
    assert (e.state, e.now) == (SimState.PAUSED, 0.0)
    e.start()
    assert e.state == SimState.RUNNING
    e.pause()
    assert e.state == SimState.PAUSED
    e.start()
    e.advance_to(1_000.0)
    assert (e.state, e.now) == (SimState.STOPPED, 80.0)  # AT-01 lasts 80 s


def test_SIM_02_pause_freezes_time_and_queues_inputs():
    e = engine()
    e.start()
    e.advance_to(5.0)
    e.pause()
    delivered = len(e.bus.delivered)
    e.advance_to(30.0)
    assert e.now == 5.0 and len(e.bus.delivered) == delivered
    e.submit(HallCallPressed(t_sim=0.0, floor=10, direction=Direction.UP))
    assert len(e.bus.delivered) == delivered and e.snapshot()["t_sim"] == 5.0
    e.start()
    e.advance_to(6.0)
    acks = [a for a in e.bus.select(HallCallAck) if a.floor == 10]
    assert [a.t_sim for a in acks] == [5.0]


def test_SIM_03_reset_returns_to_the_initial_state():
    fresh = engine().snapshot()
    e = engine()
    e.run_to_end()
    e.reset()
    assert e.snapshot() == fresh
    assert e.state == SimState.PAUSED


def test_AT_01_engine_with_virtual_passengers_and_fakes():
    e = engine()
    e.run_to_end()
    assert [m.t_sim for m in events(e, "P1", ServiceEventType.HALL_CALL_SERVED)] == [8.0]
    assert [m.t_sim for m in events(e, "P1", ServiceEventType.DEST_SERVED)] == [58.0]
    closed_at_5 = [m.t_sim for m in events(e, "P1", ServiceEventType.DOOR_CLOSED_LOCKED)]
    assert closed_at_5[-1] == 64.0
    assert [m.t_sim for m in e.bus.select(PassengerExit)] == [58.0]
    metrics = e.metrics()
    assert (metrics["mean_wait"], metrics["p95_wait"], metrics["unserved"]) == (8.0, 8.0, 0)


def test_F10_engine_snapshot_shape_and_strict_json():
    e = engine()
    before = e.snapshot()
    assert all(car["telemetry_age_s"] is None for car in before["cars"])
    e.start()
    e.advance_to(10.0)
    snap = e.snapshot()
    assert set(snap) == set(Snapshot.__annotations__)
    assert all(set(car) == set(FleetCarView.__annotations__) for car in snap["cars"])
    assert snap["sim_state"] == SimState.RUNNING and snap["t_sim"] == 10.0
    assert 0 < len(snap["last_events"]) <= 50
    json.dumps(snap, allow_nan=False)
    json.dumps(before, allow_nan=False)


def test_AT_04_completes_with_fakes():
    e = engine("AT-04")
    e.run_to_end()
    statuses = [(u.status, u.t_sim) for u in e.bus.select(MissionStatusUpdate)]
    assert (MissionStatus.COMPLETED, 124.0) in statuses
    assert [m.t_sim for m in events(e, "P1", ServiceEventType.MISSION_DELIVERY_DONE)] == [122.0]


def test_D_57_drop_rule_loses_the_first_ack():
    e = engine("AT-05-ack-lost")
    e.run_to_end()
    dropped = [x for x in e.bus.log if x.get("event") == EventType.MESSAGE_DROPPED]
    assert len(dropped) == 1
    assert dropped[0]["message"]["msg_type"] == "RouteReply"
    assert dropped[0]["message"]["car_id"] == "P1"
    assert not e.bus.select(RouteReply, car_id="P1", command_id=dropped[0]["message"]["command_id"])


def test_D_57_delay_rule_delivers_later():
    scenario = dataclasses.replace(
        scenarios.get("AT-01"),
        network=(NetworkRule(msg_type="RouteReply", car_id="P1", action=DELAY, delay_s=3.0),),
    )
    e = Engine(FAKES, scenario)
    e.run_to_end()
    (first,) = e.bus.select(RouteReply, car_id="P1")[:1]
    assert (first.t_sim, first.deliver_at) == (0.0, 3.0)


def test_LOG_01_jsonl_file_has_every_entry(tmp_path):
    path = tmp_path / "log.jsonl"
    e = engine(log_path=path)
    e.run_to_end()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == len(e.bus.log)
    assert lines[0]["event"] == EventType.SIM_RESET
    assert any(line.get("msg_type") == "HallCallAck" for line in lines)


def _live_run(tmp_path):
    e = engine()
    e.start()
    e.advance_to(3.0)
    e.submit(HallCallPressed(t_sim=0.0, floor=10, direction=Direction.UP))
    e.advance_to(12.5)
    e.pause()
    e.submit(AlarmPressed(t_sim=0.0, car_id="P2"))
    e.advance_to(40.0)  # paused: no effect
    e.start()
    e.advance_to(80.0)
    path = tmp_path / "trace.jsonl"
    e.save_trace(path)
    return e, path


def test_NFR_05_LOG_02_replay_is_identical(tmp_path):
    live, path = _live_run(tmp_path)
    replayed, identical = replay(path, FAKES)
    assert identical
    assert replayed.log_sha256() == live.log_sha256()


def test_D_59_trace_format(tmp_path):
    live, path = _live_run(tmp_path)
    header, inputs, footer = read_trace(path)
    assert header["scenario"] == "AT-01" and header["seed"] == 0
    assert header["modules"] == {"dispatch": "fake", "cabin": "fake", "cc": "fake"}
    assert [i["msg_type"] for i in inputs] == ["HallCallPressed", "AlarmPressed"]
    assert [i["t_sim"] for i in inputs] == [3.0, 12.5]
    assert footer["log_sha256"] == live.log_sha256() and footer["end_t"] == 80.0


def test_D_59_tampered_trace_is_different(tmp_path):
    _, path = _live_run(tmp_path)
    original = path.read_text()
    tampered = original.replace('"floor": 10', '"floor": 11')
    assert tampered != original
    path.write_text(tampered)
    _, identical = replay(path, FAKES)
    assert not identical


def test_submit_after_stop_is_refused():
    e = engine()
    e.run_to_end()
    with pytest.raises(RuntimeError):
        e.submit(HallCallPressed(t_sim=0.0, floor=10, direction=Direction.UP))
