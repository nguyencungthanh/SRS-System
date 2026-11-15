"""Fixed commitment references under delayed transport and door extensions."""

from contracts.config import Policy
from contracts.enums import CallStatus, Direction
from contracts.messages import CabinInit, DoorRequest, HallCallPressed, RouteProposal
from dispatch import create
from fakes.cabin import create as create_cabin
from simulation.bus import Bus
from simulation.scenario import DELAY, NetworkRule


def test_DC_04_reference_begins_at_actual_commitment_after_delayed_proposal():
    dispatch = create(Policy(version="delay-regression", R=3, W=10))
    cabin = create_cabin(CabinInit(car_id="P1", floor=40))
    bus = Bus(
        (dispatch, cabin),
        network=(NetworkRule(msg_type="RouteProposal", action=DELAY, delay_s=1.5),),
    )
    bus.start()
    bus.inject(HallCallPressed(t_sim=0.0, floor=35, direction=Direction.DOWN))
    bus.inject(HallCallPressed(t_sim=2.0, floor=38, direction=Direction.DOWN))
    # The stop at 35 is committed at t=1.5 with arrival reference 11.5.
    # Pickup at 38 adds six seconds. This door extension adds three more.
    # The resulting 20.5 arrival remains inside the fixed ten-second cap.
    bus.inject(DoorRequest(t_sim=9.5, car_id="P1", open=True))
    bus.inject(HallCallPressed(t_sim=9.5, floor=33, direction=Direction.DOWN))

    bus.run_until(9.5)

    last = [
        proposal
        for proposal in bus.select(RouteProposal)
        if any(stop.ref_id == "HC-0003" for stop in proposal.ordered_stops)
    ]
    assert len(last) == 1
    assert last[0].t_sim == 9.5
    assert [stop.ref_id for stop in last[0].ordered_stops] == ["HC-0001", "HC-0003"]
    calls = {call["request_id"]: call for call in dispatch.snapshot()["calls"]}
    assert calls["HC-0003"]["status"] == CallStatus.ASSIGNED
