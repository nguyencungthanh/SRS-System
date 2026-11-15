"""Scenario registry (owned by An). ``get("AT-01")`` returns a ``simulation.scenario.Scenario``.

AT-01 .. AT-05-*      acceptance tests (SRS 6.4, 9.2)
DEMO                  calm scripted traffic for the UI
TRAIN-1 .. TRAIN-5    training traces (D-34): fixed seeds, used to choose (R, W)
HOLDOUT-101 .. -105   held-out traces (D-34): used only to report results
QUICK-1               one short training trace for runs started from the UI (D-65)
RANDOM                random traffic with the seed given to reset (any seed)
"""

from collections.abc import Callable

from contracts.config import HOLDOUT_SEEDS, QUICK_SEEDS, QUICK_TRACE_S, TRAIN_SEEDS
from scenarios.acceptance import at01, at02, at03, at04, at05_ack_lost, at05_unsafe
from scenarios.demo import demo
from scenarios.traffic import random_traffic
from simulation.scenario import Scenario

REGISTRY: dict[str, Callable[[int], Scenario]] = {
    "AT-01": at01,
    "AT-02": at02,
    "AT-03": at03,
    "AT-04": at04,
    "AT-05-ack-lost": at05_ack_lost,
    "AT-05-unsafe": at05_unsafe,
    "DEMO": demo,
    "RANDOM": random_traffic,
}


def _fixed(name: str, seed: int, trace_s: float | None = None) -> Callable[[int], Scenario]:
    def make(_ignored_seed: int = 0) -> Scenario:
        if trace_s is None:
            return random_traffic(seed, name=name)
        return random_traffic(seed, trace_s=trace_s, name=name)

    return make


for _seed in TRAIN_SEEDS:
    REGISTRY[f"TRAIN-{_seed}"] = _fixed(f"TRAIN-{_seed}", _seed)
for _seed in HOLDOUT_SEEDS:
    REGISTRY[f"HOLDOUT-{_seed}"] = _fixed(f"HOLDOUT-{_seed}", _seed)
for _seed in QUICK_SEEDS:
    REGISTRY[f"QUICK-{_seed}"] = _fixed(f"QUICK-{_seed}", _seed, QUICK_TRACE_S)


def names() -> list[str]:
    return list(REGISTRY)


def get(name: str, seed: int = 0) -> Scenario:
    if name not in REGISTRY:
        raise KeyError(f"unknown scenario {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name](seed)
