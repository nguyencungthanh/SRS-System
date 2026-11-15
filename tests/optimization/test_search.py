"""Grid search and SATSA on synthetic surfaces (OPT-02, OPT-03, OPT-05, D-35)."""

import math

from contracts.config import R_DOMAIN, SATSA_MAX_EVALS, W_DOMAIN
from optimization.grid import best_of, domain, grid_search
from optimization.satsa import MAX_ITERATIONS, project, satsa


def bowl(theta):
    r, w = theta
    return 10 * (r - 2) ** 2 + (w - 4) ** 2 + 30.0


def batch_of(f, calls=None):
    def evaluate_batch(thetas):
        if calls is not None:
            calls.extend(thetas)
        return {t: f(t) for t in thetas}

    return evaluate_batch


def test_D_33_grid_covers_all_55_pairs_and_skips_rejected():
    assert len(domain()) == len(R_DOMAIN) * len(W_DOMAIN) == 55
    result = grid_search(batch_of(lambda t: None if t == (2, 4) else bowl(t)))
    assert len(result.scores) == 55
    assert result.rejected == [(2, 4)]
    assert result.best in ((2, 3), (2, 5))


def test_OPT_05_a_tie_keeps_the_reference():
    assert best_of({(1, 0): 5.0, (3, 5): 5.0}) == (3, 5)
    assert best_of({(1, 0): 4.9, (3, 5): 5.0}) == (1, 0)
    assert best_of({(1, 0): None}) is None


def test_OPT_02_projection_clamps_and_rounds_to_valid_pairs():
    assert project((0.2, 11.7)) == (1, 10)
    assert project((2.49, 4.5)) == (2, 5)
    assert project((4.51, -3.0)) == (5, 0)
    for x in [(-9.0, 99.0), (3.3, 3.3), (7.0, -1.0)]:
        r, w = project(x)
        assert r in R_DOMAIN and w in W_DOMAIN


def test_D_35_satsa_respects_the_budget_and_never_rescores_a_pair():
    calls = []
    result = satsa(batch_of(bowl, calls), budget=SATSA_MAX_EVALS, seed=0)
    assert result.unique_evaluations == len(calls) == len(set(calls)) <= SATSA_MAX_EVALS
    assert any(step.reused for step in result.history)  # two points, same (R, W)


def test_OPT_03_satsa_finds_a_near_optimal_pair():
    regrets = [satsa(batch_of(bowl), seed=s).best_objective - 30.0 for s in range(5)]
    assert all(r <= 2.0 for r in regrets)  # at most one W step from (2, 4)
    assert min(regrets) == 0.0


def test_OPT_03_satsa_is_deterministic_per_seed():
    a = satsa(batch_of(bowl), seed=3)
    b = satsa(batch_of(bowl), seed=3)
    assert (a.best, a.unique_evaluations) == (b.best, b.unique_evaluations)
    assert [s.theta for s in a.history] == [s.theta for s in b.history]


def test_D_66_satsa_treats_rejected_pairs_as_never_best_and_stops():
    result = satsa(batch_of(lambda t: None), seed=0)
    assert result.best is None and result.best_objective is None
    assert result.iterations <= MAX_ITERATIONS
    mixed = satsa(batch_of(lambda t: None if t[0] >= 4 else bowl(t)), seed=1)
    assert mixed.best is not None and mixed.best[0] < 4
    assert not math.isinf(mixed.best_objective)
