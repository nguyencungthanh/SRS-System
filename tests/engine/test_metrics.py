"""simulation/metrics.py: waits (D-30), commitment delay (D-63), hard invariants (D-66)."""

import scenarios
from app.modules import module_set
from simulation.engine import Engine
from simulation.metrics import RunObserver, nearest_rank, route_etas

AUTO, _ = module_set()


def tel(t, car, position, stops, door="CLOSED", moving=False, locked=True):
    return {
        "msg_type": "CabinTelemetry",
        "t_sim": t,
        "car_id": car,
        "position": float(position),
        "door": door,
        "moving": moving,
        "locked": locked,
        "stops": [{"floor": f, "kind": k, "ref_id": r, "direction": None} for f, k, r in stops],
    }


def test_D_30_nearest_rank_percentiles():
    values = [1.0, 2.0, 3.0, 4.0, 10.0]
    assert nearest_rank(values, 0.5) == 3.0
    assert nearest_rank(values, 0.95) == 10.0
    assert nearest_rank([], 0.95) is None


def test_D_63_eta_model_matches_the_srs_examples():
    # AT-01: P1 idle at 31 -> pickup 27 at 8 s; AT-02: 29 at 4 s, 27 at 14 s.
    at01 = route_etas(tel(0.0, "P1", 31, []), [{"floor": 27, "kind": "HALL_PICKUP", "ref_id": "A"}])
    assert at01[("HALL_PICKUP", "A", 27)] == 8.0
    at02 = route_etas(
        tel(0.0, "P1", 31, []),
        [
            {"floor": 29, "kind": "HALL_PICKUP", "ref_id": "B"},
            {"floor": 27, "kind": "HALL_PICKUP", "ref_id": "A"},
        ],
    )
    assert (at02[("HALL_PICKUP", "B", 29)], at02[("HALL_PICKUP", "A", 27)]) == (4.0, 14.0)


def test_D_63_one_dwell_per_floor_and_a_running_door_counts_once():
    same_floor = route_etas(
        tel(0.0, "P1", 31, []),
        [
            {"floor": 25, "kind": "HALL_PICKUP", "ref_id": "A"},
            {"floor": 25, "kind": "CAR_DEST", "ref_id": "X"},
            {"floor": 13, "kind": "HALL_PICKUP", "ref_id": "B"},
        ],
    )
    assert same_floor[("HALL_PICKUP", "B", 13)] == 12.0 + 6.0 + 24.0  # one dwell at 25
    reopened = route_etas(
        tel(0.0, "P3", 14, [], door="OPENING"),
        [
            {"floor": 14, "kind": "HALL_PICKUP", "ref_id": "A"},
            {"floor": 39, "kind": "CAR_DEST", "ref_id": "X"},
        ],
    )
    assert reopened[("CAR_DEST", "X", 39)] == 6.0 + 50.0  # the reopening IS the dwell at 14


def test_D_63_a_dispatch_route_past_the_cap_is_a_violation():
    obs = RunObserver()
    obs.observe(tel(0.0, "P1", 40, [(30, "CAR_DEST", "X")], moving=True))  # reference 20 s
    proposal = {
        "msg_type": "RouteProposal",
        "t_sim": 0.0,
        "command_id": "RC-1",
        "car_id": "P1",
        "ordered_stops": [
            {"floor": f, "kind": "HALL_PICKUP", "ref_id": r, "direction": "DOWN"}
            for f, r in ((35, "A"), (33, "B"))
        ]
        + [{"floor": 30, "kind": "CAR_DEST", "ref_id": "X", "direction": None}],
    }
    obs.observe(proposal)
    obs.observe({"msg_type": "RouteReply", "t_sim": 0.0, "command_id": "RC-1", "accepted": True})
    assert obs.delay_violations == 1  # 32 s vs the fixed reference 20 s (TC-DELAY-01)
    assert obs.breaches() == ["DELAY_CAP x1"]


def test_D_66_double_owner_and_door_interlock_are_breaches():
    obs = RunObserver()
    obs.observe(tel(0.0, "P1", 10, [(20, "HALL_PICKUP", "HC-1")]))
    obs.observe(tel(0.0, "P2", 30, [(20, "HALL_PICKUP", "HC-1")]))
    obs.observe(tel(1.0, "P3", 5.5, [], door="OPEN", moving=True, locked=False))
    assert obs.breaches() == ["DOUBLE_OWNER x1", "DOOR_INTERLOCK x1"]


def test_D_63_AT_02_grouping_is_legal_and_observed_as_a_6s_delay():
    engine = Engine(AUTO, scenarios.get("AT-02"))
    engine.run_to_end()
    assert engine.observer.delay_violations == 0
    assert engine.observer.breaches() == []
    assert engine.metrics()["delay_violations"] == 0
