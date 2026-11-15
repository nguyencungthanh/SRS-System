"""Offline tuning of (R, W).

python -m optimization --method compare            # AT-06: grid, SATSA, held-out table
python -m optimization --method compare --quick    # one short trace each, about a minute
python -m optimization --method grid|satsa         # train one method, print its choice
"""

import argparse
import sys
from pathlib import Path

from app.modules import CHOICES
from optimization.report import METHODS, Inputs, compare, optimize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m optimization", description=__doc__)
    parser.add_argument("--method", choices=[m.lower() for m in METHODS] + ["compare"])
    parser.add_argument("--quick", action="store_true", help="short traces, one seed each")
    parser.add_argument("--workers", type=int, default=None, help="processes (default: all)")
    parser.add_argument("--modules", choices=CHOICES, default="auto")
    parser.add_argument("--satsa-seed", type=int, default=0)
    parser.add_argument("--budget", type=int, default=None, help="SATSA unique pairs (D-35)")
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    if args.method is None:
        parser.error("give --method compare, grid or satsa")

    base = Inputs.standard(args.quick)
    inputs = Inputs(
        base.train_seeds,
        base.holdout_seeds,
        base.trace_s,
        budget=args.budget or base.budget,
        satsa_seed=args.satsa_seed,
    )
    try:
        inputs.validate()
    except ValueError as err:  # OPT-01: an incomplete request is rejected
        print(f"rejected: {err}", file=sys.stderr)
        return 2

    if args.method == "compare":
        compare(args.quick, args.workers, args.modules, args.out, inputs)
        return 0
    outcome = optimize(args.method.upper(), args.quick, args.workers, args.modules, inputs=inputs)
    print(
        f"{outcome.method}: best (R, W) = {outcome.best}, J = {outcome.objective}, "
        f"reference (3, 5) J = {outcome.baseline_objective}, "
        f"unique pairs simulated = {outcome.unique_evaluations}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
