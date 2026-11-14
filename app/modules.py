"""Choose the real or the fake implementation of each module (D-60)."""

import importlib
from types import ModuleType

from contracts.config import Policy
from simulation.engine import ModuleSet

REAL = {"dispatch": "dispatch", "cabin": "cabin", "cc": "control_center"}
FAKE = {"dispatch": "fakes.dispatch", "cabin": "fakes.cabin", "cc": "fakes.control_center"}
CHOICES = ("auto", "real", "fake")


def load(kind: str, choice: str = "auto") -> tuple[ModuleType, str, str]:
    """Return (package, "real" | "fake", human-readable label)."""
    if choice not in CHOICES:
        raise ValueError(f"--{kind} must be one of {CHOICES}, not {choice!r}")
    real_name = REAL[kind]
    if choice == "fake":
        return importlib.import_module(FAKE[kind]), "fake", "fake"
    try:
        module = importlib.import_module(real_name)
    except ModuleNotFoundError as err:
        if err.name != real_name:
            raise
        if choice == "real":
            message = f"--{kind} real: {real_name}/ does not exist yet; use auto or fake"
            raise SystemExit(message) from None
        return importlib.import_module(FAKE[kind]), "fake", f"fake ({real_name}/ not found)"
    if not hasattr(module, "create"):
        raise ImportError(f"{real_name}/__init__.py must export create() (contracts/interfaces.py)")
    return module, "real", "real"


def module_set(
    dispatch: str = "auto", cabin: str = "auto", cc: str = "auto"
) -> tuple[ModuleSet, dict[str, str]]:
    """Build the ModuleSet for the engine plus a label per module for printing."""
    d_mod, d_kind, d_label = load("dispatch", dispatch)
    c_mod, c_kind, c_label = load("cabin", cabin)
    x_mod, x_kind, x_label = load("cc", cc)

    def make_dispatch(policy: Policy):
        return d_mod.create(policy)

    modules = ModuleSet(
        dispatch=make_dispatch,
        cabin=c_mod.create,
        cc=x_mod.create,
        kinds={"dispatch": d_kind, "cabin": c_kind, "cc": x_kind},
    )
    return modules, {"dispatch": d_label, "cabin": c_label, "cc": x_label}


def describe(labels: dict[str, str]) -> str:
    return " | ".join(f"{kind}: {label}" for kind, label in labels.items())
