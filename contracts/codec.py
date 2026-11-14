"""Message <-> JSON conversion for the event log and replay (LOG-02, NFR-05).

``to_dict`` adds a ``"msg_type"`` key with the class name; ``from_dict`` reads it back.
Enums become their string value; nested value types (Stop, Candidate, Policy, ...) become
plain dicts; tuples become lists. ``from_dict(to_dict(m)) == m`` for every message.
"""

import json
import types
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Union, get_args, get_origin, get_type_hints

from contracts.messages import ALL_MESSAGES, Message

MESSAGE_TYPES: dict[str, type[Message]] = {cls.__name__: cls for cls in ALL_MESSAGES}
TYPE_KEY = "msg_type"


def to_dict(msg: Message) -> dict[str, Any]:
    out: dict[str, Any] = {TYPE_KEY: type(msg).__name__}
    for f in fields(msg):
        out[f.name] = _encode(getattr(msg, f.name))
    return out


def from_dict(data: dict[str, Any]) -> Message:
    cls = MESSAGE_TYPES[data[TYPE_KEY]]
    body = {k: v for k, v in data.items() if k != TYPE_KEY}
    return _decode_dataclass(cls, body)


def to_json(msg: Message) -> str:
    """One compact JSON line, keys sorted so identical messages give identical text."""
    return json.dumps(to_dict(msg), sort_keys=True, separators=(",", ":"))


def from_json(line: str) -> Message:
    return from_dict(json.loads(line))


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple | list):
        return [_encode(v) for v in value]
    raise TypeError(f"cannot encode {type(value).__name__}: {value!r}")


def _decode_dataclass(cls: type, data: dict[str, Any]) -> Any:
    hints = get_type_hints(cls)
    kwargs = {f.name: _decode(hints[f.name], data[f.name]) for f in fields(cls) if f.name in data}
    return cls(**kwargs)


def _decode(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        options = [a for a in get_args(tp) if a is not type(None)]
        for option in options:
            try:
                return _decode(option, value)
            except (TypeError, ValueError, KeyError):
                continue
        raise ValueError(f"{value!r} does not match {tp}")
    if origin is tuple:
        item_type = get_args(tp)[0]
        return tuple(_decode(item_type, v) for v in value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if is_dataclass(tp):
        if not isinstance(value, dict):
            raise TypeError(f"expected an object for {tp.__name__}, got {value!r}")
        return _decode_dataclass(tp, value)
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"expected a number, got {value!r}")
        return float(value)
    if tp is bool:
        if not isinstance(value, bool):
            raise TypeError(f"expected a bool, got {value!r}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"expected an int, got {value!r}")
        return value
    if tp is str:
        if not isinstance(value, str):
            raise TypeError(f"expected a string, got {value!r}")
        return value
    raise TypeError(f"unsupported field type {tp!r}")
