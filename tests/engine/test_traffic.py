"""scenarios/traffic.py and the TRAIN / HOLDOUT / QUICK registry entries (AN-04, D-34, D-64)."""

import scenarios
from contracts.config import (
    DRAIN_S,
    HOLDOUT_SEEDS,
    MAX_FLOOR,
    MIN_FLOOR,
    QUICK_TRACE_S,
    TRACE_DURATION_S,
    TRAFFIC_MEAN_INTERVAL_S,
    TRAIN_SEEDS,
)
from scenarios.traffic import passengers, random_traffic


def test_D_34_same_seed_same_trace_and_seeds_differ():
    assert passengers(1) == passengers(1)
    assert passengers(1) != passengers(2)


def test_D_64_rate_floors_and_times():
    trips = passengers(3)
    expected = TRACE_DURATION_S / TRAFFIC_MEAN_INTERVAL_S  # 150
    assert 0.7 * expected < len(trips) < 1.3 * expected
    assert all(MIN_FLOOR <= p.origin <= MAX_FLOOR and p.origin != p.destination for p in trips)
    assert all(MIN_FLOOR <= p.destination <= MAX_FLOOR for p in trips)
    assert [p.t for p in trips] == sorted(p.t for p in trips)
    assert trips[-1].t < TRACE_DURATION_S
    assert len({p.passenger_id for p in trips}) == len(trips)


def test_D_31_traces_end_with_a_drain_period():
    scenario = random_traffic(1)
    assert scenario.duration_s == TRACE_DURATION_S + DRAIN_S


def test_D_34_registry_names_and_disjoint_seeds():
    names = scenarios.names()
    assert [f"TRAIN-{s}" for s in TRAIN_SEEDS] == [n for n in names if n.startswith("TRAIN-")]
    assert [f"HOLDOUT-{s}" for s in HOLDOUT_SEEDS] == [n for n in names if n.startswith("HOLDOUT-")]
    assert not set(TRAIN_SEEDS) & set(HOLDOUT_SEEDS)
    assert scenarios.get("TRAIN-1").passengers == passengers(1)
    assert scenarios.get("QUICK-1").duration_s == QUICK_TRACE_S + DRAIN_S
    assert scenarios.get("RANDOM", 7).passengers == passengers(7)
