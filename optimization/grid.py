"""Exhaustive grid search over the finite (R, W) domain (OPT-05, D-33)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from contracts.config import DEFAULT_POLICY, R_DOMAIN, W_DOMAIN

Theta = tuple[int, int]
REFERENCE: Theta = (DEFAULT_POLICY.R, DEFAULT_POLICY.W)
EvaluateBatch = Callable[[Sequence[Theta]], dict[Theta, float | None]]


@dataclass(frozen=True)
class GridResult:
    scores: dict[Theta, float | None]  # J for every pair; None = rejected (D-66)

    @property
    def best(self) -> Theta | None:
        return best_of(self.scores)

    @property
    def rejected(self) -> list[Theta]:
        return [theta for theta, j in self.scores.items() if j is None]


def best_of(scores: dict[Theta, float | None]) -> Theta | None:
    """Lowest J. A tie with the reference keeps the reference: a change needs evidence."""
    valid = [(j, theta != REFERENCE, theta) for theta, j in scores.items() if j is not None]
    return min(valid)[2] if valid else None


def domain() -> list[Theta]:
    return [(r, w) for r in R_DOMAIN for w in W_DOMAIN]


def grid_search(evaluate_batch: EvaluateBatch) -> GridResult:
    thetas = domain()
    scores = evaluate_batch(thetas)
    return GridResult({theta: scores[theta] for theta in thetas})
