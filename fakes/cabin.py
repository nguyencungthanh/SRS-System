"""FakeCabin: always ACKs, jumps one whole floor every SECONDS_PER_FLOOR (framework F6).

What it does:
    * RouteProposal: always ACK and replace the whole route (no version or safety checks).
      Same command_id + same payload returns the stored reply; a different payload gets
      NACK PAYLOAD_CONFLICT (D-14). Answers RouteVersionQuery (D-17).
    * Motion: whole floors only, one Timer per floor. At a stop: CAR_ARRIVED, one service
      event per stop (by StopKind), DOOR_OPENED, then DOOR_CLOSED_LOCKED after DWELL_S.
    * Telemetry on every change and a heartbeat every TELEMETRY_HEARTBEAT_S, except while a
      HEARTBEAT_LOSS fault is active.
    * Passengers: board / exit change the load; destinations only for boarded passengers.
    * MOTION_SERIOUS -> FAULT_HOLD + Alert(FAULT). Other faults block motion until cleared.
      RecoveryApproved returns to AUTO when no motion-blocking fault is left. Entering
      FAULT_HOLD or MAINTENANCE drops HALL_PICKUP stops and keeps CAR_DEST stops (D-55).
    * Missions (D-52): waits at the pickup for LOADED (-> MISSION_PICKUP_DONE, IN_PROGRESS),
      MISSION_DELIVERY_DONE on arrival, COMPLETED on UNLOADED, FAILED on LOAD_FAILED.

What it skips: door OPENING / CLOSING states, obstruction, DoorRequest, landing-zone checks,
insertion cutoff, mission draining and the empty-cabin handoff check.
"""

from contracts.config import (
    DWELL_S,
    FULL_LOAD_KG,
    MAX_FLOOR,
    MAX_LOAD_KG,
    MIN_FLOOR,
    SECONDS_PER_FLOOR,
    TELEMETRY_HEARTBEAT_S,
)
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
    Alert,
    CabinDestinationPressed,
    CabinInit,
    CabinTelemetry,
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

BLOCKING_FAULTS = frozenset(
    {
        FaultKind.MOTION_SERIOUS,
        FaultKind.DOOR_LOCK_LOSS,
        FaultKind.POSITION_SENSOR,
        FaultKind.SENSOR_CONFLICT,
    }
)
SENSOR_FAULTS = frozenset(
    {FaultKind.POSITION_SENSOR, FaultKind.LOAD_SENSOR, FaultKind.SENSOR_CONFLICT}
)
MOVABLE_MODES = frozenset(
    {
        CabinMode.AUTO,
        CabinMode.FULL,
        CabinMode.MISSION_DRAINING,
        CabinMode.MISSION_EXCLUSIVE,
        CabinMode.FIRE,  # D-24: finish the current leg
    }
)
HOLD_MODES = frozenset({CabinMode.FAULT_HOLD, CabinMode.MAINTENANCE, CabinMode.FIRE})

SERVED_EVENT = {
    StopKind.HALL_PICKUP: ServiceEventType.HALL_CALL_SERVED,
    StopKind.CAR_DEST: ServiceEventType.DEST_SERVED,
    StopKind.MISSION_PICKUP: ServiceEventType.MISSION_PICKUP_DONE,
    StopKind.MISSION_DROPOFF: ServiceEventType.MISSION_DELIVERY_DONE,
}

KEY_HEARTBEAT = "heartbeat"
KEY_MOVE = "move"
KEY_DOOR = "door"


class FakeCabin:
    def __init__(self, init: CabinInit) -> None:
        self.car_id = init.car_id
        self.name = cabin_address(init.car_id)
        self.floor = init.floor
        self.direction = Direction.IDLE
        self.door = DoorState.CLOSED
        self.moving = False
        self.hold: CabinMode | None = init.mode if init.mode in HOLD_MODES else None
        self.exclusive = init.mode == CabinMode.MISSION_EXCLUSIVE
        self.mission_id: str | None = None
        self.awaiting_load: str | None = None  # mission waiting for LOADED at its pickup
        self.awaiting_unload: str | None = None  # mission waiting for UNLOADED at its drop-off
        self.faults: set[FaultKind] = set(init.faults)
        self.passengers: dict[str, float] = {p.passenger_id: p.weight_kg for p in init.passengers}
        self.dests: dict[str, tuple[int, DestStatus]] = {}
        self.route: list[Stop] = []
        for p in init.passengers:
            if p.destination is not None:
                self.route.append(
                    Stop(floor=p.destination, kind=StopKind.CAR_DEST, ref_id=p.passenger_id)
                )
                self.dests[p.passenger_id] = (p.destination, DestStatus.PENDING)
        self.route_version = init.route_version
        self._replies: dict[str, tuple[tuple, bool, int, NackReason | None]] = {}
        self._timer_gen = 0  # bumped to invalidate pending move / door timers
        self._last_telemetry: tuple | None = None

    # --- Module interface ------------------------------------------------------------

    def handle(self, msg: Message, now: float) -> list[Message]:
        out: list[Message] = []
        force_telemetry = False
        match msg:
            case Timer(key=key, ref_id=ref_id):
                force_telemetry = self._on_timer(key, ref_id, now, out)
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
            case FaultInjected():
                self._on_fault(msg, now, out)
            case FaultCleared():
                self.faults.discard(msg.kind)
            case MaintenanceSet(on=True):
                if self.hold != CabinMode.FAULT_HOLD:
                    self.hold = CabinMode.MAINTENANCE
                self._stop_motion()
                self._drop_hall_pickups()
            case MissionLoadConfirmed():
                self._on_mission_confirm(msg, now, out)
            case FireAlarm(active=active):
                if active and self.hold is None:
                    self.hold = CabinMode.FIRE
                elif not active and self.hold == CabinMode.FIRE:
                    self.hold = None
            case RecoveryApproved():
                if self.hold in (CabinMode.FAULT_HOLD, CabinMode.MAINTENANCE) and not (
                    self.faults & BLOCKING_FAULTS
                ):
                    self.hold = None
            case _:
                pass  # AlarmPressed, DoorRequest: ignored by the fake
        self._kick(now, out)
        self._telemetry(now, out, force=force_telemetry)
        return out

    def snapshot(self) -> CabinSnapshot:
        car: CarView = {
            "car_id": self.car_id,
            "position": float(self.floor),
            "floor_display": self.floor,
            "direction": self.direction.value,
            "door": self.door.value,
            "locked": self.door == DoorState.CLOSED,
            "load_kg": self.load_kg,
            "passengers": len(self.passengers),
            "mode": self.mode.value,
            "faults": sorted(f.value for f in self.faults),
            "route_version": self.route_version,
            "stops": [_stop_view(s) for s in self.route],
            "mission_id": self.mission_id,
        }
        dests: list[DestView] = [
            {"car_id": self.car_id, "floor": floor, "passenger_id": pid, "status": status.value}
            for pid, (floor, status) in self.dests.items()
        ]
        return {"car": car, "dests": dests}

    # --- derived state ---------------------------------------------------------------

    @property
    def load_kg(self) -> float:
        return sum(self.passengers.values())

    @property
    def mode(self) -> CabinMode:
        if self.hold is not None:
            return self.hold
        if self.load_kg > MAX_LOAD_KG:
            return CabinMode.OVERLOAD_HOLD
        if self.exclusive:
            return CabinMode.MISSION_EXCLUSIVE
        if self.load_kg >= FULL_LOAD_KG:
            return CabinMode.FULL
        return CabinMode.AUTO

    def _can_move(self) -> bool:
        return self.mode in MOVABLE_MODES and not (self.faults & BLOCKING_FAULTS)

    # --- timers and motion -----------------------------------------------------------

    def _on_timer(self, key: str, ref_id: str | None, now: float, out: list[Message]) -> bool:
        if key == TIMER_START:
            out.append(self._timer(now, TELEMETRY_HEARTBEAT_S, KEY_HEARTBEAT))
            return True
        if key == KEY_HEARTBEAT:
            out.append(self._timer(now, TELEMETRY_HEARTBEAT_S, KEY_HEARTBEAT))
            return True
        if ref_id != str(self._timer_gen):
            return False  # stale move / door timer
        if key == KEY_MOVE and self.moving:
            self._on_move(now, out)
        elif key == KEY_DOOR and self.door == DoorState.OPEN:
            self.door = DoorState.CLOSED
            out.append(self._event(now, ServiceEventType.DOOR_CLOSED_LOCKED, self.floor))
            if not self.route:
                self.direction = Direction.IDLE
                out.append(self._event(now, ServiceEventType.CAR_IDLE, self.floor))
        return False

    def _kick(self, now: float, out: list[Message]) -> None:
        """Start moving (or serve the current floor) when the cabin is free to."""
        if self.moving or self.door != DoorState.CLOSED or not self.route:
            return
        if self.awaiting_load is not None:
            return  # D-52: the exclusive leg starts only after LOADED
        if not self._can_move():
            return
        target = self.route[0].floor
        if target == self.floor:
            self._arrive(now, out)
            return
        self.moving = True
        self.direction = Direction.UP if target > self.floor else Direction.DOWN
        out.append(self._event(now, ServiceEventType.CAR_DEPARTED, self.floor))
        out.append(self._timer(now, SECONDS_PER_FLOOR, KEY_MOVE, str(self._timer_gen)))

    def _on_move(self, now: float, out: list[Message]) -> None:
        if not self.route:
            self.moving = False
            self.direction = Direction.IDLE
            return
        target = self.route[0].floor
        self.floor += 1 if target > self.floor else -1
        self.direction = Direction.UP if target > self.floor else Direction.DOWN
        if self.floor == target:
            self.moving = False
            self._arrive(now, out)
        else:
            out.append(self._timer(now, SECONDS_PER_FLOOR, KEY_MOVE, str(self._timer_gen)))

    def _arrive(self, now: float, out: list[Message]) -> None:
        out.append(self._event(now, ServiceEventType.CAR_ARRIVED, self.floor))
        while self.route and self.route[0].floor == self.floor:
            self._serve(self.route.pop(0), now, out)
            self.route_version += 1  # D-16: every change of the stop list
        self.direction = self._direction_to_next()
        self.door = DoorState.OPEN
        out.append(self._event(now, ServiceEventType.DOOR_OPENED, self.floor))
        out.append(self._timer(now, DWELL_S, KEY_DOOR, str(self._timer_gen)))

    def _serve(self, stop: Stop, now: float, out: list[Message]) -> None:
        event = SERVED_EVENT[stop.kind]
        if stop.kind == StopKind.CAR_DEST:
            pid = stop.ref_id
            out.append(self._event(now, event, self.floor, passenger_id=pid))
            if pid in self.dests:
                self.dests[pid] = (self.floor, DestStatus.SERVED)
            return
        if stop.kind == StopKind.MISSION_PICKUP:
            self.awaiting_load = stop.ref_id  # MISSION_PICKUP_DONE is sent on LOADED (D-52)
            return
        out.append(self._event(now, event, self.floor, ref_id=stop.ref_id))
        if stop.kind == StopKind.MISSION_DROPOFF:
            self.awaiting_unload = stop.ref_id

    def _stop_motion(self) -> None:
        """Stop between move steps. A door dwell already running is left to finish."""
        if not self.moving:
            return
        self.moving = False
        self.direction = Direction.IDLE
        self._timer_gen += 1  # the pending move timer becomes stale

    def _direction_to_next(self) -> Direction:
        if not self.route:
            return Direction.IDLE
        nxt = self.route[0].floor
        if nxt == self.floor:
            return self.direction
        return Direction.UP if nxt > self.floor else Direction.DOWN

    # --- message handlers ------------------------------------------------------------

    def _on_proposal(self, msg: RouteProposal, now: float, out: list[Message]) -> None:
        cached = self._replies.get(msg.command_id)
        if cached is not None:
            key, accepted, version, reason = cached
            if key == payload_key(msg):
                out.append(self._reply(now, msg.command_id, accepted, version, reason))
            else:
                out.append(
                    self._reply(
                        now, msg.command_id, False, self.route_version, NackReason.PAYLOAD_CONFLICT
                    )
                )
                out.append(
                    Alert(
                        t_sim=now,
                        kind=AlertKind.PROTOCOL_ERROR,
                        car_id=self.car_id,
                        ref_id=msg.command_id,
                        details="same command_id, different payload",
                    )
                )
            return
        self.route = list(msg.ordered_stops)
        self.route_version += 1
        if msg.mission_id is not None:
            self.mission_id = msg.mission_id
            self.exclusive = msg.exclusive
        self._replies[msg.command_id] = (payload_key(msg), True, self.route_version, None)
        out.append(self._reply(now, msg.command_id, True, self.route_version, None))

    def _on_version_query(self, msg: RouteVersionQuery, now: float, out: list[Message]) -> None:
        cached = self._replies.get(msg.command_id)
        out.append(
            RouteVersionReport(
                t_sim=now,
                command_id=msg.command_id,
                car_id=self.car_id,
                seen=cached is not None,
                accepted=cached[1] if cached is not None else None,
                route_version=self.route_version,
            )
        )

    def _on_board(self, msg: PassengerBoard, now: float, out: list[Message]) -> None:
        self.passengers[msg.passenger_id] = msg.weight_kg
        out.append(
            self._event(
                now, ServiceEventType.PASSENGER_BOARDED, self.floor, passenger_id=msg.passenger_id
            )
        )

    def _on_exit(self, msg: PassengerExit, now: float, out: list[Message]) -> None:
        if msg.passenger_id not in self.passengers:
            return
        del self.passengers[msg.passenger_id]
        out.append(
            self._event(
                now, ServiceEventType.PASSENGER_EXITED, self.floor, passenger_id=msg.passenger_id
            )
        )
        if self._remove_dest_stop(msg.passenger_id):
            self.dests.pop(msg.passenger_id, None)

    def _on_destination(self, msg: CabinDestinationPressed, now: float, out: list[Message]) -> None:
        reason = None
        if msg.passenger_id not in self.passengers:
            reason = RejectReason.NOT_BOARDED
        elif not MIN_FLOOR <= msg.floor <= MAX_FLOOR:
            reason = RejectReason.FLOOR_OUT_OF_RANGE
        if reason is not None:
            out.append(
                self._event(
                    now,
                    ServiceEventType.DEST_REJECTED,
                    msg.floor,
                    passenger_id=msg.passenger_id,
                    reason=reason,
                )
            )
            return
        self._remove_dest_stop(msg.passenger_id)
        self.route.append(Stop(floor=msg.floor, kind=StopKind.CAR_DEST, ref_id=msg.passenger_id))
        self.route_version += 1
        self.dests[msg.passenger_id] = (msg.floor, DestStatus.PENDING)
        out.append(
            self._event(
                now, ServiceEventType.DEST_ACCEPTED, msg.floor, passenger_id=msg.passenger_id
            )
        )

    def _on_mission_confirm(
        self, msg: MissionLoadConfirmed, now: float, out: list[Message]
    ) -> None:
        mid = msg.mission_id
        if msg.action == MissionAction.LOADED and mid == self.awaiting_load:
            self.awaiting_load = None
            out.append(
                self._event(now, ServiceEventType.MISSION_PICKUP_DONE, self.floor, ref_id=mid)
            )
            out.append(self._mission_status(now, mid, MissionStatus.IN_PROGRESS))
        elif msg.action == MissionAction.UNLOADED and mid == self.awaiting_unload:
            out.append(self._mission_status(now, mid, MissionStatus.COMPLETED))
            self._end_mission()
        elif msg.action == MissionAction.LOAD_FAILED and mid == self.mission_id:
            out.append(self._mission_status(now, mid, MissionStatus.FAILED))
            before = len(self.route)
            self.route = [s for s in self.route if s.ref_id != mid]
            if len(self.route) != before:
                self.route_version += 1
            self._end_mission()

    def _end_mission(self) -> None:
        self.exclusive = False
        self.mission_id = None
        self.awaiting_load = None
        self.awaiting_unload = None

    def _drop_hall_pickups(self) -> None:
        """D-55: a car that stops serving releases its hall calls, keeps onboard trips."""
        before = len(self.route)
        self.route = [s for s in self.route if s.kind != StopKind.HALL_PICKUP]
        if len(self.route) != before:
            self.route_version += 1

    def _on_fault(self, msg: FaultInjected, now: float, out: list[Message]) -> None:
        if msg.kind == FaultKind.OBSTRUCTION:
            return  # the fake has no door closing phase to interrupt
        self.faults.add(msg.kind)
        if msg.kind == FaultKind.MOTION_SERIOUS:
            self.hold = CabinMode.FAULT_HOLD
            self._drop_hall_pickups()
        if msg.kind in BLOCKING_FAULTS:
            self._stop_motion()
        kind = AlertKind.SENSOR if msg.kind in SENSOR_FAULTS else AlertKind.FAULT
        out.append(Alert(t_sim=now, kind=kind, car_id=self.car_id, details=msg.kind.value))

    def _remove_dest_stop(self, passenger_id: str) -> bool:
        before = len(self.route)
        self.route = [
            s for s in self.route if not (s.kind == StopKind.CAR_DEST and s.ref_id == passenger_id)
        ]
        if len(self.route) != before:
            self.route_version += 1
            return True
        return False

    # --- output helpers --------------------------------------------------------------

    def _telemetry(self, now: float, out: list[Message], force: bool) -> None:
        if FaultKind.HEARTBEAT_LOSS in self.faults:
            return
        msg = CabinTelemetry(
            t_sim=now,
            car_id=self.car_id,
            position=float(self.floor),
            direction=self.direction,
            moving=self.moving,
            door=self.door,
            locked=self.door == DoorState.CLOSED,
            door_zone=not self.moving,
            obstruction=False,
            load_kg=self.load_kg,
            passengers=len(self.passengers),
            mode=self.mode,
            route_version=self.route_version,
            stops=tuple(self.route),
            faults=tuple(sorted(self.faults)),
            sensors_valid=not (self.faults & SENSOR_FAULTS),
            mission_id=self.mission_id,
        )
        key = payload_key(msg)
        if force or key != self._last_telemetry:
            self._last_telemetry = key
            out.append(msg)

    def _timer(self, now: float, delay: float, key: str, ref_id: str | None = None) -> Timer:
        return Timer(t_sim=now, deliver_at=now + delay, target=self.name, key=key, ref_id=ref_id)

    def _event(self, now: float, event: ServiceEventType, floor: int, **extra) -> ServiceEvent:
        return ServiceEvent(t_sim=now, car_id=self.car_id, event=event, floor=floor, **extra)

    def _reply(
        self,
        now: float,
        command_id: str,
        accepted: bool,
        version: int,
        reason: NackReason | None,
    ) -> RouteReply:
        return RouteReply(
            t_sim=now,
            command_id=command_id,
            car_id=self.car_id,
            accepted=accepted,
            committed_version=version,
            reason=reason,
        )

    def _mission_status(
        self, now: float, mission_id: str, status: MissionStatus
    ) -> MissionStatusUpdate:
        return MissionStatusUpdate(
            t_sim=now, mission_id=mission_id, status=status, car_id=self.car_id
        )


def _stop_view(stop: Stop) -> StopView:
    return {
        "floor": stop.floor,
        "kind": stop.kind.value,
        "ref_id": stop.ref_id,
        "direction": stop.direction.value if stop.direction else None,
    }


def create(init: CabinInit) -> FakeCabin:
    return FakeCabin(init)
