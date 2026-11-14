"""Deterministic message bus: the core of the simulation engine (framework F8).

The bus owns simulated time. It keeps one priority queue ordered by
``(deliver_at, priority, sequence)`` (SRS 7.7, D-05), delivers each message to the modules
that ``contracts.routing`` names, and queues whatever they return. Every delivered message
is logged, including messages without recipients, and so are engine events such as a
dropped message (D-57). ``Engine`` (simulation/engine.py) adds states, reset and replay.
"""

import dataclasses
import heapq
from collections.abc import Callable, Iterable
from typing import Any

from contracts.codec import to_dict
from contracts.config import CARS, DEFAULT_DELIVERY_DELAY_S
from contracts.enums import EventType
from contracts.ids import PREFIX_MESSAGE, IdGen
from contracts.interfaces import Module
from contracts.messages import TIMER_START, Message, Timer
from contracts.routing import priority_of, recipients
from simulation.scenario import DELAY, DROP, NetworkRule


class ContractViolation(RuntimeError):
    """A module or caller broke a rule of the message contract."""


# More deliveries than this at one simulated instant means modules are ping-ponging
# (e.g. propose -> NACK -> propose the same thing again) and time can never advance.
MAX_DELIVERIES_PER_INSTANT = 10_000

LogEntry = dict[str, Any]


class Bus:
    def __init__(
        self,
        modules: Iterable[Module],
        cars: tuple[str, ...] = CARS,
        delivery_delay_s: float = DEFAULT_DELIVERY_DELAY_S,
        network: tuple[NetworkRule, ...] = (),
        on_record: Callable[[LogEntry], None] | None = None,
    ) -> None:
        self.modules: dict[str, Module] = {}
        for module in modules:
            if module.name in self.modules:
                raise ValueError(f"two modules named {module.name!r}")
            self.modules[module.name] = module
        self.cars = cars
        self.delivery_delay_s = delivery_delay_s
        self.network = network
        self.on_record = on_record
        self.now = 0.0
        self.delivered: list[Message] = []
        self.log: list[LogEntry] = []
        self._queue: list[tuple[float, int, int, Message]] = []
        self._seq = 0
        self._ids = IdGen(PREFIX_MESSAGE, width=6)
        self._instant = (-1.0, 0)  # (time, deliveries at that time)
        self._rule_hits = [0] * len(network)

    # --- input -----------------------------------------------------------------------

    def start(self) -> None:
        """Send ``Timer(key=TIMER_START)`` to every module at the current time (D-08)."""
        for name in self.modules:
            self.inject(Timer(t_sim=self.now, target=name, key=TIMER_START))

    def inject(self, msg: Message) -> Message:
        """Queue a message from outside the modules (scenario, UI, test). Returns it stamped."""
        if msg.t_sim < self.now:
            raise ContractViolation(
                f"cannot inject {type(msg).__name__} at t={msg.t_sim} < now={self.now}"
            )
        return self._enqueue(msg)

    def record_event(self, event: EventType, **fields: Any) -> None:
        """Log an engine event (not a module message) at the current time."""
        self._record({"event": event.value, "t_sim": self.now, **fields})

    # --- running ---------------------------------------------------------------------

    def step(self) -> Message | None:
        """Deliver the next message. Returns it, or None if the queue is empty."""
        if not self._queue:
            return None
        deliver_at, _, _, msg = heapq.heappop(self._queue)
        self.now = deliver_at
        count = self._instant[1] + 1 if self._instant[0] == deliver_at else 1
        self._instant = (deliver_at, count)
        if count > MAX_DELIVERIES_PER_INSTANT:
            raise ContractViolation(
                f"livelock: over {MAX_DELIVERIES_PER_INSTANT} messages at t={deliver_at}; "
                f"last was {type(msg).__name__}"
            )
        self.delivered.append(msg)
        self._record(to_dict(msg))
        for address in recipients(msg, self.cars):
            module = self.modules.get(address)
            if module is None:
                continue
            for out in module.handle(msg, self.now):
                self._check_output(address, out)
                self._apply_network_and_enqueue(out)
        return msg

    def run_until(self, t_end: float) -> None:
        """Deliver every message due at or before ``t_end``, then set ``now = t_end``."""
        while self._queue and self._queue[0][0] <= t_end:
            self.step()
        self.now = max(self.now, t_end)

    def pending(self) -> int:
        return len(self._queue)

    # --- reading the log -------------------------------------------------------------

    def select(self, cls: type[Message], **where: Any) -> list[Message]:
        """Delivered messages of type ``cls`` whose fields equal ``where``."""
        return [
            m
            for m in self.delivered
            if type(m) is cls and all(getattr(m, k) == v for k, v in where.items())
        ]

    # --- internals -------------------------------------------------------------------

    def _record(self, entry: LogEntry) -> None:
        self.log.append(entry)
        if self.on_record is not None:
            self.on_record(entry)

    def _check_output(self, address: str, out: Message) -> None:
        name = type(out).__name__
        if out.msg_id:
            raise ContractViolation(f"{address} returned {name} with msg_id set; leave it empty")
        if out.t_sim != self.now:
            raise ContractViolation(
                f"{address} returned {name} with t_sim={out.t_sim}; it must be now={self.now}"
            )

    def _apply_network_and_enqueue(self, msg: Message) -> None:
        if not isinstance(msg, Timer):
            for i, rule in enumerate(self.network):
                if type(msg).__name__ != rule.msg_type:
                    continue
                if rule.car_id is not None and getattr(msg, "car_id", None) != rule.car_id:
                    continue
                self._rule_hits[i] += 1
                if self._rule_hits[i] != rule.nth:
                    continue
                if rule.action == DROP:
                    stamped = dataclasses.replace(
                        msg, msg_id=self._ids.next(), deliver_at=msg.t_sim
                    )
                    self.record_event(EventType.MESSAGE_DROPPED, message=to_dict(stamped))
                    return
                if rule.action == DELAY:
                    base = msg.deliver_at if msg.deliver_at is not None else msg.t_sim
                    msg = dataclasses.replace(msg, deliver_at=base + rule.delay_s)
        self._enqueue(msg)

    def _enqueue(self, msg: Message) -> Message:
        deliver_at = msg.deliver_at
        if deliver_at is None:
            deliver_at = msg.t_sim + self.delivery_delay_s
        if deliver_at < self.now:
            raise ContractViolation(
                f"{type(msg).__name__} deliver_at={deliver_at} is before now={self.now}"
            )
        msg_id = msg.msg_id or self._ids.next()
        msg = dataclasses.replace(msg, msg_id=msg_id, deliver_at=deliver_at)
        heapq.heappush(self._queue, (deliver_at, int(priority_of(msg)), self._seq, msg))
        self._seq += 1
        return msg
