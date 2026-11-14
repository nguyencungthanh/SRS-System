"""The interface every module implements (framework F4).

Each module is a pure message handler:

* ``handle(msg, now)`` reacts to ONE delivered message and returns the messages it emits
  (possibly ``[]``). It never calls another module; the engine delivers everything.
* Emitted messages use ``t_sim=now`` and leave ``msg_id=""`` and ``deliver_at=None``,
  except ``Timer`` (see ``contracts.messages.Timer``).
* No wall-clock time, ``sleep`` or unseeded randomness (CONTRIBUTING.md, Section 8).
* ``handle()`` must not mutate ``msg``.
* ``snapshot()`` returns a plain, JSON-serialisable dict whose shape is fixed in
  ``contracts.ui`` (DispatchSnapshot, CabinSnapshot, ControlCenterSnapshot). It must not
  change module state.
* ``name`` is the module's routing address (``contracts.routing``).

How the engine builds modules: each owner's package exports a ``create`` function with the
matching factory signature below. Fakes in ``fakes/`` export the same signatures, so the
engine can swap real and fake modules with a CLI flag.

    dispatch.create(policy, algorithm)  -> DispatchModule
    cabin.create(init)                  -> CabinModule   (called once per car)
    control_center.create()             -> ControlCenterModule
"""

from typing import Any, Protocol, runtime_checkable

from contracts.config import DEFAULT_POLICY, Policy
from contracts.enums import DispatchAlgorithm
from contracts.messages import CabinInit, Message


@runtime_checkable
class Module(Protocol):
    name: str

    def handle(self, msg: Message, now: float) -> list[Message]: ...

    def snapshot(self) -> dict[str, Any]: ...


@runtime_checkable
class DispatchModule(Module, Protocol):
    """``name == "dispatch"``. ``snapshot()`` returns ``contracts.ui.DispatchSnapshot``."""


@runtime_checkable
class CabinModule(Module, Protocol):
    """``name == "cabin:<car_id>"``. ``snapshot()`` returns ``contracts.ui.CabinSnapshot``."""

    car_id: str


@runtime_checkable
class ControlCenterModule(Module, Protocol):
    """``name == "cc"``. ``snapshot()`` returns ``contracts.ui.ControlCenterSnapshot``."""


class DispatchFactory(Protocol):
    def __call__(
        self,
        policy: Policy = DEFAULT_POLICY,
        algorithm: DispatchAlgorithm = DispatchAlgorithm.COMMITMENT,
    ) -> DispatchModule: ...


class CabinFactory(Protocol):
    def __call__(self, init: CabinInit) -> CabinModule: ...


class ControlCenterFactory(Protocol):
    def __call__(self) -> ControlCenterModule: ...
