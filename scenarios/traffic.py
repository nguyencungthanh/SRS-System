"""Seeded random traffic for the training and held-out traces (AN-04, D-34, D-64).

Passengers arrive as a Poisson process (mean interval TRAFFIC_MEAN_INTERVAL_S) during
``trace_s`` seconds; origin and destination are uniform over the floors and differ. The
scenario then runs a further DRAIN_S seconds without new passengers, so a call still open
at the end is truly unserved (D-31). The same seed always gives the same trace.
"""

import random

from contracts.config import (
    DEFAULT_PASSENGER_KG,
    DEFAULT_POLICY,
    DRAIN_S,
    MAX_FLOOR,
    MIN_FLOOR,
    TRACE_DURATION_S,
    TRAFFIC_MEAN_INTERVAL_S,
    Policy,
)
from contracts.messages import CabinInit
from simulation.scenario import PassengerIntent, Scenario

# Cars spread over the building at the start, so no trace begins with a pile-up at floor 1.
FLEET = (
    CabinInit(car_id="P1", floor=1),
    CabinInit(car_id="P2", floor=10),
    CabinInit(car_id="P3", floor=20),
    CabinInit(car_id="P4", floor=30),
    CabinInit(car_id="G1", floor=1),
)


def passengers(
    seed: int,
    trace_s: float = TRACE_DURATION_S,
    interval_s: float = TRAFFIC_MEAN_INTERVAL_S,
) -> tuple[PassengerIntent, ...]:
    rng = random.Random(seed)
    t = 0.0
    out: list[PassengerIntent] = []
    while True:
        t += rng.expovariate(1.0 / interval_s)
        if t >= trace_s:
            return tuple(out)
        origin = rng.randint(MIN_FLOOR, MAX_FLOOR)
        destination = rng.randint(MIN_FLOOR, MAX_FLOOR - 1)
        if destination >= origin:
            destination += 1  # uniform over the other floors
        out.append(
            PassengerIntent(
                t=round(t, 1),
                passenger_id=f"PAX-{len(out) + 1:04d}",
                origin=origin,
                destination=destination,
                weight_kg=DEFAULT_PASSENGER_KG,
            )
        )


def random_traffic(
    seed: int,
    trace_s: float = TRACE_DURATION_S,
    policy: Policy = DEFAULT_POLICY,
    name: str | None = None,
) -> Scenario:
    return Scenario(
        name=name or f"RANDOM-{seed}",
        description=(
            f"Seeded random traffic (seed {seed}): one passenger every "
            f"{TRAFFIC_MEAN_INTERVAL_S:g} s on average for {trace_s:g} s, then {DRAIN_S:g} s drain."
        ),
        fleet=FLEET,
        passengers=passengers(seed, trace_s),
        duration_s=trace_s + DRAIN_S,
        policy=policy,
    )
