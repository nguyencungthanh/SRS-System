"""Thanh's real dispatcher on the shared engine with virtual passengers and fakes."""

import pytest

import scenarios
from app.modules import module_set
from app.replay import replay
from contracts.enums import MissionStatus, ServiceEventType, StopKind
from contracts.messages import CabinTelemetry, MissionStatusUpdate, RouteVersionQuery, ServiceEvent
from simulation.engine import Engine


def run_scenario(name):
    modules, _ = module_set("real", "fake", "fake")
    engine = Engine(modules, scenarios.get(name))
    engine.run_to_end()
    return engine


def event_times(engine, event, *, floor=None, car="P1"):
    return [
        message.t_sim
        for message in engine.bus.select(ServiceEvent, car_id=car)
        if message.event == event and (floor is None or message.floor == floor)
    ]


def test_AT_01_real_dispatch_preserves_end_to_end_passenger_timeline():
    engine = run_scenario("AT-01")

    assert event_times(engine, ServiceEventType.HALL_CALL_SERVED) == [8.0]
    assert event_times(engine, ServiceEventType.DEST_SERVED) == [58.0]
    assert event_times(engine, ServiceEventType.DOOR_CLOSED_LOCKED, floor=5) == [64.0]
    assert engine.metrics()["unserved"] == 0


def test_AT_02_real_dispatch_groups_pickups_with_virtual_passengers():
    engine = run_scenario("AT-02")

    assert event_times(engine, ServiceEventType.HALL_CALL_SERVED, floor=29) == [4.0]
    assert event_times(engine, ServiceEventType.HALL_CALL_SERVED, floor=27) == [14.0]
    assert engine.metrics()["unserved"] == 0


def test_AT_03_real_dispatch_explains_fleetwide_medical_selection():
    engine = run_scenario("AT-03")
    (reserved,) = [
        message
        for message in engine.bus.select(MissionStatusUpdate)
        if message.status == MissionStatus.RESERVED
    ]
    candidates = {candidate.car_id: candidate for candidate in reserved.candidates}

    assert reserved.car_id == "P2"
    assert [candidates[car].eta_s for car in ("P1", "P2", "P4")] == [90.0, 20.0, 58.0]
    assert candidates["P3"].excluded is not None
    assert candidates["G1"].excluded is not None
    assert any(
        message.status == MissionStatus.COMPLETED
        for message in engine.bus.select(MissionStatusUpdate)
    )


def test_AT_04_real_dispatch_preserves_drain_and_freight_timeline():
    engine = run_scenario("AT-04")

    assert event_times(engine, ServiceEventType.DEST_SERVED, floor=5) == [44.0]
    assert event_times(engine, ServiceEventType.DOOR_CLOSED_LOCKED, floor=5) == [50.0]
    assert event_times(engine, ServiceEventType.CAR_ARRIVED, floor=1) == [58.0]
    assert event_times(engine, ServiceEventType.DOOR_CLOSED_LOCKED, floor=1) == [64.0]
    assert event_times(engine, ServiceEventType.MISSION_DELIVERY_DONE, floor=30) == [122.0]


def test_AT_05_lost_ack_reconciles_and_never_duplicates_actual_ownership():
    engine = run_scenario("AT-05-ack-lost")
    assert engine.bus.select(RouteVersionQuery)
    assert event_times(engine, ServiceEventType.HALL_CALL_SERVED) == [8.0]
    per_car = {}
    for message in engine.bus.select(CabinTelemetry):
        per_car[message.car_id] = {
            stop.ref_id for stop in message.stops if stop.kind == StopKind.HALL_PICKUP
        }
        all_calls = [call for owned in per_car.values() for call in owned]
        assert len(all_calls) == len(set(all_calls))


@pytest.mark.parametrize("name", ["AT-02", "AT-03", "AT-05-ack-lost"])
def test_NFR_05_real_dispatch_saved_trace_replays_identically(name, tmp_path):
    engine = run_scenario(name)
    path = tmp_path / "dispatch-trace.jsonl"
    engine.save_trace(path)

    replayed, identical = replay(path)

    assert replayed.module_set.kinds["dispatch"] == "real"
    assert identical
