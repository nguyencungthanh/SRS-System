"""SATSA search over (R, W) (AN-07, OPT-02, OPT-03, SRS 4.8.1, 4.8.4).

Our adaptation of the Simplex-based Adaptive Tree-Seed Algorithm (Liu et al. [8]) to a small
integer domain. It keeps the three mechanisms the SRS names, and states what is ours:

* Trees and seeds (TSA): a few "trees" are points in the continuous box R in [1, 5],
  W in [0, 10]. Each iteration every tree spreads seeds:
  - adaptive Gaussian seeds around the tree; each tree's spread sigma shrinks after an
    improvement and grows after a failure (adaptive Gaussian seed update);
  - one seed pulled towards the best tree, as in TSA's search tendency.
* Stagnation: a tree that has not improved for STALL_EXTRA iterations also gets wider extra
  seeds; after STALL_RESET it is re-planted at a random point (additional seeds).
* Nelder-Mead: the weakest tree also tries a reflection, expansion and contraction against
  the centroid of the other trees (simplex refinement).

Every continuous point x is clamped and rounded to an integer pair theta = (R, W) before
it is simulated (OPT-02). A theta that was already scored is reused from the cache and
costs nothing, so the budget counts unique theta only (D-35). A rejected theta (None) is
treated as infinitely bad. The same seed gives the same search.

Settings (TREES, SIGMA_*) were chosen on synthetic test surfaces: at the D-35 budget of 30
unique pairs this variant ended closer to the true optimum than random sampling of 30
pairs; at a budget of 15 it was not always better. Measured, not assumed (OPT-05).
"""

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from contracts.config import R_DOMAIN, SATSA_MAX_EVALS, W_DOMAIN
from optimization.grid import best_of

Theta = tuple[int, int]
Point = tuple[float, float]
EvaluateBatch = Callable[[Sequence[Theta]], dict[Theta, float | None]]

TREES = 3
GAUSSIAN_SEEDS = 2
SIGMA_START = 0.15  # fraction of each axis' span
SIGMA_MIN, SIGMA_MAX = 0.03, 0.6
SHRINK, GROW = 0.8, 1.2
STALL_EXTRA = 2
STALL_RESET = 4
MAX_ITERATIONS = 50

LOW: Point = (float(min(R_DOMAIN)), float(min(W_DOMAIN)))
HIGH: Point = (float(max(R_DOMAIN)), float(max(W_DOMAIN)))
SPAN: Point = (HIGH[0] - LOW[0], HIGH[1] - LOW[1])


def project(x: Point) -> Theta:
    """Clamp to the box, then round to the nearest valid integer pair (OPT-02)."""
    r = min(max(x[0], LOW[0]), HIGH[0])
    w = min(max(x[1], LOW[1]), HIGH[1])
    return (int(math.floor(r + 0.5)), int(math.floor(w + 0.5)))


@dataclass
class Step:
    iteration: int
    x: Point
    theta: Theta
    objective: float | None
    reused: bool  # theta was already scored (no budget used)


@dataclass
class SatsaResult:
    best: Theta | None
    best_objective: float | None
    unique_evaluations: int
    iterations: int
    history: list[Step] = field(default_factory=list)


def satsa(
    evaluate_batch: EvaluateBatch,
    budget: int = SATSA_MAX_EVALS,
    seed: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> SatsaResult:
    rng = random.Random(seed)
    scores: dict[Theta, float | None] = {}
    history: list[Step] = []

    def fit(theta: Theta) -> float:
        value = scores.get(theta)
        return math.inf if value is None else value

    def score_points(points: list[Point], iteration: int) -> None:
        """Score the projected points in order, never exceeding the unique budget."""
        new: list[Theta] = []
        for x in points:
            theta = project(x)
            if theta not in scores and theta not in new and len(scores) + len(new) < budget:
                new.append(theta)
        if new:
            scores.update(evaluate_batch(new))
            if progress:
                progress(len(scores), budget)
        for x in points:
            theta = project(x)
            if theta in scores:
                history.append(Step(iteration, x, theta, scores[theta], theta not in new))

    def gaussian(center: Point, sigma: float) -> Point:
        return (
            center[0] + rng.gauss(0.0, sigma * SPAN[0]),
            center[1] + rng.gauss(0.0, sigma * SPAN[1]),
        )

    def random_point() -> Point:
        return (rng.uniform(LOW[0], HIGH[0]), rng.uniform(LOW[1], HIGH[1]))

    trees: list[Point] = [random_point() for _ in range(TREES)]
    sigma = [SIGMA_START] * TREES
    stall = [0] * TREES
    score_points(trees, 0)

    iteration = 0
    while len(scores) < budget and iteration < MAX_ITERATIONS:
        iteration += 1
        order = sorted(range(TREES), key=lambda i: fit(project(trees[i])))
        best_i = order[0]
        weak = {order[-1]}
        better = [trees[i] for i in order[: TREES - len(weak)]]
        centroid = (
            sum(p[0] for p in better) / len(better),
            sum(p[1] for p in better) / len(better),
        )

        seeds: list[list[Point]] = []
        for i, tree in enumerate(trees):
            own = [gaussian(tree, sigma[i]) for _ in range(GAUSSIAN_SEEDS)]
            other = trees[rng.choice([j for j in range(TREES) if j != i])]
            pull = rng.uniform(0.0, 1.0)
            own.append(
                (
                    tree[0] + pull * (trees[best_i][0] - other[0]),
                    tree[1] + pull * (trees[best_i][1] - other[1]),
                )
            )
            if stall[i] >= STALL_EXTRA:
                own += [gaussian(tree, min(SIGMA_MAX, 2 * sigma[i])) for _ in range(2)]
            if i in weak:
                for coef in (1.0, 2.0, -0.5):  # reflection, expansion, contraction
                    own.append(
                        (
                            centroid[0] + coef * (centroid[0] - tree[0]),
                            centroid[1] + coef * (centroid[1] - tree[1]),
                        )
                    )
            seeds.append(own)

        score_points([x for own in seeds for x in own], iteration)

        for i, own in enumerate(seeds):
            candidates = [x for x in own if project(x) in scores]
            best_seed = min(candidates, key=lambda x: fit(project(x)), default=None)
            if best_seed is not None and fit(project(best_seed)) < fit(project(trees[i])):
                trees[i] = best_seed
                sigma[i] = max(SIGMA_MIN, sigma[i] * SHRINK)
                stall[i] = 0
            else:
                sigma[i] = min(SIGMA_MAX, sigma[i] * GROW)
                stall[i] += 1
                if stall[i] >= STALL_RESET:
                    trees[i] = random_point()
                    sigma[i] = SIGMA_START
                    stall[i] = 0
                    score_points([trees[i]], iteration)

    best = best_of(scores)
    best_objective = scores[best] if best is not None else None
    return SatsaResult(
        best=best,
        best_objective=best_objective,
        unique_evaluations=len(scores),
        iterations=iteration,
        history=history,
    )
