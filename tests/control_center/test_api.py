"""API tests (HY-05).

Most tests use ``RecordingControl``, a stand-in for ``contracts.runtime.SimControl`` that
records what the API sends, so each test checks one thing: the route exists, the role is
checked first, the right message is built, and bad bodies never reach the simulation.
The last tests run An's real ``SimRunner`` with fakes and the real Control Center.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api import create_app
from app.modules import module_set
from app.runner import SimRunner
from contracts.enums import Direction, MissionType, Role
from contracts.messages import HallCallPressed, Message, MissionRequest
from contracts.runtime import SimCommand
from contracts.ui import COMMANDS, CommandResult, Snapshot, fake_snapshot


class RecordingControl:
    """Implements contracts.runtime.SimControl and records every call."""

    def __init__(self, submit_result: CommandResult | None = None) -> None:
        self.submitted: list[Message] = []
        self.controls: list[tuple[SimCommand, dict[str, Any]]] = []
        self.subscribers: list[Any] = []
        self._submit_result = submit_result or {"ok": True, "id": "MSG-0001", "reason": None}

    def submit(self, msg: Message) -> CommandResult:
        self.submitted.append(msg)
        return self._submit_result

    def control(self, command: SimCommand, **args: Any) -> CommandResult:
        self.controls.append((command, args))
        return {"ok": True, "id": None, "reason": None}

    def snapshot(self) -> Snapshot:
        return fake_snapshot()

    def subscribe(self, callback):
        self.subscribers.append(callback)
        return lambda: self.subscribers.remove(callback)

    def scenarios(self) -> list[str]:
        return ["AT-01", "DEMO"]


@pytest.fixture
def control() -> RecordingControl:
    return RecordingControl()


@pytest.fixture
def client(control) -> TestClient:
    return TestClient(create_app(control, web_dir=None))


def mission_body(**over):
    body = {
        "role": "MEDICAL_COORDINATOR",
        "mission_type": "MEDICAL",
        "origin": 2,
        "destination": 30,
    }
    body.update(over)
    return body


# --- routes ------------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", COMMANDS, ids=[c.path for c in COMMANDS])
def test_HY_05_every_contract_command_has_a_post_route(client, cmd):
    role = next(iter(cmd.roles)).value if cmd.roles else None
    response = client.post(cmd.path, json={"role": role} if role else {})
    assert response.status_code not in (404, 405), cmd.path


# --- D-53 role check first ---------------------------------------------------------------


def test_D_53_wrong_role_is_refused_and_nothing_reaches_the_simulation(client, control):
    response = client.post("/mission", json=mission_body(role="PASSENGER"))
    assert response.status_code == 403
    assert response.json() == {"ok": False, "id": None, "reason": "UNAUTHORIZED_ROLE"}
    assert control.submitted == []


def test_D_53_missing_role_is_refused_for_privileged_commands(client, control):
    assert (
        client.post("/fault", json={"car_id": "P2", "fault": "MOTION_SERIOUS"}).status_code == 403
    )
    assert client.post("/sim/start", json={}).status_code == 403
    assert control.submitted == [] and control.controls == []


def test_D_53_engine_commands_are_operator_only(client, control):
    assert client.post("/sim/pause", json={"role": "SUPERVISOR"}).status_code == 403
    assert client.post("/sim/pause", json={"role": "OPERATOR"}).status_code == 200
    assert control.controls == [(SimCommand.PAUSE, {})]


# --- messages are built from the contract ------------------------------------------------


def test_HY_05_hall_call_needs_no_role_and_builds_the_message(client, control):
    response = client.post("/hall-call", json={"floor": 12, "direction": "UP"})
    assert response.json() == {"ok": True, "id": "MSG-0001", "reason": None}
    (msg,) = control.submitted
    assert isinstance(msg, HallCallPressed)
    assert (msg.floor, msg.direction) == (12, Direction.UP)


def test_CC_01_mission_carries_the_role_for_control_center_to_check_again(client, control):
    client.post("/mission", json=mission_body())
    (msg,) = control.submitted
    assert isinstance(msg, MissionRequest)
    assert (msg.role, msg.mission_type, msg.origin) == (
        Role.MEDICAL_COORDINATOR,
        MissionType.MEDICAL,
        2,
    )


def test_HY_05_envelope_fields_from_the_client_are_ignored(client, control):
    client.post("/hall-call", json={"floor": 3, "direction": "UP", "msg_id": "MSG-9", "t_sim": 99})
    (msg,) = control.submitted
    assert (msg.msg_id, msg.t_sim) == ("", 0.0)  # SimControl.submit stamps the real time


# --- malformed bodies: 422, reason None, detail ------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        mission_body(origin="abc"),
        mission_body(origin=True),
        mission_body(mission_type="TAXI"),
        {"floor": 12},  # direction missing
        {"floor": 12, "direction": "SIDEWAYS"},
        {"floor": 12, "direction": "UP", "role": "KING"},
    ],
    ids=["string_floor", "bool_floor", "bad_enum", "missing_field", "bad_direction", "bad_role"],
)
def test_HY_05_malformed_body_is_422_and_never_submitted(client, control, body):
    path = "/mission" if "mission_type" in body else "/hall-call"
    response = client.post(path, json=body)
    assert response.status_code == 422
    data = response.json()
    assert (data["ok"], data["reason"]) == (False, None) and data["detail"]
    assert control.submitted == []


def test_HY_05_body_that_is_not_a_json_object_is_422(client):
    assert client.post("/hall-call", content=b"{nope").status_code == 422
    assert client.post("/hall-call", json=[1, 2]).status_code == 422


def test_HY_05_simulation_refusal_is_409_with_its_reason():
    refusing = RecordingControl({"ok": False, "id": None, "reason": "INVALID_STATE"})
    response = TestClient(create_app(refusing, web_dir=None)).post(
        "/hall-call", json={"floor": 3, "direction": "UP"}
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "INVALID_STATE"


# --- engine commands ---------------------------------------------------------------------


def test_HY_05_reset_passes_scenario_and_seed(client, control):
    client.post("/sim/reset", json={"role": "OPERATOR", "scenario": "AT-01", "seed": 3})
    assert control.controls == [(SimCommand.RESET, {"scenario": "AT-01", "seed": 3})]


def test_HY_05_speed_addition_checks_the_factor(client, control):
    assert client.post("/sim/speed", json={"role": "OPERATOR", "factor": "fast"}).status_code == 422
    assert client.post("/sim/speed", json={"role": "OPERATOR", "factor": 4}).status_code == 200
    assert control.controls == [(SimCommand.SPEED, {"factor": 4.0})]


def test_OPT_06_D_68_optimization_run_reaches_the_simulation(client, control):
    assert client.post("/optimization/run", json={"role": "PASSENGER"}).status_code == 403
    response = client.post("/optimization/run", json={"role": "OPERATOR", "method": "GRID"})
    assert response.status_code == 200 and response.json()["ok"] is True
    body = {"role": "OPERATOR", "method": "SATSA", "quick": False}
    assert client.post("/optimization/run", json=body).status_code == 200
    assert control.controls == [
        (SimCommand.OPTIMIZE, {"method": "GRID", "quick": True}),
        (SimCommand.OPTIMIZE, {"method": "SATSA", "quick": False}),
    ]
    bad = {"role": "OPERATOR", "method": "GRID", "quick": "yes"}
    assert client.post("/optimization/run", json=bad).status_code == 422


# --- reading state -----------------------------------------------------------------------


def test_UI_01_snapshot_and_scenarios(client):
    assert client.get("/snapshot").json()["cars"][0]["car_id"] == "P1"
    assert client.get("/scenarios").json() == ["AT-01", "DEMO"]


def test_UI_01_websocket_sends_a_snapshot_on_connect_and_unsubscribes_on_close(client, control):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["t_sim"] == fake_snapshot()["t_sim"]
        (callback,) = control.subscribers
    asyncio.run(callback(fake_snapshot(5.0)))  # socket closed: must not raise
    assert control.subscribers == []


# --- with An's real engine, fakes and the real Control Center ----------------------------


@pytest.fixture
def runner() -> SimRunner:
    modules, _ = module_set(dispatch="fake", cabin="fake", cc="real")
    return SimRunner(modules, scenario="DEMO", seed=0)


def test_HY_05_real_engine_mission_reaches_control_center(runner):
    client = TestClient(create_app(runner, web_dir=None))
    assert client.post("/sim/start", json={"role": "OPERATOR"}).json()["ok"] is True
    assert client.post("/mission", json=mission_body()).json()["ok"] is True
    runner.tick(1.0)
    (mission,) = client.get("/snapshot").json()["missions"]
    # Control Center approved it; the fake Dispatch has already moved it on (e.g. RESERVED).
    assert mission["mission_id"] == "MS-0001" and mission["status"] != "REJECTED"


# --- verification findings F2, F3, F6, F16 (15/11) ----------------------------------------


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e400"])
@pytest.mark.parametrize(
    "path,template",
    [
        (
            "/mission",
            b'{"role":"BUILDING_STAFF","mission_type":"FREIGHT","origin":2,'
            b'"destination":9,"material":"boxes","weight_kg":%s}',
        ),
        (
            "/passenger/board",
            b'{"role":"OPERATOR","car_id":"P1","passenger_id":"X","weight_kg":%s}',
        ),
        ("/sim/speed", b'{"role":"OPERATOR","factor":%s}'),
    ],
    ids=["mission_weight", "board_weight", "speed"],
)
def test_HY_05_non_finite_numbers_are_422_and_never_reach_the_simulation(
    client, control, path, template, number
):
    response = client.post(
        path, content=template % number, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert control.submitted == [] and control.controls == []


@pytest.mark.parametrize(
    "path,body",
    [
        ("/sim/reset", {"seed": "abc"}),
        ("/sim/reset", {"seed": [1]}),
        ("/sim/reset", {"seed": True}),
        ("/sim/reset", {"scenario": ["AT-01"]}),
        ("/sim/reset", {"scenario": {"name": "AT-01"}}),
        ("/sim/replay", {"trace": 5}),
        ("/sim/replay", {"trace": ["a.jsonl"]}),
    ],
)
def test_HY_05_engine_command_arguments_are_type_checked(client, control, path, body):
    response = client.post(path, json={"role": "OPERATOR", **body})
    assert response.status_code == 422 and response.json()["detail"]
    assert control.controls == []


def test_HY_05_deeply_nested_body_is_422(client, control):
    deep = b'{"floor": 3, "direction": "UP", "x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    response = client.post("/hall-call", content=deep, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert control.submitted == []


def test_CC_03_omitted_optional_field_reaches_control_center_like_null(client, control):
    client.post(
        "/mission",
        json={"role": "MEDICAL_COORDINATOR", "mission_type": "MEDICAL", "destination": 9},
    )
    (msg,) = control.submitted
    assert msg.origin is None  # Control Center answers MISSING_FIELD, same as "origin": null


def test_HY_05_omitted_required_field_gives_a_clean_message(client, control):
    response = client.post("/hall-call", json={"direction": "UP"})
    assert response.status_code == 422
    assert response.json()["detail"] == "missing field: floor"


def test_HY_05_snapshot_handlers_run_on_the_event_loop(client):
    import inspect

    app = client.app
    handlers = {
        r.path: r.endpoint
        for r in app.routes
        if getattr(r, "path", "") in ("/snapshot", "/scenarios")
    }
    assert len(handlers) == 2
    assert all(inspect.iscoroutinefunction(h) for h in handlers.values())


# --- F1: a stalled WebSocket client must not block anyone ------------------------------


def test_UI_01_stalled_client_never_blocks_publish_and_keeps_only_the_latest():
    from api.stream import ClientFeed

    async def scenario():
        never = asyncio.Event()
        stalled, healthy = ClientFeed(), ClientFeed()
        received: list[float] = []

        async def stuck_send(snap):
            await never.wait()  # a client that stopped reading

        async def good_send(snap):
            received.append(snap["t_sim"])

        tasks = [
            asyncio.create_task(stalled.run(stuck_send)),
            asyncio.create_task(healthy.run(good_send)),
        ]
        for t in range(1, 101):  # one publish per tick, like SimRunner.publish()
            stalled.offer(fake_snapshot(float(t)))  # offer() is synchronous: cannot block
            healthy.offer(fake_snapshot(float(t)))
            await asyncio.sleep(0)  # let the sender tasks run between ticks
        await asyncio.sleep(0.01)
        for task in tasks:
            task.cancel()
        return stalled.pending(), received

    stalled_pending, received = asyncio.run(scenario())
    assert stalled_pending <= 1  # the stalled client's backlog never grows
    assert received[-1] == 100.0  # the healthy client kept up to the last tick


# --- F5: refused requests are recorded and visible (SRS 5.3) -----------------------------


def test_5_3_refused_requests_are_recorded_with_time_role_and_reason(client, control):
    client.post("/mission", json=mission_body(role="PASSENGER"))
    client.post("/hall-call", json={"floor": 3, "direction": "UP"})  # accepted: not recorded
    client.post("/hall-call", json={"floor": 3})  # malformed (422): not a refusal
    (entry,) = client.get("/audit").json()
    assert entry == {
        "t_sim": fake_snapshot()["t_sim"],
        "command": "/mission",
        "role": "PASSENGER",
        "status": 403,
        "reason": "UNAUTHORIZED_ROLE",
    }


def test_5_3_simulation_refusals_are_recorded_too():
    refusing = RecordingControl({"ok": False, "id": None, "reason": "INVALID_STATE"})
    client = TestClient(create_app(refusing, web_dir=None))
    client.post("/hall-call", json={"floor": 3, "direction": "UP", "role": "PASSENGER"})
    (entry,) = client.get("/audit").json()
    assert (entry["status"], entry["reason"], entry["role"]) == (409, "INVALID_STATE", "PASSENGER")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"floor": 99, "direction": "UP"}, "FLOOR_OUT_OF_RANGE"),
        ({"floor": 0, "direction": "UP"}, "FLOOR_OUT_OF_RANGE"),
        ({"floor": 1, "direction": "DOWN"}, "TERMINAL_DIRECTION"),
        ({"floor": 40, "direction": "UP"}, "TERMINAL_DIRECTION"),
        ({"floor": 10, "direction": "IDLE"}, "INVALID_DIRECTION"),
    ],
)
def test_UI_02_D_18_impossible_hall_call_is_refused_at_once(client, control, body, reason):
    response = client.post("/hall-call", json=body)
    assert response.status_code == 409
    assert response.json() == {"ok": False, "id": None, "reason": reason}
    assert control.submitted == []
