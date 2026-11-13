"""Tests for simulation/bus.py: ordering, stamping and contract enforcement (SIM-04, D-05)."""

import pytest

from contracts.enums import Direction, FaultKind
from contracts.messages import (
    TIMER_START,
    FaultInjected,
    HallCallAck,
    HallCallPressed,
    Message,
    RouteProposal,
    Timer,
)
from simulation.bus import Bus, ContractViolation


class Recorder:
    """A module that records what it receives and can be told what to return."""

    def __init__(self, name: str, reply=None) -> None:
        self.name = name
        self.received: list[tuple[float, Message]] = []
        self.reply = reply  # callable(msg, now) -> list[Message]

    def handle(self, msg, now):
        self.received.append((now, msg))
        return self.reply(msg, now) if self.reply else []

    def snapshot(self):
        return {}


def proposal(t: float) -> RouteProposal:
    return RouteProposal(
        t_sim=t,
        command_id="RC-0001",
        car_id="P1",
        expected_route_version=0,
        ordered_stops=(),
        expiry_ts=t + 2.0,
    )


def test_SIM_04_same_time_runs_safety_physical_control_input():
    bus = Bus([])
    call = HallCallPressed(t_sim=5.0, floor=27, direction=Direction.DOWN)
    route = proposal(5.0)
    timer = Timer(t_sim=5.0, target="cabin:P1", key="door")
    fault = FaultInjected(t_sim=5.0, car_id="P1", kind=FaultKind.OBSTRUCTION)
    for msg in (call, route, timer, fault):
        bus.inject(msg)
    bus.run_until(5.0)
    assert [type(m) for m in bus.delivered] == [
        FaultInjected,
        Timer,
        RouteProposal,
        HallCallPressed,
    ]


def test_SIM_04_equal_priority_keeps_insertion_order():
    bus = Bus([])
    first = HallCallPressed(t_sim=1.0, floor=10, direction=Direction.UP)
    second = HallCallPressed(t_sim=1.0, floor=20, direction=Direction.UP)
    bus.inject(first)
    bus.inject(second)
    bus.run_until(1.0)
    assert [m.floor for m in bus.delivered] == [10, 20]


def test_SIM_04_earlier_time_first_regardless_of_priority():
    bus = Bus([])
    bus.inject(FaultInjected(t_sim=2.0, car_id="P1", kind=FaultKind.OBSTRUCTION))
    bus.inject(HallCallPressed(t_sim=1.0, floor=10, direction=Direction.UP))
    bus.run_until(3.0)
    assert [type(m) for m in bus.delivered] == [HallCallPressed, FaultInjected]


def test_D_15_bus_stamps_msg_id_and_deliver_at():
    bus = Bus([])
    stamped = bus.inject(HallCallPressed(t_sim=0.0, floor=10, direction=Direction.UP))
    assert stamped.msg_id == "MSG-000001"
    assert stamped.deliver_at == 0.0
    assert bus.inject(Timer(t_sim=0.0, deliver_at=3.0, target="x", key="k")).msg_id == (
        "MSG-000002"
    )


def test_D_08_start_sends_start_timer_to_every_module():
    a, b = Recorder("dispatch"), Recorder("cc")
    bus = Bus([a, b])
    bus.start()
    bus.run_until(0.0)
    for module in (a, b):
        assert len(module.received) == 1
        now, msg = module.received[0]
        assert now == 0.0 and isinstance(msg, Timer) and msg.key == TIMER_START


def test_F8_module_output_is_routed_and_delivered():
    def answer(msg, now):
        if isinstance(msg, HallCallPressed):
            return [
                HallCallAck(
                    t_sim=now,
                    request_id="HC-0001",
                    floor=msg.floor,
                    direction=msg.direction,
                    accepted=True,
                )
            ]
        return []

    dispatch = Recorder("dispatch", answer)
    bus = Bus([dispatch])
    bus.inject(HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN))
    bus.run_until(0.0)
    acks = bus.select(HallCallAck)
    assert len(acks) == 1 and acks[0].msg_id == "MSG-000002"
    assert bus.log[-1]["msg_type"] == "HallCallAck"


def test_F8_timer_is_delivered_back_at_deliver_at():
    def again(msg, now):
        if isinstance(msg, Timer) and msg.key == TIMER_START:
            return [Timer(t_sim=now, deliver_at=now + 2.0, target="cabin:P1", key="tick")]
        return []

    cabin = Recorder("cabin:P1", again)
    bus = Bus([cabin])
    bus.start()
    bus.run_until(1.9)
    assert len(cabin.received) == 1
    bus.run_until(2.0)
    assert [(now, m.key) for now, m in cabin.received] == [(0.0, TIMER_START), (2.0, "tick")]


def test_F8_run_until_leaves_future_messages_queued():
    bus = Bus([])
    bus.inject(Timer(t_sim=0.0, deliver_at=10.0, target="x", key="later"))
    bus.run_until(5.0)
    assert bus.now == 5.0 and bus.pending() == 1 and bus.delivered == []


def test_F4_module_must_use_now_as_t_sim():
    bad = Recorder("dispatch", lambda msg, now: [proposal(now - 1.0)])
    bus = Bus([bad])
    bus.inject(HallCallPressed(t_sim=3.0, floor=27, direction=Direction.DOWN))
    with pytest.raises(ContractViolation, match="t_sim"):
        bus.run_until(3.0)


def test_F4_module_must_leave_msg_id_empty():
    bad = Recorder("dispatch", lambda msg, now: [msg])  # echoes a stamped message
    bus = Bus([bad])
    bus.inject(HallCallPressed(t_sim=0.0, floor=27, direction=Direction.DOWN))
    with pytest.raises(ContractViolation, match="msg_id"):
        bus.run_until(0.0)


def test_F8_cannot_inject_into_the_past():
    bus = Bus([])
    bus.run_until(10.0)
    with pytest.raises(ContractViolation):
        bus.inject(HallCallPressed(t_sim=9.0, floor=27, direction=Direction.DOWN))


def test_F8_duplicate_module_names_rejected():
    with pytest.raises(ValueError):
        Bus([Recorder("cc"), Recorder("cc")])
