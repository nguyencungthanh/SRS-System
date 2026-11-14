"""Web server + UI: ``python -m app`` (then open http://localhost:8000).

Needs Huy's ``api`` package, which exports ``create_app(control: SimControl) -> FastAPI``
(contracts/runtime.py, D-58). Until it exists this prints how to run headless instead.
"""

import argparse
import asyncio
import importlib
import sys

import scenarios
from app.modules import CHOICES, describe, module_set
from app.runner import SimRunner


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app", description=__doc__)
    for kind in ("dispatch", "cabin", "cc"):
        parser.add_argument(f"--{kind}", choices=CHOICES, default="auto")
    parser.add_argument("--scenario", choices=scenarios.names(), default="DEMO")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


async def serve(app, runner: SimRunner, host: str, port: int) -> None:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
    loop_task = asyncio.create_task(runner.run())
    try:
        await server.serve()
    finally:
        runner.close()
        await loop_task


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    modules, labels = module_set(args.dispatch, args.cabin, args.cc)
    print(describe(labels))
    try:
        api = importlib.import_module("api")
    except ModuleNotFoundError as err:
        if err.name != "api":
            raise
        print(
            "api/ does not exist yet (Huy, HY-05). Run a scenario without the UI instead:\n"
            "    python -m app.headless --scenario AT-01",
            file=sys.stderr,
        )
        return 2
    runner = SimRunner(modules, args.scenario, args.seed, args.speed)
    asyncio.run(serve(api.create_app(runner), runner, args.host, args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
