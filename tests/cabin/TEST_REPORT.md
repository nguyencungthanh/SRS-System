# Cabin Controller: implementation and test report

| | |
|---|---|
| Module | Cabin Controller (`cabin/`), packages HA-01 to HA-07 |
| Owner | Hai |
| Date | 15 November 2026 (first report: 14 November) |
| Code version | `main` at `23ac101` (Dispatch, Cabin and Control Center all merged) plus branch `hai/HA-edge-cases` |
| Environment | Python 3.14.6, pytest 9.2.0, ruff 0.17.5 (Windows 11) |
| Source | Smart Elevator System SRS v1.1 (`docs/SRS-Group2-v1.1.pdf`) |

## 1. Summary

- The Cabin Controller is implemented and merged. One class runs as five independent instances (P1-P4, G1).
- **Integrated acceptance now runs on the real system.** Dispatch (Thanh) and Control Center (Huy) are merged, and AT-01 to AT-05 reproduce the SRS numbers with all three real modules (Section 3.4).
- **New since the first report:** a real-world edge-case test set, requested by the supervisor. It has 38 scenarios and a random stress test that checks 13 safety rules after each of 123,973 messages. It is described in [EDGE_CASES.md](EDGE_CASES.md). **It found 6 real defects in the cabin, all fixed** (Section 5.2).
- **Cabin tests:** contract tests 34 of 35 pass; the cabin's own tests 115 of 115 pass. The one failure is a defect in the test harness, not in the cabin (Section 5.1).
- **Whole repository: 497 passed, 1 failed (the same test), 1 skipped.** Lint and formatting are clean.
- Replaying a recorded run gives an identical log.

## 2. What was implemented

The cabin is a pure message handler: it receives one message at a time and returns the messages it emits, with no wall-clock time and no randomness. It owns simulated motion, doors, load and route acceptance, and it can veto any route on safety grounds.

| Package | Behaviour | SRS requirements |
|---|---|---|
| HA-01 Cabin state | Position, direction, mode, route and version, doors and lock, load, passenger list and faults for each car. Telemetry at start, on every change, and at least every 1 s; none while a heartbeat-loss fault is active. | SYS-02, MOVE-01, SEN-01 |
| HA-02 Motion | Continuous position at 2 s per floor with no jumps. The car stops only at route stops. A new stop less than 1 floor ahead is refused, and so is any stop that would need an instant reversal. A floor pressed too late is served on a later pass; if the car's only stop is withdrawn, it stops at the next floor it can safely reach. | MOVE-02, MOVE-03, MOVE-05, MOVE-06 |
| HA-03 Doors | Explicit state machine: CLOSED, OPENING (1 s), OPEN (4 s), CLOSING (1 s), plus OBSTRUCTED. Doors open only when stationary at a landing. An obstruction while closing reopens the door. The car moves only with doors closed and locked. Open/close buttons are supported; a call for the floor where the doors are open is served on the spot. | DOOR-01 to DOOR-05, SAF-01, SAF-02 |
| HA-04 Route proposals | A proposal is checked for expiry, route version, mode, sensors, mission reservation, stop validity, onboard destinations, load and motion. Every refusal (NACK) carries a reason and keeps the old route. A repeated command returns the stored answer; the same command ID with different content is an error. Dispatch can ask what happened to a command. | CB-01, SIM-06 |
| HA-05 Boarding and load | People get on or off only at a landing with the doors fully open. Destinations are accepted only from passengers on board. Pickup and drop-off are separate events. At 900 kg or more the car takes no new hall pickups. Above 1000 kg it holds with doors open until someone exits. | CB-02 to CB-05, DOOR-06, REQ-05, SEN-03, SEN-04 |
| HA-06 Faults and recovery | A serious fault between floors stops the car where it is, keeps the doors locked and the passengers aboard, alerts the operator, and releases unpicked hall calls. Return to service needs the fault cleared **and** supervisor approval. Alarm, fire and fault are separate events; conflicting sensor readings are never treated as safe. | SAF-03, EMG-01, EMG-02, EMG-04, SEN-02 |
| HA-07 Missions | For a MEDICAL or FREIGHT mission, onboard passengers are dropped off first; a route that puts the mission ahead of a passenger is refused. The car is handed over only when stationary with zero passengers and a load reading that agrees; otherwise the mission is held for review. The exclusive leg starts only when staff confirm loading **and** the cabin's own load check passes. Unloading completes the mission; a failed loading ends it. | SEN-05, SEN-06, MED-05, FRT-02 to FRT-04 |

Code layout: `cabin.py` (controller), `doors.py` (door state table), `motion.py` (position and stop rules), `proposal.py` (route checks and reply cache), `mission.py` (mission phases). About 1,150 lines of code plus 1,450 lines of tests.

## 3. Test results

### 3.1 Test suites (`pytest`)

| Suite | Owner | Passed | Failed | Skipped |
|---|---|---:|---:|---:|
| Cabin contract tests (`tests/contract/test_cabin.py`) | An | 34 | 1 | 0 |
| Cabin own tests (`tests/cabin/`) | Hai | 115 | 0 | 0 |
| Dispatch contract and own tests | An, Thanh | 92 | 0 | 0 |
| Control Center contract and own tests | An, Huy | 92 | 0 | 0 |
| Framework tests (contracts, message bus, fakes, engine) | An | 164 | 0 | 1 |
| **Total** | | **497** | **1** | **1** |

Static checks: `ruff check .` passes; `ruff format --check .` reports all 79 files formatted.

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

### 3.3 The cabin's own tests

| File | Tests | What it covers |
|---|---:|---|
| `test_cabin_rules.py` | 37 | The pure rules: door state table, position and cutoff maths, every NACK reason and the order of the checks, the command cache, mission gating |
| `test_cabin_behaviour.py` | 9 | Behaviour the contract tests miss: releasing hall calls on fault or maintenance, OBSTRUCTED reported, door buttons ignored in a fault hold, sensor-fault stop and resume, medical leg, manual exit |
| `test_cabin_edge_cases.py` | 38 | Real-world scenarios EC-01 to EC-10: full car, door held, trapped between floors, sensor glitches, changed destinations, shared floor, fire, failed missions, unreliable messages, ends of the shaft ([EDGE_CASES.md](EDGE_CASES.md)) |
| `test_cabin_stress.py` | 31 | EC-11: 30 random seeds of 10 minutes each, 13 safety rules checked after every message, plus a no-deadlock check and a coverage guard |

### 3.4 Acceptance scenarios on the integrated system

Command: `python -m app.headless --scenario <id>`. All three modules are the **real** implementations.

| Scenario | Result | SRS oracle | Status |
|---|---|---|---|
| AT-01 ordinary trip | P1 pickup at 27 at 8 s; drop-off at 5 at 58 s | 8 / 58 s | **PASS** |
| AT-02 grouping | P1 picks up 29 at 4 s, then 27 at 14 s | 4 / 14 s | **PASS** |
| AT-03 medical selection | P2 reserved and serves the medical pickup at floor 2; P1 and P4 finish their onboard trips | P2 chosen | **PASS** |
| AT-04 freight after passenger | passenger dropped at 5 at 44 s; material loaded at 60 s; delivered to 30 at 122 s; completed at 124 s | 44 / 58-64 / 122 s | **PASS** |
| AT-05 lost ACK | the call is served once, by P1 | no second owner | **PASS** |
| AT-05 unsafe car | P1 refuses (conflicting sensors); P2 serves the call | cabin veto | **PASS** |
| DEMO traffic | all calls served (unserved 0); mean wait 36.9 s, P95 101 s | runs to completion | PASS |
| Replay | DEMO trace replayed | identical log (NFR-05) | **IDENTICAL** |

The six fixes on this branch change none of these results: AT-01 to AT-05 give the same times, and DEMO gives identical metrics before and after.

## 4. Status of the acceptance tests

| Test | Status |
|---|---|
| AT-01 to AT-05 on the real integrated system | **PASS** (Section 3.4) |
| AT-06 optimizer | NOT RUN: owned by An, not built yet |

## 5. Known issues and changes

### 5.1 Defect in a framework test (owner: An)

`test_SIM_04_obstruction_beats_door_close_at_the_same_instant` cannot pass for any cabin. The test advances the clock to 14.0 s, which delivers the door-close event, and only then injects the obstruction meant for the same instant. By then the car has already left.

A separate script queued the same obstruction before 14.0 s was processed. The cabin then reopened at 14.0 s and departed at 20.0 s, exactly the expected result. The fix is to inject the obstruction before advancing the clock. The test is owned by An and was left unchanged. It is still the only red test on `main`.

### 5.2 Defects found by the edge-case set (fixed)

| # | Defect | Fix |
|---|---|---|
| 1 | Passengers could board or exit through closed doors, even between floors (a trapped passenger "walked out") | Only at a landing with the doors fully open |
| 2 | A floor pressed too late (0.5 floor away) became the next stop: an emergency-style stop | Served on a later pass |
| 3 | Pressing the current floor caused a second full door cycle | Served on the spot; the open period restarts |
| 4 | Staff "loaded" started the mission even when the cabin read over 1000 kg | The cabin's load check must also pass; OVERLOAD alert while waiting |
| 5 | A mission route with the pickup before a passenger's stop was accepted, which jams the car | Refused (`ONBOARD_COMMITMENT`) |
| 6 | When Dispatch withdrew the only call, the car kept running to the old target | Stops at the next safely reachable floor |

Details and the tests that found them: [EDGE_CASES.md](EDGE_CASES.md), Section 4.

### 5.3 Framework inconsistencies reported to An

1. The maintenance decision (D-09) says "maintenance finished" must be recorded before recovery, but the contract test allows recovery without it. The cabin follows the test.
2. Two decisions disagree on the result of a failed loading: D-25 says FAILED or HOLD_PENDING_REVIEW, D-52 says FAILED. The cabin sends FAILED, as D-52 and the test require.
3. The SRS requires an OBSTRUCTED door state, but no team decision says when it occurs. The cabin reports it for an instant when an obstruction is detected.

### 5.4 Assumptions awaiting team decisions

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
| O-1 | How long a door may be held open by obstructions | No limit (suggest a "nudging" rule) |
| O-2 | Loading never confirmed | Car waits at the pickup indefinitely (suggest a timeout to review) |
| O-3 | Passenger who never presses a button during a mission drain | Mission waits with no alert (suggest an operator alert) |
| O-4 | Only call withdrawn while moving | Stops at the next reachable floor with doors closed |

## 6. Next steps

1. Merge `hai/HA-edge-cases` (edge-case set and the six fixes).
2. An fixes the SIM_04 test; the team answers Q1, Q3 to Q9 and O-1 to O-4; the cabin is adjusted where needed.
3. When An's optimizer (AT-06) exists, check that the cabin still passes the stress invariants under its generated traffic.

## How to reproduce

```bash
pip install -r requirements.txt
pytest                                       # whole suite
pytest tests/contract/test_cabin.py          # cabin contract tests
pytest tests/cabin                           # cabin own tests (incl. edge cases and stress)
ruff check . && ruff format --check .
python -m app.headless --scenario AT-01      # all modules real
python -m app.headless --scenario DEMO --trace trace.jsonl
python -m app.headless --replay trace.jsonl  # expect: replay: IDENTICAL
```
