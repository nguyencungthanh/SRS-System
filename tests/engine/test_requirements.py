"""Requirements that no other test named yet (SRS 7.1, 7.6, 7.8, 7.9, Table NFR).

Each test runs the integrated system (real modules when present, else the fakes, D-60) and
checks the requirement's own words. Together with the per-module suites this makes every
SRS requirement ID appear in at least one test name.
"""

import ast
import json
from pathlib import Path

import scenarios
from app.modules import module_set
from app.runner import SimRunner
from contracts.config import CARS, DELAY_CAP_S, FLOORS, MAX_FLOOR, MIN_FLOOR, TELEMETRY_HEARTBEAT_S
from contracts.enums import Direction, FaultKind, ServiceEventType, SimState
from contracts.messages import (
    CabinInit,
    CabinTelemetry,
    FaultInjected,
    HallCallPressed,
    PolicyRecommended,
)
from contracts.routing import recipients
from contracts.runtime import SimCommand
from optimization.metrics import measure
from optimization.report import Outcome
from scenarios.traffic import random_traffic
from simulation.engine import Engine, ModuleSet
from simulation.scenario import Scenario

ROOT = Path(__file__).resolve().parents[2]
AUTO, _ = module_set()


def run(scenario: Scenario, modules: ModuleSet = AUTO) -> Engine:
    engine = Engine(modules, scenario)
    engine.run_to_end()
    return engine


def fault_scenario() -> Scenario:
    """P1 owns 27 DOWN, then breaks down at t=3; P2 must take the call over."""
    return Scenario(
        name="FAULT-REASSIGN",
        fleet=(CabinInit(car_id="P1", floor=31), CabinInit(car_id="P2", floor=10)),
        duration_s=120.0,
        inputs=(
            HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN),
            FaultInjected(t_sim=3.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS),
        ),
    )


# --- 7.1 Building and fleet ------------------------------------------------------------


def test_SYS_01_forty_floors_and_five_separately_identified_cabins():
    assert (MIN_FLOOR, MAX_FLOOR, FLOORS) == (1, 40, 40)
    assert CARS == ("P1", "P2", "P3", "P4", "G1")
    engine = Engine(AUTO, scenarios.get("DEMO"))
    cars = engine.snapshot()["cars"]
    assert [c["car_id"] for c in cars] == list(CARS)
    assert len({id(engine.cabins[c]) for c in CARS}) == 5  # one instance each


HARDWARE_IMPORTS = {
    "socket", "serial", "usb", "can", "ctypes", "subprocess", "requests", "urllib",
    "http", "smbus", "smbus2", "RPi", "gpiozero", "pymodbus", "minimalmodbus", "telnetlib",
}  # fmt: skip
MODULE_PACKAGES = ("contracts", "simulation", "dispatch", "cabin", "control_center", "fakes")


def test_SYS_04_modules_only_talk_to_simulated_adapters():
    found = []
    for package in MODULE_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                found += [
                    f"{path.relative_to(ROOT)}: {n}"
                    for n in names
                    if n.split(".")[0] in HARDWARE_IMPORTS
                ]
    assert found == []
    # Actuators are simulated: a fault reaches only the simulated cabin, never machinery.
    assert recipients(FaultInjected(t_sim=0.0, car_id="P1", kind=FaultKind.MOTION_SERIOUS)) == (
        "cabin:P1",
    )


# --- 7.6 Sensors -------------------------------------------------------------------------


def test_SEN_01_telemetry_carries_every_reading_with_a_timestamp_and_car_id():
    engine = run(scenarios.get("AT-01"))
    samples = [e for e in engine.bus.log if e.get("msg_type") == "CabinTelemetry"]
    assert samples
    readings = ("position", "door_zone", "locked", "load_kg", "obstruction", "faults")
    for e in samples:
        assert e["car_id"] in CARS and isinstance(e["t_sim"], float)
        assert all(key in e for key in readings)
    for car in {e["car_id"] for e in samples}:
        times = [e["t_sim"] for e in samples if e["car_id"] == car]
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        assert max(gaps) <= TELEMETRY_HEARTBEAT_S + 1e-9  # D-11 heartbeat


# --- 7.8 Simulation ----------------------------------------------------------------------


class Spy:
    """Wraps a cabin and records the type of every message it sends."""

    def __init__(self, inner, sent: set[str]) -> None:
        self.inner, self.sent = inner, sent

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def handle(self, msg, now):
        out = self.inner.handle(msg, now)
        self.sent.update(type(m).__name__ for m in out)
        return out


FEEDBACK = {
    "CabinTelemetry", "ServiceEvent", "RouteReply", "RouteVersionReport",
    "MissionStatusUpdate", "Alert", "Timer",
}  # fmt: skip
OWNERSHIP = {"RouteProposal", "RouteVersionQuery", "HallCallAck", "MissionApproval"}


def test_SIM_08_cabins_publish_feedback_and_never_rewrite_ownership():
    sent: set[str] = set()
    spied = ModuleSet(
        dispatch=AUTO.dispatch, cabin=lambda init: Spy(AUTO.cabin(init), sent), cc=AUTO.cc, kinds={}
    )
    for scenario in (*(scenarios.get(n) for n in ("AT-03", "AT-05-ack-lost")), fault_scenario()):
        run(scenario, spied)
    assert sent <= FEEDBACK, sent - FEEDBACK
    assert not sent & OWNERSHIP
    assert {"CabinTelemetry", "ServiceEvent", "RouteReply"} <= sent


# --- 7.8 / UI ----------------------------------------------------------------------------


def test_UI_03_destinations_show_pending_then_served():
    engine = Engine(AUTO, scenarios.get("AT-01"))
    engine.start()
    engine.advance_to(30.0)  # P1 picked the passenger up at 27, destination 5 chosen
    pending = [d for d in engine.snapshot()["dests"] if d["floor"] == 5]
    assert [d["status"] for d in pending] == ["PENDING"]
    engine.advance_to(engine.end_t)
    served = [d for d in engine.snapshot()["dests"] if d["floor"] == 5]
    assert [d["status"] for d in served] == ["SERVED"]
    # The page offers only valid floors and draws the two states differently.
    js = (ROOT / "web" / "app.js").read_text()
    css = (ROOT / "web" / "style.css").read_text()
    assert f"const FLOORS = {MAX_FLOOR};" in js
    assert 'return "pending"' in js and 'return served ? "served"' in js
    assert '.dest-btn[data-state="pending"]' in css and '.dest-btn[data-state="served"]' in css


def test_UI_05_console_shows_session_state_time_ownership_and_route_versions():
    runner = SimRunner(AUTO, "AT-01")
    runner.control(SimCommand.START)
    for _ in range(10):
        runner.tick(1.0)
    snap = runner.snapshot()
    assert snap["sim_state"] == SimState.RUNNING and snap["t_sim"] > 0
    (call,) = snap["calls"]
    assert call["owner"] == "P1"
    p1 = next(c for c in snap["cars"] if c["car_id"] == "P1")
    assert p1["route_version"] >= 1 and p1["stops"]
    html = (ROOT / "web" / "index.html").read_text()
    js = (ROOT / "web" / "app.js").read_text()
    assert 'id="sim-state"' in html and 'id="sim-time"' in html
    assert "c.owner" in js and "car.route_version" in js


# --- Table NFR ---------------------------------------------------------------------------


def test_NFR_02_inserted_stops_never_break_a_reference_eta_by_more_than_the_cap():
    for seed in (7, 8):
        engine = run(random_traffic(seed, trace_s=600))
        assert engine.observer.calls > 30  # plenty of insertions into running routes
        assert engine.observer.delay_violations == 0, f"seed {seed}"
    grouped = run(scenarios.get("AT-02"))  # 27's pickup moves 14 - 8 = 6 s <= DELAY_CAP
    assert grouped.observer.delay_violations == 0 and DELAY_CAP_S == 10.0


def test_NFR_04_dispatch_time_unserved_and_fault_reassigned_calls_are_reported():
    from optimization.evaluator import Job, run_job

    metrics = run_job(Job(1, 3, 5, 300.0, "COMMITMENT", "auto", "QUICK"))
    assert metrics.dispatch_ms_median is not None and metrics.dispatch_ms_max is not None
    assert 0.0 < metrics.dispatch_ms_median <= metrics.dispatch_ms_max
    assert metrics.unserved == 0 and metrics.fault_reassigned == 0
    engine = run(fault_scenario())
    assert engine.metrics()["fault_reassigned"] == 1
    assert engine.metrics()["unserved"] == 0
    served = [
        e["car_id"]
        for e in engine.bus.log
        if e.get("event") == ServiceEventType.HALL_CALL_SERVED.value
    ]
    assert served == ["P2"]


def test_NFR_06_offline_runs_record_everything_the_srs_lists():
    lines = [json.loads(s) for s in (ROOT / "results" / "optimization_runs.jsonl").open()]
    by_type: dict[str, list[dict]] = {}
    for line in lines:
        by_type.setdefault(line["type"], []).append(line)
    (header,) = by_type["run"]
    assert "satsa_seed" in header["inputs"]  # optimizer seed
    assert "rejected" in header["summary"]  # invalid candidates
    candidates = by_type["candidate"]
    assert len(candidates) == 55
    for c in candidates:
        assert len(c["theta"]) == 2 and "objective" in c and "rejected" in c  # config + fitness
        assert {r["scenario"] for r in c["runs"]} == {f"TRAIN-{s}" for s in range(1, 6)}
    assert by_type["satsa_step"]
    holdout = by_type["holdout"]
    assert holdout and all(r["scenario"].startswith("HOLDOUT-") for h in holdout for r in h["runs"])
    assert by_type["approval"][0]["status"] == "PENDING"  # decided later in Control Center


# --- 7.9 Logging and metrics ------------------------------------------------------------


def test_LOG_03_the_run_records_every_metric_listed():
    for key in ("mean_wait", "p95_wait", "unserved", "delay_violations"):
        assert key in run(scenarios.get("AT-01")).metrics()
    at01 = run(scenarios.get("AT-01")).metrics()
    assert (at01["mean_wait"], at01["p95_wait"], at01["unserved"]) == (8.0, 8.0, 0)
    medical = measure(run(scenarios.get("AT-03")), 0, "COMMITMENT")
    assert len(medical.medical_pickup_s) == 1 and medical.medical_pickup_s[0] > 0
    freight = measure(run(scenarios.get("AT-04")), 0, "COMMITMENT")
    assert len(freight.freight_completion_s) == 1 and freight.freight_completion_s[0] > 0


def test_LOG_04_optimization_runs_are_kept_apart_from_route_execution():
    def optimizer(method, quick, progress):
        return Outcome(method, (2, 3), 40.0, 45.0, 6)

    runner = SimRunner(AUTO, "AT-01", optimizer=optimizer)
    runner.control(SimCommand.START)
    runner.control(SimCommand.OPTIMIZE, method="SATSA", quick=True)
    runner.wait_for_optimizations()
    runner.tick(0.5)
    (view,) = runner.snapshot()["optimization"]["runs"]
    assert view["best"]["R"] == 2 and view["best"]["W"] == 3  # candidate theta
    assert (view["objective"], view["decision"]) == (40.0, "PENDING")  # approval status
    (rec,) = runner.engine.bus.select(PolicyRecommended)
    assert rec.scenario_set == ("QUICK-1",)  # input trace set
    # The live run only received the recommendation: no candidate was simulated on it, and
    # its cabins kept running the default policy.
    assert runner.snapshot()["policy"]["version"] != view["best"]["version"]
    assert all(m.car_id in CARS for m in runner.engine.bus.select(CabinTelemetry))
    header = json.loads((ROOT / "results" / "optimization_runs.jsonl").open().readline())
    assert {"lam", "mu", "train_seeds", "holdout_seeds"} <= set(header["inputs"])
