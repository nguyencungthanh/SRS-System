"""FakeDispatch: assigns the nearest idle passenger car (framework F6).

What it does:
    * Hall Calls: validate floor and direction, deduplicate (floor, direction), answer with
      HallCallAck (D-18). PENDING calls are retried on every incoming message (D-12).
    * A car is idle when its telemetry is fresh, its mode is AUTO, it has no stops and it
      owns no call, mission or unanswered proposal. Nearest idle car wins; ties by CAR_ORDER.
    * ACK -> ASSIGNED, NACK -> back to PENDING, HALL_CALL_SERVED -> removed. An ASSIGNED
      call that disappears from its car's telemetry goes back to PENDING (D-55).
    * Missions: one Candidate per car (ETA = distance x SECONDS_PER_FLOOR, or an exclusion
      reason), lowest ETA wins, exclusive pickup + drop-off route. RESERVED after ACK;
      UNAVAILABLE (sent once) while no car qualifies (D-28, D-29).
    * FireAlarm stops new Hall Call assignments (D-24). PolicyActivated replaces the policy.

What it skips: ACK timeout and reconciliation, insertion into busy cars, grouping (R, W),
DELAY_CAP, AGE_LIMIT, medical drain ETAs, baseline algorithms.
"""

from dataclasses import dataclass, field

from contracts.config import (
    ACK_TIMEOUT_S,
    CAR_ORDER,
    DEFAULT_POLICY,
    MAX_FLOOR,
    MIN_FLOOR,
    PASSENGER_CARS,
    SECONDS_PER_FLOOR,
    TELEMETRY_STALE_S,
    Policy,
)
from contracts.enums import (
    CabinMode,
    CallStatus,
    Direction,
    DispatchAlgorithm,
    ExclusionReason,
    MissionStatus,
    RejectReason,
    ServiceEventType,
    StopKind,
)
from contracts.ids import PREFIX_COMMAND, PREFIX_HALL_CALL, IdGen
from contracts.messages import (
    CabinTelemetry,
    Candidate,
    FireAlarm,
    HallCallAck,
    HallCallPressed,
    Message,
    MissionApproval,
    MissionStatusUpdate,
    PolicyActivated,
    RouteProposal,
    RouteReply,
    ServiceEvent,
    Stop,
)
from contracts.routing import DISPATCH
from contracts.ui import CallView, DispatchSnapshot

EXCLUDED_BY_MODE = {
    CabinMode.MAINTENANCE: ExclusionReason.MAINTENANCE,
    CabinMode.FAULT_HOLD: ExclusionReason.FAULT_HOLD,
    CabinMode.FIRE: ExclusionReason.FIRE,
    CabinMode.OVERLOAD_HOLD: ExclusionReason.OVERLOAD,
}


@dataclass
class _Call:
    request_id: str
    floor: int
    direction: Direction
    created_at: float
    status: CallStatus = CallStatus.PENDING
    owner: str | None = None
    committed_version: int | None = None


@dataclass
class _Mission:
    approval: MissionApproval
    status: MissionStatus = MissionStatus.APPROVED
    car_id: str | None = None
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)


class FakeDispatch:
    name = DISPATCH

    def __init__(
        self,
        policy: Policy = DEFAULT_POLICY,
        algorithm: DispatchAlgorithm = DispatchAlgorithm.COMMITMENT,
    ) -> None:
        self.policy = policy
        self.algorithm = algorithm  # ignored by the fake
        self.fire = False
        self.calls: dict[str, _Call] = {}
        self.active: dict[tuple[int, Direction], str] = {}
        self.missions: dict[str, _Mission] = {}
        self.telemetry: dict[str, CabinTelemetry] = {}
        self.proposals: dict[str, tuple[str, str, str]] = {}  # command_id -> (kind, ref, car)
        # ref -> (car, route_version) of the last NACK: do not offer the same thing to the
        # same car again until its telemetry shows a new route_version (avoids a livelock).
        self.refused: dict[str, tuple[str, int]] = {}
        self._hall_ids = IdGen(PREFIX_HALL_CALL)
        self._command_ids = IdGen(PREFIX_COMMAND)

    # --- Module interface ------------------------------------------------------------

    def handle(self, msg: Message, now: float) -> list[Message]:
        out: list[Message] = []
        match msg:
            case HallCallPressed():
                self._on_hall_call(msg, now, out)
            case CabinTelemetry():
                self.telemetry[msg.car_id] = msg
                self._release_dropped_calls(msg)
            case RouteReply():
                self._on_reply(msg, now, out)
            case ServiceEvent():
                self._on_service_event(msg)
            case MissionApproval():
                self.missions[msg.mission_id] = _Mission(approval=msg)
            case PolicyActivated():
                self.policy = msg.policy
            case FireAlarm(active=active):
                self.fire = active
            case _:
                pass
        self._assign(now, out)
        return out

    def snapshot(self) -> DispatchSnapshot:
        calls: list[CallView] = [
            {
                "request_id": c.request_id,
                "floor": c.floor,
                "direction": c.direction.value,
                "status": c.status.value,
                "owner": c.owner,
                "created_at": c.created_at,
            }
            for c in sorted(self.calls.values(), key=lambda c: (c.created_at, c.request_id))
        ]
        return {
            "calls": calls,
            "policy": {"version": self.policy.version, "R": self.policy.R, "W": self.policy.W},
        }

    # --- handlers --------------------------------------------------------------------

    def _on_hall_call(self, msg: HallCallPressed, now: float, out: list[Message]) -> None:
        reason = None
        if msg.direction == Direction.IDLE:
            reason = RejectReason.INVALID_DIRECTION
        elif not MIN_FLOOR <= msg.floor <= MAX_FLOOR:
            reason = RejectReason.FLOOR_OUT_OF_RANGE
        elif (msg.floor, msg.direction) in (
            (MIN_FLOOR, Direction.DOWN),
            (MAX_FLOOR, Direction.UP),
        ):
            reason = RejectReason.TERMINAL_DIRECTION
        if reason is not None:
            out.append(
                HallCallAck(
                    t_sim=now,
                    request_id=None,
                    floor=msg.floor,
                    direction=msg.direction,
                    accepted=False,
                    reason=reason,
                )
            )
            return
        existing = self.active.get((msg.floor, msg.direction))
        if existing is not None:
            call = self.calls[existing]
            duplicate = True
        else:
            call = _Call(self._hall_ids.next(), msg.floor, msg.direction, created_at=now)
            self.calls[call.request_id] = call
            self.active[(call.floor, call.direction)] = call.request_id
            duplicate = False
        out.append(
            HallCallAck(
                t_sim=now,
                request_id=call.request_id,
                floor=call.floor,
                direction=call.direction,
                accepted=True,
                duplicate=duplicate,
                status=call.status,
            )
        )

    def _on_reply(self, msg: RouteReply, now: float, out: list[Message]) -> None:
        pending = self.proposals.pop(msg.command_id, None)
        if pending is None:
            return  # duplicate or unknown reply
        kind, ref, car_id = pending
        if not msg.accepted:
            self.refused[ref] = (car_id, msg.committed_version)
        if kind == "call":
            call = self.calls.get(ref)
            if call is None:
                return
            if msg.accepted:
                call.status = CallStatus.ASSIGNED
                call.committed_version = msg.committed_version
            else:
                call.status, call.owner = CallStatus.PENDING, None
            return
        mission = self.missions.get(ref)
        if mission is None:
            return
        if msg.accepted:
            mission.status = MissionStatus.RESERVED
            out.append(
                MissionStatusUpdate(
                    t_sim=now,
                    mission_id=ref,
                    status=MissionStatus.RESERVED,
                    car_id=car_id,
                    candidates=mission.candidates,
                )
            )
        else:
            mission.car_id = None

    def _release_dropped_calls(self, tel: CabinTelemetry) -> None:
        listed = {s.ref_id for s in tel.stops if s.kind == StopKind.HALL_PICKUP}
        for call in self.calls.values():
            if (
                call.owner == tel.car_id
                and call.status == CallStatus.ASSIGNED
                and call.committed_version is not None
                and tel.route_version >= call.committed_version
                and call.request_id not in listed
            ):
                call.status, call.owner, call.committed_version = CallStatus.PENDING, None, None

    def _on_service_event(self, msg: ServiceEvent) -> None:
        if msg.event == ServiceEventType.HALL_CALL_SERVED and msg.ref_id in self.calls:
            call = self.calls.pop(msg.ref_id)
            self.active.pop((call.floor, call.direction), None)
        elif msg.event == ServiceEventType.MISSION_DELIVERY_DONE and msg.ref_id:
            self.missions.pop(msg.ref_id, None)

    # --- assignment ------------------------------------------------------------------

    def _busy_cars(self) -> set[str]:
        busy = {car for _, _, car in self.proposals.values()}
        busy |= {c.owner for c in self.calls.values() if c.owner is not None}
        busy |= {m.car_id for m in self.missions.values() if m.car_id is not None}
        return busy

    def _exclusion(self, car_id: str, now: float, busy: set[str]) -> ExclusionReason | None:
        tel = self.telemetry.get(car_id)
        if tel is None or now - tel.t_sim > TELEMETRY_STALE_S:
            return ExclusionReason.STALE_TELEMETRY
        if tel.mode in EXCLUDED_BY_MODE:
            return EXCLUDED_BY_MODE[tel.mode]
        if not tel.sensors_valid:
            return ExclusionReason.SENSORS_INVALID
        if car_id in busy or tel.stops or tel.mode != CabinMode.AUTO:
            return ExclusionReason.MISSION_BUSY
        return None

    def _refused(self, ref: str, car_id: str) -> bool:
        tel = self.telemetry.get(car_id)
        return tel is not None and self.refused.get(ref) == (car_id, tel.route_version)

    def _eta(self, car_id: str, floor: int) -> float:
        return abs(self.telemetry[car_id].position - floor) * SECONDS_PER_FLOOR

    def _assign(self, now: float, out: list[Message]) -> None:
        busy = self._busy_cars()
        for mission_id in sorted(self.missions):
            mission = self.missions[mission_id]
            if mission.car_id is None:
                self._assign_mission(mission, now, busy, out)
        if self.fire:
            return
        pending = [c for c in self.calls.values() if c.status == CallStatus.PENDING]
        for call in sorted(pending, key=lambda c: (c.created_at, c.request_id)):
            idle = [
                car
                for car in PASSENGER_CARS
                if self._exclusion(car, now, busy) is None
                and not self._refused(call.request_id, car)
            ]
            if not idle:
                break
            car = min(idle, key=lambda c: (self._eta(c, call.floor), CAR_ORDER.index(c)))
            stop = Stop(
                floor=call.floor,
                kind=StopKind.HALL_PICKUP,
                ref_id=call.request_id,
                direction=call.direction,
            )
            command_id = self._propose(car, (stop,), now, out)
            self.proposals[command_id] = ("call", call.request_id, car)
            call.status, call.owner = CallStatus.RESERVED, car
            busy.add(car)

    def _assign_mission(
        self, mission: _Mission, now: float, busy: set[str], out: list[Message]
    ) -> None:
        approval = mission.approval
        candidates: list[Candidate] = []
        for car in CAR_ORDER:
            if car not in approval.allowed_cars:
                reason = ExclusionReason.NOT_CAPABLE
            else:
                reason = self._exclusion(car, now, busy)
            if reason is None:
                candidates.append(Candidate(car_id=car, eta_s=self._eta(car, approval.origin)))
            else:
                candidates.append(Candidate(car_id=car, excluded=reason))
        mission.candidates = tuple(candidates)
        eligible = [c for c in candidates if c.eta_s is not None]
        if eligible and all(self._refused(approval.mission_id, c.car_id) for c in eligible):
            return  # wait for a refusing car to change before offering again
        eligible = [c for c in eligible if not self._refused(approval.mission_id, c.car_id)]
        if not eligible:
            if mission.status != MissionStatus.UNAVAILABLE:
                mission.status = MissionStatus.UNAVAILABLE
                out.append(
                    MissionStatusUpdate(
                        t_sim=now,
                        mission_id=approval.mission_id,
                        status=MissionStatus.UNAVAILABLE,
                        candidates=mission.candidates,
                    )
                )
            return
        best = min(eligible, key=lambda c: (c.eta_s, CAR_ORDER.index(c.car_id)))
        stops = (
            Stop(floor=approval.origin, kind=StopKind.MISSION_PICKUP, ref_id=approval.mission_id),
            Stop(
                floor=approval.destination,
                kind=StopKind.MISSION_DROPOFF,
                ref_id=approval.mission_id,
            ),
        )
        command_id = self._propose(
            best.car_id, stops, now, out, mission_id=approval.mission_id, exclusive=True
        )
        self.proposals[command_id] = ("mission", approval.mission_id, best.car_id)
        mission.car_id = best.car_id
        busy.add(best.car_id)

    def _propose(
        self,
        car_id: str,
        stops: tuple[Stop, ...],
        now: float,
        out: list[Message],
        mission_id: str | None = None,
        exclusive: bool = False,
    ) -> str:
        command_id = self._command_ids.next()
        out.append(
            RouteProposal(
                t_sim=now,
                command_id=command_id,
                car_id=car_id,
                expected_route_version=self.telemetry[car_id].route_version,
                ordered_stops=stops,
                expiry_ts=now + ACK_TIMEOUT_S,
                mission_id=mission_id,
                exclusive=exclusive,
            )
        )
        return command_id


def create(
    policy: Policy = DEFAULT_POLICY,
    algorithm: DispatchAlgorithm = DispatchAlgorithm.COMMITMENT,
) -> FakeDispatch:
    return FakeDispatch(policy, algorithm)
