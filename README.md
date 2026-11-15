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
python -m app                              # web UI on http://localhost:8000 once api/ exists; until then prints a hint and exits 2
```

Module flags for both entry points: `--dispatch`, `--cabin`, `--cc` = `auto` (default: the real package if it exists, otherwise the fake) | `real` | `fake`. Example: `python -m app.headless --scenario AT-01 --cabin real --dispatch fake --cc fake`.

Scenarios: `AT-01`, `AT-02`, `AT-03`, `AT-04`, `AT-05-ack-lost`, `AT-05-unsafe`, `DEMO` (see `scenarios/`).

## Repository layout

| Folder | Owner | What |
|---|---|---|
| `contracts/` | An | messages, enums, config, routing, interfaces, UI and runtime contracts |
| `fakes/` | An | simple stand-ins for each module (not reference implementations) |
| `simulation/` | An | bus, engine (states, reset, log, replay), virtual passengers, scenario types |
| `scenarios/` | An | AT-01..AT-05 and DEMO |
| `app/` | An | `python -m app`, `python -m app.headless`, real-time runner |
| `tests/contract/`, `tests/engine/` | An | contract exams for each module; engine and app tests |
| `dispatch/`, `tests/dispatch/` | Thanh | Dispatch controller |
| `cabin/`, `tests/cabin/` | Hai | Cabin controller |
| `control_center/`, `api/`, `web/`, `tests/control_center/` | Huy | Control Center, REST/WebSocket API, UI |

## Status

| Part | Status |
|---|---|
| Framework F1-F11: contracts, fakes, contract tests, engine, app, AI context (An) | done, merged, contract v1 frozen |
| Dispatch (Thanh), Cabin (Hai), Control Center + API + UI (Huy) | done; every contract test passes on the real modules |
| System tests across all real modules (Hai, `tests/cabin/test_system_*.py`) | done |
| Metrics report, random traffic, evaluator, grid search, SATSA (AN-03..AN-07) | in progress |
| Real gRPC transport, real authentication, real-world fire procedures | deferred by design (PROJECT_PLAN Section 1) |

Acceptance tests on the **real** modules (`python -m app.headless --scenario <name>`):

| Test | SRS oracle | Result |
|---|---|---|
| AT-01 | pickup 8 s, arrival at 5 at 58 s, doors closed 64 s | PASS (8 / 58 / 64) |
| AT-02 | 29 picked up at 4 s, 27 at 14 s | PASS (4 / 14) |
| AT-03 | P1 = 90, P2 = 20, P4 = 58; P2 chosen; P3, G1 excluded | PASS |
| AT-04 | 44 / 50 / 58 / 64 / 122 s, no ordinary stops on the leg | PASS (LOADED confirmed at 60 s) |
| AT-05 | lost ACK / unsafe cabin: never two owners | PASS (both variants) |
| AT-06 | SATSA vs grid vs (3, 5) on held-out traces | NOT RUN yet (AN-06, AN-07) |

`pytest` on the merged code: 617 passed, 1 skipped (the "no api/ yet" check). The full suite takes about 4 minutes because of the system stress tests.
