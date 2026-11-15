"""Live run measurements read from the event log (AN-03, D-30, D-63, D-66).

``RunObserver.observe(entry)`` takes every log entry (the JSON dicts of ``Bus.log``) in
order. It never touches a module, so the same observer works for the engine's snapshot and
for the offline evaluator.

Pickup waits (D-30): served time minus the call's ``created_at`` (accepted HallCallAck).

Commitment delay (D-63): when a HALL_PICKUP or CAR_DEST stop first appears in a car's
telemetry, its reference ETA is fixed with eq. (1) and (2) from that telemetry. Every route
Dispatch gets ACKed is then checked against those references: a modeled ETA more than
DELAY_CAP_S later than the reference is a violation (a Dispatch decision broke DC-04).
Separately, ``served - reference`` is kept as the observed commitment lateness; it also
includes delays nobody decided (destinations passengers press, door re-openings), so it is
reported, not judged. A door cycle in progress counts at its longest remaining time, and
consecutive stops at one floor share one dwell. Mission stops are left out: an exclusive
leg waits for staff by design.

Hard invariants (D-66): a hall call listed by two cars at once, or a car that reports
moving with its door not closed and locked.
"""

import math
from typing import Any

from contracts.config import (
    DELAY_CAP_S,
    DOOR_CLOSING_S,
    DOOR_OPEN_S,
    DWELL_S,
    SECONDS_PER_FLOOR,
)
from contracts.enums import CabinMode, DoorState, ServiceEventType, StopKind

TOLERANCE_S = 1e-6
PROTECTED_KINDS = (StopKind.HALL_PICKUP.value, StopKind.CAR_DEST.value)
HELD_MODES = (CabinMode.FAULT_HOLD.value, CabinMode.MAINTENANCE.value)
REMAINING_DOOR_S = {
    DoorState.OPENING.value: DWELL_S,
    DoorState.OPEN.value: DOOR_OPEN_S + DOOR_CLOSING_S,
    DoorState.CLOSING.value: DOOR_CLOSING_S,
    DoorState.OBSTRUCTED.value: DWELL_S,
}

Entry = dict[str, Any]


def nearest_rank(sorted_values: list[float], q: float) -> float | None:
    """Percentile by the nearest-rank method (D-30)."""
    if not sorted_values:
        return None
    k = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[k - 1]


def reference_etas(telemetry: Entry) -> dict[tuple[str, str | None, int], float]:
    """ETA of every stop in a telemetry entry, by eq. (1) and (2) (D-63)."""
    return route_etas(telemetry, telemetry["stops"])


def route_etas(telemetry: Entry, stops: list[Entry]) -> dict[tuple[str, str | None, int], float]:
    """ETA of every stop in ``stops`` for the car in ``telemetry`` (eq. 1, 2)."""
    t = telemetry["t_sim"] + REMAINING_DOOR_S.get(telemetry["door"], 0.0)
    position = telemetry["position"]
    # A door cycle already running at this landing IS the dwell of this floor: its remaining
    # time is counted above, so leaving this floor costs no second dwell.
    door_running = not telemetry["moving"] and telemetry["door"] != DoorState.CLOSED.value
    previous_floor: int | None = round(position) if door_running else None
    dwell_paid = door_running
    etas: dict[tuple[str, str | None, int], float] = {}
    for stop in stops:
        if previous_floor is not None and stop["floor"] != previous_floor:
            if not dwell_paid:
                t += DWELL_S  # one dwell per served floor, before leaving it
            dwell_paid = False
        t += abs(position - stop["floor"]) * SECONDS_PER_FLOOR
        position = previous_floor = stop["floor"]
        etas.setdefault((stop["kind"], stop["ref_id"], stop["floor"]), t)
    return etas


def _held(tel: Entry | None) -> bool:
    """The car cannot serve: a fault, a held/maintenance mode or invalid sensors (SEN-02)."""
    if tel is None:
        return False
    return (
        bool(tel.get("faults"))
        or tel.get("mode") in HELD_MODES
        or not tel.get("sensors_valid", True)
    )


class RunObserver:
    def __init__(self) -> None:
        self.created: dict[str, float] = {}
        self.waits: list[float] = []
        self.served: set[str] = set()
        self.references: dict[str, dict[tuple[str, str | None, int], float]] = {}
        self.delays: list[float] = []  # observed lateness, served - reference
        self.late_commitments = 0  # observed lateness above DELAY_CAP_S (reported only)
        self.delay_violations = 0  # ACKed Dispatch routes breaking DELAY_CAP (D-63, D-66)
        self._last_tel: dict[str, Entry] = {}
        self._proposals: dict[str, Entry] = {}
        self.double_owner = 0
        self.door_breaches = 0
        self._pickups_by_car: dict[str, set[str]] = {}
        self._owner: dict[str, str] = {}  # hall call -> car that last carried its pickup
        self.fault_reassigned: set[str] = set()  # calls moved off a held car (NFR-04)

    # --- feeding ----------------------------------------------------------------------

    def observe(self, entry: Entry) -> None:
        kind = entry.get("msg_type")
        if kind == "HallCallAck":
            if entry["accepted"] and not entry["duplicate"]:
                self.created[entry["request_id"]] = entry["t_sim"]
        elif kind == "CabinTelemetry":
            self._telemetry(entry)
        elif kind == "ServiceEvent":
            self._service(entry)
        elif kind == "RouteProposal":
            self._proposals[entry["command_id"]] = entry
        elif kind == "RouteReply" and entry["accepted"]:
            proposal = self._proposals.pop(entry["command_id"], None)
            if proposal is not None:
                self._judge_decision(proposal)

    def _judge_decision(self, proposal: Entry) -> None:
        car = proposal["car_id"]
        tel = self._last_tel.get(car)
        refs = self.references.get(car, {})
        if tel is None:
            return
        for key, eta in route_etas(tel, proposal["ordered_stops"]).items():
            reference = refs.get(key)
            if reference is not None and eta - reference > DELAY_CAP_S + TOLERANCE_S:
                self.delay_violations += 1

    def _telemetry(self, tel: Entry) -> None:
        car = tel["car_id"]
        self._last_tel[car] = tel
        refs = self.references.setdefault(car, {})
        etas = reference_etas(tel)
        current = set()
        for key, eta in etas.items():
            if key[0] in PROTECTED_KINDS:
                refs.setdefault(key, eta)  # fixed at first sight, never reset (DC-04)
                current.add(key)
        for key in list(refs):
            if key not in current:
                del refs[key]  # removed without service (fault, reroute): not judged
        pickups = {
            s["ref_id"]
            for s in tel["stops"]
            if s["kind"] == StopKind.HALL_PICKUP.value and s["ref_id"]
        }
        self._pickups_by_car[car] = pickups
        for request in pickups:
            before = self._owner.get(request)
            if before is not None and before != car and _held(self._last_tel.get(before)):
                self.fault_reassigned.add(request)
            self._owner[request] = car
        for other, theirs in self._pickups_by_car.items():
            if other != car and pickups & theirs:
                self.double_owner += 1
        if tel["moving"] and (tel["door"] != DoorState.CLOSED.value or not tel["locked"]):
            self.door_breaches += 1

    def _service(self, event: Entry) -> None:
        name = event["event"]
        if name == ServiceEventType.HALL_CALL_SERVED.value:
            request = event["ref_id"]
            created = self.created.get(request)
            if created is not None and request not in self.served:
                self.served.add(request)
                self.waits.append(event["t_sim"] - created)
            self._judge(event, StopKind.HALL_PICKUP.value, request)
        elif name == ServiceEventType.DEST_SERVED.value:
            self._judge(event, StopKind.CAR_DEST.value, event["passenger_id"])

    def _judge(self, event: Entry, kind: str, ref: str | None) -> None:
        refs = self.references.get(event["car_id"], {})
        reference = refs.pop((kind, ref, event["floor"]), None)
        if reference is None:
            return
        delay = event["t_sim"] - reference
        self.delays.append(delay)
        if delay > DELAY_CAP_S + TOLERANCE_S:
            self.late_commitments += 1

    # --- results ----------------------------------------------------------------------

    @property
    def calls(self) -> int:
        return len(self.created)

    @property
    def unserved(self) -> int:
        return len(self.created) - len(self.served)

    def wait_stats(self) -> dict[str, float | None]:
        waits = sorted(self.waits)
        return {
            "mean_wait": sum(waits) / len(waits) if waits else None,
            "median_wait": nearest_rank(waits, 0.50),
            "p95_wait": nearest_rank(waits, 0.95),
            "max_wait": waits[-1] if waits else None,
        }

    def breaches(self) -> list[str]:
        """Hard-invariant breaches (D-66), empty when the run is valid."""
        found = []
        if self.delay_violations:
            found.append(f"DELAY_CAP x{self.delay_violations}")
        if self.double_owner:
            found.append(f"DOUBLE_OWNER x{self.double_owner}")
        if self.door_breaches:
            found.append(f"DOOR_INTERLOCK x{self.door_breaches}")
        return found
