"""Wear NFR (D-71..D-79): trip counter, monthly check, reset, Dispatch balancing."""

import dataclasses

import pytest

import dispatch
import scenarios
from app.modules import module_set
from contracts.config import (
    WEAR_CARS,
    WEAR_MAX_PENALTY_S,
    WEAR_MONTH_LIMIT,
    WEAR_PENALTY_S_PER_FLOOR,
    WEAR_RESET_MONTHS,
)
from contracts.enums import Direction, DispatchAlgorithm
from contracts.messages import CabinInit, HallCallPressed, RouteProposal, TripCounterReset
from contracts.ui import WearMonthView, WearView
from simulation.engine import Engine, ModuleSet
from simulation.metrics import RunObserver, wear_spread
from simulation.scenario import Scenario

AUTO, _ = module_set()
FAKES, _ = module_set("fake", "fake", "fake")


def run(scenario: Scenario, modules: ModuleSet = AUTO) -> Engine:
    engine = Engine(modules, scenario)
    engine.run_to_end()
    return engine


def trips(engine: Engine) -> dict[str, float]:
    return {c["car_id"]: c["trip_floors"] for c in engine.snapshot()["cars"]}


@pytest.mark.parametrize("modules", [AUTO, FAKES], ids=["real", "fake"])
def test_D_71_the_trip_counter_counts_travelled_floors(modules):
    engine = run(scenarios.get("AT-01"), modules)
    counted = trips(engine)
    assert counted["P1"] == pytest.approx(26.0)  # 31 -> 27 -> 5
    assert all(counted[car] == 0.0 for car in ("P2", "P3", "P4", "G1"))
    log = engine.bus.log
    p1 = [e["trip_floors"] for e in log if e.get("car_id") == "P1" and "trip_floors" in e]
    assert p1 == sorted(p1)  # it only grows, also during a leg
    assert any(0 < v < 4 for v in p1)  # the leg under way is counted, not only at arrival


def test_D_77_a_starting_value_and_the_six_monthly_reset():
    month = 10.0  # AT-01 lasts 80 s: the reset comes at 6 x 10 = 60 s, after the 58 s arrival
    base = scenarios.get("AT-01")
    fleet = tuple(dataclasses.replace(c, trip_floors=100.0) for c in base.fleet)
    engine = run(dataclasses.replace(base, fleet=fleet, wear_month_s=month))
    (reset,) = engine.bus.select(TripCounterReset)
    assert reset.t_sim == WEAR_RESET_MONTHS * month == 60.0
    sixth = engine.observer.months[5]
    assert sixth["counters"]["P1"] == pytest.approx(126.0)  # before the reset
    assert trips(engine)["P1"] == 0.0 and trips(engine)["P2"] == 0.0  # after it


def test_D_72_spread_is_max_minus_min_over_max():
    assert wear_spread({"P1": 100.0, "P2": 95.0, "P3": 99.0}) == pytest.approx(0.05)
    assert wear_spread({"P1": 0.0, "P2": 0.0}) == 0.0
    assert wear_spread({"P1": 10.0}) is None


def tel(t, car, trip, mode="AUTO"):
    return {
        "msg_type": "CabinTelemetry",
        "t_sim": t,
        "car_id": car,
        "trip_floors": float(trip),
        "mode": mode,
        "position": 1.0,
        "door": "CLOSED",
        "moving": False,
        "locked": True,
        "stops": [],
    }


def test_D_73_D_75_D_76_months_compare_p1_p4_and_leave_out_maintenance():
    obs = RunObserver(month_s=100.0)
    for car, trip in zip(WEAR_CARS, (1000, 980, 960, 500), strict=True):
        obs.observe(tel(10, car, trip))
    obs.observe(tel(10, "G1", 0))  # the goods car is never compared (D-73)
    obs.observe(tel(20, "P4", 500, mode="MAINTENANCE"))
    obs.observe(tel(150, "P1", 1100))  # closes month 1 (P4 still in maintenance)
    obs.observe(tel(160, "P4", 520))  # back in service during month 2
    obs.observe(tel(250, "P1", 1100))  # closes month 2
    obs.observe(tel(350, "P1", 1100))  # closes month 3
    first, second, third = obs.months
    assert set(first["counters"]) == set(WEAR_CARS)
    assert first["excluded"] == ["P4"] and first["spread"] == pytest.approx(0.04)
    assert first["ok"] and WEAR_MONTH_LIMIT == 0.05
    assert second["excluded"] == ["P4"]  # in maintenance for part of month 2 as well
    # Month 3 compares P4 again with its counter as it is (D-75): it is far behind.
    assert third["excluded"] == [] and third["spread"] == pytest.approx(580 / 1100)
    assert not third["ok"]
    view = obs.wear_view()
    assert set(view) == set(WearView.__annotations__)
    assert set(view["months"][0]) == set(WearMonthView.__annotations__)


def first_choice(p1: int, p2: int, wear: tuple[float, float], gain=WEAR_PENALTY_S_PER_FLOOR) -> str:
    """Which of P1 / P2 Dispatch proposes for 20 UP, given floors and trip counters."""
    modules = ModuleSet(
        dispatch=lambda policy: dispatch.create(
            policy, DispatchAlgorithm.COMMITMENT, wear_gain=gain
        ),
        cabin=AUTO.cabin,
        cc=AUTO.cc,
        kinds={},
    )
    scenario = Scenario(
        name="WEAR-CHOICE",
        fleet=(
            CabinInit(car_id="P1", floor=p1, trip_floors=wear[0]),
            CabinInit(car_id="P2", floor=p2, trip_floors=wear[1]),
        ),
        inputs=(HallCallPressed(t_sim=0.0, floor=20, direction=Direction.UP),),
        duration_s=1.0,
    )
    proposals = run(scenario, modules).bus.select(RouteProposal)
    return proposals[0].car_id


def test_D_78_a_wear_penalty_shifts_calls_to_the_less_worn_car():
    assert (WEAR_PENALTY_S_PER_FLOOR, WEAR_MAX_PENALTY_S) == (0.2, 8.0)
    # Pickup at 20: a car on 18 or 22 needs 4 s, on 19 2 s, on 20 0 s, on 25 10 s.
    assert first_choice(18, 22, (0.0, 0.0)) == "P1"  # same ETA, same wear: car order
    assert first_choice(18, 22, (500.0, 0.0)) == "P2"  # same ETA: the less-worn car
    assert first_choice(19, 22, (500.0, 0.0)) == "P2"  # 2 + 8 s penalty > 4 s
    assert first_choice(20, 22, (10.0, 0.0)) == "P1"  # 10 floors ahead: 0 + 2 s < 4 s
    assert first_choice(20, 25, (500.0, 0.0)) == "P1"  # capped: 0 + 8 s < 10 s, ETA wins
    assert first_choice(18, 22, (500.0, 0.0), gain=None) == "P1"  # balancing off: as before
