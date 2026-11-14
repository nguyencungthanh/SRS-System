# Smart Elevator System - Implementation Plan

> **What this file is:** the plan for *what* we build and *who* builds it: goal, scope, locked stack, architecture, each person's work packages, integration order and acceptance tests.
> **Use it to:** find your tasks and the SRS sections behind them, and to know when your part counts as done. For *how* we work together (Git, PRs, AI rules), see [CONTRIBUTING.md](CONTRIBUTING.md).

Team: **An, Thanh, Hai, Huy** (coding team). Source: [docs/SRS-Group2-v1.1.pdf](docs/SRS-Group2-v1.1.pdf), Smart Elevator System SRS v1.1.

## 1. Goal and scope

Build the software-only Smart Elevator simulator described in the SRS:

- 40 floors
- passenger cars P1-P4 and goods car G1
- Dispatch, Cabin, Control Center
- a deterministic simulation engine
- a web UI
- offline SATSA tuning

Work proceeds in this order: the framework is built and reviewed by the whole team, then frozen (Section 4); each module passes its own contract tests against fakes; An integrates the real modules (Section 6); finally AT-01 to AT-06 pass, or are listed as known failures.

The project is graded 50% on implementation and 50% on how we used AI. Coders do not make slides. Each coder does leave short AI notes in every PR (CONTRIBUTING.md, Section 6), which the presenters use as raw material.

### Scope decisions

The main scope choices are below. Every smaller value the SRS leaves open (timeouts, door timing, capabilities, λ/μ...) is in [DECISIONS.md](DECISIONS.md).

| Item | Decision |
|---|---|
| Transport | **In-process only.** Messages keep the exact SRS 3.3 / Appendix D fields. Real gRPC is deferred, which SRS 3.4 allows: "a single-process simulator can implement the same logical interfaces". |
| Authentication | Role picker in the UI plus backend role checks. No passwords or login system. |
| Fire recall | Event exists and is displayed. Only minimal behaviour: suspend ordinary planning. |
| SATSA | In scope. Grid search over the 55 (R, W) pairs is built first, then SATSA on the same evaluator. |
| Missing reference script | `Smart_Elevator_Dispatch_Feasibility_Demo.py` is not in this repo. The SRS numbers are re-derived in our own tests instead. |

## 2. Locked stack

All four use exactly this. Do not let your AI pick a different library.

| Item | Choice |
|---|---|
| Language | Python >= 3.12 |
| Data types | `dataclasses` + `enum.Enum` (no Pydantic inside the core) |
| API | FastAPI: REST for commands, one WebSocket pushing snapshots |
| UI | One plain HTML/CSS/JS page in `web/`, no build step, no framework |
| Tests | `pytest` |
| Lint / format | `ruff check` + `ruff format` |
| Run | `python -m app` (server + UI), `python -m app.headless --scenario AT-01` |
| Dependencies | `requirements.txt` pinned by An. Nobody adds a package without telling An. |

## 3. Working model: one framework, three independent modules

```text
                 ┌──────────────── An: framework ─────────────────┐
                 │ contracts/  messages · enums · config ·        │
                 │             interfaces · routing               │
                 │ fakes/      FakeDispatch · FakeCabin · FakeCC  │
                 │ tests/contract/  the "exam" for each module    │
                 │ simulation/ event queue · clock · replay · log │
                 │ app/        wiring: --dispatch=real|fake ...   │
                 └───────▲───────────────▲───────────────▲────────┘
                         │ implements    │ implements    │ implements
                   Thanh: dispatch/  Hai: cabin/   Huy: control_center/ + api/ + web/
```

- **The framework is the only shared dependency.** Thanh, Hai and Huy never import each other's code and never need to talk to each other to make progress.
- **Each module is a pure message handler.** `handle(msg, now) -> list[Message]`. It never calls another module. The engine delivers every message.
- **Each module is "done" when its contract tests pass.** Those tests live in `tests/contract/test_<module>.py`. You can always run the whole system with your module real and the others fake.
- **Integration is swapping fakes for real modules.** An does this; nobody else needs to coordinate it.
- **If the framework is wrong or missing something, tell An.** Do not patch `contracts/` yourself (CONTRIBUTING.md, Section 4).

## 4. The framework (An, first - blocks everyone)

An builds this first, using AI, from the SRS. The whole team then reviews it together before splitting up. This review matters: if the framework is wrong, every module is wrong.

| # | Deliverable | Content |
|---|---|---|
| F1 | `contracts/messages.py` | One frozen dataclass per message in the table below. Every message has `msg_id`, `t_sim` and `deliver_at`. |
| F2 | `contracts/enums.py` | `Direction`, `DoorState` (CLOSED, OPENING, OPEN, CLOSING, OBSTRUCTED), `CabinMode` (AUTO, FULL, OVERLOAD_HOLD, FAULT_HOLD, MAINTENANCE, MISSION_DRAINING, MISSION_EXCLUSIVE, FIRE), `CallStatus` (PENDING, RESERVED, ASSIGNED, SERVED), `MissionStatus` (REQUESTED, APPROVED, REJECTED, UNAVAILABLE, RESERVED, DRAINING, IN_PROGRESS, COMPLETED, FAILED, HOLD_PENDING_REVIEW), `NackReason`, `Role`, `EventType`, plus the other enums in `contracts/enums.py` |
| F3 | `contracts/config.py` | `SECONDS_PER_FLOOR=2`, `DWELL_S=6`, `FLOORS=40`, `CARS=("P1","P2","P3","P4","G1")`, `R=3`, `W=5`, `DELAY_CAP_S=10`, `AGE_LIMIT_S=60`, `FULL_LOAD_KG=900`, `MAX_LOAD_KG=1000`, `INSERT_CUTOFF_FLOORS=1`, `ACK_TIMEOUT_S`, `TELEMETRY_STALE_S`, door timings, capabilities, `POLICY_VERSION`. Values for anything the SRS leaves open come from `DECISIONS.md`. |
| F4 | `contracts/interfaces.py` | `class Module(Protocol): name: str; def handle(self, msg, now) -> list[Message]`. Typed subclasses `DispatchModule`, `CabinModule` (one instance per car), `ControlCenterModule`, and `snapshot() -> dict` for the UI. Each package exports a factory: `dispatch.create(policy, algorithm)`, `cabin.create(init)`, `control_center.create()`; fakes export the same. |
| F5 | `contracts/routing.py` | Which module receives which message type (table below). |
| F6 | `fakes/` | `FakeDispatch`: assigns the nearest idle car. `FakeCabin`: always ACKs and jumps between floors on timers. `FakeControlCenter`: approves everything. Simple, but they emit correctly shaped messages. |
| F7 | `tests/contract/` | One file per module, written from SRS numbers **before** the module exists. Covers the AT-01 to AT-06 oracles plus the edge cases in Section 7. Tests use only `contracts/` and fakes. |
| F8 | `simulation/` + `app/` | Event queue ordered by (time, priority category, sequence) as in SRS 7.7. Also: clock, pause/reset, seeded replay, JSON-lines event log, and CLI flags to choose real or fake per module. |
| F9 | `AI_CONTEXT.md` | The text every coder pastes into their AI first: stack, rules, the module interface, and "only edit your own folder". |
| F10 | `contracts/ui.py` | The engine-to-UI contract: snapshot shape pushed over the WebSocket, and the list of REST commands (see "Engine-UI contract" below). `FakeSnapshot` data so Huy can build the UI before the engine runs. |
| F11 | `DECISIONS.md` | Every value the SRS leaves open (timeouts, staleness, door timing, capabilities, λ/μ, traces). Reviewed together with the contract. |

### Messages (from SRS 3.3, 3.4, 7.9 and Appendix D)

Contract v1. The source of truth is `contracts/messages.py` (fields, docstrings) and `contracts/routing.py` (recipients, priority). Every message also has `msg_id`, `t_sim`, `deliver_at`. The engine logs every message.

| Message | Producer | Consumer(s) | Key fields |
|---|---|---|---|
| `HallCallPressed` | UI / scenario | Dispatch | floor, direction |
| `HallCallAck` | Dispatch | log only | request_id?, floor, direction, accepted, duplicate, status?, reason? (D-18) |
| `CabinDestinationPressed` | UI / scenario | Cabin | car_id, floor, passenger_id |
| `PassengerBoard` / `PassengerExit` | Scenario / UI | Cabin | car_id, passenger_id, weight_kg |
| `DoorRequest` | UI | Cabin | car_id, open (D-51) |
| `AlarmPressed` | UI | Cabin, Control Center | car_id |
| `RouteProposal` | Dispatch | Cabin (by car_id) | command_id, car_id, expected_route_version, ordered_stops: `Stop`s (D-16), expiry_ts, mission_id?, exclusive |
| `RouteReply` | Cabin | Dispatch | command_id, car_id, accepted, committed_version, reason? |
| `RouteVersionQuery` / `RouteVersionReport` | Dispatch / Cabin | Cabin / Dispatch | command_id, car_id / + seen, accepted, route_version (D-17) |
| `CabinTelemetry` | Cabin | Dispatch, Control Center | car_id, position, direction, moving, door, locked, door_zone, obstruction, load_kg, passengers, mode, route_version, stops, faults, sensors_valid, mission_id? |
| `ServiceEvent` | Cabin | Dispatch, Control Center | car_id, event (`ServiceEventType`), floor, ref_id?, passenger_id?, reason? |
| `MissionRequest` | UI | Control Center | role, mission_type, origin?, destination?, material?, weight_kg? |
| `MissionApproval` | Control Center | Dispatch | mission_id, role, mission_type, origin, destination, allowed_cars, material?, weight_kg? |
| `MissionStatusUpdate` | Dispatch / Cabin / CC | Control Center | mission_id, status, car_id?, reason?, candidates (D-28) |
| `MissionLoadConfirmed` | UI | Cabin | mission_id, car_id, action LOADED/UNLOADED/LOAD_FAILED, weight_kg (D-25) |
| `Alert` / `AlertAcknowledged` | any / UI | Control Center | kind, car_id?, ref_id?, details / alert_id, role |
| `FaultInjected` / `FaultCleared` | Scenario / UI | Cabin | car_id, kind (D-09) |
| `FireAlarm` | Scenario / UI | Dispatch, every Cabin, Control Center | active (D-19) |
| `MaintenanceSet` | UI | Cabin | car_id, on, role (D-09) |
| `RecoveryRequest` / `RecoveryApproved` | UI / Control Center | Control Center / Cabin | car_id, role / approved_by |
| `PolicyRecommended` / `PolicyDecision` | Optimizer / UI | Control Center | run_id, policy, objective, scenario_set / run_id, approve, role (D-37) |
| `PolicyActivated` | Control Center | Dispatch | policy: `Policy(version, R, W)` (D-36) |
| `Timer` | any module | that same module | target, key, ref_id? (D-08) |

**After the freeze names do not change** without the procedure in CONTRIBUTING.md, Section 4.

### Engine-UI contract (F10)

The UI never talks to a module directly. It sends commands over REST, and the engine turns each command into a message (table above). The UI reads state only from snapshots.

**Snapshot** - pushed over the WebSocket `/ws` after every processed timestamp (and once on connect):

```text
{
  t_sim, sim_state (RUNNING / PAUSED / STOPPED), seed, contract_version, policy {version, R, W},
  cars:     [{car_id, position, floor_display, direction, door, locked, load_kg, passengers,
              mode, faults, route_version, stops, mission_id, telemetry_age_s}],
  calls:    [{request_id, floor, direction, status, owner, created_at}],
  dests:    [{car_id, floor, passenger_id, status}],
  missions: [{mission_id, mission_type, origin, destination, status, car_id, reason,
              candidates: [{car_id, eta_s, excluded}]}],
  alerts:   [{alert_id, kind, car_id, details, t_sim, acknowledged}],
  metrics:  {mean_wait, median_wait, p95_wait, max_wait, unserved, delay_violations},
  last_events: [the latest ~50 log lines]
}
```

**REST commands** (all `POST`, JSON body; each returns `{ok, id or reason}`):

| Endpoint | Becomes message | Who may call |
|---|---|---|
| `/hall-call` | `HallCallPressed` | anyone |
| `/destination` | `CabinDestinationPressed` | passenger in that car |
| `/passenger/board`, `/passenger/exit` | `PassengerBoard` / `PassengerExit` | simulator operator |
| `/door` | `DoorRequest` | passenger in that car |
| `/alarm` | `AlarmPressed` | anyone |
| `/mission` | `MissionRequest` | MEDICAL_COORDINATOR, BUILDING_STAFF |
| `/mission/confirm` | `MissionLoadConfirmed` | MEDICAL_COORDINATOR, BUILDING_STAFF |
| `/fault`, `/fire` | `FaultInjected` / `FireAlarm` | simulator operator |
| `/fault/clear` | `FaultCleared` | OPERATOR, MAINTAINER |
| `/maintenance` | `MaintenanceSet` | MAINTAINER |
| `/alert/ack` | `AlertAcknowledged` | OPERATOR, SUPERVISOR |
| `/recovery` | `RecoveryRequest` | SUPERVISOR |
| `/policy/approve` | `PolicyDecision`; Control Center then emits `PolicyActivated` | SUPERVISOR |
| `/sim/start`, `/sim/pause`, `/sim/reset`, `/sim/replay` | engine control, not a module message | simulator operator |
| `/optimization/run` | starts an offline grid / SATSA run | OPERATOR |

`GET /snapshot` returns the current snapshot once (for debugging and tests). Roles follow DECISIONS.md D-23.

## 5. Ownership and work packages

| Person | Owns (folders) | One-line responsibility |
|---|---|---|
| **An** | `contracts/`, `fakes/`, `tests/contract/`, `simulation/`, `app/`, `optimization/`, `scenarios/` | Framework, engine, integration, metrics, grid search + SATSA |
| **Thanh** | `dispatch/`, `tests/dispatch/` | Hall calls, ETA, insertion, grouping, ownership, mission car selection |
| **Hai** | `cabin/`, `tests/cabin/` | Motion, doors, load, sensors, route ACK/NACK, faults, mission execution |
| **Huy** | `control_center/`, `api/`, `web/`, `tests/control_center/` | Roles, missions, alerts, recovery, policy approval, API, all UI screens |

For each package: give your AI the listed SRS sections, `AI_CONTEXT.md`, `contracts/` and your contract test file. Ask for a plan first, correct the plan, then implement.

### An - framework, engine, evaluation

| ID | Package | SRS | Done when |
|---|---|---|---|
| AN-01 | Framework F1-F11 (Section 4) | 3.3, 3.4, 7.1, 7.7, 7.9, App. D | Reviewed by the team and frozen. Whole system runs with all fakes. |
| AN-02 | Deterministic engine: queue order, clock, RUNNING/PAUSED/STOPPED, reset, seeded replay, fault injection | SIM-01..05, SIM-08, SYS-01, SYS-04, NFR-05 | Same trace + seed produces an identical log. Pause freezes everything. |
| AN-03 | Event log + metrics: mean, median, P95 and max wait; unserved; delay violations; medical/freight times; dispatch wall time | LOG-01..03, NFR-03, NFR-04 | Metrics printed after a headless run |
| AN-04 | Scenarios: AT-01 to AT-06 setups plus seeded random traffic generators (train / held-out) | 6.4, 6.5, 9.2 | `python -m app.headless --scenario AT-0x` works |
| AN-05 | Integration in the order of Section 6; route failing tests back to their owner | 9.6 | AT-01 to AT-05 pass on real modules |
| AN-06 | Evaluator `evaluate(R, W, traces) -> metrics or rejection`, fitness J(θ), caching, hard-invariant rejection; grid search over R=1..5, W=0..10; comparison with fixed (3,5) and the two baseline policies Thanh provides | OPT-04, OPT-05, LOG-04, NFR-06, 6.5 | Comparison table on held-out traces |
| AN-07 | SATSA search: Gaussian seed updates, stagnation-triggered extra seeds, Nelder-Mead refinement, clamp/round to integer (R, W) | OPT-01..03, 4.8 | AT-06 passes; SATSA result shown next to grid and (3,5) |

### Thanh - Dispatch

| ID | Package | SRS | Done when |
|---|---|---|---|
| TH-01 | Hall-call repository: validate 1-40, reject DOWN@1 and UP@40, deduplicate (floor, direction), keep `created_at`, PENDING→RESERVED→ASSIGNED→SERVED | DC-01, REQ-01, REQ-02, SYS-03 | `test_DC_01_*`, `test_REQ_01_*`, `test_REQ_02_*`, `test_SYS_03_*` green |
| TH-02 | Eligible cars, ETA (eq. 1, 2), safe same-direction insertion, one-floor cutoff, cumulative DELAY_CAP against the **fixed** committed reference | DC-02, DC-04, DC-05, MOVE-04, NFR-02 | AT-01 pickup 8 s; TC-DELAY-01 rejects 32 vs 20 |
| TH-03 | Ownership: reserve one car, confirm only on ACK, NACK→try next car, timeout→`RouteVersionQuery` before releasing, idempotent `command_id` | REQ-03, REQ-04, SAF-04, SIM-07 | AT-05: never two owners |
| TH-04 | Grouping R/W with a fixed anchor and no waiting; re-evaluate PENDING calls on telemetry; AGE_LIMIT protection; read R/W from the active policy | DC-03, DC-06, SIM-05 | AT-02: 29 at 4 s, 27 at 14 s |
| TH-05 | Medical selection (eq. 5) across all capable cars after onboard drain; deterministic tie-break; UNAVAILABLE if none. Freight: G1 first, then permitted P-car. Return unserved calls to the pool with original ages. | MED-01..04, EMG-03, FRT-02 (selection) | AT-03: P1=90, P2=20, P4=58, P2 chosen |
| TH-06 | Two baseline policies behind the same interface: first-idle, directional sweep; `PolicyActivated` applies only at the next decision boundary | 6.5, OPT-06 (consumer) | An's evaluator can run all three policies |

### Hai - Cabin (one class, five instances)

| ID | Package | SRS | Done when |
|---|---|---|---|
| HA-01 | Cabin state: float position, direction, mode, route + version, door, lock, load, passenger list, faults; telemetry on every change | SYS-02, MOVE-01, SEN-01 | Telemetry matches contract |
| HA-02 | Motion: 2 s/floor without teleporting, stop only at valid stops, no reversal while committed, reject late insertion under 1 floor | MOVE-02, MOVE-03, MOVE-05, MOVE-06 | `test_MOVE_*` green |
| HA-03 | Door state machine with landing-zone check, 6 s dwell, obstruction → OPENING, move only when CLOSED and locked | DOOR-01..05, SAF-01, SAF-02 | TC-DOOR-01/02 green |
| HA-04 | `RouteProposal` handling: version and local feasibility checks; ACK or NACK with a reason; keep the old route on NACK; cache the result per `command_id`; same id with a different payload is an error; answer `RouteVersionQuery` | CB-01, SIM-06 | TC-IDEMP-01, AT-05 cabin side |
| HA-05 | Boarding and destinations only after boarding; pickup and drop-off are separate events; 900 kg bypasses new pickups; >1000 kg holds; manual exit on overload | CB-02..05, DOOR-06, REQ-05, SEN-03, SEN-04 | AT-01 events: 8 s pickup, 58 s arrival, 64 s dwell end |
| HA-06 | Faults: FAULT_HOLD between floors (doors locked, passengers kept); ALARM, FIRE and FAULT kept as separate events; return to AUTO only on `RecoveryApproved` **and** passing readiness checks; stale or conflicting sensors never count as safe | SAF-03, EMG-01, EMG-02, EMG-04 (cabin side), SEN-02 | TC-FAULT-01 green |
| HA-07 | Mission execution: drain onboard passengers; empty handoff only when stationary, count=0 and sensors consistent, else HOLD_PENDING_REVIEW; direct exclusive leg; delivery/unloading confirmation; failed loading | SEN-05, SEN-06, MED-05, FRT-02..04 | AT-04: 44 / 50 / 58 / 64 / 122 s |

### Huy - Control Center, API, UI

| ID | Package | SRS | Done when |
|---|---|---|---|
| HY-01 | Roles and permissions matrix; validate mission requests (floors, type, material, capability); a rejected grant changes nothing; audit log | CC-01, CC-03, FRT-01, 5.3 | `test_CC_01_*`, `test_CC_03_*`, `test_FRT_01_*` green |
| HY-02 | Mission lifecycle: track `MissionStatusUpdate`; persistent UNAVAILABLE + alert; never shows a failed assignment as success | MED-04, EMG-05, CC-02, UI-04 | TC-MED-02 green |
| HY-03 | Alerts: ALARM is never mapped to MEDICAL; supervisor recovery flow | EMG-01, CC-04, EMG-04 | TC-RECOVERY-01 green |
| HY-04 | Policy registry: versioned (R, W) recommendations from An, approve/reject, emit `PolicyActivated` | OPT-06, US-016 | Unapproved config changes nothing |
| HY-05 | FastAPI: the REST commands and WebSocket snapshot stream exactly as in the Engine-UI contract (Section 4, F10) | 3.1, 7.8 | Works against fakes |
| HY-06 | UI screens: Fleet view, Hall panel, Cabin panel, Control Center, Simulation console, Optimization view | UI-01..05, 3.1, 7.8 | All six screens work on fake data first, then on real data |

## 6. Dependencies and integration order

Before integration nobody waits for anybody except An's framework. Use the fakes.

| Person | Needs from | Until it exists, use |
|---|---|---|
| An | nobody | fake `evaluate()` for SATSA development |
| Thanh | framework | `FakeCabin` (always ACKs) |
| Hai | framework | contract tests send `RouteProposal`s directly |
| Huy | framework (F10) | `FakeSnapshot` data, then the engine with `--dispatch=fake --cabin=fake --cc=fake` |

Integration (An runs it; owners stay reachable to fix their module):

| Step | Plug in | Requires | Check |
|---|---|---|---|
| ① | Engine (all fakes) | framework | Replay gives an identical log |
| ② | Thanh's Dispatch + Hai's Cabin | ① | AT-01, AT-02, AT-05 |
| ③ | Huy's Control Center (missions) | ② | AT-03, AT-04 |
| ④ | Huy's UI on the real engine | ① (better after ②, ③) | Five cars visible and moving; panels work |
| ⑤ | Evaluator + grid + SATSA on real modules | ② | AT-06; comparison table |

If a module is not ready, integration continues with its fake. A late module never blocks the others.

## 7. Acceptance tests (oracles from the SRS)

All tests start as NOT RUN. Only an actual run changes that.

| Test | Setup | Expected | Main owner |
|---|---|---|---|
| AT-01 | P1 idle at 31; 27 DOWN at t=0; destination 5 | Pickup 8 s; arrival at 5 at 58 s; dwell ends 64 s; separate pickup and drop-off events | Thanh + Hai |
| AT-02 | P1 at 31; 27 DOWN at t=0, 29 DOWN at t=2 | Group allowed; 29 at 4 s, 27 at 14 s (+6 s ≤ 10); first assignment attempt immediate | Thanh |
| TC-DELAY-01 | Committed ETA 20 s; insertion → 26 s, then → 32 s | 26 accepted (+6); 32 rejected (+12 vs **20**, not +6 vs 26) | Thanh |
| AT-03 | Medical at floor 2; P1 36→40, P2 idle at 12, P3 maintenance, P4 28→22, G1 goods-only | P1=90, P2=20, P4=58; P2 chosen after ACK; P3 and G1 excluded with a reason | Thanh + Huy |
| AT-04 | P1 carrying 27→5; freight 1→30 | 44 arrival, 50 exit, 58 material pickup, 64 loaded, 122 delivered; no ordinary stops on the leg | Hai + Huy |
| AT-05 | ACK lost / cabin unsafe | Reservation kept; same `command_id` returns the stored result; changed payload is an error; never two owners | Thanh + Hai |
| AT-06 | Fixed train/held-out traces; R=1..5, W=0..10 | Every (R, W) valid; violators rejected; SATSA vs grid vs (3,5) reported on held-out | An |

Edge cases the contract tests must cover:

- exact and just-outside R, W and DELAY_CAP bounds
- 899 / 900 / 1000 / 1001 kg
- exactly 1 floor vs less than 1 floor to the stop
- stale or conflicting sensors
- all cars busy (call stays PENDING)
- opposite-direction call
- late ACK
- obstruction and door-close at the same timestamp (safety first)
- low load but passengers > 0
- no eligible medical car
- two SATSA points mapping to the same (R, W)

## 8. Requirement accountability (all 89 IDs)

| IDs | Owner |
|---|---|
| DC-01..06, REQ-01..04, SAF-04, SYS-03, SIM-07, MED-01..03, EMG-03, NFR-02 | Thanh |
| CB-01..05, MOVE-01..06, DOOR-01..06, SEN-01..06, SAF-01..03, SYS-02, SYS-04, REQ-05, MED-05, FRT-02..04, EMG-02, SIM-06 | Hai |
| CC-01..04, MED-04, FRT-01, EMG-01, EMG-04, EMG-05, UI-01..05, OPT-06, NFR-01 | Huy |
| SYS-01, SIM-01..05, SIM-08, LOG-01..04, NFR-03..06, OPT-01..05 | An |

## 9. Definition of Done and scope control

A package is **Done** when:

- its contract tests and own unit tests pass;
- it runs in the integrated app;
- its PR is merged with AI notes;
- any SRS ambiguity it hit is recorded in `DECISIONS.md`.

If time runs out, cut in this order:

1. UI polish
2. Optimization view styling
3. Extra traffic scenarios
4. SATSA, but keep the grid search
5. Fire behaviour

Never cut:

- door interlocks
- single ownership
- the DELAY_CAP reference rule
- deterministic replay

Anything cut is listed as **deferred** in the README, not presented as done.
