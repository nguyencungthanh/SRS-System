# Cabin Controller: real-world edge-case test set

| | |
|---|---|
| Module | Cabin Controller (`cabin/`), owner Hai |
| Date | 15 November 2026 |
| Requested by | Supervisor: "more detailed test sets that match some real-world edge cases" |
| Base | `main` at `23ac101` (all three real modules merged), branch `hai/HA-edge-cases` |
| Test files | `tests/cabin/test_cabin_edge_cases.py` (scenarios), `tests/cabin/test_cabin_stress.py` (random stress) |

## 1. Summary

The existing tests check the worked examples in the SRS. This set adds the situations that go wrong in real buildings: crowded cars, people holding doors, passengers trapped between floors, sensor failures, people changing their minds, fire, missions that fail, unreliable messages and the ends of the shaft. It also adds a random stress test that checks the safety rules after every single message.

| | Tests | Result |
|---|---:|---|
| Real-world scenarios EC-01 to EC-10 | 38 | 38 PASS |
| Random stress test EC-11 (30 seeds, 10 min each, plus coverage check) | 31 | 31 PASS |
| Earlier cabin unit tests | 46 | 46 PASS |
| Cabin contract tests (An) | 35 | 34 PASS, 1 FAIL (`SIM_04`, known test-harness defect, Section 6) |
| Whole repository, all real modules | 499 | 497 PASS, 1 FAIL (same test), 1 SKIP |

**The new tests found 6 real defects in the cabin.** All six are fixed on this branch (Section 4). The six fixes do not change any SRS acceptance result: with all real modules, AT-01 to AT-05 give the same times as before, and the DEMO scenario gives identical metrics.

## 2. How to run

```bash
pytest tests/cabin/test_cabin_edge_cases.py     # real-world scenarios (< 1 s)
pytest tests/cabin/test_cabin_stress.py         # random stress test (about 15 s)
pytest tests/cabin -k EC_03                     # one situation
```

Expected times follow the SRS model: 2 s per floor; door cycle OPENING 1 s, OPEN 4 s, CLOSING 1 s (6 s per stop, D-03). Test names start with the edge-case ID, then the SRS requirement, for example `test_EC_03_EMG_02_EMG_04_trapped_between_floors_full_story`.

## 3. The scenarios

### EC-01 Rush hour: a full car

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-01a | 12 people (900 kg) board at floor 12; one wants floor 20. Dispatch then offers a new call at floor 15. | Car is FULL and refuses the new call (`FULL_LOAD`), goes nonstop to 20 (arrives 22 s). After two people get out it is no longer full and accepts calls again. | CB-02, SEN-03 |
| EC-01b | 13 adults (975 kg) and a child (30 kg) board: 1005 kg. | Car holds with the doors **open** and does not move. As soon as the child steps out, the doors close (12 s), it leaves (13 s) and arrives at 20 at 29 s. | SEN-03, SEN-04, CB-05 |

### EC-02 Someone holds the door

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-02a | A bag blocks the closing door five times in a row. | Each obstruction reopens the door and adds 5.5 s; the car finally leaves at 33.5 s instead of 6 s. It never moves with a door open. | DOOR-04 |
| EC-02b | Obstruction while the door is opening or fully open. | Nothing changes (only a closing door reopens); the door closes at 6 s as normal. | DOOR-04 |
| EC-02c | Passenger presses "close door" during the open period. | Door closes at once; the car arrives 3 s earlier. | D-51 |
| EC-02d | Passenger keeps pressing "open door" every 3 s. | Each press restarts the 4 s open period; the door closes 4 s after the last press. | DOOR-03 |
| EC-02e | "Open door" pressed in an idle, empty car. | One full door cycle; the car does not move. | D-51 |

### EC-03 Trapped between floors

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-03 | Car carrying one passenger has a serious fault at 22.5 (between floors 22 and 23). The passenger presses the alarm, presses "open door" and tries to get out. A supervisor approves recovery too early; later a maintainer clears the fault and the supervisor approves again. | Car stops exactly at 22.5, doors stay closed and locked, the passenger stays on board, nothing else happens. Exactly one FAULT alert; the cabin never raises an ALARM alert (Control Center does). The early approval is refused. After clear + approval the car resumes from 22.5 and delivers the passenger at 40 s. | EMG-02, EMG-04, SAF-03, D-19 |

### EC-04 Sensor glitches and lost signal

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-04a | Position sensor fails mid-trip, recovers 3 s later. | Car stops where it is and refuses new routes (`POSITION_INVALID`) while the reading is invalid; resumes when it is back. | SEN-02 |
| EC-04b | Telemetry link lost for 5 s. | No telemetry during the loss (Dispatch sees the car as stale after 3 s, D-11); commands are still answered; telemetry returns the moment the link is back. | D-11 |
| EC-04c | Two faults at once (conflicting sensors and door-lock loss); they are fixed 2 s apart. | Car stays stopped until **both** are cleared; the lock is never reported as proven while lost. | SEN-02 |

### EC-05 A passenger changes their mind or presses the wrong button

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-05a | Passenger presses 20, then changes to 25. | One destination (25); the car does not stop at 20. | CB-03 |
| EC-05b | Same button pressed twice. | One destination, one event. | CB-03 |
| EC-05c | Passenger presses the floor the car is standing at. | Served on the spot; the doors stay open longer; no trip and no second door cycle. | DOOR-03 |
| EC-05d | Buttons 0, 41 and -3. | Rejected (`FLOOR_OUT_OF_RANGE`); route unchanged. | SYS-03 |
| EC-05e | Passenger presses the next floor when the car is only 0.5 floors from it. | No emergency-style stop: the car serves its other passenger first (floor 30) and comes back for 21. | MOVE-06 |
| EC-05f | Same, but exactly 1 floor away. | Served on the way. | MOVE-06 |

### EC-06 Several people for the same floor

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-06 | Three passengers want floor 20 and there is also a hall call at 20. | One arrival and one door cycle; three separate drop-off events and one pickup event, all at 22 s. | REQ-05, CB-04 |

### EC-07 Fire alarm

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-07a | Fire alarm while the doors are open with a passenger on board. | Mode FIRE; new routes refused; the passenger is still delivered. | D-24 |
| EC-07b | Fire alarm ends. | Back to AUTO; new routes accepted. | D-24 |

### EC-08 Missions that go wrong

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-08a | Freight mission, but the passenger on board does not get off at their floor. | The car waits; it never leaves for the freight pickup with someone inside. When the passenger presses "open" and steps out, the handoff is confirmed and the mission continues. | SEN-05, FRT-02 |
| EC-08b | Staff never confirm loading. | Car waits at the pickup with doors open (see open question O-2). | D-52 |
| EC-08c | Staff confirm loading before the car has arrived. | Ignored; only a confirmation at the pickup counts. | D-52 |
| EC-08d | Staff confirm loading, but the cabin scale reads 1100 kg (over 1000). Staff then unload to 800 kg and confirm again. | The car does **not** start the mission: OVERLOAD alert, doors open, no IN_PROGRESS. After the second confirmation it leaves (81 s) and delivers (139 s). | FRT-03, SEN-03 |
| EC-08e | Mission cancelled while the car is still dropping a passenger. | Mission FAILED; the passenger is still delivered; the car returns to AUTO and never goes to the pickup. | FRT-04, 5.5 |
| EC-08f | A confirmation arrives for a different mission. | Ignored. | D-52 |
| EC-08g | Dispatch sends a freight route with the pickup **before** a passenger's stop. | Refused (`ONBOARD_COMMITMENT`); the passenger is delivered first. | FRT-02, MED-05 |

### EC-09 Unreliable messages from Dispatch

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-09a | The same route command arrives three times. | Three identical ACKs; the route is executed once (version 1). | SIM-06 |
| EC-09b | An old command, built from out-of-date telemetry, arrives after a newer one. | Refused (`VERSION_MISMATCH`); the newer route stays. | CB-01 |
| EC-09c | Dispatch asks about a command the cabin refused. | Report says "seen, not accepted". | D-17 |
| EC-09d | Dispatch withdraws the car's only call while it is moving. | The car stops at the next floor it can safely reach (15) without opening its doors, instead of running on to the old target. | MOVE-06, D-55 |

### EC-10 The ends of the building

| ID | Situation | Expected behaviour | SRS |
|---|---|---|---|
| EC-10a | Full-height run 40 to 1 and 1 to 40. | 39 floors in 78 s; position always within floors 1 to 40. | MOVE-03, SYS-01 |
| EC-10b | New stop while moving down, at exactly 1 floor, 0.2 floor and 1.2 floors ahead. | Accepted, refused (`INSERT_CUTOFF`), accepted. | MOVE-06 |

### EC-11 Random stress test

For each of 30 seeds, one car receives 10 minutes of random but reproducible input. The input mix:
- route commands: good, stale, late, repeated and conflicting
- people boarding with random weights (55 to 250 kg) and pressing any button, including invalid floors
- exits, door buttons, obstructions
- all six fault types, fault clearing, recovery approvals, fire and maintenance

A checker wraps the real cabin and inspects its full internal state after **every** message (123,973 messages in total). It reads the internal state because some faults stop telemetry on purpose.

| # | Invariant checked after every message | SRS |
|---|---|---|
| I1 | Never moving unless the door is closed and locked | SAF-01, MOVE-02 |
| I2 | Doors open only when stationary in a landing zone | SAF-02, DOOR-02 |
| I3 | Position always within floors 1 to 40 | SYS-01 |
| I4 | Route version never goes back | D-16 |
| I5 | No teleporting: at most 0.5 floor per second, no jump within one instant | MOVE-03 |
| I6 | People get on or off only at a landing with the doors open | 5.5, SEN-04 |
| I7, I8 | Never moving in FAULT_HOLD or above 1000 kg | EMG-02, SEN-03 |
| I9 | Every destination belongs to someone on board | 2.5, DOOR-06 |
| I10 | An ACK commits exactly one new version; a NACK has a reason and leaves the route unchanged | CB-01 |
| I11 | A stop is served only when stationary at its own floor | CB-04 |
| I12 | No telemetry while the heartbeat is lost | D-11 |
| I13 | The cabin never raises an ALARM alert | D-19, EMG-01 |

**No deadlock:** after the random phase, every fault is repaired, the fire ends, recovery is approved and anyone over the limit steps out. The car must then finish every stop and come to rest with doors closed within 50 minutes. All 30 seeds do.

**Coverage guard:** a separate test fails if the random runs stop reaching the dangerous states, so a weakened generator cannot pass silently. Across the 30 seeds the runs reached:

| What | Count |
|---|---:|
| ACK | 436 |
| NACK by reason | VERSION_MISMATCH 643, FIRE 818, FAULT_HOLD 393, EXPIRED 358, MAINTENANCE 237, POSITION_INVALID 199, SENSORS_INVALID 199, DOORS_NOT_LOCKED 104, REVERSAL_REQUIRED 92, ONBOARD_COMMITMENT 23, INSERT_CUTOFF 5 |
| Door reopens | 34 |
| Calls / destinations served | 205 / 204 |
| Boardings / exits | 162 / 67 |
| Messages handled in FAULT_HOLD / FIRE / MAINTENANCE / FULL / OVERLOAD_HOLD | 5,054 / 11,065 / 3,249 / 3,025 / 3 |

Overload is reached only rarely (3 messages) and `FULL_LOAD` refusals not at all, so those two cases rely mainly on the scenarios EC-01a/b and EC-08d.

## 4. Defects found and fixed

All six were invisible to the original tests, which only exercise the SRS worked examples.

| # | Found by | Defect | Real-world effect | Fix |
|---|---|---|---|---|
| 1 | EC-03, EC-11 (all 30 seeds) | Passengers could board or exit whenever the car was not moving, including with doors closed and **between floors** | A trapped passenger "walked out" of a car stuck between floors | Boarding and exits only at a landing with doors fully open |
| 2 | EC-05e | A floor pressed too late was inserted as the next stop | The car would stop within 0.5 floor: an emergency-style stop that MOVE-06 forbids for Dispatch | Destinations closer than the cutoff are served on a later pass |
| 3 | EC-05c | Pressing the current floor let the doors close, then reopened them for a second full cycle | Needless 6 s delay | Served on the spot; the open period restarts |
| 4 | EC-08d | Staff "loaded" confirmation started the mission even when the cabin scale read over 1000 kg | Mission reported IN_PROGRESS for a car that cannot move | The cabin's own load check must also pass (FRT-03); OVERLOAD alert while waiting |
| 5 | EC-08g | A mission route with the pickup before a passenger's stop was accepted | Deadlock: the car could not leave for the pickup while the passenger was on board, and could not reach the passenger's floor | Refused with `ONBOARD_COMMITMENT` |
| 6 | EC-09d | When Dispatch withdrew the only call, the car kept going to the old target floor | Up to a full-height empty run | Stops at the next safely reachable floor |

## 5. Open questions for the team

These are gaps in the SRS and DECISIONS.md that the tests exposed. The current behaviour is tested, but the team should decide.

| # | Gap | Current behaviour | Suggestion |
|---|---|---|---|
| O-1 | No limit on how long a door can be held (EC-02a) | Every obstruction reopens, without limit | Add a "nudging" decision: after N reopens, close slowly and alert the operator |
| O-2 | No timeout when loading is never confirmed (EC-08b) | Car waits at the pickup with doors open indefinitely | Add a timeout that sends the mission to HOLD_PENDING_REVIEW with an alert |
| O-3 | A passenger who never presses a button blocks a mission drain (EC-08a) | Mission stays DRAINING with no alert, because count and load agree | Alert the operator when draining stalls, not only on a count/load conflict (SEN-06) |
| O-4 | Withdrawn call while moving (EC-09d) | Stops at the next reachable floor, doors closed | Confirm this is wanted |

These sit alongside the earlier questions Q1 and Q3 to Q9 in [TEST_REPORT.md](TEST_REPORT.md).

## 6. Known failure outside this module

`tests/contract/test_cabin.py::test_SIM_04_obstruction_beats_door_close_at_the_same_instant` (owned by An) still fails. The test injects the obstruction only after the clock has already processed the 14.0 s door-close, so no cabin can pass it. Scenarios in this set queue their inputs in advance (`inject_all`), which is why EC-02a passes with obstructions at exact instants.
