"""Read a trace file and replay it (D-59)."""

import json
from pathlib import Path
from typing import Any

import scenarios
from app.modules import module_set
from simulation.engine import Engine, ModuleSet


def read_trace(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    lines = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    header = next(line for line in lines if line["type"] == "header")
    footer = next(line for line in lines if line["type"] == "footer")
    inputs = [line["message"] for line in lines if line["type"] == "input"]
    return header, inputs, footer


def replay(path: Path, modules: ModuleSet | None = None) -> tuple[Engine, bool]:
    """Re-run a trace with the module kinds it recorded. Returns (engine, identical)."""
    header, inputs, footer = read_trace(path)
    if modules is None:
        modules, _ = module_set(**header["modules"])
    scenario = scenarios.get(header["scenario"], header["seed"])
    engine = Engine(modules, scenario, header["seed"])
    engine.replay_inputs(inputs, footer["end_t"])
    return engine, engine.log_sha256() == footer["log_sha256"]
