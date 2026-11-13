# Decisions - what the SRS leaves open

> **What this file is:** every value or behaviour the SRS does not pin down, and what the team chose instead.
> **Use it to:** look up a number before you (or your AI) guess one. If you hit a new gap, add a row here through a PR and tell the team; do not decide it silently in your own code.

Status values: **Proposed** (An's default, awaiting team review), **Agreed** (team accepted), **Changed** (replaced; the row says by what).
Rows marked **Agreed** were accepted by the team on 14/11/2026. Rows marked **Proposed** came out of writing contract v1 (`contracts/`) and wait for the team review. Constants listed here live in `contracts/config.py`; code never hard-codes them.

## Simulation model

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-01 | Time unit | "simulation seconds" | `float` simulation seconds, starting at `0.0` for every scenario | all | Agreed |
| D-02 | Cabin position | Continuous position p(t) (7.3) | `float`, floor 1 = `1.0`, floor 40 = `40.0`. The displayed floor is derived (nearest floor) and never used for safety decisions | Hai, Thanh, Huy | Agreed |
| D-03 | Door timing inside the 6 s dwell | 6 s dwell per served stop; door states in 7.4 | The whole door cycle takes exactly `DWELL_S = 6`: OPENING 1 s, OPEN 4 s, CLOSING 1 s. This reproduces the AT-01 oracle (pickup at 8 s, departure at 14 s) | Hai | Agreed |
| D-04 | Obstruction during closing | Re-enter OPENING (DOOR-04) | Obstruction sends the door to OPENING (1 s), then a fresh OPEN period of 4 s, then CLOSING. Each obstruction adds time; this extra time counts toward ETAs | Hai | Agreed |
| D-05 | Same-timestamp event order | 5 categories in 7.7 | Priority number, lower runs first: `0` safety input, `1` cabin/physical result, `2` control message, `3` passenger input, `4` reporting. Ties broken by insertion sequence number | An | Agreed |
| D-06 | Message delivery delay | Not specified | Default delivery delay `0.0` s. Scenarios may set a delay or drop a message (to test AT-05) through `deliver_at` | An, Thanh, Hai | Agreed |
| D-07 | Passenger weight | Load in kg, no per-person value | A passenger without an explicit weight counts as `75` kg | Hai, An (scenarios) | Agreed |
| D-08 | Timers inside a pure handler | Motion, doors and ACK timeouts need timed events (3.4, 7.3, 7.4); `handle()` cannot wait | A module returns `Timer(target=<its own name>, key, ref_id)` with a future `deliver_at`; the engine delivers it back to `target`. Priority `1` (physical). Timers cannot be cancelled: the module ignores stale ones, e.g. with a counter in `key` or `ref_id`. At `t = 0` and after every reset the engine sends `Timer(target=<name>, key="start")` (`TIMER_START`) to every module, so a module can send its first telemetry and start its own timers | Hai, Thanh, An | Proposed |
| D-09 | Initial cabin state, maintenance, fault inputs | AT-03 needs P3 in maintenance and P1 already carrying a passenger; faults are injected (7.6) | `cabin.create(CabinInit(car_id, floor, mode, passengers, faults, route_version))`; onboard destinations become CAR_DEST stops. `MaintenanceSet(on=True)` enters MAINTENANCE; `on=False` only records the work as done, and return to AUTO still needs `RecoveryApproved` (EMG-04). `FaultInjected` / `FaultCleared` per `FaultKind`: `OBSTRUCTION` is momentary (one input = one reopen), every other fault stays until cleared | Hai, An, Huy | Proposed |

## Dispatch and ownership

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-10 | ACK timeout | "deadline expires" (3.4), no value | `ACK_TIMEOUT_S = 2.0`. `RouteProposal.expiry_ts = t_sim + 2.0`. After timeout Dispatch sends `RouteVersionQuery` before releasing ownership | Thanh, Hai | Agreed |
| D-11 | Telemetry freshness | "stale" readings excluded, no value | Cabin sends telemetry on every state change and as a heartbeat at least every `1.0` s. Telemetry older than `TELEMETRY_STALE_S = 3.0` makes the car ineligible | Hai, Thanh | Agreed |
| D-12 | When PENDING calls are re-evaluated | "on state change" (9.3) | On every `CabinTelemetry`, `ServiceEvent`, `RouteReply` and new Hall Call | Thanh | Agreed |
| D-13 | Tie-break between equal ETAs | "stable tie-breaking" (DC-05, MED-02) | Lowest ETA, then fewest committed stops, then car ID in order P1, P2, P3, P4, G1 | Thanh | Agreed |
| D-14 | Same `command_id`, different payload | "is an error" (App. D) | Cabin replies NACK with reason `PAYLOAD_CONFLICT`, keeps its route, and the engine logs an error event | Hai, Thanh | Agreed |
| D-15 | `command_id` / `request_id` format | Not specified | Strings with a prefix and a counter, no UUIDs, so replay stays identical. The engine stamps `msg_id` (`MSG-000001`). Each owner counts its own IDs with `contracts/ids.py` `IdGen`: Dispatch `HC-`, `RC-`; Control Center `MS-`; scenarios and UI `PAX-`; optimizer `RUN-`, `POL-`. `alert_id` is the Alert's `msg_id`. No seed is needed: a counter inside a deterministic module is already deterministic | all | Changed (contract v1: one counter per owner instead of one engine generator) |
| D-16 | What a route stop is | `ordered_stops` (3.4) has no shape | `Stop(floor, kind, ref_id, direction)`. Kinds: HALL_PICKUP (ref = `request_id`, direction set), CAR_DEST (ref = `passenger_id`), MISSION_PICKUP / MISSION_DROPOFF (ref = `mission_id`). `ordered_stops` is the complete new route, not a delta. `route_version` identifies the exact stop list: the Cabin increments it on **every** change (accepted proposal, accepted destination, served stop, stop removed by the Cabin), so a proposal built from older telemetry gets `VERSION_MISMATCH` instead of re-adding a stop that was just served | Thanh, Hai | Proposed |
| D-17 | "Same payload" and reconciliation | Idempotency invariant (App. D); reconcile after a missing ACK (REQ-04) | Same payload = equal `payload_key(msg)`: every field except `msg_id`, `t_sim`, `deliver_at`. `expiry_ts` is part of the payload, so a retry must resend the identical proposal. `RouteVersionReport` answers about the queried `command_id`: `seen`, the stored `accepted`, and the current `route_version` | Thanh, Hai | Proposed |
| D-18 | Invalid Hall Call | Reject it (SYS-03); `CallStatus` has no rejected state | `HallCallAck(accepted=False, request_id=None, reason=FLOOR_OUT_OF_RANGE, TERMINAL_DIRECTION or INVALID_DIRECTION)`. `HallCallAck` goes only to the passengers module (D-56); the UI reads call state from the snapshot | Thanh, Huy | Proposed |
| D-19 | Fan-out and priority of inputs | Not specified | `FireAlarm` goes to Dispatch, all 5 Cabins and Control Center. `AlarmPressed` goes to that Cabin and Control Center, never to Dispatch (EMG-01), with priority `3` (occupant input, not a safety input). Only Control Center turns `AlarmPressed` into an ALARM alert; the Cabin applies its alarm policy but does not send `Alert(ALARM)`, so one press gives one alert. Full table: `contracts/routing.py` | all | Proposed |

## Missions and capabilities

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-20 | Medical capability | Building configures it (2.6, App. B) | P1-P4 may serve MEDICAL. G1 may not. A car in MAINTENANCE, FAULT_HOLD, FIRE or with stale telemetry is excluded with a reason | Thanh, Huy | Agreed |
| D-21 | Freight capability | G1 first; "any configured passenger car if policy allows" (4.5) | G1 is tried first. If G1 is not eligible, P1-P4 may carry freight up to `MAX_LOAD_KG`. The AT-04 scenario makes G1 ineligible so P1 is chosen | Thanh, Huy | Agreed |
| D-22 | Pending hall pickups of a car chosen for MEDICAL | Returned to the pool (MED-03) | Only onboard drop-offs count in MedicalETA (eq. 5). Unpicked hall calls of that car return to PENDING with their original `created_at` | Thanh | Agreed |
| D-23 | Roles | Role-gated missions; auth mechanism open (App. B) | Role chosen in the UI (PASSENGER, MEDICAL_COORDINATOR, BUILDING_STAFF, OPERATOR, SUPERVISOR, MAINTAINER). Backend checks the role on every privileged command. No passwords | Huy | Agreed |
| D-24 | Fire recall | "configured simulated fire response" (7.6) | Minimal: Dispatch stops assigning new ordinary work, UI shows FIRE alert. Cabins finish their current leg safely. Nothing more | Thanh, Hai, Huy | Agreed |
| D-25 | Loading and unloading confirmation | "staff confirms loading", "unloading confirmation" (FRT-03, FRT-04), "verified medical boarding" (MED-05) | `MissionLoadConfirmed(mission_id, car_id, action, weight_kg)` from the UI (`/mission/confirm`) to the Cabin. LOADED starts the exclusive leg, UNLOADED completes the mission, LOAD_FAILED ends it FAILED or HOLD_PENDING_REVIEW. Same message for FREIGHT and MEDICAL | Hai, Huy | Proposed |
| D-26 | Name of the held mission state | SEN-06 "HOLD_PENDING_REVIEW"; FRT-04 "held/failed" | One status, `HOLD_PENDING_REVIEW`; the plan's `HELD` is dropped | Hai, Huy | Proposed |
| D-27 | Who may call simulator commands | REST table says "simulator operator" | That is role `OPERATOR`; no extra role | Huy | Proposed |
| D-28 | Showing why each car was or was not chosen | AT-03: P3 and G1 "excluded with a reason" | `MissionStatusUpdate.candidates` holds one `Candidate(car_id, eta_s, excluded)` per car Dispatch considered | Thanh, Huy | Proposed |
| D-29 | Who sends which mission status | "Dispatch / Cabin / CC" all send `MissionStatusUpdate` | Control Center: APPROVED, REJECTED. Dispatch: RESERVED (after the Cabin ACK), UNAVAILABLE. Cabin: DRAINING, IN_PROGRESS, COMPLETED, FAILED, HOLD_PENDING_REVIEW. Control Center raises the operator alert for UNAVAILABLE itself when it receives that status; Dispatch sends no separate `Alert` for it | Thanh, Hai, Huy | Proposed |

## Metrics and optimization

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-30 | Wait time | Mean, median, P95, max pickup wait (NFR-03) | Wait = time of `HALL_CALL_SERVED` minus the call's `created_at`. Percentiles use the nearest-rank method | An | Agreed |
| D-31 | Unserved call | Counted in J(θ) | A call not served when the scenario ends. Each trace ends with a drain period of `300` s without new calls | An | Agreed |
| D-32 | λ and μ in J(θ) | "fixed and approved before each run" (4.8.2) | `λ = 0.5`, `μ = 100` s | An | Agreed |
| D-33 | Search domain | R in 1..5, W in 0..10 (proposed) | Adopted as written: 55 pairs. Baseline (3, 5) always evaluated | An | Agreed |
| D-34 | Traffic traces | Fixed training and held-out traces | 5 training seeds (`1-5`) and 5 held-out seeds (`101-105`). Each trace: `1800` s of random Hall Calls (uniform floors, valid direction), then the drain period from D-31 | An | Agreed |
| D-35 | SATSA budget | "approved offline evaluation budget" | At most `30` unique (R, W) evaluations per run, so SATSA uses fewer evaluations than the 55-pair grid | An | Agreed |
| D-36 | R and W as a value, not a constant | R, W change per optimization run and on approval (OPT-02, OPT-06) | Frozen `Policy(version, R, W)` passed to `dispatch.create(policy, algorithm)` and replaced only by `PolicyActivated`. `config.py` keeps only the default `POL-0` = (3, 5) | Thanh, Huy, An | Proposed |
| D-37 | Policy recommendation flow | OptimizationResult (App. D); approval (OPT-06) | Optimizer sends `PolicyRecommended(run_id, policy, objective, scenario_set)` to Control Center. The supervisor answers with `PolicyDecision(run_id, approve)`; on approval Control Center emits `PolicyActivated` | Huy, An | Proposed |

## Project scope

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-40 | Transport | gRPC target; in-process allowed (3.4) | In-process only. Message fields keep the SRS 3.3 / Appendix D names | all | Agreed |
| D-41 | Reference script | `Smart_Elevator_Dispatch_Feasibility_Demo.py` (6.3) | Not available. The SRS worked examples are re-derived as contract tests instead | An | Agreed |

## Contract details (found while writing the contract tests, F7)

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-50 | What R and W restrict | "same-direction grouping only if floor span ≤ R and anchor time window ≤ W" (DC-03), but insertion is also allowed by MOVE-04 | A new Hall Call may join a car that already has **pending HALL_PICKUP stops in the same direction** only if the whole group (those pickups plus the new call) has max floor − min floor ≤ R and max `created_at` − min `created_at` ≤ W; the anchor is the oldest call in the group. CAR_DEST stops never count. A car with no pending hall pickup can always take the call (still subject to DELAY_CAP, the insertion cutoff and eligibility). Without this rule R and W would change nothing, and the optimizer would have nothing to tune | Thanh, An | Proposed |
| D-51 | Door buttons in the cabin | Cabin panel "door requests" (3.1, 7.8); DOOR-03 extension; DOOR-05 reject while moving | New message `DoorRequest(car_id, open)`, endpoint `/door`. `open=True`: while OPEN restart the 4 s OPEN period; while CLOSING reopen like an obstruction (D-04); while stationary at a landing with the door CLOSED start a normal door cycle. `open=False` while OPEN: start CLOSING now. Every other case, including any request while moving, is ignored | Hai, Huy | Proposed |
| D-52 | When mission events happen | "staff confirms loading", "delivery and unloading confirmation" (FRT-03, FRT-04, MED-05) | At the pickup the Cabin waits for `MissionLoadConfirmed(LOADED)`: then it sends `MISSION_PICKUP_DONE` and `IN_PROGRESS`, and leaves when both the dwell is over and LOADED has arrived. At the destination it sends `MISSION_DELIVERY_DONE` on arrival and `COMPLETED` on `UNLOADED`, which also ends the exclusive reservation. `LOAD_FAILED` sends `FAILED` and ends the reservation. AT-04: arrive at 1 at 58 s, LOADED during the dwell, leave at 64 s, arrive at 30 at 122 s | Hai, Thanh, Huy | Proposed |
| D-53 | Who may do what | Role-gated missions (CC-01, 5.3); D-23 lists roles only | MEDICAL request: MEDICAL_COORDINATOR. FREIGHT request: BUILDING_STAFF. Recovery request and policy decision: SUPERVISOR. Alert acknowledgement: OPERATOR or SUPERVISOR. The API checks the role first (`contracts/ui.py` COMMANDS); Control Center checks again and ignores or rejects anything else | Huy | Proposed |
| D-54 | A valid freight declaration | Material type, weight, floors, rated payload (FRT-01) | FREIGHT needs a non-empty `material`, a `weight_kg`, and `weight_kg ≤ FREIGHT_MAX_KG` (1000 kg; exactly 1000 is valid). No per-car material restrictions. MEDICAL needs no material or weight. Both need origin and destination in 1..40 and different from each other | Huy | Proposed |
| D-55 | Hall calls of a car that stops serving | Reassign unpicked calls "after ownership reconciliation" (SAF-03, EMG-03) | When a Cabin enters FAULT_HOLD or MAINTENANCE it removes its HALL_PICKUP stops (route_version + 1) and keeps its CAR_DEST stops. Dispatch treats telemetry that no longer lists an assigned call as the release, and only then gives the call to another car, with its original `created_at` | Hai, Thanh | Proposed |

## Engine and runtime (F8)

| ID | Topic | What the SRS says | Decision | Affects | Status |
|---|---|---|---|---|---|
| D-56 | Who moves the virtual passengers | Boarding, destination choice and exit are simulated (7.2, SEN-04); a Hall Call trace has no destination | Module `passengers` (`simulation/passengers.py`, owned by An). A scenario lists `PassengerIntent(t, passenger_id, origin, destination, weight_kg)`. At `t` it presses the Hall Call. When a car that served that call opens its door at the origin, waiting passengers board (`PassengerBoard`, then `CabinDestinationPressed`) while the cabin stays ≤ `MAX_LOAD_KG`; the rest press again after `DWELL_S` (SEN-04). On every `DOOR_OPENED`, passengers whose destination is that floor exit first. Passengers already on board in the scenario's fleet are known from the start. It receives `HallCallAck` and `ServiceEvent` | An, Thanh, Hai | Proposed |
| D-57 | Losing or delaying a message (AT-05) | "a RouteProposal times out while its original ACK is delayed" (7.7); D-06 | A scenario lists `NetworkRule(msg_type, action, car_id, nth, delay_s)`. The bus applies it to module outputs (never to Timers): the `nth` matching message is dropped (logged as `MESSAGE_DROPPED`) or delayed by `delay_s` | An | Proposed |
| D-58 | How the API drives the engine | Not specified | `contracts/runtime.py` `SimControl`: `submit(msg)`, `control(command, **args)` (START, PAUSE, RESET, REPLAY, SPEED), `snapshot()`, `subscribe(callback)`, `scenarios()`. Implemented by `app/runner.py`. Huy's `api/` exports `create_app(control) -> FastAPI` and builds messages with `t_sim=0.0`; the runner replaces `t_sim` with the simulation time. Wall-clock pacing exists only in `app/`, never in `simulation/` | Huy, An | Proposed |
| D-59 | Trace and replay format | "replay a saved event trace and seed" (SIM-03, LOG-02) | JSON lines: a header (`scenario`, `seed`, `contract_version`, module kinds, policy), one line per submitted input, and a footer (`end_t`, `log_sha256`). Replay resets the scenario, runs to each input's time, injects it, runs to `end_t` and compares the SHA-256 of the log: IDENTICAL or DIFFERENT | An | Proposed |
| D-60 | Real or fake modules by default | `--dispatch=real\|fake` (PROJECT_PLAN F8) | Each flag also accepts `auto` (the default): use the real package if it can be imported, otherwise the fake, and print which one was chosen | all | Proposed |
| D-61 | `delay_violations` in the snapshot | Commitment delay violations are a metric (LOG-03) | Only Dispatch knows committed reference ETAs, so the engine reports `0` until the evaluator (AN-03) measures it from the log | An, Huy | Proposed |
