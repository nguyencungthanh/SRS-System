"""Simulation engine (framework F8): states, reset, inputs, log, replay, snapshot.

``Engine`` wraps the deterministic ``Bus``. It never reads a wall clock: whoever drives it
(``app/headless.py`` as fast as possible, ``app/runner.py`` in real time) calls
``advance_to(t)``. Every UI input goes through ``submit()`` and is kept as the trace, so a
run can be replayed exactly (SIM-03, LOG-02, NFR-05, D-59).
"""

import dataclasses
import hashlib
import json
import math
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from contracts import CONTRACT_VERSION
from contracts.codec import from_dict, to_dict
from contracts.config import CAR_ORDER, Policy
from contracts.enums import CallStatus, EventType, ServiceEventType, SimState
from contracts.interfaces import CabinModule, ControlCenterModule, DispatchModule
from contracts.messages import CabinInit, Message
from contracts.ui import FleetCarView, MetricsView, Snapshot
from simulation.bus import Bus, LogEntry
from simulation.passengers import PassengerAgent
from simulation.scenario import Scenario

QUIET_TYPES = {"CabinTelemetry", "Timer"}  # left out of the snapshot's last_events
CONTROL_EVENTS = {
    EventType.SIM_STARTED.value,
    EventType.SIM_PAUSED.value,
    EventType.SIM_RESUMED.value,
    EventType.SIM_STOPPED.value,
    EventType.SIM_RESET.value,
}
LAST_EVENTS = 50


@dataclasses.dataclass(frozen=True)
class ModuleSet:
    """Which implementation builds each module (chosen by app/modules.py, D-60)."""

    dispatch: Callable[[Policy], DispatchModule]
    cabin: Callable[[CabinInit], CabinModule]
    cc: Callable[[], ControlCenterModule]
    kinds: dict[str, str]  # {"dispatch": "real" | "fake", "cabin": ..., "cc": ...}


class Engine:
    def __init__(
        self,
        modules: ModuleSet,
        scenario: Scenario,
        seed: int = 0,
        log_path: Path | None = None,
    ) -> None:
        self.module_set = modules
        self.log_path = log_path
        self._log_file: IO[str] | None = None
        self.reset(scenario, seed)

    # --- lifecycle (SIM-01..03) ------------------------------------------------------

    def reset(self, scenario: Scenario | None = None, seed: int | None = None) -> None:
        self.scenario = scenario or self.scenario
        self.seed = self.seed if seed is None else seed
        sc = self.scenario
        self.dispatch = self.module_set.dispatch(sc.policy)
        self.cc = self.module_set.cc()
        self.cabins = {init.car_id: self.module_set.cabin(init) for init in sc.fleet}
        self.passengers = PassengerAgent(sc.passengers, sc.fleet)
        self._open_log()
        self.inputs: list[dict[str, Any]] = []
        self._last_tel: dict[str, float] = {}
        self._created: dict[str, float] = {}
        self._waits: list[float] = []
        self._last_events: deque[str] = deque(maxlen=LAST_EVENTS)
        self.bus = Bus(
            [self.dispatch, self.cc, *self.cabins.values(), self.passengers],
            cars=tuple(init.car_id for init in sc.fleet),
            network=sc.network,
            on_record=self._on_record,
        )
        self.state = SimState.PAUSED
        self.bus.record_event(EventType.SIM_RESET, scenario=sc.name, seed=self.seed)
        self.bus.start()
        for msg in sc.inputs:
            self.bus.inject(msg)

    def start(self) -> None:
        if self.state == SimState.PAUSED:
            self.state = SimState.RUNNING
            self.bus.record_event(EventType.SIM_STARTED)

    def pause(self) -> None:
        if self.state == SimState.RUNNING:
            self.state = SimState.PAUSED
            self.bus.record_event(EventType.SIM_PAUSED)

    def stop(self) -> None:
        if self.state != SimState.STOPPED:
            self.state = SimState.STOPPED
            self.bus.record_event(EventType.SIM_STOPPED)
            if self._log_file is not None:
                self._log_file.flush()

    @property
    def now(self) -> float:
        return self.bus.now

    @property
    def end_t(self) -> float:
        return self.scenario.duration_s

    def advance_to(self, t: float) -> None:
        """Run the simulation up to ``t`` (SIM-02: nothing moves unless RUNNING)."""
        if self.state != SimState.RUNNING:
            return
        target = min(t, self.end_t)
        self.bus.run_until(target)
        if target >= self.end_t:
            self.stop()

    def run_to_end(self) -> None:
        self.start()
        self.advance_to(self.end_t)

    # --- inputs and trace (D-59) -----------------------------------------------------

    def submit(self, msg: Message) -> Message:
        """Queue a UI / operator input at the current time and keep it in the trace."""
        if self.state == SimState.STOPPED:
            raise RuntimeError("the simulation is stopped; reset it first")
        msg = dataclasses.replace(msg, msg_id="", t_sim=self.now, deliver_at=None)
        stamped = self.bus.inject(msg)
        self.inputs.append(to_dict(msg))
        return stamped

    def replay_inputs(self, inputs: list[dict[str, Any]], end_t: float) -> None:
        """Re-run recorded inputs exactly as they were submitted, then run to ``end_t``."""
        self.state = SimState.RUNNING
        for entry in inputs:
            msg = from_dict(entry)
            self.bus.run_until(msg.t_sim)
            self.submit(msg)
        self.bus.run_until(min(end_t, self.end_t))
        self.stop()

    def trace_lines(self) -> list[dict[str, Any]]:
        header = {
            "type": "header",
            "scenario": self.scenario.name,
            "seed": self.seed,
            "contract_version": CONTRACT_VERSION,
            "modules": self.module_set.kinds,
            "policy": dataclasses.asdict(self.scenario.policy),
        }
        inputs = [{"type": "input", "message": m} for m in self.inputs]
        footer = {"type": "footer", "end_t": self.now, "log_sha256": self.log_sha256()}
        return [header, *inputs, footer]

    def save_trace(self, path: Path) -> None:
        with Path(path).open("w") as f:
            for line in self.trace_lines():
                f.write(json.dumps(line, sort_keys=True) + "\n")

    def log_sha256(self) -> str:
        """Hash of every module message and network event, in order (NFR-05).

        Operator actions (start, pause, reset, stop) are left out: they change when the
        simulation runs, not what it decides, and a replay does not repeat them.
        """
        digest = hashlib.sha256()
        for entry in self.bus.log:
            if entry.get("event") in CONTROL_EVENTS:
                continue
            digest.update(_line(entry).encode())
            digest.update(b"\n")
        return digest.hexdigest()

    # --- snapshot (contracts/ui.py) --------------------------------------------------

    def snapshot(self) -> Snapshot:
        cars: list[FleetCarView] = []
        dests = []
        for car_id in CAR_ORDER:
            cabin = self.cabins.get(car_id)
            if cabin is None:
                continue
            part = cabin.snapshot()
            age = self.now - self._last_tel[car_id] if car_id in self._last_tel else None
            cars.append({**part["car"], "telemetry_age_s": age})
            dests.extend(part["dests"])
        dispatch = self.dispatch.snapshot()
        cc = self.cc.snapshot()
        return {
            "t_sim": self.now,
            "sim_state": self.state.value,
            "seed": self.seed,
            "contract_version": CONTRACT_VERSION,
            "policy": dispatch["policy"],
            "cars": cars,
            "calls": dispatch["calls"],
            "dests": dests,
            "missions": cc["missions"],
            "alerts": cc["alerts"],
            "metrics": self.metrics(),
            "last_events": list(self._last_events),
        }

    def metrics(self) -> MetricsView:
        """Pickup waits per D-30 (served time - created_at, nearest-rank percentiles)."""
        waits = sorted(self._waits)
        unserved = sum(
            1 for c in self.dispatch.snapshot()["calls"] if c["status"] != CallStatus.SERVED
        )
        return {
            "mean_wait": sum(waits) / len(waits) if waits else None,
            "median_wait": _nearest_rank(waits, 0.50),
            "p95_wait": _nearest_rank(waits, 0.95),
            "max_wait": waits[-1] if waits else None,
            "unserved": unserved,
            "delay_violations": 0,  # D-61: measured by the evaluator (AN-03)
        }

    # --- log --------------------------------------------------------------------------

    def _open_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
        if self.log_path is not None:
            self._log_file = Path(self.log_path).open("w")

    def _on_record(self, entry: LogEntry) -> None:
        if self._log_file is not None:
            self._log_file.write(_line(entry) + "\n")
        kind = entry.get("msg_type")
        t = entry["t_sim"]
        if kind == "CabinTelemetry":
            self._last_tel[entry["car_id"]] = t
        elif kind == "HallCallAck" and entry["accepted"] and not entry["duplicate"]:
            self._created[entry["request_id"]] = t
        elif kind == "ServiceEvent" and entry["event"] == ServiceEventType.HALL_CALL_SERVED:
            created = self._created.pop(entry["ref_id"], None)
            if created is not None:
                self._waits.append(t - created)
        if kind not in QUIET_TYPES:
            self._last_events.append(_describe(entry))


def _line(entry: LogEntry) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def _nearest_rank(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    k = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[k - 1]


_SHOWN_FIELDS = (
    "car_id",
    "event",
    "floor",
    "direction",
    "request_id",
    "command_id",
    "ref_id",
    "passenger_id",
    "mission_id",
    "status",
    "kind",
    "accepted",
    "reason",
)


def _describe(entry: LogEntry) -> str:
    t = entry["t_sim"]
    clock = f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:04.1f}"
    name = entry.get("msg_type") or entry.get("event")
    shown = _SHOWN_FIELDS if "msg_type" in entry else ("scenario", "seed")
    parts = [f"{k}={entry[k]}" for k in shown if entry.get(k) not in (None, "")]
    return " ".join([clock, str(name), *parts])
