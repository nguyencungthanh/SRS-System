"""Message-driven Dispatch, with one unresolved route command per car.

Telemetry is authoritative; an unanswered proposal retains its reservation until a
reply or reconciliation report resolves it. Stop arrival references are recorded
once, rather than moved forward on each heartbeat or insertion (DC-04).
"""

from contracts.config import (
    ACK_TIMEOUT_S,
    AGE_LIMIT_S,
    CAR_ORDER,
    DOOR_CLOSING_S,
    DOOR_OPEN_S,
    DWELL_S,
    FREIGHT_FIRST_CHOICE,
    MAX_FLOOR,
    MIN_FLOOR,
    PASSENGER_CARS,
    Policy,
)
from contracts.enums import (
    CabinMode,
    CallStatus,
    Direction,
    DispatchAlgorithm,
    DoorState,
    MissionStatus,
    MissionType,
    RejectReason,
    ServiceEventType,
    StopKind,
)
from contracts.ids import PREFIX_COMMAND, PREFIX_HALL_CALL, IdGen
from contracts.messages import (
    CabinTelemetry,
    FireAlarm,
    HallCallAck,
    HallCallPressed,
    Message,
    MissionApproval,
    MissionStatusUpdate,
    PolicyActivated,
    RouteProposal,
    RouteReply,
    RouteVersionQuery,
    RouteVersionReport,
    ServiceEvent,
    Stop,
    Timer,
)
from contracts.ui import DispatchSnapshot

from .missions import mission_candidates, mission_route
from .planning import car_fingerprint, hall_route, ordinary_exclusion, route_etas
from .state import Command, CommandState, HallCall, Mission


class Dispatch:
    name = "dispatch"

    def __init__(self, policy: Policy, algorithm: DispatchAlgorithm) -> None:
        self.policy = policy
        self.algorithm = DispatchAlgorithm(algorithm)
        self._calls: dict[str, HallCall] = {}
        self._active_calls: dict[tuple[int, Direction], str] = {}
        self._telemetry: dict[str, CabinTelemetry] = {}
        self._references: dict[str, dict[Stop, float]] = {}
        self._ready_at: dict[str, float] = {}
        self._minimum_version: dict[str, int] = {}
        self._missions: dict[str, Mission] = {}
        self._commands: dict[str, Command] = {}
        self._inflight: dict[str, str] = {}
        self._call_ids = IdGen(PREFIX_HALL_CALL)
        self._command_ids = IdGen(PREFIX_COMMAND)
        self._fire = False

    def handle(self, msg: Message, now: float) -> list[Message]:
        out: list[Message] = []
        if isinstance(msg, PolicyActivated):
            self.policy = msg.policy
            return out  # OPT-06: activation itself never changes an accepted route.
        if isinstance(msg, HallCallPressed):
            out.append(self._hall_call(msg, now))
        elif isinstance(msg, CabinTelemetry):
            self._observe(msg, now)
        elif isinstance(msg, ServiceEvent):
            self._service(msg, now)
        elif isinstance(msg, RouteReply):
            self._reply(msg, now, out)
        elif isinstance(msg, RouteVersionReport):
            self._report(msg, now, out)
        elif isinstance(msg, MissionApproval):
            self._missions.setdefault(msg.mission_id, Mission(msg))
        elif isinstance(msg, FireAlarm):
            self._fire = msg.active
        elif isinstance(msg, Timer):
            self._timer(msg, now, out)
        else:
            return out
        self._plan(now, out)
        return out

    def snapshot(self) -> DispatchSnapshot:
        return {
            "calls": [
                {
                    "request_id": call.request_id,
                    "floor": call.floor,
                    "direction": call.direction.value,
                    "status": call.status.value,
                    "owner": call.owner,
                    "created_at": call.created_at,
                }
                for call in self._calls.values()
            ],
            "policy": {"version": self.policy.version, "R": self.policy.R, "W": self.policy.W},
        }

    def _hall_call(self, msg: HallCallPressed, now: float) -> HallCallAck:
        reason = None
        if not MIN_FLOOR <= msg.floor <= MAX_FLOOR:
            reason = RejectReason.FLOOR_OUT_OF_RANGE
        elif msg.direction not in (Direction.UP, Direction.DOWN):
            reason = RejectReason.INVALID_DIRECTION
        elif (msg.floor, msg.direction) in (
            (MIN_FLOOR, Direction.DOWN),
            (MAX_FLOOR, Direction.UP),
        ):
            reason = RejectReason.TERMINAL_DIRECTION
        if reason is not None:
            return HallCallAck(
                t_sim=now,
                request_id=None,
                floor=msg.floor,
                direction=msg.direction,
                accepted=False,
                reason=reason,
            )
        key = (msg.floor, msg.direction)
        existing = self._active_calls.get(key)
        duplicate = existing is not None
        if existing is None:
            existing = self._call_ids.next()
            self._calls[existing] = HallCall(existing, msg.floor, msg.direction, now)
            self._active_calls[key] = existing
        call = self._calls[existing]
        return HallCallAck(
            t_sim=now,
            request_id=call.request_id,
            floor=msg.floor,
            direction=msg.direction,
            accepted=True,
            duplicate=duplicate,
            status=call.status,
        )

    def _observe(self, msg: CabinTelemetry, now: float) -> None:
        if msg.car_id not in CAR_ORDER:
            return
        old = self._telemetry.get(msg.car_id)
        if old is not None and (msg.t_sim < old.t_sim or msg.route_version < old.route_version):
            return
        self._telemetry[msg.car_id] = msg
        if old is None or old.door != msg.door:
            remaining = {
                DoorState.OPENING: DWELL_S,
                DoorState.OPEN: DOOR_OPEN_S + DOOR_CLOSING_S,
                DoorState.CLOSING: DOOR_CLOSING_S,
            }.get(msg.door, 0.0)
            self._ready_at[msg.car_id] = now + remaining
        references = self._references.setdefault(msg.car_id, {})
        estimates = route_etas(msg, msg.stops, now, ready_at=self._ready_at.get(msg.car_id))
        for stop, eta in estimates.items():
            references.setdefault(stop, eta)
        current_stops = set(msg.stops)
        for stop in tuple(references):
            if stop not in current_stops:
                del references[stop]
        # Fault and maintenance releases are authoritative only after the Cabin
        # actually removes the pickups; a held car can still list retained work.
        self._release_dropped_pickups(msg)
        if old is not None and old.mission_id is not None and msg.mission_id is None:
            mission = self._missions.get(old.mission_id)
            if mission is not None and mission.status == MissionStatus.RESERVED:
                mission.status = MissionStatus.COMPLETED

    def _release_dropped_pickups(self, telemetry: CabinTelemetry) -> None:
        if telemetry.mode not in (CabinMode.FAULT_HOLD, CabinMode.MAINTENANCE):
            return
        kept = {s.ref_id for s in telemetry.stops if s.kind == StopKind.HALL_PICKUP}
        for call in self._calls.values():
            if (
                call.status == CallStatus.ASSIGNED
                and call.owner == telemetry.car_id
                and call.request_id not in kept
            ):
                self._release(call)

    def _service(self, msg: ServiceEvent, now: float) -> None:
        if msg.event == ServiceEventType.HALL_CALL_SERVED:
            call = self._calls.get(msg.ref_id or "")
            if (
                call is not None
                and call.owner == msg.car_id
                and call.floor == msg.floor
                and call.status in (CallStatus.RESERVED, CallStatus.ASSIGNED)
            ):
                call.status = CallStatus.SERVED
                key = (call.floor, call.direction)
                if self._active_calls.get(key) == call.request_id:
                    self._active_calls.pop(key)
                self._await_stop_removal(msg, StopKind.HALL_PICKUP, call.request_id)
        elif msg.event == ServiceEventType.DEST_SERVED:
            self._await_stop_removal(msg, StopKind.CAR_DEST, msg.passenger_id or msg.ref_id)
        elif msg.event in (ServiceEventType.DOOR_OPENING, ServiceEventType.DOOR_REOPENED):
            self._ready_at[msg.car_id] = now + DWELL_S
        elif msg.event == ServiceEventType.DOOR_OPENED:
            self._ready_at[msg.car_id] = now + DOOR_OPEN_S + DOOR_CLOSING_S
        elif msg.event == ServiceEventType.DOOR_CLOSING:
            self._ready_at[msg.car_id] = now + DOOR_CLOSING_S
        elif msg.event == ServiceEventType.DOOR_CLOSED_LOCKED:
            self._ready_at[msg.car_id] = now

    def _await_stop_removal(self, event: ServiceEvent, kind: StopKind, ref_id: str | None) -> None:
        telemetry = self._telemetry.get(event.car_id)
        if telemetry is not None and any(
            stop.kind == kind and stop.ref_id == ref_id and stop.floor == event.floor
            for stop in telemetry.stops
        ):
            # ServiceEvent may arrive before the versioned stop-list telemetry.
            # Wait for that update instead of proposing a route that resurrects it.
            self._minimum_version[event.car_id] = max(
                self._minimum_version.get(event.car_id, 0), telemetry.route_version + 1
            )

    def _matching(self, command_id: str, car_id: str) -> Command | None:
        command = self._commands.get(command_id)
        if (
            command is None
            or command.proposal.car_id != car_id
            or self._inflight.get(car_id) != command_id
        ):
            return None
        return command

    def _reply(self, msg: RouteReply, now: float, out: list[Message]) -> None:
        command = self._matching(msg.command_id, msg.car_id)
        if command is None:
            return
        if msg.accepted:
            if msg.committed_version != command.proposal.expected_route_version + 1:
                return
            self._accept(command, msg.committed_version, now, out, accepted_at=msg.t_sim)
        else:
            self._minimum_version[msg.car_id] = msg.committed_version
            self._reject(command, suppress=True)

    def _report(self, msg: RouteVersionReport, now: float, out: list[Message]) -> None:
        command = self._matching(msg.command_id, msg.car_id)
        if command is None or command.state != CommandState.RECONCILING:
            return
        self._minimum_version[msg.car_id] = msg.route_version
        if msg.seen and msg.accepted is True:
            if msg.route_version < command.proposal.expected_route_version + 1:
                return
            self._accept(command, msg.route_version, now, out)
        elif not msg.seen or msg.accepted is False:
            self._reject(command, suppress=msg.seen)

    def _accept(
        self,
        command: Command,
        version: int,
        now: float,
        out: list[Message],
        *,
        accepted_at: float | None = None,
    ) -> None:
        proposal = command.proposal
        command.state = CommandState.ACCEPTED
        self._inflight.pop(proposal.car_id, None)
        # An ACK establishes ownership, not the car's current physical state.
        # Route construction waits for authoritative telemetry at this version.
        self._minimum_version[proposal.car_id] = max(
            self._minimum_version.get(proposal.car_id, 0), version
        )
        references = self._references.setdefault(proposal.car_id, {})
        telemetry = self._telemetry.get(proposal.car_id)
        if accepted_at is not None and telemetry is not None:
            estimates = route_etas(
                telemetry,
                proposal.ordered_stops,
                accepted_at,
                ready_at=self._ready_at.get(proposal.car_id),
            )
            for stop, eta in estimates.items():
                references.setdefault(stop, eta)
        for request_id in command.calls:
            call = self._calls[request_id]
            if call.status == CallStatus.RESERVED and call.owner == proposal.car_id:
                call.status = CallStatus.ASSIGNED
        if telemetry is not None and telemetry.route_version >= version:
            # An accepted command can already have been removed by a later fault
            # while its ACK was lost. Reconciliation resolves the reservation;
            # the newer authoritative stop list then releases the dropped pickup.
            self._release_dropped_pickups(telemetry)
        if proposal.mission_id is not None:
            mission = self._missions[proposal.mission_id]
            mission.status = MissionStatus.RESERVED
            mission.owner = proposal.car_id
            for call in self._calls.values():
                if call.owner == proposal.car_id and call.status == CallStatus.ASSIGNED:
                    self._release(call)
            out.append(
                MissionStatusUpdate(
                    t_sim=now,
                    mission_id=proposal.mission_id,
                    status=MissionStatus.RESERVED,
                    car_id=proposal.car_id,
                    candidates=command.candidates,
                )
            )

    def _reject(self, command: Command, *, suppress: bool) -> None:
        proposal = command.proposal
        command.state = CommandState.REJECTED
        self._inflight.pop(proposal.car_id, None)
        for request_id in command.calls:
            call = self._calls[request_id]
            if call.status == CallStatus.RESERVED and call.owner == proposal.car_id:
                self._release(call)
                if suppress:
                    call.rejected[proposal.car_id] = command.fingerprint
        if proposal.mission_id is not None:
            mission = self._missions[proposal.mission_id]
            mission.status = MissionStatus.APPROVED
            mission.owner = None
            if suppress:
                mission.rejected[proposal.car_id] = command.fingerprint

    @staticmethod
    def _release(call: HallCall) -> None:
        call.status = CallStatus.PENDING
        call.owner = None

    def _timer(self, msg: Timer, now: float, out: list[Message]) -> None:
        if msg.target != self.name or msg.key != "route-timeout":
            return
        command = self._commands.get(msg.ref_id or "")
        if command is None or command.state not in (
            CommandState.WAITING,
            CommandState.RECONCILING,
        ):
            return
        if now < command.proposal.expiry_ts:
            return
        command.state = CommandState.RECONCILING
        out.extend(
            [
                RouteVersionQuery(
                    t_sim=now,
                    command_id=command.proposal.command_id,
                    car_id=command.proposal.car_id,
                ),
                Timer(
                    t_sim=now,
                    deliver_at=now + ACK_TIMEOUT_S,
                    target=self.name,
                    key="route-timeout",
                    ref_id=command.proposal.command_id,
                ),
            ]
        )

    def _usable(self, car_id: str) -> bool:
        telemetry = self._telemetry.get(car_id)
        return (
            telemetry is not None
            and car_id not in self._inflight
            and telemetry.route_version >= self._minimum_version.get(car_id, 0)
        )

    def _mission_busy(self) -> set[str]:
        return {
            mission.owner
            for mission in self._missions.values()
            if mission.owner is not None and mission.status == MissionStatus.RESERVED
        }

    def _plan(self, now: float, out: list[Message]) -> None:
        self._plan_missions(now, out)
        if self._fire:
            return
        busy = self._mission_busy()
        pending = sorted(
            (c for c in self._calls.values() if c.status == CallStatus.PENDING),
            key=lambda c: (now - c.created_at < AGE_LIMIT_S, c.created_at, c.request_id),
        )
        created = {c.request_id: c.created_at for c in self._calls.values()}
        for call in pending:
            pickup = Stop(
                floor=call.floor,
                kind=StopKind.HALL_PICKUP,
                ref_id=call.request_id,
                direction=call.direction,
            )
            choices: list[tuple[tuple[float, int, int], str, tuple[Stop, ...]]] = []
            for car_id in PASSENGER_CARS:
                if not self._usable(car_id) or car_id in busy:
                    continue
                telemetry = self._telemetry[car_id]
                if ordinary_exclusion(telemetry, now) is not None:
                    continue
                if call.rejected.get(car_id) == car_fingerprint(telemetry):
                    continue
                route = hall_route(
                    telemetry,
                    pickup,
                    now,
                    self._references.get(car_id, {}),
                    self.policy,
                    created,
                    self.algorithm,
                    ready_at=self._ready_at.get(car_id),
                )
                if route is None:
                    continue
                eta = route_etas(telemetry, route, now, ready_at=self._ready_at.get(car_id))[pickup]
                rank = (eta, len(telemetry.stops), CAR_ORDER.index(car_id))
                if self.algorithm == DispatchAlgorithm.FIRST_IDLE:
                    rank = (float(CAR_ORDER.index(car_id)), len(telemetry.stops), 0)
                choices.append((rank, car_id, route))
            if choices:
                _, car_id, route = min(choices)
                call.owner = car_id
                call.status = CallStatus.RESERVED
                self._propose(car_id, route, (call.request_id,), now, out)

    def _plan_missions(self, now: float, out: list[Message]) -> None:
        for mission in sorted(
            self._missions.values(),
            key=lambda item: item.approval.mission_type != MissionType.MEDICAL,
        ):
            if mission.status not in (MissionStatus.APPROVED, MissionStatus.UNAVAILABLE):
                continue
            if mission.owner is not None:
                continue
            blocked = self._mission_busy() | set(self._inflight)
            blocked.update(c for c in CAR_ORDER if not self._usable(c))
            candidates = mission_candidates(
                mission.approval, self._telemetry, now, blocked, ready_at=self._ready_at
            )
            choices = []
            for candidate in candidates:
                if candidate.excluded is not None or candidate.eta_s is None:
                    continue
                telemetry = self._telemetry[candidate.car_id]
                if mission.rejected.get(candidate.car_id) == car_fingerprint(telemetry):
                    continue
                freight_priority = (
                    mission.approval.mission_type == MissionType.FREIGHT
                    and candidate.car_id != FREIGHT_FIRST_CHOICE
                )
                choices.append(
                    (
                        freight_priority,
                        candidate.eta_s,
                        len(telemetry.stops),
                        CAR_ORDER.index(candidate.car_id),
                        candidate.car_id,
                    )
                )
            if not choices:
                if mission.status != MissionStatus.UNAVAILABLE:
                    mission.status = MissionStatus.UNAVAILABLE
                    out.append(
                        MissionStatusUpdate(
                            t_sim=now,
                            mission_id=mission.approval.mission_id,
                            status=MissionStatus.UNAVAILABLE,
                            candidates=candidates,
                        )
                    )
                continue
            car_id = min(choices)[-1]
            mission.owner = car_id
            self._propose(
                car_id,
                mission_route(mission.approval, self._telemetry[car_id]),
                (),
                now,
                out,
                mission=mission,
                candidates=candidates,
            )

    def _propose(
        self,
        car_id: str,
        route: tuple[Stop, ...],
        calls: tuple[str, ...],
        now: float,
        out: list[Message],
        *,
        mission: Mission | None = None,
        candidates=(),
    ) -> None:
        telemetry = self._telemetry[car_id]
        proposal = RouteProposal(
            t_sim=now,
            command_id=self._command_ids.next(),
            car_id=car_id,
            expected_route_version=telemetry.route_version,
            ordered_stops=route,
            expiry_ts=now + ACK_TIMEOUT_S,
            mission_id=mission.approval.mission_id if mission else None,
            exclusive=mission is not None,
        )
        self._commands[proposal.command_id] = Command(
            proposal, calls, car_fingerprint(telemetry), candidates
        )
        self._inflight[car_id] = proposal.command_id
        out.extend(
            [
                proposal,
                Timer(
                    t_sim=now,
                    deliver_at=proposal.expiry_ts,
                    target=self.name,
                    key="route-timeout",
                    ref_id=proposal.command_id,
                ),
            ]
        )
