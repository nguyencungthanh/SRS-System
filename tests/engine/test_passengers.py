"""Virtual passengers (D-56): press, board within MAX_LOAD_KG, choose destination, exit."""

from app.modules import module_set
from contracts.messages import (
    CabinDestinationPressed,
    CabinInit,
    HallCallPressed,
    Passenger,
    PassengerBoard,
    PassengerExit,
)
from simulation.engine import Engine
from simulation.scenario import PassengerIntent, Scenario

FAKES, _ = module_set("fake", "fake", "fake")


def run(fleet, passengers, duration=200.0) -> Engine:
    e = Engine(
        FAKES,
        Scenario(name="T", fleet=tuple(fleet), passengers=tuple(passengers), duration_s=duration),
    )
    e.run_to_end()
    return e


def test_D_56_two_passengers_same_call_board_together():
    e = run(
        [CabinInit(car_id="P1", floor=31)],
        [
            PassengerIntent(t=0.0, passenger_id="A", origin=27, destination=5),
            PassengerIntent(t=1.0, passenger_id="B", origin=27, destination=10),
        ],
    )
    boards = e.bus.select(PassengerBoard)
    assert [(b.passenger_id, b.t_sim) for b in boards] == [("A", 8.0), ("B", 8.0)]
    assert sorted(d.floor for d in e.bus.select(CabinDestinationPressed)) == [5, 10]
    assert {x.passenger_id for x in e.bus.select(PassengerExit)} == {"A", "B"}
    assert len(e.bus.select(HallCallPressed)) == 2  # one canonical call (REQ-02)


def test_SEN_04_D_56_passenger_over_max_load_waits_and_presses_again():
    e = run(
        [CabinInit(car_id="P1", floor=1)],
        [
            PassengerIntent(t=0.0, passenger_id="A", origin=1, destination=10, weight_kg=600.0),
            PassengerIntent(t=0.0, passenger_id="B", origin=1, destination=20, weight_kg=600.0),
        ],
    )
    boards = [(b.passenger_id, b.t_sim) for b in e.bus.select(PassengerBoard)]
    assert boards[0] == ("A", 0.0)
    assert boards[1][0] == "B" and boards[1][1] > 0.0  # left behind, boarded on a later trip
    assert len(e.bus.select(HallCallPressed)) == 3  # A, B, then B again
    assert {x.passenger_id for x in e.bus.select(PassengerExit)} == {"A", "B"}


def test_D_56_riders_already_on_board_exit_at_their_floor():
    rider = Passenger(passenger_id="R", destination=5)
    e = run([CabinInit(car_id="P1", floor=27, passengers=(rider,))], [], duration=60.0)
    (exit_msg,) = e.bus.select(PassengerExit)
    assert (exit_msg.passenger_id, exit_msg.t_sim) == ("R", 44.0)
    assert e.passengers.snapshot()["arrived"] == 1


def test_D_56_invalid_trip_is_dropped_without_a_hall_call():
    e = run(
        [CabinInit(car_id="P1", floor=1)],
        [
            PassengerIntent(t=0.0, passenger_id="X", origin=41, destination=5),
            PassengerIntent(t=0.0, passenger_id="Y", origin=5, destination=5),
        ],
        duration=10.0,
    )
    assert not e.bus.select(HallCallPressed)
    assert e.passengers.snapshot()["gave_up"] == 2
