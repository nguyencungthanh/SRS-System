"""FastAPI application (HY-05): the only way the browser talks to the simulation.

``create_app(control)`` is called by ``app/__main__.py`` with An's ``SimRunner``. The API
only knows the ``contracts.runtime.SimControl`` interface, never the engine or a module.

Endpoints
    POST <path>          one per ``contracts.ui.COMMANDS`` row (role check -> message ->
                         ``control.submit``) or engine command (``control.control``)
    POST /sim/speed      (addition) engine speed factor; SimCommand.SPEED had no endpoint
    GET  /snapshot       current Snapshot once
    GET  /scenarios      (addition) scenario names accepted by /sim/reset
    GET  /audit          (addition) refused requests with simulation time (SRS 5.3, F5)
    WS   /ws             Snapshot on connect and after every processed timestamp; a slow
                         client only falls behind itself (api/stream.py)
    GET  /               the UI in web/ (static files), if the folder exists

Every POST answers a ``contracts.ui.CommandResult`` {ok, id, reason}:
    200 ok                     accepted (id = queued msg_id, or the engine's result)
    403 UNAUTHORIZED_ROLE      wrong role; nothing reaches the simulation
    409 <RejectReason>         the simulation refused (e.g. INVALID_STATE when stopped)
    422 reason=None + detail   the body is malformed (Huy's decision, 14/11)
    501 reason=None + detail   /optimization/run: no SimControl method yet (gap sent to An)
"""

import json
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.audit import RefusalLog
from api.commands import (
    BadRequest,
    Refused,
    build_message,
    check_role,
    hall_call_problem,
    parse_body,
    parse_role,
    require,
)
from api.stream import ClientFeed
from contracts.runtime import SimCommand, SimControl
from contracts.ui import COMMANDS, SNAPSHOT_GET, SNAPSHOT_WS, Command, CommandResult, Snapshot

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

SPEED_PATH = "/sim/speed"
SCENARIOS_PATH = "/scenarios"
AUDIT_PATH = "/audit"
OPTIMIZATION_PATH = "/optimization/run"

# Engine commands: path -> (SimCommand, {body key: required type}) passed to control().
ENGINE_COMMANDS: dict[str, tuple[SimCommand, dict[str, type]]] = {
    "/sim/start": (SimCommand.START, {}),
    "/sim/pause": (SimCommand.PAUSE, {}),
    "/sim/reset": (SimCommand.RESET, {"scenario": str, "seed": int}),
    "/sim/replay": (SimCommand.REPLAY, {"trace": str, "seed": int}),
}

HTTP_OK, HTTP_FORBIDDEN, HTTP_CONFLICT = 200, 403, 409
HTTP_UNPROCESSABLE, HTTP_NOT_IMPLEMENTED = 422, 501


def create_app(control: SimControl, web_dir: Path | None = WEB_DIR) -> FastAPI:
    app = FastAPI(title="Smart Elevator System", version="1.0")
    by_path = {cmd.path: cmd for cmd in COMMANDS}

    refusals = RefusalLog()

    def post(path: str, endpoint) -> None:
        app.add_api_route(path, _audited(endpoint, path, control, refusals), methods=["POST"])

    for cmd in COMMANDS:
        if cmd.message is not None:
            post(cmd.path, _message_endpoint(control, cmd))
        elif cmd.path in ENGINE_COMMANDS:
            post(cmd.path, _engine_endpoint(control, cmd))

    post(OPTIMIZATION_PATH, _optimization_endpoint(by_path[OPTIMIZATION_PATH]))
    speed_cmd = Command(SPEED_PATH, None, by_path["/sim/start"].roles, "body: {factor}")
    post(SPEED_PATH, _speed_endpoint(control, speed_cmd))

    @app.get(AUDIT_PATH)
    async def get_audit() -> JSONResponse:
        return JSONResponse(refusals.entries())

    # async: a plain ``def`` would run in a worker thread while the simulation loop changes
    # module state on the event-loop thread ("dictionary changed size during iteration").
    @app.get(SNAPSHOT_GET)
    async def get_snapshot() -> JSONResponse:
        return JSONResponse(control.snapshot())

    @app.get(SCENARIOS_PATH)
    async def get_scenarios() -> JSONResponse:
        return JSONResponse(control.scenarios())

    @app.websocket(SNAPSHOT_WS)
    async def snapshot_stream(ws: WebSocket) -> None:
        await ws.accept()
        feed = ClientFeed()

        async def push(snap: Snapshot) -> None:
            feed.offer(snap)  # called by SimRunner.publish(): must never wait for this socket

        unsubscribe = control.subscribe(push)
        feed.offer(control.snapshot())
        try:
            # anyio (Starlette's own concurrency layer), not raw asyncio tasks: the server
            # and the test client cancel the app through anyio cancel scopes, and raw task
            # cancellation inside them leaked a CancelledError (flaky test on Windows).
            async with anyio.create_task_group() as tg:

                async def send_all() -> None:
                    try:
                        await feed.run(ws.send_json)
                    except Exception:  # noqa: BLE001 - a failed send means the client is gone
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(send_all)
                await _until_closed(ws)
                tg.cancel_scope.cancel()
        finally:
            unsubscribe()  # exactly once, whichever side ended first

    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


# --- endpoint factories ------------------------------------------------------------------

HTTP_REFUSED = frozenset({403, 409})


def _audited(endpoint, path: str, control: SimControl, refusals: RefusalLog):
    """Record every refused request (403 wrong role, 409 refused by the simulation)."""

    async def wrapped(request: Request) -> JSONResponse:
        response = await endpoint(request)
        if response.status_code in HTTP_REFUSED:
            try:
                role = parse_body(await request.body()).get("role")  # body is cached
            except BadRequest:
                role = None
            reason = json.loads(response.body).get("reason")
            refusals.record(
                control.snapshot()["t_sim"],
                path,
                role if isinstance(role, str) else None,
                response.status_code,
                reason,
            )
        return response

    wrapped.__name__ = endpoint.__name__
    return wrapped


def _message_endpoint(control: SimControl, cmd: Command):
    async def endpoint(request: Request) -> JSONResponse:
        try:
            body = await _json_body(request)
            check_role(cmd, parse_role(body))
            msg = build_message(cmd, body)
        except Refused as err:
            return _result(HTTP_FORBIDDEN, ok=False, reason=err.reason.value)
        except BadRequest as err:
            return _result(HTTP_UNPROCESSABLE, ok=False, detail=str(err))
        problem = hall_call_problem(msg)
        if problem is not None:
            return _result(HTTP_CONFLICT, ok=False, reason=problem.value)
        return _from_sim(control.submit(msg))

    endpoint.__name__ = _route_name(cmd.path)
    return endpoint


def _engine_endpoint(control: SimControl, cmd: Command):
    command, arg_types = ENGINE_COMMANDS[cmd.path]

    async def endpoint(request: Request) -> JSONResponse:
        try:
            body = await _json_body(request)
            check_role(cmd, parse_role(body))
            args = {k: require(body, k, tp) for k, tp in arg_types.items()}
        except Refused as err:
            return _result(HTTP_FORBIDDEN, ok=False, reason=err.reason.value)
        except BadRequest as err:
            return _result(HTTP_UNPROCESSABLE, ok=False, detail=str(err))
        args = {k: v for k, v in args.items() if v is not None}
        return _from_sim(control.control(command, **args))

    endpoint.__name__ = _route_name(cmd.path)
    return endpoint


def _speed_endpoint(control: SimControl, cmd: Command):
    async def endpoint(request: Request) -> JSONResponse:
        try:
            body = await _json_body(request)
            check_role(cmd, parse_role(body))
            factor = body.get("factor")
            if isinstance(factor, bool) or not isinstance(factor, (int, float)):
                raise BadRequest("factor: expected a number")
        except Refused as err:
            return _result(HTTP_FORBIDDEN, ok=False, reason=err.reason.value)
        except BadRequest as err:
            return _result(HTTP_UNPROCESSABLE, ok=False, detail=str(err))
        return _from_sim(control.control(SimCommand.SPEED, factor=float(factor)))

    endpoint.__name__ = "sim_speed"
    return endpoint


def _optimization_endpoint(cmd: Command):
    async def endpoint(request: Request) -> JSONResponse:
        try:
            check_role(cmd, parse_role(await _json_body(request)))
        except Refused as err:
            return _result(HTTP_FORBIDDEN, ok=False, reason=err.reason.value)
        except BadRequest as err:
            return _result(HTTP_UNPROCESSABLE, ok=False, detail=str(err))
        return _result(
            HTTP_NOT_IMPLEMENTED,
            ok=False,
            detail="SimControl has no way to start an optimization run yet (asked An)",
        )

    endpoint.__name__ = "optimization_run"
    return endpoint


# --- helpers -----------------------------------------------------------------------------


async def _until_closed(ws: WebSocket) -> None:
    """The client sends nothing; this returns when it disconnects."""
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        return


async def _json_body(request: Request) -> dict[str, Any]:
    return parse_body(await request.body())


def _from_sim(result: CommandResult) -> JSONResponse:
    status = HTTP_OK if result["ok"] else HTTP_CONFLICT
    return JSONResponse(dict(result), status_code=status)


def _result(
    status: int,
    *,
    ok: bool,
    ident: str | None = None,
    reason: str | None = None,
    detail: str | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {"ok": ok, "id": ident, "reason": reason}
    if detail is not None:
        content["detail"] = detail
    return JSONResponse(content, status_code=status)


def _route_name(path: str) -> str:
    return path.strip("/").replace("/", "_").replace("-", "_")
