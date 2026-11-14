"""DEMO: a calm, fully scripted scenario for building and showing the UI."""

from contracts.messages import CabinInit
from simulation.scenario import PassengerIntent, Scenario

_TRIPS = [
    (2.0, 1, 18),
    (6.0, 25, 3),
    (10.0, 12, 33),
    (15.0, 40, 1),
    (21.0, 7, 29),
    (28.0, 30, 10),
    (36.0, 1, 38),
    (45.0, 22, 2),
    (55.0, 16, 35),
    (65.0, 34, 8),
    (80.0, 3, 21),
    (95.0, 27, 5),
]


def demo(seed: int = 0) -> Scenario:
    return Scenario(
        name="DEMO",
        description="Five cars, a dozen passengers over two minutes. For the UI.",
        fleet=(
            CabinInit(car_id="P1", floor=1),
            CabinInit(car_id="P2", floor=10),
            CabinInit(car_id="P3", floor=20),
            CabinInit(car_id="P4", floor=30),
            CabinInit(car_id="G1", floor=1),
        ),
        passengers=tuple(
            PassengerIntent(t=t, passenger_id=f"PAX-{i + 1:04d}", origin=o, destination=d)
            for i, (t, o, d) in enumerate(_TRIPS)
        ),
        duration_s=3600.0,
    )
