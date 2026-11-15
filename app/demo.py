"""Record the explainer demo from real runs: ``python -m app.demo`` -> web/demo/stories.js.

Each story is an ordinary scenario run by the ordinary engine with the team's modules. While
it runs, a profiler watches the modules and notes, for every message, which functions handled
it and what a few of them decided: Dispatch's ETA for each car, the Cabin's safety checks,
Control Center's permission check, the door state machine. The page in web/demo/ only plays
this recording back, so everything it shows is what the code did, not a drawing of it.

    python -m app.demo                 # all stories, real modules (fakes if missing, D-60)
    python -m app.demo --list          # print each story's events instead of writing
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import scenarios
from app.modules import describe, module_set
from contracts.config import (
    DELAY_CAP_S,
    DWELL_S,
    FULL_LOAD_KG,
    MAX_FLOOR,
    MAX_LOAD_KG,
    SECONDS_PER_FLOOR,
)
from contracts.enums import (
    Direction,
    FaultKind,
    MissionAction,
    MissionType,
    Role,
)
from contracts.messages import (
    CabinInit,
    CabinTelemetry,
    FaultInjected,
    Message,
    MissionLoadConfirmed,
    MissionRequest,
    PassengerBoard,
    PassengerExit,
    Timer,
)
from contracts.routing import recipients
from simulation.engine import Engine
from simulation.scenario import PassengerIntent, Scenario

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "demo" / "stories.js"
FRAME_S = 0.5  # building snapshot interval
TRACED = ("dispatch", "cabin", "control_center", "fakes", "simulation/passengers.py")
MAX_DEPTH = 2  # handle() = 0; deeper calls are listed only when watched
NOISY = {  # tiny helpers that would drown the call list
    "Cabin.load_kg", "Cabin.mode", "Cabin._pos", "Cabin._pos_now", "Cabin._floor",
    "Cabin._at_landing", "Cabin._locked", "Cabin._sensors_valid", "Cabin._reported_direction",
    "Cabin._pending_dests", "Cabin._timer", "Cabin._event", "Cabin._telemetry", "_stop_view",
    "Cabin._doorway_open", "Cabin._route_state", "Cabin._reply", "Cabin._mission_status",
    "Cabin._alert", "Cabin.snapshot", "Dispatch.snapshot", "ControlCenter.snapshot",
    "ControlCenter._record", "ControlCenter._prune", "Dispatch._usable", "sign", "position_at",
    "travel_time", "direction_to", "at_floor", "is_landing", "car_fingerprint", "_stop_key",
    "Dispatch._mission_busy", "Dispatch._matching", "PassengerAgent.snapshot",
}  # fmt: skip

# Plain-English lines for the functions a viewer meets most. A test checks every name exists.
EXPLAIN = {
    "Dispatch.handle": "Entry point: route the message to the right handler, then re-plan.",
    "Dispatch._hall_call": "Validate the hall call and open (or merge) a request with an id.",
    "Dispatch._plan": "For every pending call, ask each usable car for a legal route; pick the "
    "smallest ETA.",
    "Dispatch._plan_missions": "Rank cars for an approved emergency or freight mission.",
    "Dispatch._propose": "Send the chosen car a RouteProposal and start the ACK timeout.",
    "Dispatch._observe": "Remember the car's latest state; it is the only truth Dispatch uses.",
    "Dispatch._reply": "ACK: the car now owns the call. NACK: free it and plan again.",
    "Dispatch._service": "A stop was served: close the request.",
    "Dispatch._mission_released": "A held car gave a mission back: re-select another car (D-62).",
    "ordinary_exclusion": "Is this car allowed to take ordinary calls right now?",
    "hall_route": "Insert the pickup into the car's route, keeping every promise within DELAY_CAP.",
    "route_etas": "Travel 2 s/floor + 6 s per stop: when would the car reach each stop?",
    "mission_candidates": "Each eligible car's ETA to the mission origin, or why it is excluded.",
    "Cabin.handle": "Entry point of one physical car: every input passes the safety logic here.",
    "Cabin._on_proposal": "Check the proposed route against safety rules; ACK or NACK.",
    "validate": "The Cabin's eight checks before it accepts any route.",
    "Cabin._try_depart": "Leave only if doors are closed and locked and the car may move.",
    "Cabin._can_move": "Motion is allowed only without overload, faults or a held mode.",
    "Cabin._arrive": "Stop at the floor and start serving it.",
    "Cabin._serve_here": "Open the doors and report what was served at this floor.",
    "Cabin._door": "Advance the door state machine by one input.",
    "next_state": "The door state machine: which state follows this input?",
    "Cabin._on_board": "A passenger stepped in: add their weight to the load.",
    "Cabin._on_exit": "A passenger stepped out: remove their weight.",
    "Cabin._on_destination": "Accept a floor button only from someone on board.",
    "Cabin._on_fault": "A serious fault: stop, hold, drop hall pickups, raise an alert.",
    "Cabin._send_telemetry": "Publish position, doors, load and route version (SEN-01).",
    "Cabin._on_mission_confirm": "Staff confirmed loading or unloading for the mission.",
    "Cabin._mission_hit_by_fault": "The mission car broke down: give the mission back.",
    "ControlCenter.handle": "Entry point for operator requests and mission status.",
    "ControlCenter._on_mission_request": "Check the role and the request, then approve it.",
    "ControlCenter._on_status": "Track mission progress; never step back to an older state.",
    "ControlCenter._raise_alert": "Show an alert to the operator.",
    "validate_request": "Floors, mission type and required fields of a mission request.",
    "may_request": "May this role request this kind of mission?",
    "PassengerAgent.handle": "Simulated people: press buttons, board, choose floors, exit.",
    "PassengerAgent._press": "A person presses the hall button for their direction.",
    "PassengerAgent._on_door_opened": "Doors opened here: riders exit, waiting people board.",
    "PassengerAgent._on_ack": "Remember which request id the person is waiting for.",
    "Dispatch._release_dropped_pickups": "A held car dropped a pickup: put the call back in "
    "the queue.",
    "Dispatch._await_stop_removal": "Wait for the car's next route version before planning on it.",
    "Dispatch._accept": "The car confirmed: the calls are ASSIGNED to it.",
    "Dispatch._reject": "The car refused: free the calls so another car can be chosen.",
    "Dispatch._timer": "No reply before the timeout: ask the car for its real route version.",
    "Dispatch._report": "Reconcile with the version the car reports (AT-05).",
    "ControlCenter._dispatch": "Send each kind of message to its handler.",
    "Cabin._progress": "After any change: hand-off check, serve this floor, try to leave.",
    "Cabin._check_handoff": "Before a mission: is the car really empty (SEN-05)?",
    "Cabin._serve_while_open": "Serve a stop for this floor without a second door cycle.",
    "Cabin._retarget": "The route changed while moving: aim at a stop the car can still make.",
    "Cabin._on_timer": "A timer fired: heartbeat, arrival at a floor, or a door phase ending.",
    "Cabin._door_phase_over": "A door phase ended: move the door on, unless it must stay open.",
    "Cabin._must_hold_open": "Overload or a mission waiting for staff keeps the doors open.",
    "Cabin._release_door": "The reason to hold the doors is gone: let them close.",
    "Cabin._set_target": "Start moving toward the next stop.",
    "Cabin._on_doors_closed": "End of a door cycle: check a mission drain is really empty.",
    "Cabin._drop_hall_pickups": "A held car cannot serve hall calls: drop them from its route.",
    "Cabin._start_mission": "Reserve the car for the mission (exclusive).",
    "Cabin._on_version_query": "Tell Dispatch which route version the car really has.",
    "CommandCache.lookup": "Was this exact command answered before? Then repeat the answer.",
    "CommandCache.store": "Remember the answer, so a resent command gets the same reply.",
}

LANE_OF = {"dispatch": "dispatch", "cc": "cc", "passengers": "world", "world": "world"}


def lane_of(address: str) -> str:
    return "cabin" if address.startswith("cabin:") else LANE_OF.get(address, "world")


# --- source index: qualname -> (file, line, doc) ----------------------------------------


@dataclass(frozen=True)
class FunctionInfo:
    file: str
    line: int
    doc: str


def traced_files() -> dict[str, str]:
    """Absolute path -> repository path of every file the profiler watches."""
    out: dict[str, str] = {}
    for item in TRACED:
        path = ROOT / item
        files = [path] if path.suffix == ".py" else sorted(path.rglob("*.py"))
        for f in files:
            out[str(f)] = f.relative_to(ROOT).as_posix()
    return out


def source_index(files: dict[str, str]) -> dict[tuple[str, str], FunctionInfo]:
    index: dict[tuple[str, str], FunctionInfo] = {}

    def visit(node: ast.AST, prefix: str, rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", rel)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                doc = (ast.get_docstring(child) or "").strip().split("\n")[0]
                index[(rel, prefix + child.name)] = FunctionInfo(rel, child.lineno, doc)

    for path, rel in files.items():
        visit(ast.parse(Path(path).read_text()), "", rel)
    return index


def proposal_checks() -> list[dict[str, Any]]:
    """The numbered sections of cabin/proposal.py:validate and the NACKs each can return."""
    path = ROOT / "cabin" / "proposal.py"
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    start = next(i for i, s in enumerate(lines) if s.startswith("def validate("))
    sections: list[dict[str, Any]] = []
    for s in lines[start + 1 :]:
        if s and not s.startswith((" ", "#")):
            break
        head = re.match(r"\s+# (\d+)\. (.+)", s)
        if head:
            sections.append({"n": int(head[1]), "title": head[2], "reasons": []})
        elif sections:
            sections[-1]["reasons"] += re.findall(r"NackReason\.(\w+)", s)
    return sections


# --- the profiler -------------------------------------------------------------------------


def _short(value: Any) -> Any:
    return getattr(value, "value", value)


def _watch_return(name: str, frame, caller, value: Any) -> dict[str, Any] | None:
    """What a watched function decided, read from its frame when it returns."""
    loc = frame.f_locals
    if name == "ordinary_exclusion":
        tel = loc.get("telemetry")
        car = tel.car_id if tel is not None else None
        return {"car": car, "excluded": _short(value)} if car else None
    if name == "hall_route":
        pickup = loc["pickup"]
        floors = None if value is None else [s.floor for s in value]
        found = {"car": loc["telemetry"].car_id, "ref": pickup.ref_id, "route": floors}
        stops = loc["telemetry"].stops
        if value is not None and stops and "baseline" in loc:
            # The DELAY_CAP guard, recomputed for the route it returned: every stop the
            # car already had keeps its fixed reference ETA within the cap (DC-04).
            etas = frame.f_globals["route_etas"](
                loc["telemetry"], value, loc["now"], ready_at=loc.get("ready_at")
            )
            refs, baseline = loc["references"], loc["baseline"]
            found["budget"] = [
                {
                    "floor": s.floor,
                    "ref": s.ref_id,
                    "reference": round(refs.get(s, baseline[s]), 1),
                    "new": round(etas[s], 1),
                }
                for s in stops
            ]
        return found
    if name == "route_etas" and caller is not None and caller.f_code.co_name == "_plan":
        pickup = caller.f_locals.get("pickup")
        car = caller.f_locals.get("car_id")
        if pickup is None or car is None or pickup not in value:
            return None
        now = caller.f_locals.get("now", 0.0)
        return {"car": car, "ref": pickup.ref_id, "eta": round(value[pickup] - now, 1)}
    if name == "mission_candidates":
        return {
            "candidates": [
                {"car": c.car_id, "eta": c.eta_s, "excluded": _short(c.excluded)} for c in value
            ]
        }
    if name == "validate" and "state" in loc:
        return {"command": loc["msg"].command_id, "nack": _short(value)}
    if name == "next_state":
        if value is None:
            return None
        return {"from": _short(loc["state"]), "input": _short(loc["event"]), "to": _short(value)}
    if name == "Cabin._can_move":
        cabin = loc["self"]
        return {"car": cabin.car_id, "ok": bool(value), "mode": _short(cabin.mode)}
    if name == "Cabin._must_hold_open" and value:
        cabin = loc["self"]
        return {"car": cabin.car_id, "load": cabin.load_kg, "mode": _short(cabin.mode)}
    if name == "validate_request":
        return {"problem": _short(value)}
    if name == "may_request":
        return {"role": _short(loc["role"]), "type": _short(loc["mission_type"]), "ok": value}
    return None


WATCHED = {
    "ordinary_exclusion", "hall_route", "route_etas", "mission_candidates", "validate",
    "next_state", "Cabin._can_move", "validate_request", "may_request", "Cabin._must_hold_open",
}  # fmt: skip


@dataclass
class Handling:
    module: str
    trigger: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    watched: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    out: list[str] = field(default_factory=list)


class Recorder:
    """Wraps every module of an engine; notes who sent each message and what ran for it."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.files = traced_files()
        self.index = source_index(self.files)
        self.sender: dict[str, str] = {}
        self.made_by: dict[str, Handling] = {}  # msg_id -> the handling that produced it
        self.handled: dict[str, list[Handling]] = {}
        self._current: str | None = None
        self._record: Handling | None = None
        bus = engine.bus
        for name, module in list(bus.modules.items()):
            bus.modules[name] = _Wrapped(module, self)
        original = bus._enqueue

        def enqueue(msg: Message) -> Message:
            stamped = original(msg)
            self.sender[stamped.msg_id] = self._current or "world"
            if self._record is not None:
                self.made_by[stamped.msg_id] = self._record
            return stamped

        bus._enqueue = enqueue  # type: ignore[method-assign]
        inject = bus.inject

        def from_outside(msg: Message) -> Message:  # scenario, staff, UI: not a module
            self._current, self._record = None, None
            return inject(msg)

        bus.inject = from_outside  # type: ignore[method-assign]

    def handle(self, module: Any, msg: Message, now: float) -> list[Message]:
        trigger = type(msg).__name__
        if isinstance(msg, Timer):
            trigger = f"Timer '{msg.key}'"
        record = Handling(module.name, trigger)
        stack: list[tuple[Any, bool]] = []  # watched frames: (frame, listed)

        def profile(frame, event, arg):
            code = frame.f_code
            rel = self.files.get(code.co_filename)
            if rel is None or event not in ("call", "return"):
                return
            name = code.co_qualname
            if event == "call":
                depth = len(stack)
                listed = "<" not in name and name not in NOISY and depth <= MAX_DEPTH
                stack.append((frame, listed))
                if listed:
                    info = self.index.get((rel, name))
                    record.calls.append(
                        {
                            "fn": name,
                            "file": rel,
                            "line": info.line if info else code.co_firstlineno,
                            "depth": depth,
                        }
                    )
                return
            if not stack:
                return
            stack.pop()
            if name in WATCHED:
                caller = stack[-1][0] if stack else None
                found = _watch_return(name, frame, caller, arg)
                if found is not None:
                    record.watched.append((name, found))

        # The bus queues the outputs right after handle() returns: they are this module's.
        self._current, self._record = module.name, record
        previous = sys.getprofile()
        sys.setprofile(profile)
        try:
            out = module.handle(msg, now)
        finally:
            sys.setprofile(previous)
        record.out = [type(m).__name__ for m in out if not isinstance(m, Timer)]
        record.calls = _collapse(record.calls)
        self.handled.setdefault(msg.msg_id, []).append(record)
        return out


class _Wrapped:
    def __init__(self, inner: Any, recorder: Recorder) -> None:
        self.inner, self.recorder = inner, recorder
        self.name = inner.name

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def handle(self, msg: Message, now: float) -> list[Message]:
        return self.recorder.handle(self.inner, msg, now)


def _collapse(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold a repeated call (same function, same parent) into its first one with a count:
    ``hall_route x4`` instead of four copies. The repeats' own sub-calls are dropped."""
    out: list[dict[str, Any]] = []
    parents: list[dict[str, Any] | None] = []  # parents[d] = latest kept call at depth d
    seen: dict[tuple[int, str], dict[str, Any]] = {}  # (id(parent), fn) -> kept call
    skip_below: int | None = None
    for call in calls:
        depth = call["depth"]
        if skip_below is not None:
            if depth > skip_below:
                continue
            skip_below = None
        parent = parents[depth - 1] if 0 < depth <= len(parents) else None
        key = (id(parent), call["fn"])
        if key in seen:
            seen[key]["n"] = seen[key].get("n", 1) + 1
            skip_below = depth
            continue
        kept = dict(call)
        seen[key] = kept
        out.append(kept)
        del parents[depth:]
        parents.append(kept)
    return out


# --- what the page shows inside each lane --------------------------------------------


def inside_of(handling: Handling, outputs: list[Message], checks: list[dict]) -> list[dict]:
    """Turn the watched values into small panels: ETA table, checklist, door strip, ..."""
    panels: list[dict[str, Any]] = []
    w = handling.watched
    proposals = [m for m in outputs if type(m).__name__ == "RouteProposal"]
    # Dispatch: one ETA table per pending call it tried to place.
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    excluded: dict[str, str] = {}
    for name, v in w:
        if name == "ordinary_exclusion" and v["excluded"]:
            excluded[v["car"]] = v["excluded"]
        elif name == "hall_route":
            row = {"car": v["car"], "route": v["route"]}
            if "budget" in v:
                row["budget"] = v["budget"]
            rows.setdefault(v["ref"], {})[v["car"]] = row
        elif name == "route_etas":
            rows.setdefault(v["ref"], {}).setdefault(v["car"], {"car": v["car"]})["eta"] = v["eta"]
    for ref, cars in rows.items():
        chosen = next(
            (p.car_id for p in proposals if any(s.ref_id == ref for s in p.ordered_stops)), None
        )
        table = []
        for row in cars.values():
            if row.get("route") is None and "eta" not in row:
                row["note"] = "no route keeps every promise within DELAY_CAP"
            table.append(row)
        table += [{"car": c, "excluded": r} for c, r in excluded.items() if c not in cars]
        panels.append({"kind": "eta", "ref": ref, "rows": table, "chosen": chosen})
    for name, v in w:
        if name == "mission_candidates":
            chosen = next((p.car_id for p in proposals if p.mission_id), None)
            panels.append({"kind": "mission", "rows": v["candidates"], "chosen": chosen})
        elif name == "validate":
            failed = None
            if v["nack"]:
                failed = next(
                    (s["n"] for s in checks if v["nack"] in s["reasons"]),
                    checks[-1]["n"] if checks else None,
                )
            panels.append(
                {
                    "kind": "checks",
                    "nack": v["nack"],
                    "failed": failed,
                    "items": [{"n": s["n"], "title": s["title"]} for s in checks],
                }
            )
        elif name == "next_state":
            panels.append({"kind": "door", **v})
        elif name == "Cabin._must_hold_open":
            panels.append({"kind": "hold", **v})
        elif name == "Cabin._can_move" and not v["ok"]:
            panels.append({"kind": "blocked", "car": v["car"], "mode": v["mode"]})
        elif name == "may_request":
            panels.append({"kind": "role", **v})
        elif name == "validate_request":
            panels.append({"kind": "request", "problem": v["problem"]})
    return _dedupe(panels)


def _dedupe(panels: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in panels:
        key = json.dumps(p, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# --- plain-English message summaries ------------------------------------------------------


def _stops(stops) -> str:
    marks = {"UP": "↑", "DOWN": "↓"}
    parts = []
    for s in stops:
        kind = _short(s.kind)
        tag = {"HALL_PICKUP": "pick up", "CAR_DEST": "drop off"}.get(
            kind, kind.lower().replace("_", " ")
        )
        parts.append(f"{s.floor}{marks.get(_short(s.direction) or '', '')} {tag}")
    return ", ".join(parts) or "no stops"


def summary(m: Message) -> str:  # noqa: C901 - one line per message type
    n = type(m).__name__
    g = lambda k: _short(getattr(m, k, None))  # noqa: E731
    if n == "HallCallPressed":
        return f"Hall button pressed on floor {m.floor}, going {g('direction')}."
    if n == "HallCallAck":
        if not m.accepted:
            return f"Dispatch refuses the call on floor {m.floor}: {g('reason')}."
        what = "joins the open request" if m.duplicate else "opens request"
        return f"Dispatch {what} {m.request_id} (floor {m.floor} {g('direction')})."
    if n == "RouteProposal":
        mission = f" for mission {m.mission_id}" if m.mission_id else ""
        return (
            f"Dispatch asks {m.car_id} to take route v{m.expected_route_version}+1{mission}: "
            f"{_stops(m.ordered_stops)}."
        )
    if n == "RouteReply":
        if m.accepted:
            return f"{m.car_id} accepts. Its route is now version {m.committed_version}."
        return f"{m.car_id} refuses (NACK): {g('reason')}."
    if n == "CabinTelemetry":
        move = f"moving {g('direction')}" if m.moving else "standing"
        return (
            f"{m.car_id} reports: floor {m.position:g}, {move}, doors {g('door')}, "
            f"{m.load_kg:g} kg, mode {g('mode')}, route v{m.route_version}."
        )
    if n == "ServiceEvent":
        event = g("event")
        text = {
            "CAR_DEPARTED": "departs",
            "CAR_ARRIVED": "arrives",
            "CAR_IDLE": "is idle",
            "DOOR_OPENING": "opens its doors",
            "DOOR_OPENED": "doors are open",
            "DOOR_CLOSING": "closes its doors",
            "DOOR_CLOSED_LOCKED": "doors closed and locked",
            "DOOR_REOPENED": "re-opens: something is in the doorway",
            "HALL_CALL_SERVED": f"serves hall call {m.ref_id}",
            "PASSENGER_BOARDED": f"confirms {m.passenger_id} boarded",
            "PASSENGER_EXITED": f"confirms {m.passenger_id} left",
            "DEST_ACCEPTED": f"accepts destination {m.floor} from {m.passenger_id}",
            "DEST_REJECTED": f"rejects destination {m.floor}: {g('reason')}",
            "DEST_SERVED": f"drops {m.passenger_id} at floor {m.floor}",
            "DRAIN_COMPLETE": "is empty, ready for the mission",
            "HANDOFF_OK": "verified empty for the handoff",
            "MISSION_PICKUP_DONE": f"picked up mission {m.ref_id}",
            "MISSION_DELIVERY_DONE": f"delivered mission {m.ref_id}",
        }.get(event, event)
        if event.startswith("DEST_"):
            return f"{m.car_id} {text}."
        return f"{m.car_id} {text} at floor {m.floor}."
    if n == "PassengerBoard":
        return f"{m.passenger_id} steps into {m.car_id} ({m.weight_kg:g} kg)."
    if n == "PassengerExit":
        return f"{m.passenger_id} steps out of {m.car_id}."
    if n == "CabinDestinationPressed":
        return f"{m.passenger_id} presses {m.floor} inside {m.car_id}."
    if n == "MissionRequest":
        return (
            f"{g('role').replace('_', ' ').title()} requests a {g('mission_type')} mission: "
            f"floor {m.origin} to {m.destination}."
        )
    if n == "MissionApproval":
        return f"Control Center approves {g('mission_type')} mission {m.mission_id}."
    if n == "MissionStatusUpdate":
        car = f" ({m.car_id})" if getattr(m, "car_id", None) else ""
        return f"Mission {m.mission_id} is now {g('status')}{car}."
    if n == "MissionLoadConfirmed":
        return f"Staff confirm {g('action')} for {m.mission_id} in {m.car_id}."
    if n == "Alert":
        return f"Alert to the operator: {g('kind')} - {m.details}"
    if n == "FaultInjected":
        return f"Simulated hardware fault on {m.car_id}: {g('kind')}."
    if n == "FaultCleared":
        return f"Fault cleared on {m.car_id}: {g('kind')}."
    if n == "Timer":
        car = (m.target or "").removeprefix("cabin:")
        return f"{car}'s {m.key} timer runs out."
    if n == "RouteVersionQuery":
        return f"Dispatch asks {m.car_id} for its current route version."
    if n == "RouteVersionReport":
        return f"{m.car_id} reports route version {m.route_version}."
    return n


# --- stories ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Caption:
    """A key moment: the first event (after the previous caption) matching ``when``."""

    when: Callable[[Message], bool]
    text: str
    pause: bool = True


@dataclass(frozen=True)
class Story:
    key: str
    title: str
    tagline: str
    requirements: tuple[str, ...]
    scenario: Callable[[], Scenario]
    until: float
    captions: tuple[Caption, ...]
    staff: bool = False
    note: str = ""


def is_(name: str, **where: Any) -> Callable[[Message], bool]:
    def match(m: Message) -> bool:
        return type(m).__name__ == name and all(
            _short(getattr(m, k, None)) == v for k, v in where.items()
        )

    return match


def _fleet(**floors: int) -> tuple[CabinInit, ...]:
    default = {"P1": 1, "P2": 1, "P3": 1, "P4": 1, "G1": 1} | floors
    return tuple(CabinInit(car_id=c, floor=f) for c, f in default.items())


def breakdown_scenario() -> Scenario:
    """SY-24 (Hai): the medical car breaks down on its way; another car must take over."""
    return Scenario(
        name="SY-24",
        fleet=_fleet(P1=40, P2=10, P3=25, P4=35),
        inputs=(
            MissionRequest(
                t_sim=0.0,
                role=Role.MEDICAL_COORDINATOR,
                mission_type=MissionType.MEDICAL,
                origin=2,
                destination=30,
            ),
            FaultInjected(t_sim=3.0, car_id="P2", kind=FaultKind.MOTION_SERIOUS),
        ),
        duration_s=400.0,
    )


def crowd_scenario() -> Scenario:
    """SY-04 (Hai): 16 people (1200 kg) at the lobby, more than one car may carry. One more
    person (scripted) squeezes in past the limit and steps out again, to show SEN-03."""
    crowd = tuple(
        PassengerIntent(t=0.0, passenger_id=f"PAX-{i:02d}", origin=1, destination=20 + i)
        for i in range(1, 17)
    )
    return Scenario(
        name="SY-04",
        fleet=_fleet(P1=1, P2=30, P3=35, P4=40),
        passengers=crowd,
        inputs=(
            PassengerBoard(t_sim=1.5, car_id="P1", passenger_id="LATE", weight_kg=90.0),
            PassengerExit(t_sim=9.0, car_id="P1", passenger_id="LATE"),
        ),
        duration_s=400.0,
    )


STORIES: tuple[Story, ...] = (
    Story(
        key="overview",
        title="A normal minute",
        tagline="Four modules run side by side. They never call each other: they only "
        "exchange messages.",
        requirements=("SYS-01", "SIM-08", "UI-05"),
        scenario=lambda: scenarios.get("DEMO"),
        until=75.0,
        captions=(
            Caption(
                is_("HallCallPressed"),
                "Someone on floor 1 wants to go up. The button press is a message to "
                "Dispatch - nothing else happens yet.",
            ),
            Caption(
                is_("RouteProposal"),
                "Dispatch compared the cars and picked one. It does not move the car: it "
                "PROPOSES a route.",
            ),
            Caption(
                is_("RouteReply", accepted=True),
                "The car checked the route for safety and accepted it. Only now does it own "
                "the call.",
            ),
            Caption(
                is_("PassengerBoard"),
                "Doors open, the simulated passenger steps in and chooses a floor.",
            ),
            Caption(
                is_("HallCallPressed", floor=40),
                "Calls keep coming while cars are busy. Watch several cars work in "
                "parallel, each with its own route.",
                pause=False,
            ),
        ),
    ),
    Story(
        key="hall-call",
        title="One hall call, step by step",
        tagline="AT-01: P1 is idle at 31. Someone on 27 presses DOWN and goes to 5. "
        "The SRS expects pickup at 8 s, drop-off at 58 s.",
        requirements=("AT-01", "DC-01", "CB-01", "DOOR-01", "REQ-05"),
        scenario=lambda: scenarios.get("AT-01"),
        until=70.0,
        captions=(
            Caption(is_("HallCallPressed"), "Floor 27 presses DOWN."),
            Caption(
                is_("HallCallAck"),
                "Dispatch gives the call an id. Then it plans: each passenger car gets an "
                "ETA. G1 is a goods car, so it never takes hall calls.",
            ),
            Caption(
                is_("RouteProposal"),
                f"P1 wins: 4 floors x {SECONDS_PER_FLOOR:g} s = 8 s. Dispatch sends the "
                "route with the version it last saw.",
            ),
            Caption(
                is_("RouteReply", accepted=True),
                "The Cabin runs its eight checks (version, mode, sensors, ...). All pass: ACK.",
            ),
            Caption(
                is_("ServiceEvent", event="HALL_CALL_SERVED"),
                "t = 8 s, exactly the SRS oracle. Doors open through the door state machine.",
            ),
            Caption(
                is_("CabinDestinationPressed"),
                "The passenger presses 5 inside the car. The Cabin owns this promise.",
            ),
            Caption(
                is_("ServiceEvent", event="DEST_SERVED"),
                "t = 58 s: 8 + 6 s dwell + 22 floors x 2 s. Drop-off reported separately "
                "from pickup.",
            ),
        ),
    ),
    Story(
        key="grouping",
        title="Picking someone up on the way",
        tagline="AT-02: 27 DOWN at 0 s, then 29 DOWN at 2 s. P1 can stop at 29 first - "
        f"but only if 27 is not delayed by more than DELAY_CAP = {DELAY_CAP_S:g} s.",
        requirements=("AT-02", "DC-03", "DC-04", "NFR-02"),
        scenario=lambda: scenarios.get("AT-02"),
        until=40.0,
        captions=(
            Caption(
                is_("RouteReply", accepted=True),
                "27 is promised to P1 with a reference ETA of 8 s. That number is now fixed.",
            ),
            Caption(
                is_("HallCallPressed", floor=29),
                "A second call: 29 DOWN, on P1's way down.",
            ),
            Caption(
                is_("RouteProposal"),
                f"Inserting 29 delays 27 from 8 s to 14 s: +{14 - 8} s, within the "
                f"{DELAY_CAP_S:g} s budget. Dispatch proposes the new route.",
            ),
            Caption(
                is_("ServiceEvent", event="HALL_CALL_SERVED", floor=29),
                f"29 served at 4 s, then a {DWELL_S:g} s dwell.",
            ),
            Caption(
                is_("ServiceEvent", event="HALL_CALL_SERVED", floor=27),
                "27 served at 14 s: the promise held.",
            ),
        ),
    ),
    Story(
        key="medical",
        title="A medical emergency",
        tagline="AT-03: a medical coordinator asks for a car at floor 2. Control Center "
        "checks the role, Dispatch ranks the cars, the chosen car drops its passengers first.",
        requirements=("AT-03", "MED-01", "MED-02", "MED-05", "SEC-01"),
        scenario=lambda: scenarios.get("AT-03"),
        until=100.0,
        captions=(
            Caption(
                is_("MissionRequest"),
                "Only a medical coordinator may ask for this. Control Center checks the role "
                "first.",
            ),
            Caption(
                is_("MissionApproval"),
                "Approved. Control Center never picks the car - it hands the mission to Dispatch.",
            ),
            Caption(
                is_("RouteProposal"),
                "Dispatch ranks every car: P3 is in maintenance, G1 is not allowed. P2 has the "
                "smallest ETA (20 s).",
            ),
            Caption(
                is_("ServiceEvent", event="MISSION_PICKUP_DONE"),
                "Staff confirmed the patient is loaded. The car now runs the mission only.",
            ),
            Caption(
                is_("ServiceEvent", event="MISSION_DELIVERY_DONE"),
                "Delivered at floor 30.",
            ),
        ),
    ),
    Story(
        key="breakdown",
        title="Case A: the medical car breaks down",
        tagline="SY-24, a system test by Hai: P2 is on its way to the patient when its motor "
        "fails. The patient must still be picked up.",
        requirements=("SY-24", "EMG-05", "MED-04", "D-62"),
        scenario=breakdown_scenario,
        until=80.0,
        staff=True,
        note="Bug found by this test: at first the mission stayed RESERVED on the broken car "
        "forever. Fixed in dispatch/controller.py, Dispatch._mission_released (D-62).",
        captions=(
            Caption(is_("MissionApproval"), "Medical mission approved, as in AT-03."),
            Caption(is_("RouteReply", accepted=True), "P2 accepts the mission route."),
            Caption(
                is_("FaultInjected"),
                "t = 3 s: the simulated motor of P2 fails. Only the Cabin hears it.",
            ),
            Caption(
                is_("Alert"),
                "The Cabin stops, holds and tells the operator. It does not touch Dispatch's "
                "data - it only reports.",
            ),
            Caption(
                is_("RouteProposal"),
                "Dispatch sees P2 in FAULT_HOLD in the telemetry, releases the mission and "
                "re-selects. The next best car gets it.",
            ),
            Caption(
                is_("ServiceEvent", event="MISSION_PICKUP_DONE"),
                "The patient is picked up by another car.",
            ),
        ),
    ),
    Story(
        key="crowd",
        title="Case B: a crowd bigger than one car",
        tagline=f"SY-04, a system test by Hai: 16 people (1200 kg) at the lobby. A car takes "
        f"no new pickups at {FULL_LOAD_KG:g} kg and never moves above {MAX_LOAD_KG:g} kg.",
        requirements=("SY-04", "SEN-03", "SEN-04", "CB-02"),
        scenario=crowd_scenario,
        until=60.0,
        captions=(
            Caption(
                is_("HallCallPressed"),
                "Sixteen people press UP at the lobby. P1 is standing right there.",
            ),
            Caption(
                is_("PassengerBoard"),
                "People step in. Every boarding is a message; the Cabin adds 75 kg each time.",
            ),
            Caption(
                lambda m: isinstance(m, CabinTelemetry) and _short(m.mode) == "FULL",
                f"{FULL_LOAD_KG:g} kg reached: P1 is FULL. From now on it refuses new hall "
                "pickups (CB-02). The simulated passengers stop boarding before 1000 kg.",
            ),
            Caption(
                is_("PassengerBoard", passenger_id="LATE"),
                "One more person squeezes in anyway (scripted): 975 + 90 = 1065 kg.",
            ),
            Caption(
                lambda m: isinstance(m, CabinTelemetry) and _short(m.mode) == "OVERLOAD_HOLD",
                f"Over {MAX_LOAD_KG:g} kg: OVERLOAD_HOLD. The Cabin keeps the doors open and "
                "will not move (SEN-03, SEN-04).",
            ),
            Caption(
                is_("RouteProposal", car_id="P2"),
                "The three people left behind press again. Dispatch sends P2 for them.",
            ),
            Caption(
                is_("PassengerExit", passenger_id="LATE"),
                "The extra person steps out: back to 975 kg.",
            ),
            Caption(
                is_("ServiceEvent", event="CAR_DEPARTED", car_id="P1"),
                "Under the limit again: doors close and lock, and only then does P1 leave.",
            ),
        ),
    ),
)


# --- recording ----------------------------------------------------------------------------


def staff_step(engine: Engine, sent: set) -> None:
    """Building staff confirm loading / unloading when a mission car stands open (SY tests)."""
    snap = engine.snapshot()
    cars = {c["car_id"]: c for c in snap["cars"]}
    for m in snap["missions"]:
        car = cars.get(m["car_id"] or "")
        if car is None or car["mission_id"] != m["mission_id"] or car["door"] != "OPEN":
            continue
        if car["position"] == m["origin"]:
            action = MissionAction.LOADED
        elif car["position"] == m["destination"] and (m["mission_id"], "LOADED") in sent:
            action = MissionAction.UNLOADED
        else:
            continue
        if (m["mission_id"], action.value) in sent:
            continue
        sent.add((m["mission_id"], action.value))
        engine.submit(
            MissionLoadConfirmed(
                t_sim=0.0, mission_id=m["mission_id"], car_id=car["car_id"], action=action
            )
        )


def _frame(engine: Engine) -> dict[str, Any]:
    snap = engine.snapshot()
    agent = engine.passengers
    waiting: dict[str, int] = {}
    for pid in agent.waiting:
        intent = agent.intents[pid]
        key = f"{intent.origin}{'U' if intent.direction == Direction.UP else 'D'}"
        waiting[key] = waiting.get(key, 0) + 1
    return {
        "t": round(engine.now, 2),
        "cars": [
            {
                "id": c["car_id"],
                "pos": round(c["position"], 3),
                "door": c["door"],
                "dir": c["direction"],
                "mode": c["mode"],
                "load": c["load_kg"],
                "pax": c["passengers"],
                "rv": c["route_version"],
                "stops": [[s["floor"], s["kind"]] for s in c["stops"]],
                "faults": c["faults"],
                "mission": c["mission_id"],
            }
            for c in snap["cars"]
        ],
        "calls": [
            [c["floor"], c["direction"], c["status"], c["owner"], c["request_id"]]
            for c in snap["calls"]
            if c["status"] not in ("COMPLETED", "CANCELLED")
        ],
        "waiting": waiting,
        "missions": [
            [m["mission_id"], m["mission_type"], m["status"], m["car_id"], m["origin"]]
            for m in snap["missions"]
        ],
    }


def _meaningful(m: Message, last: dict[str, tuple]) -> bool:
    """Telemetry heartbeats repeat the same state; keep only samples that changed it."""
    if not isinstance(m, CabinTelemetry):
        return True
    state = (
        m.moving,
        _short(m.door),
        _short(m.mode),
        m.route_version,
        m.faults,
        m.passengers,
        tuple(s.floor for s in m.stops),
    )
    changed = last.get(m.car_id) != state
    last[m.car_id] = state
    return changed


def record(story: Story, modules) -> dict[str, Any]:
    engine = Engine(modules, story.scenario())
    recorder = Recorder(engine)
    checks = proposal_checks()
    engine.start()
    engine.advance_to(0.0)  # frame 0 shows the state after the t = 0 messages
    frames = [_frame(engine)]
    sent: set = set()
    t = 0.0
    while t < story.until:
        t = round(t + FRAME_S, 6)
        engine.advance_to(t)
        if story.staff:
            staff_step(engine, sent)
        frames.append(_frame(engine))

    table: list[dict[str, Any]] = []  # handlings, deduplicated: events point into it
    index: dict[str, int] = {}
    converted: dict[int, int | None] = {}

    def ref(handling: Handling | None) -> int | None:
        if handling is None:
            return None
        if id(handling) in converted:
            return converted[id(handling)]
        entry = {
            "module": handling.module,
            "lane": lane_of(handling.module),
            "trigger": handling.trigger,
            "calls": [
                {**c, "why": EXPLAIN.get(c["fn"]) or _doc(recorder, c)} for c in handling.calls
            ],
            "inside": inside_of(handling, _outputs_of(recorder, handling), checks),
            "out": handling.out,
        }
        key = json.dumps(entry, sort_keys=True, default=str)
        if key not in index:
            index[key] = len(table)
            table.append(entry)
        converted[id(handling)] = index[key]
        return index[key]

    events: list[dict[str, Any]] = []
    last_tel: dict[str, tuple] = {}
    hidden = 0
    for msg in engine.bus.delivered:
        at = msg.deliver_at if msg.deliver_at is not None else msg.t_sim
        if at > story.until:
            break
        handled = recorder.handled.get(msg.msg_id, [])
        if isinstance(msg, Timer):
            # A timer is shown only when it changed something without saying so, such as
            # the door timer that keeps an overloaded car open.
            quiet = [h for h in handled if not h.out and _panel_worthy(h)]
            if not quiet:
                hidden += 1
                continue
        elif not _meaningful(msg, last_tel):
            hidden += 1
            continue
        source = recorder.sender.get(msg.msg_id, "world")
        events.append(
            {
                "t": round(at, 2),
                "id": msg.msg_id,
                "type": type(msg).__name__,
                "from": source,
                "fromLane": lane_of(source),
                "to": list(recipients(msg, engine.bus.cars)),
                "minor": isinstance(msg, CabinTelemetry),
                "car": getattr(msg, "car_id", None) or _timer_car(msg),
                "text": summary(msg),
                # A timer was set long before; what it does is in handledBy.
                "madeBy": None if isinstance(msg, Timer) else ref(recorder.made_by.get(msg.msg_id)),
                "handledBy": [ref(h) for h in handled if h.calls],
            }
        )
    _attach_captions(story, engine, events)
    return {
        "key": story.key,
        "title": story.title,
        "tagline": story.tagline,
        "requirements": list(story.requirements),
        "note": story.note,
        "until": story.until,
        "frameS": FRAME_S,
        "cars": [c.car_id for c in engine.scenario.fleet],
        "frames": frames,
        "handlings": table,
        "events": events,
        "hiddenMessages": hidden,
    }


def _panel_worthy(handling: Handling) -> bool:
    return any(name == "Cabin._must_hold_open" for name, _ in handling.watched)


def _timer_car(msg: Message) -> str | None:
    target = getattr(msg, "target", "") or ""
    return target.split(":", 1)[1] if target.startswith("cabin:") else None


def _doc(recorder: Recorder, call: dict[str, Any]) -> str:
    info = recorder.index.get((call["file"], call["fn"]))
    return info.doc if info else ""


def _outputs_of(recorder: Recorder, handling: Handling) -> list[Message]:
    """The messages a handling sent, in order."""
    bus = recorder.engine.bus
    pool = bus.delivered + [q[3] for q in bus._queue]
    return [m for m in pool if recorder.made_by.get(m.msg_id) is handling]


def _attach_captions(story: Story, engine: Engine, events: list[dict[str, Any]]) -> None:
    by_id = {m.msg_id: m for m in engine.bus.delivered}
    start = 0
    for caption in story.captions:
        for i in range(start, len(events)):
            if caption.when(by_id[events[i]["id"]]):
                events[i]["caption"] = caption.text
                events[i]["pause"] = caption.pause
                events[i]["minor"] = False  # a key moment is always shown
                start = i + 1
                break
        else:
            raise SystemExit(f"{story.key}: no event for caption {caption.text[:50]!r}")


# --- entry point --------------------------------------------------------------------------


def build(modules=None) -> dict[str, Any]:
    modules = modules or module_set()[0]
    return {
        "generatedBy": "python -m app.demo",
        "modules": modules.kinds,
        "constants": {
            "floors": MAX_FLOOR,
            "secondsPerFloor": SECONDS_PER_FLOOR,
            "dwell": DWELL_S,
            "delayCap": DELAY_CAP_S,
            "fullLoad": FULL_LOAD_KG,
            "maxLoad": MAX_LOAD_KG,
        },
        "stories": [record(story, modules) for story in STORIES],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.demo", description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the events, write nothing")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    modules, labels = module_set()
    print(describe(labels))
    data = build(modules)
    if args.list:
        for story in data["stories"]:
            print(f"\n== {story['key']}: {len(story['events'])} events")
            for e in story["events"]:
                if e["minor"]:
                    continue
                cap = "  <<" if e.get("caption") else ""
                print(f"{e['t']:7.2f} {e['from']:>10} -> {','.join(e['to']):<18} {e['text']}{cap}")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), default=str)
    args.out.write_text(
        "// Generated by `python -m app.demo` from real runs. Do not edit by hand.\n"
        f"window.DEMO = {text};\n"
    )
    print(f"wrote {args.out.relative_to(ROOT)} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
