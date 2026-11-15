"""Turn an HTTP request body into a module message (HY-05).

One generic path for every row of ``contracts.ui.COMMANDS`` that has a message type, so
there is no hand-written code per endpoint:

    1. role check against ``Command.roles`` (D-53). The role comes from the body's "role"
       key. For messages that carry a role (MissionRequest, RecoveryRequest, ...) it is
       also passed into the message, so Control Center checks it a second time.
    2. type check of plain fields (int / float / str / bool). ``contracts.codec`` converts
       enums and nested types but passes plain values through unchanged, so without this a
       floor sent as "abc" would reach a module and crash it. Non-finite numbers (NaN,
       Infinity) are refused earlier, when the body is parsed (``parse_body``).
       A missing field whose type allows None is passed as None, so the module answers
       with its own reason (e.g. MISSING_FIELD); any other missing field is a 422.
    3. ``contracts.codec.from_dict`` builds the message. ``t_sim`` is a placeholder:
       ``SimControl.submit`` stamps the real simulation time.
"""

import json
import math
import types
from dataclasses import MISSING, dataclass, fields
from typing import Any, Union, get_args, get_origin, get_type_hints

from contracts.codec import TYPE_KEY, from_dict
from contracts.enums import RejectReason, Role
from contracts.messages import BASE_FIELDS, Message
from contracts.ui import Command

ROLE_KEY = "role"
PLAIN_TYPES: tuple[type, ...] = (bool, int, float, str)


class BadRequest(Exception):
    """The body cannot become a valid message (missing field, wrong type, bad enum)."""


@dataclass(frozen=True)
class Refused(Exception):
    """The caller is not allowed to send this command."""

    reason: RejectReason


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # e.g. 1e400 parses to inf
        raise BadRequest(f"number out of range: {text}")
    return value


def _no_constant(name: str) -> float:
    raise BadRequest(f"{name} is not allowed")  # NaN, Infinity, -Infinity


def parse_body(raw: bytes) -> dict[str, Any]:
    """Parse a JSON object body. Refuses NaN / Infinity (Python's json accepts them, but they
    would poison module state and make the snapshot invalid JSON) and very deep nesting."""
    if not raw:
        return {}
    try:
        body = json.loads(raw, parse_float=_finite_float, parse_constant=_no_constant)
    except BadRequest:
        raise
    except RecursionError as err:
        raise BadRequest("the body is nested too deeply") from err
    except ValueError as err:  # includes JSONDecodeError and UnicodeDecodeError
        raise BadRequest("the body is not valid JSON") from err
    if not isinstance(body, dict):
        raise BadRequest("the body must be a JSON object")
    return body


def parse_role(body: dict[str, Any]) -> Role | None:
    raw = body.get(ROLE_KEY)
    if raw is None:
        return None
    try:
        return Role(raw)
    except ValueError as err:
        raise BadRequest(f"unknown role {raw!r}") from err


def require(body: dict[str, Any], key: str, tp: type) -> Any:
    """An optional engine-command argument of exactly type ``tp`` (bool is not an int)."""
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, tp):
        raise BadRequest(f"{key}: expected {tp.__name__}, got {type(value).__name__}")
    return value


def check_role(cmd: Command, role: Role | None) -> None:
    """D-53: refuse before anything reaches the simulation."""
    if cmd.roles is not None and role not in cmd.roles:
        raise Refused(RejectReason.UNAUTHORIZED_ROLE)


def build_message(cmd: Command, body: dict[str, Any]) -> Message:
    assert cmd.message is not None, f"{cmd.path} is an engine command, not a message"
    cls = cmd.message
    hints = get_type_hints(cls)
    own = [f for f in fields(cls) if f.name not in BASE_FIELDS]
    data = {f.name: body[f.name] for f in own if f.name in body}
    for f in own:
        if f.name in data or f.default is not MISSING or f.default_factory is not MISSING:
            continue
        if type(None) in get_args(hints[f.name]):
            data[f.name] = None  # the module decides (e.g. MISSING_FIELD), same as null
        else:
            raise BadRequest(f"missing field: {f.name}")
    for name, value in data.items():
        _check_plain(name, hints[name], value)
    try:
        return from_dict({TYPE_KEY: cls.__name__, **data, "t_sim": 0.0})
    except (TypeError, ValueError, KeyError) as err:
        raise BadRequest(f"{cls.__name__}: {err}") from err


def _check_plain(name: str, hint: Any, value: Any) -> None:
    """Reject a plain field of the wrong JSON type. Enums / dataclasses: left to the codec."""
    if value is None:
        return  # missing optional values are decided by the module (e.g. MISSING_FIELD)
    options = _non_none(hint)
    plain = [t for t in options if t in PLAIN_TYPES]
    if not plain or len(plain) != len(options):
        return
    if isinstance(value, bool) and bool not in plain:
        raise BadRequest(f"{name}: expected {_names(plain)}, got a boolean")
    if any(_fits(t, value) for t in plain):
        return
    raise BadRequest(f"{name}: expected {_names(plain)}, got {type(value).__name__}")


def _non_none(hint: Any) -> list[Any]:
    if get_origin(hint) in (Union, types.UnionType):
        return [a for a in get_args(hint) if a is not type(None)]
    return [hint]


def _fits(tp: type, value: Any) -> bool:
    if tp is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, tp)


def _names(types_: list[type]) -> str:
    return " or ".join(t.__name__ for t in types_)
