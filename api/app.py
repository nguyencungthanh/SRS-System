"""FastAPI application (HY-05): the only way the browser talks to the simulation.

``create_app(control)`` is called by ``app/__main__.py`` with An's ``SimRunner``. The API
only knows the ``contracts.runtime.SimControl`` interface, never the engine or a module.

Endpoints
    POST <path>          one per ``contracts.ui.COMMANDS`` row (role check -> message ->
                         ``control.submit``) or engine command (``control.control``)
    POST /sim/speed      (addition) engine speed factor; SimCommand.SPEED had no endpoint
    GET  /snapshot       current Snapshot once
    GET  /scenarios      (addition) scenario names accepted by /sim/reset
    WS   /ws             Snapshot on connect and after every processed timestamp
    GET  /               the UI in web/ (static files), if the folder exists

Every POST answers a ``contracts.ui.CommandResult`` {ok, id, reason}:
    200 ok                     accepted (id = queued msg_id, or the engine's result)
    403 UNAUTHORIZED_ROLE      wrong role; nothing reaches the simulation
    409 <RejectReason>         the simulation refused (e.g. INVALID_STATE when stopped)
    422 reason=None + detail   the body is malformed (Huy's decision, 14/11)
    501 reason=None + detail   /optimization/run: no SimControl method yet (gap sent to An)
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.commands import BadRequest, Refused, build_message, check_role, parse_role
from contracts.runtime import SimCommand, SimControl
from contracts.ui import COMMANDS, SNAPSHOT_GET, SNAPSHOT_WS, Command, CommandResult, Snapshot

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

SPEED_PATH = "/sim/speed"
SCENARIOS_PATH = "/scenarios"
OPTIMIZATION_PATH = "/optimization/run"

# Engine commands: path -> (SimCommand, body keys passed to control()).
ENGINE_COMMANDS: dict[str, tuple[SimCommand, tuple[str, ...]]] = {
    "/sim/start": (SimCommand.START, ()),
    "/sim/pause": (SimCommand.PAUSE, ()),
    "/sim/reset": (SimCommand.RESET, ("scenario", "seed")),
    "/sim/replay": (SimCommand.REPLAY, ("trace", "seed")),
}

HTTP_OK, HTTP_FORBIDDEN, HTTP_CONFLICT = 200, 403, 409
HTTP_UNPROCESSABLE, HTTP_NOT_IMPLEMENTED = 422, 501


def create_app(control: SimControl, web_dir: Path | None = WEB_DIR) -> FastAPI:
    app = FastAPI(title="Smart Elevator System", version="1.0")
    by_path = {cmd.path: cmd for cmd in COMMANDS}

    for cmd in COMMANDS:
        if cmd.message is not None:
            app.add_api_route(cmd.path, _message_endpoint(control, cmd), methods=["POST"])
        elif cmd.path in ENGINE_COMMANDS:
            app.add_api_route(cmd.path, _engine_endpoint(control, cmd), methods=["POST"])

    app.add_api_route(
        OPTIMIZATION_PATH, _optimization_endpoint(by_path[OPTIMIZATION_PATH]), methods=["POST"]
    )
    speed_cmd = Command(SPEED_PATH, None, by_path["/sim/start"].roles, "body: {factor}")
    app.add_api_route(SPEED_PATH, _speed_endpoint(control, speed_cmd), methods=["POST"])

    @app.get(SNAPSHOT_GET)
    def get_snapshot() -> JSONResponse:
        return JSONResponse(control.snapshot())

    @app.get(SCENARIOS_PATH)
    def get_scenarios() -> JSONResponse:
        return JSONResponse(control.scenarios())

    @app.websocket(SNAPSHOT_WS)
    async def snapshot_stream(ws: WebSocket) -> None:
        await ws.accept()
        subscription: dict[str, Any] = {"unsubscribe": None}

        def stop() -> None:
            # SimControl does not promise that unsubscribing twice is safe: do it once.
            unsubscribe, subscription["unsubscribe"] = subscription["unsubscribe"], None
            if unsubscribe is not None:
                unsubscribe()

        async def push(snap: Snapshot) -> None:
            # SimRunner.publish() does not catch errors from subscribers: a socket that
            # closed between two ticks must not stop the simulation loop for everyone.
            try:
                await ws.send_json(snap)
            except Exception:  # noqa: BLE001 - any send failure means this client is gone
                stop()

        subscription["unsubscribe"] = control.subscribe(push)
        try:
            await push(control.snapshot())
            while True:
                await ws.receive_text()  # the client sends nothing; this waits for close
        except WebSocketDisconnect:
            pass
        finally:
            stop()

    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


# --- endpoint factories ------------------------------------------------------------------


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
        return _from_sim(control.submit(msg))

    endpoint.__name__ = _route_name(cmd.path)
    return endpoint


def _engine_endpoint(control: SimControl, cmd: Command):
    command, keys = ENGINE_COMMANDS[cmd.path]

    async def endpoint(request: Request) -> JSONResponse:
        try:
            body = await _json_body(request)
            check_role(cmd, parse_role(body))
        except Refused as err:
            return _result(HTTP_FORBIDDEN, ok=False, reason=err.reason.value)
        except BadRequest as err:
            return _result(HTTP_UNPROCESSABLE, ok=False, detail=str(err))
        args = {k: body[k] for k in keys if body.get(k) is not None}
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


async def _json_body(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        body = await request.json()
    except ValueError as err:
        raise BadRequest("the body is not valid JSON") from err
    if not isinstance(body, dict):
        raise BadRequest("the body must be a JSON object")
    return body


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
