# AI_CONTEXT - paste this into your AI first

> **What this file is:** the shared starting context for every coder's AI assistant (framework F9).
> **Use it to:** start every AI session. Paste the "Everyone" part plus **your own section**, then the files listed there. Keep it in English so every tool reads it the same way.

---

## Everyone

### The project in five lines

We build a software-only **Smart Elevator simulator** from `docs/SRS-Group2-v1.1.pdf` (SRS v1.1): 40 floors, passenger cars P1-P4, goods car G1.
Three modules talk **only through messages**: **Dispatch** (assigns hall calls and missions), **Cabin** (one per car: motion, doors, load, safety veto), **Control Center** (roles, missions, alerts, recovery, policy approval).
A deterministic engine (An) delivers every message; a web UI (Huy) shows it. An offline optimizer tunes the grouping parameters (R, W).
The framework in `contracts/`, `fakes/`, `simulation/`, `app/`, `tests/contract/` is done and owned by An. Your job is one module.

### Locked stack and rules (do not deviate)

- Python >= 3.12, `dataclasses` + `enum` only (no Pydantic in the core), `pytest`, `ruff`. FastAPI only in `api/`. Plain HTML/CSS/JS in `web/`, no build step.
- **No new dependencies.** Do not add packages to `requirements.txt`.
- **Edit only your own folders** (listed in your section). Never edit `contracts/`, `fakes/`, `tests/contract/`, `simulation/`, `app/`, `scenarios/` or another person's folder. If the contract is wrong or missing something, stop and tell An.
- **Never import another person's module.** Import only from `contracts` (and the standard library).
- **Never edit or skip a contract test** to make it pass. If a test looks wrong, explain why and tell An.
- **No wall clock, no sleep, no unseeded randomness** in module code. Time is the `now` argument.
- **No magic numbers.** Every SRS constant is in `contracts/config.py`; every open value is decided in `DECISIONS.md`. If the SRS is unclear and `DECISIONS.md` does not cover it, do not guess: stop and ask.
- **Reasons are enum values** (`NackReason`, `RejectReason`, `ExclusionReason`), never free text.
- **State machines are explicit**: an enum state plus a transition function or table.
- Test names follow the SRS ID: `test_DOOR_04_obstruction_reopens`.

### How a module works

```python
# <package>/__init__.py exports create(...) with the signature in contracts/interfaces.py
class MyModule:
    name = "dispatch"  # or "cabin:P1" ... "cabin:G1", or "cc"

    def handle(self, msg: Message, now: float) -> list[Message]: ...
    def snapshot(self) -> dict: ...  # shape fixed in contracts/ui.py
```

- `handle` reacts to **one** delivered message and returns what it emits (maybe `[]`). It never calls another module. The engine routes outputs with `contracts/routing.py`; you never choose recipients.
- Every emitted message: `t_sim=now`, `msg_id=""` (the engine stamps it), `deliver_at=None`. The bus raises `ContractViolation` otherwise.
- **Waiting = a Timer to yourself**: `Timer(t_sim=now, deliver_at=now + delay, target=self.name, key="...")`. Timers cannot be cancelled; ignore stale ones (put a counter in `key` or `ref_id`).
- At t=0 (and after reset) the engine sends you `Timer(key=TIMER_START)` (D-08). Use it to send first telemetry / start timers.
- Messages are frozen dataclasses with tuples. Build new ones; never mutate.
- Same inputs must give the same outputs in the same order (replay, NFR-05).
- More than 10 000 messages at one instant is treated as a livelock (e.g. propose -> NACK -> propose the same thing again forever).

### Where things are

| File | What it holds |
|---|---|
| `contracts/messages.py` | every message and value type (`Stop`, `CabinInit`, `Candidate`, `Passenger`), `payload_key`, `TIMER_START` |
| `contracts/enums.py` | all enums, including every reason enum |
| `contracts/config.py` | SRS constants, decision values, `Policy(version, R, W)` |
| `contracts/routing.py` | who receives which message, same-time priority |
| `contracts/interfaces.py` | `Module` protocol and `create()` signatures |
| `contracts/ui.py` | snapshot shapes, REST command table, `fake_snapshot()` |
| `contracts/runtime.py` | `SimControl`: how `api/` drives the engine |
| `contracts/ids.py` | `IdGen("HC")` -> `HC-0001`, ... (D-15) |
| `DECISIONS.md` | every value or behaviour the SRS leaves open (D-xx) |
| `fakes/` | simple stand-ins. **Not reference implementations: do not copy their logic.** |
| `tests/contract/test_<module>.py` | your exam, written from SRS numbers |

### Workflow with your AI

1. Paste this file (your section), the files your section lists, and your contract test file.
2. Ask for a **plan first**. Read it, fix it, then let it code.
3. Run your contract tests often. Run everything before a PR.
4. In every PR, write AI notes: tool, what the AI did, **what it got wrong**, what you fixed (CONTRIBUTING.md Section 7).

### Commands

```bash
pytest tests/contract/test_<module>.py        # your exam (skipped until your package exists)
pytest                                         # everything
ruff check . && ruff format .
python -m app.headless --scenario AT-01        # your module real if it exists, others fake
python -m app.headless --scenario AT-01 --dispatch real --cabin fake --cc fake
```

---

## Thanh - Dispatch (`dispatch/`, `tests/dispatch/`)

**Read:** SRS 3.3-3.4 (communication rule), 4.1, 4.4, 4.5 (selection), **4.7 (all)**, 4.9 A-C, 6.5 (baselines), 7.1, 7.2, App. D.
**Decisions:** D-10..D-19, D-20..D-22, D-24, D-28, D-29, D-36, D-50, D-55.
**Exam:** `tests/contract/test_dispatch.py` (34 tests).
**Export:** `create(policy: Policy = DEFAULT_POLICY, algorithm: DispatchAlgorithm = COMMITMENT)`; `name = "dispatch"`; `snapshot()` -> `DispatchSnapshot`.

Easy to get wrong:
- `RouteProposal.ordered_stops` is the **complete** route, including the car's existing `CAR_DEST` stops; `expected_route_version` is the version in the latest telemetry. The cabin bumps the version on **every** stop-list change, including a served stop (D-16).
- DELAY_CAP compares against the **reference ETA fixed when the stop was committed**, never the last reroute (TC-DELAY-01: 32 vs 20 is rejected).
- Grouping (D-50): R and W limit which calls may join a car that already has pending same-direction hall pickups. Never wait for W to pass (DC-03).
- One-floor insertion cutoff on the continuous position (26.8 -> 27 is too late; 26.0 -> 27 is fine).
- Exclude cars that are stale (> 3 s), FAULT_HOLD, MAINTENANCE, FIRE, FULL (>= 900 kg) or with `sensors_valid=False`. G1 takes no ordinary hall calls.
- Ownership: RESERVED until ACK; on missing ACK, a Timer at `expiry_ts` then `RouteVersionQuery`; never offer the call to another car before the report (AT-05). A retry must resend the identical proposal (D-17). After a NACK, do not re-offer the same thing to the same unchanged car.
- MEDICAL: MedicalETA = remaining onboard drop-offs + their dwells + deadhead (eq. 5); every car gets a `Candidate` with ETA or exclusion reason (D-28). Send `RESERVED` only after the ACK; `UNAVAILABLE` if none (D-29). A medical car's unpicked hall calls return to PENDING with their original `created_at` (MED-03).
- FREIGHT: G1 first if eligible, otherwise a P-car (D-21).
- A car that drops a hall pickup from its telemetry (fault, maintenance) releases it (D-55).
- `PolicyActivated` changes only later decisions; it never re-plans accepted routes.
- Also provide the two baselines behind the same interface: `FIRST_IDLE`, `SWEEP`.

## Hai - Cabin (`cabin/`, `tests/cabin/`)

**Read:** SRS 3.2, 4.2, 4.6, **7.3, 7.4, 7.5, 7.6**, 4.9 A and D, 9.3, App. D.
**Decisions:** D-02, D-03, D-04, D-07, D-08, D-09, D-11, D-14, D-16, D-17, D-19, D-24, D-25, D-29, D-51, D-52, D-55.
**Exam:** `tests/contract/test_cabin.py` (35 tests).
**Export:** `create(init: CabinInit)`, one instance per car; `name = "cabin:<car_id>"`; `snapshot()` -> `CabinSnapshot`.

Easy to get wrong:
- Telemetry on start, on every change, and at least every 1 s (heartbeat Timer). None while a `HEARTBEAT_LOSS` fault is active.
- Position is continuous: 0.5 floor/s, no jumps (at t=1 after leaving 31 downward: 30.5). Arrive -> `CAR_ARRIVED`, then the door cycle OPENING 1 s, OPEN 4 s, CLOSING 1 s, CLOSED_LOCKED (D-03). AT-01: 8 / 9 / 13 / 14 s.
- Obstruction while CLOSING -> `DOOR_REOPENED`, full 6 s again (D-04); safety input first at the same instant (obstruction at exactly 14.0 wins). `DoorRequest` per D-51.
- Every NACK has a reason: VERSION_MISMATCH, PAYLOAD_CONFLICT, EXPIRED, INSERT_CUTOFF, REVERSAL_REQUIRED, ONBOARD_COMMITMENT, SENSORS_INVALID, FULL_LOAD, FAULT_HOLD, MAINTENANCE, FIRE, MISSION_EXCLUSIVE. A NACK keeps the old route.
- Store the reply per `command_id` with `payload_key`: same payload -> same reply; different payload -> `PAYLOAD_CONFLICT`. Answer `RouteVersionQuery` with `seen` / `accepted` (D-17).
- Load: >= 900 kg -> FULL, NACK new HALL_PICKUPs; > 1000 kg -> OVERLOAD_HOLD, no departure until someone exits. Destinations only from boarded passengers (DOOR-06).
- `MOTION_SERIOUS` between floors: stop where you are, doors stay locked, keep passengers, `Alert(FAULT)`, drop HALL_PICKUP stops (D-55). Back to AUTO only after `FaultCleared` **and** `RecoveryApproved`. Conflicting sensors are never "safe".
- Never send `Alert(ALARM)`: Control Center makes ALARM alerts (D-19).
- Missions (D-52): DRAINING while onboard passengers leave; `HANDOFF_OK` only when stationary and passenger **count** is 0 (not "load is low"); otherwise HOLD_PENDING_REVIEW + `Alert(HANDOFF_REVIEW)`. Wait for `LOADED`, then the direct exclusive leg; `MISSION_DELIVERY_DONE` on arrival; `COMPLETED` on `UNLOADED`; `FAILED` on `LOAD_FAILED`. AT-04: 44 / 50 / 58 / 64 / 122 s.

## Huy - Control Center, API, UI (`control_center/`, `api/`, `web/`, `tests/control_center/`)

**Read:** SRS 3.1, 4.3, 4.4 (MED-04), 4.5 (FRT-01), 5.3, 7.6, **7.8**, App. D.
**Decisions:** D-19, D-20, D-21, D-23, D-26..D-29, D-37, D-51, D-53, D-54, D-58, D-61.
**Exam:** `tests/contract/test_control_center.py` (28 tests).
**Export (CC):** `create()`; `name = "cc"`; `snapshot()` -> `ControlCenterSnapshot`.
**Export (API):** `api.create_app(control: SimControl) -> FastAPI` (contracts/runtime.py). `python -m app` calls it.

Easy to get wrong:
- Role matrix (D-53): MEDICAL by MEDICAL_COORDINATOR, FREIGHT by BUILDING_STAFF, recovery and policy decisions by SUPERVISOR, alert acks by OPERATOR or SUPERVISOR. A rejected request emits `REJECTED` + reason and nothing else (no `MissionApproval`).
- Validation (D-54): floors 1..40, origin != destination; FREIGHT needs a non-empty material and a weight <= 1000 kg (1000 is valid).
- `MissionApproval.allowed_cars` from `MEDICAL_CAPABLE` / `FREIGHT_CAPABLE` in config.
- Control Center sends only APPROVED / REJECTED statuses (D-29). UNAVAILABLE stays visible until a new status arrives, with exactly one UNAVAILABLE alert; never show it as success (UI-04).
- `alert_id` is the alert message's `msg_id`. ALARM alerts come only from `AlarmPressed`, never a mission (EMG-01).
- Recovery only for a car whose telemetry shows FAULT_HOLD or MAINTENANCE.
- API: REST bodies are the message fields without `msg_id`, `t_sim`, `deliver_at` (+ `role` when the message has none); build messages with `t_sim=0.0` and call `control.submit(msg)`. Engine commands go through `control.control(SimCommand...)`. The WebSocket `/ws` sends `control.snapshot()` on connect and every snapshot from `control.subscribe(...)`. `GET /snapshot` returns one snapshot. Check the roles in `contracts/ui.py` `COMMANDS`.
- Build the UI on `contracts.ui.fake_snapshot(t)` first, then on `python -m app` (DEMO scenario, all fakes).

---

## Starter prompt (fill in the brackets)

```text
You are helping me implement the [Dispatch | Cabin | Control Center + API] module of a
student Smart Elevator simulator. Above is AI_CONTEXT.md, then [the files from my section],
then my contract test file. Rules: edit only [my folders]; import only from contracts and
the standard library; no new dependencies; no wall clock or randomness; every constant from
contracts/config.py; every open question from DECISIONS.md - if something is not covered,
stop and ask me instead of guessing; never change tests/contract.
First, give me a short plan: the state you will keep, the message handlers, the state
machine(s), and which contract tests each step makes pass. Do not write code until I
approve the plan.
```
