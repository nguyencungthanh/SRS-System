"""Scenario registry (owned by An). ``get("AT-01")`` returns a ``simulation.scenario.Scenario``.

Random traffic for training / held-out traces (AN-04) will be added here later.
"""

from collections.abc import Callable

from scenarios.acceptance import at01, at02, at03, at04, at05_ack_lost, at05_unsafe
from scenarios.demo import demo
from simulation.scenario import Scenario

REGISTRY: dict[str, Callable[[int], Scenario]] = {
    "AT-01": at01,
    "AT-02": at02,
    "AT-03": at03,
    "AT-04": at04,
    "AT-05-ack-lost": at05_ack_lost,
    "AT-05-unsafe": at05_unsafe,
    "DEMO": demo,
}


def names() -> list[str]:
    return list(REGISTRY)


def get(name: str, seed: int = 0) -> Scenario:
    if name not in REGISTRY:
        raise KeyError(f"unknown scenario {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name](seed)
