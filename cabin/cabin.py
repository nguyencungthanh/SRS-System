"""The Cabin Controller: one instance per car (SYS-02). Owner: Hai.

A pure message handler (contracts/interfaces.py). It owns simulated motion, doors, load,
route acceptance and the local safety veto, and talks to the rest of the system only
through the messages it returns.

Timers to itself (D-08), each carrying an epoch so stale ones are ignored:
    "hb"      telemetry heartbeat every TELEMETRY_HEARTBEAT_S (D-11)
    "arrive"  arrival at the current target floor
    "door"    end of the current door phase

After every input the cabin runs ``_progress`` (empty handoff check, then try to open or
depart) and sends telemetry if anything observable changed.
"""

from typing import Any

from cabin import motion
from cabin.doors import DURATION, ENTRY_EVENT, DoorInput, next_state
from cabin.mission import (
    MODE as MISSION_MODE,
)
from cabin.mission import (
    WAITING_FOR_CONFIRMATION,
    MissionPhase,
    blocks_departure,
    load_conflicts,
)
from cabin.proposal import SENSOR_FAULTS, CommandCache, RouteState, StoredReply, validate
from contracts.config import FULL_LOAD_KG, MAX_FLOOR, MAX_LOAD_KG, MIN_FLOOR, TELEMETRY_HEARTBEAT_S
from contracts.enums import (
    AlertKind,
    CabinMode,
    DestStatus,
    Direction,
    DoorState,
    FaultKind,
    MissionAction,
    MissionStatus,
    NackReason,
    RejectReason,
    ServiceEventType,
    StopKind,
)
from contracts.messages import (
    TIMER_START,
    AlarmPressed,
    Alert,
    CabinDestinationPressed,
    CabinInit,
    CabinTelemetry,
    DoorRequest,
    FaultCleared,
    FaultInjected,
    FireAlarm,
    MaintenanceSet,
    Message,
    MissionLoadConfirmed,
    MissionStatusUpdate,
    PassengerBoard,
    PassengerExit,
    RecoveryApproved,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    ServiceEvent,
    Stop,
    Timer,
    payload_key,
)
from contracts.routing import cabin_address
from contracts.ui import CabinSnapshot, CarView, DestView, StopView

E = ServiceEventType
KEY_HEARTBEAT = "hb"
KEY_ARRIVE = "arrive"
KEY_DOOR = "door"

# Faults that stop the car where it is until cleared (open question Q3: only
# MOTION_SERIOUS needs supervisor recovery; the others resume once FaultCleared).
STOPPING_FAULTS = SENSOR_FAULTS | {FaultKind.DOOR_LOCK_LOSS}


class Cabin:
    def __init__(self, init: CabinInit) -> None:
        self.car_id = init.car_id
        self.name = cabin_address(init.car_id)

        # motion (D-02): position is derived from where/when the current leg started
        self.now = 0.0  # last simulated time handled
        self.pos0 = float(init.floor)
        self.t0 = 0.0
        self.moving = False
        self.direction = Direction.IDLE  # travel direction while moving
        self.target: int | None = None
        self.motion_epoch = 0

        # doors (D-03)
        self.door = DoorState.CLOSED
        self.door_epoch = 0
        self.door_held = False  # OPEN period over, kept open (overload / mission)
        self.obstruction_at: float | None = None

        # passengers and load
        self.onboard: dict[str, float] = {}  # passenger_id -> kg
        self.cargo_kg = 0.0
        self.dests: dict[str, tuple[int, DestStatus]] = {}

        # route (D-16)
        self.route: list[Stop] = []
        self.version = init.route_version
        self.replies = CommandCache()

        # safety
        self.faults: set[FaultKind] = set(init.faults)
        self.hold: CabinMode | None = None  # FAULT_HOLD or MAINTENANCE
        self.fire = init.mode == CabinMode.FIRE
        if init.mode in (CabinMode.FAULT_HOLD, CabinMode.MAINTENANCE):
            self.hold = init.mode
        if FaultKind.MOTION_SERIOUS in self.faults:
            self.hold = CabinMode.FAULT_HOLD

        # mission (D-52)
        self.mission_id: str | None = None
        self.phase = MissionPhase.NONE

        # telemetry
        self.heartbeat_epoch = 0
        self.last_telemetry: tuple[Any, ...] | None = None
        self.idle = True

        for p in init.passengers:
            self.onboard[p.passenger_id] = p.weight_kg
            if p.destination is not None:
                stop = Stop(floor=p.destination, kind=StopKind.CAR_DEST, ref_id=p.passenger_id)
                self.route = motion.insert_in_sweep(self.route, self.pos0, stop)
                self.dests[p.passenger_id] = (p.destination, DestStatus.PENDING)

    # ==================================================================================
    # Module interface
    # ==================================================================================

    def handle(self, msg: Message, now: float) -> list[Message]:
        self.now = now
        out: list[Message] = []
        heartbeat = False
        match msg:
            case Timer():
                heartbeat = self._on_timer(msg, now, out)
            case RouteProposal():
                self._on_proposal(msg, now, out)
            case RouteVersionQuery():
                self._on_version_query(msg, now, out)
            case PassengerBoard():
                self._on_board(msg, now, out)
            case PassengerExit():
                self._on_exit(msg, now, out)
            case CabinDestinationPressed():
                self._on_destination(msg, now, out)
            case DoorRequest():
                self._on_door_request(msg, now, out)
            case FaultInjected():
                self._on_fault(msg, now, out)
            case FaultCleared():
                self.faults.discard(msg.kind)
            case RecoveryApproved():
                self._on_recovery()
            case MaintenanceSet(on=True):
                if self.hold is None:
                    self.hold = CabinMode.MAINTENANCE
                    self._drop_hall_pickups()
            case FireAlarm():
                self.fire = msg.active  # Q6: back to normal when the alarm ends
            case MissionLoadConfirmed():
                self._on_mission_confirm(msg, now, out)
            case AlarmPressed():
                pass  # Control Center raises the ALARM alert, never the cabin (D-19)
            case _:
                pass
        self._progress(now, out)
        self._send_telemetry(now, out, heartbeat)
        return out

    def snapshot(self) -> CabinSnapshot:
        car: CarView = {
            "car_id": self.car_id,
            "position": self._pos_now(),
            "floor_display": round(self._pos_now()),
            "direction": self._reported_direction(self._pos_now()).value,
            "door": self.door.value,
            "locked": self._locked(),
            "load_kg": self.load_kg,
            "passengers": len(self.onboard),
            "mode": self.mode.value,
            "faults": sorted(f.value for f in self.faults),
            "route_version": self.version,
            "stops": [_stop_view(s) for s in self.route],
            "mission_id": self.mission_id,
        }
        dests: list[DestView] = [
            {"car_id": self.car_id, "floor": floor, "passenger_id": pid, "status": status.value}
            for pid, (floor, status) in self.dests.items()
        ]
        return {"car": car, "dests": dests}

    # ==================================================================================
    # Derived state
    # ==================================================================================

    @property
    def load_kg(self) -> float:
        return sum(self.onboard.values()) + self.cargo_kg

    @property
    def mode(self) -> CabinMode:
        """One reported mode; the more restrictive state wins."""
        if self.hold is not None:
            return self.hold
        if self.fire:
            return CabinMode.FIRE
        if self.load_kg > MAX_LOAD_KG:
            return CabinMode.OVERLOAD_HOLD
        mission_mode = MISSION_MODE[self.phase]
        if mission_mode is not None:
            return mission_mode
        if self.load_kg >= FULL_LOAD_KG:
            return CabinMode.FULL
        return CabinMode.AUTO

    def _pos(self, now: float) -> float:
        if self.moving:
            return motion.position_at(self.pos0, self.t0, self.direction, now)
        return self.pos0

    def _pos_now(self) -> float:
        """Position at the last simulated time this cabin handled (used by snapshot())."""
        return self._pos(self.now)

    def _floor(self) -> int:
        return round(self.pos0)

    def _at_landing(self) -> bool:
        return not self.moving and motion.is_landing(self.pos0)

    def _locked(self) -> bool:
        return self.door == DoorState.CLOSED and FaultKind.DOOR_LOCK_LOSS not in self.faults

    def _sensors_valid(self) -> bool:
        return not (self.faults & SENSOR_FAULTS)

    def _reported_direction(self, pos: float) -> Direction:
        if self.moving:
            return self.direction
        if self.route:
            return motion.direction_to(pos, self.route[0].floor)
        return Direction.IDLE

    def _pending_dests(self) -> frozenset[tuple[str, int]]:
        return frozenset(
            (s.ref_id, s.floor)
            for s in self.route
            if s.kind == StopKind.CAR_DEST and s.ref_id is not None
        )

    # ==================================================================================
    # Timers and telemetry
    # ==================================================================================

    def _timer(self, now: float, delay: float, key: str, epoch: int) -> Timer:
        return Timer(
            t_sim=now, deliver_at=now + delay, target=self.name, key=key, ref_id=str(epoch)
        )

    def _on_timer(self, msg: Timer, now: float, out: list[Message]) -> bool:
        """Returns True when this timer is a heartbeat (telemetry is due)."""
        if msg.key == TIMER_START:
            self.heartbeat_epoch += 1
            out.append(self._timer(now, TELEMETRY_HEARTBEAT_S, KEY_HEARTBEAT, self.heartbeat_epoch))
            return True
        if msg.key == KEY_HEARTBEAT and msg.ref_id == str(self.heartbeat_epoch):
            out.append(self._timer(now, TELEMETRY_HEARTBEAT_S, KEY_HEARTBEAT, self.heartbeat_epoch))
            return True
        if msg.key == KEY_ARRIVE and msg.ref_id == str(self.motion_epoch) and self.moving:
            self._arrive(now, out)
        elif msg.key == KEY_DOOR and msg.ref_id == str(self.door_epoch):
            self._door_phase_over(now, out)
        return False

    def _telemetry(self, now: float) -> CabinTelemetry:
        pos = self._pos(now)
        return CabinTelemetry(
            t_sim=now,
            car_id=self.car_id,
            position=pos,
            direction=self._reported_direction(pos),
            moving=self.moving,
            door=self.door,
            locked=self._locked(),
            door_zone=not self.moving and motion.is_landing(pos),
            obstruction=self.obstruction_at == now,
            load_kg=self.load_kg,
            passengers=len(self.onboard),
            mode=self.mode,
            route_version=self.version,
            stops=tuple(self.route),
            faults=tuple(sorted(self.faults)),
            sensors_valid=self._sensors_valid(),
            mission_id=self.mission_id,
        )

    def _send_telemetry(self, now: float, out: list[Message], heartbeat: bool) -> None:
        """On every change and on every heartbeat (D-11); never while HEARTBEAT_LOSS."""
        if FaultKind.HEARTBEAT_LOSS in self.faults:
            return
        tel = self._telemetry(now)
        key = payload_key(tel)
        if heartbeat or key != self.last_telemetry:
            out.append(tel)
            self.last_telemetry = key

    def _event(self, now: float, out: list[Message], event: ServiceEventType, **kw: Any) -> None:
        kw.setdefault("floor", round(self._pos(now)))
        out.append(ServiceEvent(t_sim=now, car_id=self.car_id, event=event, **kw))

    def _mission_status(self, now: float, out: list[Message], status: MissionStatus) -> None:
        assert self.mission_id is not None
        out.append(
            MissionStatusUpdate(
                t_sim=now, mission_id=self.mission_id, status=status, car_id=self.car_id
            )
        )

    def _alert(self, now: float, out: list[Message], kind: AlertKind, **kw: Any) -> None:
        out.append(Alert(t_sim=now, kind=kind, car_id=self.car_id, **kw))

    # ==================================================================================
    # Motion (HA-02)
    # ==================================================================================

    def _progress(self, now: float, out: list[Message]) -> None:
        self._check_handoff(now, out)
        self._try_depart(now, out)
        if not self.route and not self.moving and self.door == DoorState.CLOSED:
            if not self.idle:
                self.idle = True
                self._event(now, out, E.CAR_IDLE)
        elif self.route:
            self.idle = False

    def _can_move(self) -> bool:
        """MOVE-02, SAF-01, CB-02: locked doors, valid position, load, no inhibiting fault."""
        return (
            self.hold != CabinMode.FAULT_HOLD
            and self._locked()
            and self._sensors_valid()
            and self.load_kg <= MAX_LOAD_KG
            and not (self.faults & STOPPING_FAULTS)
        )

    def _try_depart(self, now: float, out: list[Message]) -> None:
        if self.moving or self.door != DoorState.CLOSED or not self.route:
            return
        nxt = self.route[0]
        if motion.at_floor(self.pos0, nxt.floor):
            if self.hold != CabinMode.FAULT_HOLD:
                self._serve_here(now, out)
            return
        if not self._can_move() or blocks_departure(self.phase, nxt):
            return
        self._event(now, out, E.CAR_DEPARTED, floor=round(self.pos0))
        self.direction = motion.direction_to(self.pos0, nxt.floor)
        self.moving = True
        self.t0 = now
        self._set_target(nxt.floor, now, out)

    def _set_target(self, floor: int, now: float, out: list[Message]) -> None:
        """(Re)aim the moving cabin at ``floor`` from its current position."""
        pos = self._pos(now)
        self.pos0, self.t0, self.target = pos, now, floor
        self.motion_epoch += 1
        out.append(self._timer(now, motion.travel_time(pos, floor), KEY_ARRIVE, self.motion_epoch))

    def _retarget(self, now: float, out: list[Message]) -> None:
        """After a route change while moving: aim at the new first stop if it lies ahead."""
        if not self.moving or not self.route or self.route[0].floor == self.target:
            return
        ahead = (self.route[0].floor - self._pos(now)) * motion.sign(self.direction)
        if ahead >= -motion.EPS:
            self._set_target(self.route[0].floor, now, out)

    def _stop_motion(self, now: float) -> None:
        """Stop exactly where the cabin is (EMG-02, SAF-03); pending arrival is invalidated."""
        if self.moving:
            self.pos0 = self._pos(now)
            self.t0 = now
            self.moving = False
            self.motion_epoch += 1

    def _arrive(self, now: float, out: list[Message]) -> None:
        assert self.target is not None
        self.pos0, self.t0, self.moving = float(self.target), now, False
        self._event(now, out, E.CAR_ARRIVED, floor=self.target)
        self._serve_here(now, out)

    def _serve_here(self, now: float, out: list[Message]) -> None:
        """Serve every stop for this floor at the head of the route, then open the doors."""
        floor = self._floor()
        self._check_handoff(now, out)
        changed = open_doors = False
        while self.route and self.route[0].floor == floor:
            stop = self.route[0]
            if stop.kind == StopKind.HALL_PICKUP:
                self._event(now, out, E.HALL_CALL_SERVED, floor=floor, ref_id=stop.ref_id)
            elif stop.kind == StopKind.CAR_DEST:
                self._event(now, out, E.DEST_SERVED, floor=floor, passenger_id=stop.ref_id)
                if stop.ref_id is not None:
                    self.dests[stop.ref_id] = (floor, DestStatus.SERVED)
            elif stop.kind == StopKind.MISSION_PICKUP:
                if self.phase == MissionPhase.TO_PICKUP:
                    self.phase = MissionPhase.AT_PICKUP
                    open_doors = True
                break  # stays in the route until LOADED
            elif stop.kind == StopKind.MISSION_DROPOFF:
                if self.phase == MissionPhase.EXCLUSIVE:
                    self.phase = MissionPhase.AT_DROPOFF
                    self._event(now, out, E.MISSION_DELIVERY_DONE, floor=floor, ref_id=stop.ref_id)
                    open_doors = True
                break  # stays in the route until UNLOADED
            self.route.pop(0)
            changed = True
        if changed:
            self.version += 1
        if changed or open_doors:
            self._door(DoorInput.OPEN_CMD, now, out)

    # ==================================================================================
    # Doors (HA-03)
    # ==================================================================================

    def _door(self, event: DoorInput, now: float, out: list[Message]) -> None:
        if event in (DoorInput.OPEN_CMD, DoorInput.OPEN_BUTTON) and self.door == DoorState.CLOSED:
            # DOOR-02, DOOR-05: only stationary, in a landing door zone, no fault hold
            if not self._at_landing() or self.hold == CabinMode.FAULT_HOLD:
                return
        new = next_state(self.door, event)
        if new is None:
            return
        changed = new != self.door
        self.door = new
        self.door_epoch += 1
        self.door_held = False
        if changed:
            self._event(now, out, ENTRY_EVENT[new])
        duration = DURATION[new]
        if duration is not None:
            out.append(self._timer(now, duration, KEY_DOOR, self.door_epoch))
        if new == DoorState.CLOSED:
            self._on_doors_closed(now, out)

    def _door_phase_over(self, now: float, out: list[Message]) -> None:
        if self.door == DoorState.OPEN and self._must_hold_open():
            self.door_held = True
            return
        self._door(DoorInput.TIMER, now, out)

    def _must_hold_open(self) -> bool:
        """CB-05: overload holds the doors (Q5); D-52: wait for the staff confirmation."""
        return self.load_kg > MAX_LOAD_KG or self.phase in WAITING_FOR_CONFIRMATION

    def _release_door(self, now: float, out: list[Message]) -> None:
        if self.door == DoorState.OPEN and self.door_held and not self._must_hold_open():
            self._door(DoorInput.TIMER, now, out)

    def _on_door_request(self, msg: DoorRequest, now: float, out: list[Message]) -> None:
        if self.moving:
            return  # DOOR-05
        if msg.open:
            self._door(DoorInput.OPEN_BUTTON, now, out)
        elif not self.door_held:
            self._door(DoorInput.CLOSE_BUTTON, now, out)

    def _on_doors_closed(self, now: float, out: list[Message]) -> None:
        """End of a door cycle. Q7: a drain stop whose people did not all leave and whose
        load contradicts the count goes to HOLD_PENDING_REVIEW (SEN-06)."""
        if self.phase != MissionPhase.DRAINING or self._pending_dests() or not self.onboard:
            return
        load_ok = FaultKind.LOAD_SENSOR not in self.faults
        if load_conflicts(len(self.onboard), self.load_kg, load_ok):
            self.phase = MissionPhase.REVIEW
            self._mission_status(now, out, MissionStatus.HOLD_PENDING_REVIEW)
            self._alert(now, out, AlertKind.HANDOFF_REVIEW, ref_id=self.mission_id)

    # ==================================================================================
    # Route proposals (HA-04)
    # ==================================================================================

    def _route_state(self, now: float) -> RouteState:
        return RouteState(
            version=self.version,
            route=tuple(self.route),
            pending_dests=self._pending_dests(),
            hold=self.hold,
            fire=self.fire,
            faults=frozenset(self.faults),
            mission_id=self.mission_id,
            load_kg=self.load_kg,
            moving=self.moving,
            position=self._pos(now),
            direction=self.direction,
        )

    def _reply(self, msg: RouteProposal, now: float, reply: StoredReply) -> RouteReply:
        return RouteReply(
            t_sim=now,
            command_id=msg.command_id,
            car_id=self.car_id,
            accepted=reply.accepted,
            committed_version=reply.committed_version,
            reason=reply.reason,
        )

    def _on_proposal(self, msg: RouteProposal, now: float, out: list[Message]) -> None:
        stored = self.replies.lookup(msg)
        if isinstance(stored, StoredReply):  # identical retry: same answer, no new route
            out.append(self._reply(msg, now, stored))
            return
        if stored == NackReason.PAYLOAD_CONFLICT:  # D-14: an error, route unchanged
            out.append(self._reply(msg, now, StoredReply(False, self.version, stored)))
            self._alert(now, out, AlertKind.PROTOCOL_ERROR, ref_id=msg.command_id)
            return

        reason = validate(msg, self._route_state(now), now)
        if reason is not None:
            reply = StoredReply(False, self.version, reason)
        else:
            self.route = list(msg.ordered_stops)
            self.version += 1
            reply = StoredReply(True, self.version, None)
            if msg.mission_id is not None and msg.mission_id != self.mission_id:
                self._start_mission(msg.mission_id, now, out)
            self._retarget(now, out)
        self.replies.store(msg, reply)
        out.append(self._reply(msg, now, reply))

    def _on_version_query(self, msg: RouteVersionQuery, now: float, out: list[Message]) -> None:
        stored = self.replies.get(msg.command_id)
        out.append(
            RouteVersionReport(
                t_sim=now,
                command_id=msg.command_id,
                car_id=self.car_id,
                seen=stored is not None,
                accepted=stored.accepted if stored else None,
                route_version=self.version,
            )
        )

    def _drop_hall_pickups(self) -> None:
        """D-55: a car that stops serving releases its unpicked hall calls."""
        kept = [s for s in self.route if s.kind != StopKind.HALL_PICKUP]
        if len(kept) != len(self.route):
            self.route = kept
            self.version += 1

    # ==================================================================================
    # Passengers, destinations and load (HA-05)
    # ==================================================================================

    def _on_board(self, msg: PassengerBoard, now: float, out: list[Message]) -> None:
        if self.moving or msg.passenger_id in self.onboard:
            return
        self.onboard[msg.passenger_id] = msg.weight_kg
        self._event(now, out, E.PASSENGER_BOARDED, passenger_id=msg.passenger_id)

    def _on_exit(self, msg: PassengerExit, now: float, out: list[Message]) -> None:
        if self.moving or msg.passenger_id not in self.onboard:
            return  # never remove anyone from a moving cabin (5.5)
        del self.onboard[msg.passenger_id]
        self._event(now, out, E.PASSENGER_EXITED, passenger_id=msg.passenger_id)
        # Manual exit before the destination (SEN-04): that trip is over.
        kept = [
            s
            for s in self.route
            if not (s.kind == StopKind.CAR_DEST and s.ref_id == msg.passenger_id)
        ]
        if len(kept) != len(self.route):
            self.route = kept
            self.version += 1
            self.dests.pop(msg.passenger_id, None)
        self._release_door(now, out)

    def _on_destination(self, msg: CabinDestinationPressed, now: float, out: list[Message]) -> None:
        pid, floor = msg.passenger_id, msg.floor
        if pid not in self.onboard:  # DOOR-06
            self._reject_destination(now, out, msg, RejectReason.NOT_BOARDED)
            return
        if not MIN_FLOOR <= floor <= MAX_FLOOR:
            self._reject_destination(now, out, msg, RejectReason.FLOOR_OUT_OF_RANGE)
            return
        current = self.dests.get(pid)
        if current == (floor, DestStatus.PENDING):
            return  # pressed again: nothing new
        self.route = [
            s for s in self.route if not (s.kind == StopKind.CAR_DEST and s.ref_id == pid)
        ]
        stop = Stop(floor=floor, kind=StopKind.CAR_DEST, ref_id=pid)
        self.route = motion.insert_in_sweep(self.route, self._pos(now), stop)
        self.version += 1
        self.dests[pid] = (floor, DestStatus.PENDING)
        self._event(now, out, E.DEST_ACCEPTED, floor=floor, passenger_id=pid)
        self._retarget(now, out)

    def _reject_destination(
        self, now: float, out: list[Message], msg: CabinDestinationPressed, reason: RejectReason
    ) -> None:
        self._event(
            now,
            out,
            E.DEST_REJECTED,
            floor=msg.floor,
            passenger_id=msg.passenger_id,
            reason=reason,
        )

    # ==================================================================================
    # Faults, recovery (HA-06)
    # ==================================================================================

    def _on_fault(self, msg: FaultInjected, now: float, out: list[Message]) -> None:
        if msg.kind == FaultKind.OBSTRUCTION:  # momentary: one input, one reopen (D-09)
            self.obstruction_at = now
            self._door(DoorInput.OBSTRUCTION, now, out)
            return
        if msg.kind in self.faults:
            return
        self.faults.add(msg.kind)
        if msg.kind == FaultKind.MOTION_SERIOUS:  # EMG-02, SAF-03
            self._stop_motion(now)
            self.hold = CabinMode.FAULT_HOLD
            self._drop_hall_pickups()
            self._alert(now, out, AlertKind.FAULT, details=msg.kind.value)
        elif msg.kind in STOPPING_FAULTS:  # Q3
            self._stop_motion(now)
            self._alert(now, out, AlertKind.SENSOR, details=msg.kind.value)

    def _on_recovery(self) -> None:
        """EMG-04: the approval alone is not enough; the cabin's readiness checks must pass."""
        if self.hold == CabinMode.FAULT_HOLD:
            if FaultKind.MOTION_SERIOUS not in self.faults and not (self.faults & STOPPING_FAULTS):
                self.hold = None
        elif self.hold == CabinMode.MAINTENANCE:
            self.hold = None  # Q1: MaintenanceSet(on=False) not required yet

    # ==================================================================================
    # Missions (HA-07)
    # ==================================================================================

    def _start_mission(self, mission_id: str, now: float, out: list[Message]) -> None:
        self.mission_id = mission_id
        self.phase = MissionPhase.DRAINING
        self.cargo_kg = 0.0
        if self.onboard or self._pending_dests():
            self._mission_status(now, out, MissionStatus.DRAINING)

    def _check_handoff(self, now: float, out: list[Message]) -> None:
        """SEN-05: stationary at a landing, count 0, no destinations left, load agrees."""
        if self.phase not in (MissionPhase.DRAINING, MissionPhase.REVIEW):
            return
        if not self._at_landing() or self.onboard or self._pending_dests():
            return
        if load_conflicts(0, self.load_kg, FaultKind.LOAD_SENSOR not in self.faults):
            return
        if not self._sensors_valid():
            return
        self._event(now, out, E.DRAIN_COMPLETE, ref_id=self.mission_id)
        self._event(now, out, E.HANDOFF_OK, ref_id=self.mission_id)
        self.phase = MissionPhase.TO_PICKUP

    def _on_mission_confirm(
        self, msg: MissionLoadConfirmed, now: float, out: list[Message]
    ) -> None:
        if msg.mission_id != self.mission_id or self.phase == MissionPhase.NONE:
            return
        if msg.action == MissionAction.LOAD_FAILED:  # FRT-04
            self._mission_status(now, out, MissionStatus.FAILED)
            self._end_mission()
        elif msg.action == MissionAction.LOADED and self.phase == MissionPhase.AT_PICKUP:
            self.cargo_kg = msg.weight_kg
            self._event(now, out, E.MISSION_PICKUP_DONE, ref_id=self.mission_id)
            self._mission_status(now, out, MissionStatus.IN_PROGRESS)
            self.route = [s for s in self.route if s.kind != StopKind.MISSION_PICKUP]
            self.version += 1
            self.phase = MissionPhase.EXCLUSIVE
        elif msg.action == MissionAction.UNLOADED and self.phase == MissionPhase.AT_DROPOFF:
            self._mission_status(now, out, MissionStatus.COMPLETED)
            self._end_mission()
        self._release_door(now, out)

    def _end_mission(self) -> None:
        """Release the exclusive reservation (FRT-04) and drop the mission's stops."""
        kept = [s for s in self.route if s.ref_id != self.mission_id]
        if len(kept) != len(self.route):
            self.route = kept
            self.version += 1
        self.mission_id = None
        self.phase = MissionPhase.NONE
        self.cargo_kg = 0.0


def _stop_view(stop: Stop) -> StopView:
    return {
        "floor": stop.floor,
        "kind": stop.kind.value,
        "ref_id": stop.ref_id,
        "direction": stop.direction.value if stop.direction else None,
    }
