# Whole-system real-world test set

| | |
|---|---|
| Scope | The whole simulator: Dispatch (Thanh), Cabin (Hai), Control Center (Huy), the virtual passengers and the engine (An) |
| Author | Hai |
| Date | 15 November 2026 |
| Base | `main` at `3c8233e` (all modules merged), branch `hai/HA-system-tests` |
| Test files | `tests/cabin/test_system_scenarios.py` (SY-01 to SY-17), `tests/cabin/test_system_missions_ops.py` (SY-21 to SY-31), `tests/cabin/test_system_stress.py` (SY-20), harness `tests/cabin/system_helpers.py` |
| Rule followed | Defects in other people's modules are **documented, not fixed**. Defects in the cabin (our module) are fixed. |

## 1. Summary

| Set | Tests | Result |
|---|---:|---|
| Real-world system scenarios SY-01 to SY-17 | 26 | 26 PASS |
| Missions, failures and operator actions SY-21 to SY-31 | 16 | 15 PASS, 1 XFAIL (finding F-1, Dispatch) |
| Whole-system random stress SY-20 (20 seeds, coverage guard, replay check) | 22 | 22 PASS |
| Whole repository | 564 | 561 PASS, 1 XFAIL (F-1), 1 FAIL (F-2, An's `SIM_04` test), 1 SKIP |

- **Findings in other people's modules: 2.** F-1 in Dispatch: a broken-down medical car's mission is never given to another car. F-2: a framework test that no cabin can pass. Both are documented in Section 5 and neither was changed.
- **Defects found in our cabin: 3, all fixed** (Section 4). One was a regression introduced by last round's edge-case fix, which only the whole-system tests could see.
- **The SRS acceptance results are unchanged:** AT-01 8 / 58 s, AT-02 4 / 14 s, AT-03 P2, AT-04 44 / 60 / 122 / 124 s, both AT-05 cases and DEMO give the same results as before.
- **Stress volume:** the 20 random seeds delivered 646,894 messages and carried 1,200 passengers on 878 hall calls through 92 faults, 62 door obstructions, 13 maintenance windows, 3 fires, 19 missions and 38 network faults. Nobody was stranded and every mission finished.

## 2. How the tests work

```bash
pytest tests/cabin/test_system_scenarios.py tests/cabin/test_system_missions_ops.py   # ~8 s
pytest tests/cabin/test_system_stress.py                                             # ~45 s
pytest tests/cabin -k SY_05 -rxX                                                     # one case, show xfails
```

- **All real modules.** Each test builds a scenario and runs it through An's engine with `module_set("real", "real", "real")`, exactly like `python -m app.headless`. Passengers press buttons, board and exit through An's virtual-passenger module (D-56). The tests only use public entry points; they never read another module's internals.
- **Staff bot.** Building staff and the medical team confirm loading when the mission car stands open at the pickup, and unloading at the destination. They do this once, as real staff would.
- **Strict expected failures.** A test that fails because of another person's module is marked `xfail(strict=True)` with its finding ID. The suite stays readable, nothing is hidden (pytest reports it as XFAIL), and the test turns red as an XPASS the moment the owner fixes it, so the marker must then be removed.

### System-wide checks after every run

| # | Check | SRS |
|---|---|---|
| S1 | A hall call never belongs to two cars at the same time | SAF-04 |
| S2 | Every car: never moves with the door open, doors open only stationary in a landing zone | SAF-01, SAF-02 |
| S3 | No car moves over 1000 kg | SEN-03 |
| S4 | Each hall call is completed exactly once | CB-04 |
| S5 | No mission is approved that nobody requested (an alarm never becomes a medical mission) | EMG-01 |
| S6 | The goods car G1 is never given an ordinary hall call | D-20, D-21 |
| S7 | Every valid passenger reaches their destination: nobody waiting, nobody left inside | NFR-03, SIM-05 |

The stress test adds:
- every call keeps its original creation time through faults and reassignment (EMG-03);
- every mission ends, and no mission is reserved by two cars;
- the whole fleet is back in AUTO at the end;
- two runs of the same seed produce identical logs (NFR-05).

## 3. The scenarios

### Everyday traffic

| ID | Situation | Expected behaviour | Result |
|---|---|---|---|
| SY-01 | Morning up-peak: 30 people from the lobby within 60 s | Everyone delivered; no wait over 10 min | PASS (after cabin fix C-1) |
| SY-02 | Evening down-peak: 30 people to the lobby within 60 s | Same | PASS |
| SY-03 | Lunch: 40 trips between random floors in 2 min | Same | PASS |
| SY-04 | 16 people (1200 kg) wait at the lobby for one car | Car fills to at most 1000 kg; the rest press again and are carried later | PASS |
| SY-13 | 8 people press 15 DOWN within 3 s | One canonical call, served once; all 8 delivered | PASS |
| SY-14 | UP and DOWN pressed at the same floor | Two separate calls, both served | PASS |
| SY-15 | DOWN at 1, UP at 40, floors 0 and 41 | All refused with the right reason; no car moves | PASS |

### Breakdowns and maintenance

| ID | Situation | Expected behaviour | Result |
|---|---|---|---|
| SY-05 | P1 breaks down between floors with passenger A inside and passenger B's call assigned | B's call goes to a healthy car; A stays inside until the fault is cleared and a supervisor approves recovery; exactly one FAULT alert | PASS |
| SY-05b | Same | B's call keeps its original creation time (1.0 s) after reassignment | PASS |
| SY-06 | A car is taken for maintenance during traffic | It hands over its calls and serves none after that; everyone delivered | PASS |
| SY-16a | A car stops sending telemetry just before a call next to it | The call goes to another car (stale data excludes a car) | PASS |
| SY-16b | Recovery requested for a healthy car | Refused | PASS |
| SY-16c | Recovery approved by an OPERATOR, not a SUPERVISOR | Refused; car stays in FAULT_HOLD | PASS |
| SY-26 | Every passenger car is out of service for 100 s while people press buttons | Calls stay PENDING (G1 never takes them); served after the recovery | PASS |

### Emergencies and missions

| ID | Situation | Expected behaviour | Result |
|---|---|---|---|
| SY-07 | Medical emergency during busy traffic | Mission completes on one capable car; ordinary passengers still all delivered | PASS |
| SY-08 | Medical request when every passenger car is in maintenance; one recovers after 30 s | UNAVAILABLE with exactly one alert; re-evaluated on recovery and completed | PASS |
| SY-09a | Freight request with the goods car available | G1 chosen and the mission completes | PASS |
| SY-09b | Bad requests: 1200 kg, no material, passenger asks for freight, staff ask for medical, same origin and destination, floor 0 | Each REJECTED with its reason; no route changes | PASS (6 cases) |
| SY-21 | Medical request during traffic | Chosen car has the lowest ETA of all candidates; G1 excluded as NOT_CAPABLE | PASS |
| SY-22 | Two medical emergencies at once | Two different cars, both completed | PASS |
| SY-23 | Medical and freight at the same time | Freight on G1, medical on a passenger car, both completed | PASS |
| SY-24a | The car reserved for a medical call breaks down on the way, for good | The operator sees an explicit FAILED status, not "reserved" forever | PASS (after cabin fix C-3) |
| SY-24b | Same | The patient is still picked up by another car | **XFAIL: finding F-1** |
| SY-25a | The freight car breaks down mid-trip with the load on board | Mission shown as HOLD_PENDING_REVIEW | PASS (after cabin fix C-3) |
| SY-25b | Same, then repaired and recovery approved | Mission resumes (IN_PROGRESS) and completes | PASS |
| SY-30 | Medical request during a fire alarm | Approved, then completed once the alarm ends | PASS |
| SY-31 | Maintenance ordered on the car running a medical mission | The patient is still served | PASS (see O-7) |

### Fire, alarms, network, operators

| ID | Situation | Expected behaviour | Result |
|---|---|---|---|
| SY-10 | Fire alarm for 70 s during traffic | No new ordinary call dispatched during the alarm; FIRE alert; everyone delivered afterwards | PASS |
| SY-11 | Lost and delayed replies, a delayed proposal, lost telemetry | Never two owners; everyone delivered | PASS (after cabin fix C-1) |
| SY-12 | Passenger presses the alarm in a moving car | One ALARM alert; no medical mission | PASS |
| SY-17 | Same traffic, medical mission and sensor fault, run twice | Identical logs | PASS |
| SY-27 | A passenger, then an operator, acknowledge an alert | Only the operator's acknowledgement counts | PASS |
| SY-28 | Optimizer recommends a new policy; nobody / an operator / a supervisor (reject) / a supervisor (approve) decides | Only the supervisor's approval activates it at Dispatch | PASS (4 cases) |
| SY-29 | Pause the whole building, let time pass, resume | Nothing moves while paused; everyone delivered after resuming | PASS |

### SY-20 Whole-system random stress

Each of the 20 seeds builds a random 10-minute day, followed by a 40-minute calm-down:

| Ingredient | Per seed | Total over 20 seeds |
|---|---|---|
| Passengers (mixed up / down / inter-floor) | 60 | 1,200 carried, 878 hall calls |
| Faults, each repaired later, recovery requested | 3 to 6 | 92: door-lock loss 20, sensor conflict 22, serious motion 17, position sensor 12, heartbeat loss 12, load sensor 9 |
| Door obstructions | 0 to 6 | 62 |
| Maintenance window | 50 % | 13 |
| Fire alarm | 30 % | 3 |
| Occupant alarms | 0 to 3 | |
| Medical / freight missions, confirmed by the staff bot | 0 to 2 | 12 medical, 7 freight: all 19 completed |
| Network faults (lost / delayed replies, proposals, telemetry) | 0 to 4 | 38 |

All 20 seeds pass every check after the cabin fixes. A coverage test fails if the seeds stop including any kind of disruption, and a replay test checks that 3 seeds give identical logs when run twice.

Passenger waits across the seeds: mean 38 to 101 s, worst P95 244 s, worst single wait 287 s (observation O-5).

## 4. Defects found in our cabin (fixed)

| # | Found by | Defect | Effect | Fix |
|---|---|---|---|---|
| C-1 | SY-01, SY-11 | A call for the floor where the doors were already open was marked served, but the cabin did not announce the open door again. The virtual passengers board on "doors opened" (D-56). | 5 of 30 people in the morning peak watched an open car leave without them. This was a **regression from last round's fix 3** that the cabin-only tests could not see. | Re-announce DOOR_OPENED when a new call is served at the open door |
| C-2 | SY-20 seed 4 | The last passenger got off at the freight pickup floor itself. The cabin still thought it had to travel to the pickup, ignored staff's "loaded" confirmation, closed and reopened the doors, and waited forever for a second confirmation. | Mission stuck for 48 minutes | Handoff at the open pickup counts as arrived; cabin test EC-08h added |
| C-3 | SY-24, SY-25 | A serious fault during a mission sent no mission status | Freight stayed "in progress" forever with the load stuck in a broken car (against FRT-04); a medical mission stayed "reserved" on a car that would never arrive (against EMG-05) | Nothing loaded yet: FAILED (reason FAULT_HOLD) and release. Loaded: HOLD_PENDING_REVIEW, then IN_PROGRESS again after authorised recovery |

## 5. Findings in other people's modules (documented, not fixed)

### F-1 A medical patient is abandoned when the reserved car breaks down

| | |
|---|---|
| Owner | **Dispatch (Thanh)**; MED-04 re-evaluation is listed under Huy in PROJECT_PLAN Section 8, so the team must decide who re-dispatches |
| Requirement | MED-04 "re-evaluate upon fleet-state change"; EMG-05 "shall not silently drop the emergency task" |
| Test | `test_SY_24b_MED_04_patient_still_picked_up_by_another_car` (strict xfail) |
| Reproduce | Medical call at floor 2; P2 (idle at 10) is reserved at 0 s; P2 has a serious fault at 3 s and is never repaired |
| Observed | The cabin reports FAILED (reason FAULT_HOLD) at 3.0 s and drops the mission. Control Center shows FAILED. Dispatch never proposes the mission to another car, although P1, P3 and P4 were eligible (ETAs 76 s, 46 s and 66 s). Dispatch's own record says **COMPLETED**. |
| Cause | `dispatch/controller.py:182-185`: when a car's telemetry stops listing the mission, Dispatch sets the mission COMPLETED without checking why. |
| Real-world effect | The emergency is visible to the operator as FAILED but no car is sent; someone must re-request it by hand. |
| Suggestion | Treat a mission dropped with FAILED, or by a car in FAULT_HOLD, as "re-select", and record a decision row on who re-dispatches (Dispatch automatically, or Control Center through a new request). |

### F-2 A framework test no cabin can pass (known since the first report)

| | |
|---|---|
| Owner | **An** (`tests/contract/test_cabin.py`) |
| Test | `test_SIM_04_obstruction_beats_door_close_at_the_same_instant` |
| Cause | The test advances the clock to 14.0 s, which delivers the door close, and only then injects the obstruction for the same instant |
| Fix suggested | Inject the obstruction before advancing the clock |

This is the only red test in the repository. It cannot be marked xfail from Hai's folder.

## 6. Observations and open questions for the team

| # | Observation | Why it matters |
|---|---|---|
| O-5 | Under heavy disruption the worst passenger wait was 287 s (P95 up to 244 s), although calls older than AGE_LIMIT = 60 s are prioritised. | DC-06 says 60 s is protection, not a guaranteed pickup, so this is not a failure; the team may want a target for the report. |
| O-6 | The engine always reports `delay_violations = 0`, because only Dispatch knows the committed ETAs (D-61). | The DELAY_CAP rule across the whole system is not measured by these tests. |
| O-7 | Maintenance ordered on a car running a medical mission: the car finishes the mission while in MAINTENANCE mode. | Safe for the patient, but no rule says whether maintenance should wait, cancel or hand over the mission. |
| O-8 | These system tests sit in `tests/cabin/` only because that is Hai's folder. | They test everyone's code; An may prefer to move them to a shared `tests/system/` folder. |
| O-9 | Alerts raised by Control Center itself (ALARM, FIRE, UNAVAILABLE) exist only in its snapshot, not as messages. | Not a defect, but anyone testing alerts must read the snapshot; the harness does. |

The earlier cabin questions Q1, Q3 to Q9 and O-1 to O-4 are in [TEST_REPORT.md](TEST_REPORT.md) and [EDGE_CASES.md](EDGE_CASES.md).
