"""Virtual passengers (D-56): press Hall Calls, board, choose destinations, exit.

A module like any other (``handle`` / ``snapshot``), addressed ``"passengers"``. It only
reacts to messages, so the same passengers behave identically with real or fake modules.

    PassengerIntent at t    -> HallCallPressed(origin, direction)
    HallCallAck (accepted)  -> remember the request_id of everyone waiting at that floor/direction
    HALL_CALL_SERVED(R, X)  -> car X will open its doors for request R
    DOOR_OPENED(X, F)       -> passengers whose destination is F exit (PassengerExit); then
                               the people waiting for R board (PassengerBoard +
                               CabinDestinationPressed) while the load stays <= MAX_LOAD_KG;
                               anyone left behind presses again after DWELL_S (SEN-04)
"""

from contracts.config import DWELL_S, MAX_FLOOR, MAX_LOAD_KG, MIN_FLOOR
from contracts.enums import Direction, ServiceEventType
from contracts.messages import (
    TIMER_START,
    CabinDestinationPressed,
    CabinInit,
    HallCallAck,
    HallCallPressed,
    Message,
    PassengerBoard,
    PassengerExit,
    ServiceEvent,
    Timer,
)
from contracts.routing import PASSENGERS
from simulation.scenario import PassengerIntent

KEY_PRESS = "press"


class PassengerAgent:
    name = PASSENGERS

    def __init__(
        self, intents: tuple[PassengerIntent, ...], fleet: tuple[CabinInit, ...] = ()
    ) -> None:
        self.intents = {p.passenger_id: p for p in intents}
        self.request_of: dict[str, str] = {}  # waiting passenger -> request_id
        self.unacked: dict[tuple[int, Direction], list[str]] = {}  # pressed, no ack yet
        self.waiting: list[str] = []  # pressed, not on board, in press order
        self.served: dict[str, tuple[str, int]] = {}  # request_id -> (car, floor)
        self.onboard: dict[str, dict[str, tuple[int, float]]] = {}  # car -> pid -> (dest, kg)
        self.gave_up: list[str] = []  # invalid trips
        self.arrived: list[str] = []
        for cabin in fleet:
            for p in cabin.passengers:
                if p.destination is not None:
                    self.onboard.setdefault(cabin.car_id, {})[p.passenger_id] = (
                        p.destination,
                        p.weight_kg,
                    )

    # --- Module interface ------------------------------------------------------------

    def handle(self, msg: Message, now: float) -> list[Message]:
        out: list[Message] = []
        match msg:
            case Timer(key=key, ref_id=pid) if key == TIMER_START:
                for intent in self.intents.values():
                    out.append(
                        Timer(
                            t_sim=now,
                            deliver_at=max(intent.t, now),
                            target=self.name,
                            key=KEY_PRESS,
                            ref_id=intent.passenger_id,
                        )
                    )
            case Timer(key=key, ref_id=pid) if key == KEY_PRESS and pid in self.intents:
                self._press(self.intents[pid], now, out)
            case HallCallAck():
                self._on_ack(msg)
            case ServiceEvent(event=ServiceEventType.HALL_CALL_SERVED, ref_id=ref) if ref:
                self.served[ref] = (msg.car_id, msg.floor)
            case ServiceEvent(event=ServiceEventType.DOOR_OPENED):
                self._on_door_opened(msg.car_id, msg.floor, now, out)
            case _:
                pass
        return out

    def snapshot(self) -> dict:
        return {
            "waiting": len(self.waiting),
            "onboard": sum(len(v) for v in self.onboard.values()),
            "arrived": len(self.arrived),
            "gave_up": len(self.gave_up),
        }

    # --- behaviour -------------------------------------------------------------------

    def _press(self, intent: PassengerIntent, now: float, out: list[Message]) -> None:
        valid = (
            MIN_FLOOR <= intent.origin <= MAX_FLOOR
            and MIN_FLOOR <= intent.destination <= MAX_FLOOR
            and intent.origin != intent.destination
        )
        if not valid:
            self.gave_up.append(intent.passenger_id)
            return
        pid = intent.passenger_id
        if pid not in self.waiting:
            self.waiting.append(pid)
        self.request_of.pop(pid, None)
        self.unacked.setdefault((intent.origin, intent.direction), []).append(pid)
        out.append(HallCallPressed(t_sim=now, floor=intent.origin, direction=intent.direction))

    def _on_ack(self, ack: HallCallAck) -> None:
        pids = self.unacked.pop((ack.floor, ack.direction), [])
        for pid in pids:
            if ack.accepted and ack.request_id is not None:
                self.request_of[pid] = ack.request_id
            else:
                self.waiting.remove(pid)
                self.gave_up.append(pid)

    def _on_door_opened(self, car_id: str, floor: int, now: float, out: list[Message]) -> None:
        riders = self.onboard.setdefault(car_id, {})
        for pid, (dest, _) in list(riders.items()):
            if dest == floor:
                del riders[pid]
                self.arrived.append(pid)
                out.append(PassengerExit(t_sim=now, car_id=car_id, passenger_id=pid))

        for request_id, (car, at) in list(self.served.items()):
            if car != car_id or at != floor:
                continue
            del self.served[request_id]
            load = sum(kg for _, kg in riders.values())
            for pid in [p for p in self.waiting if self.request_of.get(p) == request_id]:
                intent = self.intents[pid]
                self.request_of.pop(pid)
                if load + intent.weight_kg > MAX_LOAD_KG:
                    out.append(
                        Timer(
                            t_sim=now,
                            deliver_at=now + DWELL_S,
                            target=self.name,
                            key=KEY_PRESS,
                            ref_id=pid,
                        )
                    )
                    continue
                self.waiting.remove(pid)
                riders[pid] = (intent.destination, intent.weight_kg)
                load += intent.weight_kg
                out.append(
                    PassengerBoard(
                        t_sim=now, car_id=car_id, passenger_id=pid, weight_kg=intent.weight_kg
                    )
                )
                out.append(
                    CabinDestinationPressed(
                        t_sim=now, car_id=car_id, floor=intent.destination, passenger_id=pid
                    )
                )
