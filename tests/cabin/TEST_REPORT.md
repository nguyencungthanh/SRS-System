# Cabin Controller: implementation and test report

| | |
|---|---|
| Module | Cabin Controller (`cabin/`), packages HA-01 to HA-07 |
| Owner | Hai |
| Date | 14 November 2026 |
| Code version | `main` at commit `9fc7dc7` (PR #4 merged) |
| Environment | Python 3.14.6, pytest 9.2.0, ruff 0.17.5 (Windows 11) |
| Source | Smart Elevator System SRS v1.1 (`docs/SRS-Group2-v1.1.pdf`) |

## 1. Summary

- The Cabin Controller is implemented and merged. One class runs as five independent instances (P1-P4, G1).
- **Cabin contract tests: 34 of 35 pass.** The one failure is a defect in the test harness, not in the cabin (Section 5.1).
- **Own unit tests: 46 of 46 pass.**
- **Whole repository: 245 passed, 1 failed (the same test), 62 skipped.** The skipped tests belong to Dispatch and Control Center, which are not merged yet.
- Lint and formatting are clean.
- In the simulation engine, the cabin reproduces the SRS timelines for AT-01 (8 / 58 / 64 s) and AT-04 (44 / 50 / 58 / 64 / 122 s). Replaying a recorded trace gives an identical log.
- **Not yet tested:** the cabin together with the real Dispatch and Control Center. Those modules are not on `main`, so every run below uses their stand-ins ("fakes"). Integrated acceptance is **NOT RUN**.

## 2. What was implemented

The cabin is a pure message handler: it receives one message at a time and returns the messages it emits, with no wall-clock time and no randomness. It owns simulated motion, doors, load and route acceptance, and it can veto any route on safety grounds.

| Package | Behaviour | SRS requirements |
|---|---|---|
| HA-01 Cabin state | Position, direction, mode, route and version, doors and lock, load, passenger list and faults for each car. Telemetry at start, on every change, and at least every 1 s; none while a heartbeat-loss fault is active. | SYS-02, MOVE-01, SEN-01 |
| HA-02 Motion | Continuous position at 2 s per floor with no jumps. The car stops only at route stops. A new stop less than 1 floor ahead is refused, and so is any stop that would need an instant reversal. | MOVE-02, MOVE-03, MOVE-05, MOVE-06 |
| HA-03 Doors | Explicit state machine: CLOSED, OPENING (1 s), OPEN (4 s), CLOSING (1 s), plus OBSTRUCTED. Doors open only when stationary at a landing. An obstruction while closing reopens the door. The car moves only with doors closed and locked. Open/close buttons are supported. | DOOR-01 to DOOR-05, SAF-01, SAF-02 |
| HA-04 Route proposals | A proposal is checked for expiry, route version, mode, sensors, mission reservation, stop validity, onboard destinations, load and motion. Every refusal (NACK) carries a reason and keeps the old route. A repeated command returns the stored answer; the same command ID with different content is an error. Dispatch can ask what happened to a command. | CB-01, SIM-06 |
| HA-05 Boarding and load | Destinations are accepted only from passengers on board. Pickup and drop-off are separate events. At 900 kg or more the car takes no new hall pickups. Above 1000 kg it holds with doors open until someone exits. | CB-02 to CB-05, DOOR-06, REQ-05, SEN-03, SEN-04 |
| HA-06 Faults and recovery | A serious fault between floors stops the car where it is, keeps the doors locked and the passengers aboard, alerts the operator, and releases unpicked hall calls. Return to service needs the fault cleared **and** supervisor approval. Alarm, fire and fault are separate events; conflicting sensor readings are never treated as safe. | SAF-03, EMG-01, EMG-02, EMG-04, SEN-02 |
| HA-07 Missions | For a MEDICAL or FREIGHT mission, onboard passengers are first dropped off. The car is handed over only when stationary with zero passengers and a load reading that agrees; otherwise the mission is held for review. Then a direct exclusive leg runs with load and unload confirmation, or the mission fails. | SEN-05, SEN-06, MED-05, FRT-02 to FRT-04 |

Code layout: `cabin.py` (controller), `doors.py` (door state table), `motion.py` (position and stop rules), `proposal.py` (route checks and reply cache), `mission.py` (mission phases). About 1,000 lines of code plus 420 lines of tests.

## 3. Test results

### 3.1 Test suites (`pytest`)

| Suite | Passed | Failed | Skipped | Notes |
|---|---:|---:|---:|---|
| Cabin contract tests (`tests/contract/test_cabin.py`) | 34 | 1 | 0 | Written by An from SRS numbers before the cabin existed |
| Cabin unit tests (`tests/cabin/`) | 46 | 0 | 0 | Written with the cabin; cover the cases below |
| Framework tests (contracts, message bus, fakes, engine) | 165 | 0 | 0 | Unaffected by the cabin |
| Dispatch contract tests | 0 | 0 | 34 | Dispatch not merged yet |
| Control Center contract tests | 0 | 0 | 28 | Control Center not merged yet |
| **Total** | **245** | **1** | **62** | |

Static checks: `ruff check .` passes; `ruff format --check .` reports all 53 files formatted.

### 3.2 Cabin contract tests by package

| Package | Tests | Result |
|---|---|---|
| HA-01, HA-02 state and motion | telemetry and heartbeat, initial state, AT-01 timeline, continuous position, door interlocks, separate pickup and drop-off events | 6 / 6 PASS |
| HA-03 doors | 6 s door cycle, obstruction reopens, open button extends the dwell, button ignored while moving, **obstruction at the same instant as door close** | 4 / 5 PASS; 1 FAIL (Section 5.1) |
| HA-04 route proposals | wrong version, repeated command, version query, expired proposal, late insertion refused / exactly 1 floor accepted, no reversal, dropping an onboard destination refused, conflicting sensors refused | 9 / 9 PASS |
| HA-05 load | 899 / 900 kg, 1000 / 1001 kg, destination before boarding | 5 / 5 PASS |
| HA-06 faults | serious fault between floors, recovery needs clear + approval, alarm raises no cabin alert, fire, maintenance | 5 / 5 PASS |
| HA-07 missions | AT-04 freight timeline, low load with a passenger blocks the handoff, failed loading | 3 / 3 PASS |
| General | same inputs give the same log, snapshot shape | 2 / 2 PASS |

### 3.3 What the unit tests add

The contract tests check observable behaviour. The unit tests also cover:
- every NACK reason, and the order in which the checks run
- the boundary cases: exactly 1 floor, the expiry instant, exactly 900 kg
- the rule that Dispatch may not invent a passenger destination
- that hall calls are released when a car faults or enters maintenance
- that the OBSTRUCTED door state is actually reported
- door buttons ignored during a fault hold
- a sensor fault stops the car and clearing it resumes the trip
- a medical leg from an empty idle car
- manual exit at a landing, and refusal to remove anyone from a moving car

### 3.4 Acceptance scenarios in the simulation engine

Command: `python -m app.headless --scenario <id> --cabin real`. Dispatch and Control Center are **fakes** in every run.

| Scenario | Cabin result | SRS oracle | Counts as acceptance? |
|---|---|---|---|
| AT-01 ordinary trip | pickup at 8 s, arrival at floor 5 at 58 s, doors closed at 64 s | 8 / 58 / 64 s | Cabin side matches. Full AT-01 needs the real Dispatch |
| AT-04 freight after passenger | arrive floor 5 at 44 s, passenger out, leave at 50 s, material pickup at 58 s, leave loaded at 64 s, deliver at 122 s, completed on unloading | 44 / 50 / 58 / 64 / 122 s | Cabin side matches. Full AT-04 needs the real Control Center |
| AT-02, AT-03, AT-05 (ack lost / unsafe), DEMO | run to completion, unserved calls 0 | led by Dispatch / Control Center | No: the numbers come from the fake Dispatch |
| Replay | AT-04 trace replayed | identical log required (NFR-05) | **IDENTICAL** |

## 4. Status of the acceptance tests

| Test | Status |
|---|---|
| AT-01, AT-04 (cabin side) | PASS against fakes |
| AT-01 to AT-05 on the real integrated system | NOT RUN: Dispatch and Control Center not merged |
| AT-06 optimizer | NOT RUN: owned by An, not built yet |

## 5. Known issues

### 5.1 Defect in a framework test (owner: An)

`test_SIM_04_obstruction_beats_door_close_at_the_same_instant` cannot pass for any cabin. The test advances the clock to 14.0 s, which delivers the door-close event, and only then injects the obstruction meant for the same instant. By then the car has already left.

A separate script queued the same obstruction before 14.0 s was processed. The cabin then reopened at 14.0 s and departed at 20.0 s, exactly the expected result. The fix is to inject the obstruction before advancing the clock. The test is owned by An and was left unchanged. It is the only red test on `main`.

### 5.2 Framework inconsistencies reported to An

1. The maintenance decision (D-09) says "maintenance finished" must be recorded before recovery, but the contract test allows recovery without it. The cabin follows the test.
2. Two decisions disagree on the result of a failed loading: D-25 says FAILED or HOLD_PENDING_REVIEW, D-52 says FAILED. The cabin sends FAILED, as D-52 and the test require.
3. The SRS requires an OBSTRUCTED door state, but no team decision says when it occurs. The cabin reports it for an instant when an obstruction is detected.

### 5.3 Assumptions awaiting team decisions

The SRS and DECISIONS.md do not define the following. Each one is marked in the code so a decision is a small change.

| # | Question | Current behaviour |
|---|---|---|
| Q1 | Recovery from maintenance | Supervisor approval alone returns the car to service |
| Q3 | Reaction to non-serious faults (lock, position, load, conflicting sensors) | Car stops where it is, operator alerted, resumes once the fault is cleared |
| Q4 | OBSTRUCTED door state | Reported for 0 s, then the door reopens |
| Q5 | Doors during overload | Stay open; closing starts as soon as the load is back under the limit |
| Q6 | Fire alarm | Car finishes its current route and refuses new ones; normal service when the alarm ends |
| Q7 | When a mission is held for review | At the end of the drop-off stop, if the passenger count and load disagree |
| Q8 | Where a new destination goes in the route | Where the car passes that floor on its current path, otherwise at the end |
| Q9 | Several stops on one floor | Served by a single door cycle |

## 6. Next steps

1. An fixes the SIM_04 test and answers Q1 and Q3 to Q9; the cabin is adjusted where needed.
2. When Dispatch (Thanh) and Control Center (Huy) are merged, run AT-01 to AT-05 on the real integrated system and update Section 4.
3. Rerun this report after each integration step.

## How to reproduce

```bash
pip install -r requirements.txt
pytest                                   # whole suite
pytest tests/contract/test_cabin.py      # cabin contract tests
pytest tests/cabin                       # cabin unit tests
ruff check . && ruff format --check .
python -m app.headless --scenario AT-01 --cabin real
python -m app.headless --scenario AT-04 --cabin real --trace trace.jsonl
python -m app.headless --replay trace.jsonl --cabin real   # expect: replay: IDENTICAL
```
