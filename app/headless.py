"""Run a scenario without the UI, as fast as possible.

python -m app.headless --scenario AT-01
python -m app.headless --scenario AT-04 --cabin fake --log out.jsonl --trace trace.jsonl
python -m app.headless --replay trace.jsonl          # prints IDENTICAL or DIFFERENT
"""

import argparse
import sys
from pathlib import Path

import scenarios
from app.modules import CHOICES, describe, module_set
from app.replay import replay
from contracts.enums import ServiceEventType
from contracts.messages import MissionStatusUpdate, RouteReply, ServiceEvent
from simulation.engine import Engine

KEY_EVENTS = {
    ServiceEventType.HALL_CALL_SERVED,
    ServiceEventType.DEST_SERVED,
    ServiceEventType.DOOR_CLOSED_LOCKED,
    ServiceEventType.HANDOFF_OK,
    ServiceEventType.MISSION_PICKUP_DONE,
    ServiceEventType.MISSION_DELIVERY_DONE,
}


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.headless", description=__doc__)
    parser.add_argument("--scenario", choices=scenarios.names())
    parser.add_argument("--seed", type=int, default=0)
    for kind in ("dispatch", "cabin", "cc"):
        parser.add_argument(f"--{kind}", choices=CHOICES, default="auto")
    parser.add_argument("--until", type=float, help="stop at this simulated time")
    parser.add_argument("--log", type=Path, help="write the JSON-lines event log here")
    parser.add_argument("--trace", type=Path, help="save a replayable trace here")
    parser.add_argument("--replay", type=Path, help="replay a trace and compare the log")
    args = parser.parse_args(argv)
    if not args.scenario and not args.replay:
        parser.error("give --scenario NAME or --replay TRACE")
    return args


def summary(engine: Engine) -> list[str]:
    lines = [f"scenario {engine.scenario.name} (seed {engine.seed}): {engine.scenario.description}"]
    delivered = iter(engine.bus.delivered)
    for entry in engine.bus.log:  # same order as the log; messages and network events
        if entry.get("event") == "MESSAGE_DROPPED":
            dropped = entry["message"]
            lines.append(f"{entry['t_sim']:8.1f}  network dropped {dropped['msg_type']}")
            continue
        if "msg_type" not in entry:
            continue
        msg = next(delivered)
        if isinstance(msg, ServiceEvent) and msg.event in KEY_EVENTS:
            ref = msg.ref_id or msg.passenger_id or ""
            what = f"{msg.event.value:<22} floor {msg.floor:<3} {ref}"
            lines.append(f"{msg.t_sim:8.1f}  {msg.car_id:<3} {what}".rstrip())
        elif isinstance(msg, RouteReply) and not msg.accepted:
            lines.append(f"{msg.t_sim:8.1f}  {msg.car_id:<3} NACK {msg.command_id} {msg.reason}")
        elif isinstance(msg, MissionStatusUpdate):
            car = f" car {msg.car_id}" if msg.car_id else ""
            reason = f" ({msg.reason})" if msg.reason else ""
            lines.append(f"{msg.t_sim:8.1f}  {msg.mission_id} {msg.status.value}{car}{reason}")
            for c in msg.candidates:
                detail = f"ETA {c.eta_s:g} s" if c.eta_s is not None else f"excluded: {c.excluded}"
                lines.append(f"{'':12}{c.car_id}: {detail}")
    m = engine.metrics()
    lines.append(
        "metrics: " + ", ".join(f"{k}={v if v is None else round(v, 2)}" for k, v in m.items())
    )
    lines.append(
        f"state {engine.state.value} at t={engine.now:g}; log sha256 {engine.log_sha256()}"
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.replay:
        engine, identical = replay(args.replay)
        print(f"modules: {engine.module_set.kinds}")
        print("\n".join(summary(engine)))
        print("replay: IDENTICAL" if identical else "replay: DIFFERENT")
        return 0 if identical else 1

    modules, labels = module_set(args.dispatch, args.cabin, args.cc)
    print(describe(labels))
    scenario = scenarios.get(args.scenario, args.seed)
    engine = Engine(modules, scenario, args.seed, args.log)
    engine.start()
    engine.advance_to(args.until if args.until is not None else engine.end_t)
    print("\n".join(summary(engine)))
    if args.log:
        print(f"log: {args.log}")
    if args.trace:
        engine.save_trace(args.trace)
        print(f"trace: {args.trace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
