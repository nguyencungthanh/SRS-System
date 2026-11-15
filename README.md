# Smart Elevator System - simulator

Software-only simulator of the Smart Elevator System described in [docs/SRS-Group2-v1.1.pdf](docs/SRS-Group2-v1.1.pdf): 40 floors, passenger cars P1-P4, goods car G1, a Dispatch controller, one Cabin controller per car, a Control Center, a deterministic simulation engine, a web UI and offline SATSA tuning.

| Read this | For |
|---|---|
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | what we build, who builds it, acceptance tests |
| [CONTRIBUTING.md](CONTRIBUTING.md) | folders, Git, PRs, working with AI |
| [DECISIONS.md](DECISIONS.md) | every value the SRS leaves open (D-xx) |
| [AI_CONTEXT.md](AI_CONTEXT.md) | what to paste into your AI before you start |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python >= 3.12
pip install -r requirements.txt
```

## Commands

Every command below was run on the current `main` content; the comment shows what to expect.

```bash
pytest                                     # all tests (about 4 minutes)
```

```bash
pytest --fakes                             # also runs the fake_ok contract tests against fakes/
```

```bash
pytest tests/contract/test_cabin.py        # one module's exam
```

```bash
ruff check . && ruff format --check .      # lint + format check
```

```bash
python -m app.headless --scenario AT-01    # 8.0 HALL_CALL_SERVED, 58.0 DEST_SERVED, 64.0 doors closed at floor 5
```

```bash
python -m app.headless --scenario AT-03 --trace trace.jsonl --log log.jsonl
```

```bash
python -m app.headless --replay trace.jsonl   # prints "replay: IDENTICAL"
```

```bash
python -m app                              # web UI on http://localhost:8000 (DEMO scenario)
```

```bash
python -m optimization --method compare --quick   # AT-06 on one short trace each, about 20 s
```

```bash
python -m optimization --method compare           # AT-06 in full (55 pairs x 5 traces), about 6 minutes on 8 cores; writes results/
```

Module flags for both entry points: `--dispatch`, `--cabin`, `--cc` = `auto` (default: the real package if it exists, otherwise the fake) | `real` | `fake`. Example: `python -m app.headless --scenario AT-01 --cabin real --dispatch fake --cc fake`.

Scenarios: `AT-01`, `AT-02`, `AT-03`, `AT-04`, `AT-05-ack-lost`, `AT-05-unsafe`, `DEMO`, `TRAIN-1`..`TRAIN-5`, `HOLDOUT-101`..`HOLDOUT-105`, `QUICK-1`, `RANDOM` (uses the seed given to reset). See `scenarios/`.

## Repository layout

| Folder | Owner | What |
|---|---|---|
| `contracts/` | An | messages, enums, config, routing, interfaces, UI and runtime contracts |
| `fakes/` | An | simple stand-ins for each module (not reference implementations) |
| `simulation/` | An | bus, engine (states, reset, log, replay), virtual passengers, scenario types |
| `scenarios/` | An | AT-01..AT-05, DEMO, seeded random traffic (training / held-out) |
| `app/` | An | `python -m app`, `python -m app.headless`, real-time runner |
| `optimization/` | An | metrics, evaluator, grid search, SATSA, AT-06 report (`python -m optimization`) |
| `results/` | An | committed output of the full AT-06 study |
| `tests/contract/`, `tests/engine/`, `tests/optimization/` | An | contract exams for each module; engine, app and optimization tests |
| `dispatch/`, `tests/dispatch/` | Thanh | Dispatch controller |
| `cabin/`, `tests/cabin/` | Hai | Cabin controller |
| `control_center/`, `api/`, `web/`, `tests/control_center/` | Huy | Control Center, REST/WebSocket API, UI |

## Status

| Part | Status |
|---|---|
| Framework F1-F11: contracts, fakes, contract tests, engine, app, AI context (An) | done, merged, contract v1 frozen |
| Dispatch (Thanh), Cabin (Hai), Control Center + API + UI (Huy) | done; every contract test passes on the real modules |
| System tests across all real modules (Hai, `tests/cabin/test_system_*.py`) | done |
| Metrics, random traffic, evaluator, grid search, SATSA, UI optimization flow (AN-03..AN-07) | done |
| Real gRPC transport, real authentication, real-world fire procedures | deferred by design (PROJECT_PLAN Section 1) |

Acceptance tests on the **real** modules (`python -m app.headless --scenario <name>`):

| Test | SRS oracle | Result |
|---|---|---|
| AT-01 | pickup 8 s, arrival at 5 at 58 s, doors closed 64 s | PASS (8 / 58 / 64) |
| AT-02 | 29 picked up at 4 s, 27 at 14 s | PASS (4 / 14) |
| AT-03 | P1 = 90, P2 = 20, P4 = 58; P2 chosen; P3, G1 excluded | PASS |
| AT-04 | 44 / 50 / 58 / 64 / 122 s, no ordinary stops on the leg | PASS (LOADED confirmed at 60 s) |
| AT-05 | lost ACK / unsafe cabin: never two owners | PASS (both variants) |
| AT-06 | every (R, W) valid or rejected; SATSA vs grid vs (3, 5) on held-out traces | PASS: all 55 pairs valid, comparison in [results/optimization.md](results/optimization.md) |

`pytest` on the merged code: 617 passed, 1 skipped (the "no api/ yet" check). The full suite takes about 4 minutes because of the system stress tests.

## Tuning results (AT-06, held-out traces)

From [results/optimization.md](results/optimization.md) (5 training and 5 held-out traces of 30 minutes, real modules). Lower J is better.

| Configuration | J (held-out) | Mean wait | P95 wait |
|---|---|---|---|
| Fixed (R, W) = (3, 5) | 119.4 | 53.2 s | 132.3 s |
| Grid search best (5, 7) | 114.9 | 51.6 s | 126.6 s |
| SATSA best, seed 0: (1, 0) | 123.1 | 55.0 s | 136.1 s |
| SWEEP baseline | 102.6 | 45.6 s | 113.9 s |
| FIRST_IDLE baseline | 316.8 | 165.1 s | 303.4 s |

What this shows, without over-claiming:

- Tuning (R, W) by grid search lowered held-out J by about 4 % against the fixed (3, 5).
- SATSA used 30 simulated pairs instead of 55. With seed 0 it stopped on a flat region and its choice did worse than (3, 5) on held-out traces; over 20 seeds it found the grid's optimum 12 times. The J surface is flat in large areas, which suits an exhaustive search of this small domain better.
- The directional SWEEP baseline waited less than our commitment-aware dispatcher on this traffic. It also delayed committed stops more often (late commitments 158 vs 145 over the 5 traces). Our dispatcher never broke DELAY_CAP in any run.
- No configuration was rejected; no run had a double owner or a door-interlock breach.

