# Smart Elevator System - implementation plan for four people

Prepared for **Thanh, An, Huy, and Hai** on **13 November 2026**.

Source: [Group No. 2 SRS.pdf](<Group No. 2 SRS.pdf>), Smart Elevator System SRS v1.1. This plan covers the software described in Sections 1-9 and Appendices A-D, including all 16 user stories. Team assignments, estimates, schedule, repository layout, and collaboration rules below are proposed working arrangements.

## 1. Goal and planning assumptions

Build a reproducible, software-only Smart Elevator System with:

- A 40-floor building, four passenger cars P1-P4, and one goods car G1.
- Ordinary hall calls, boarding, and destinations selected inside the cabin.
- Dispatch based on feasible pickup ETA, directional grouping, protected commitments, and unique call ownership.
- Independent cabin motion, doors, load checks, sensor feedback, safety vetoes, faults, and recovery.
- Authorized MEDICAL and FREIGHT missions through a Control Center.
- Hall and cabin panels, fleet monitoring, simulation controls, and an optimization view.
- Deterministic event ordering, replay, logs, metrics, typed contracts, and gRPC integration.
- Offline SATSA parameter search, comparison with baselines and grid search, and approved policy activation.

Use **eight weeks, organized as four two-week sprints**, as an initial schedule. No deadline or technology stack was supplied. Assume comparable experience and approximately 10-12 available hours per person per week; review this assumption at kickoff. Use relative weeks rather than calendar dates until the team chooses a start date.

Plan **40 delivery points per person: 160 points total, 25% each**. Points estimate complexity, effort, and uncertainty; they are not hours. Each package includes design, implementation, relevant tests, error handling, and documentation. Everyone also reviews code and participates in integration. Reserve about 20% of available time for review, integration, and uncertainty rather than filling every hour with feature work.

The repository currently contains the source PDF and no application implementation or initial commit. The PDF describes design targets, not a completed system. It mentions `Smart_Elevator_Dispatch_Feasibility_Demo.py`, but that script is absent from this workspace. Obtain it if available, or independently reconstruct and verify the numerical examples. Do not report the PDF's historical check results as results from this repository.

## 2. Equal ownership and module boundaries

| Person | Primary delivery responsibility | Main outputs | Planned points |
| --- | --- | --- | ---: |
| Thanh | Dispatch and SATSA search | Canonical calls, ETA and grouping policy, ownership reconciliation, mission candidate selection, offline search algorithm | 40 |
| An | Cabin execution and local safety | Motion and door state machines, boarding and load model, versioned route acceptance, fault isolation, exclusive mission execution | 40 |
| Huy | User interfaces and Control Center | Five interface surfaces, role checks, mission workflows, alerts, recovery authorization, optimization approval | 40 |
| Hai | Simulation and integration infrastructure | Event clock and replay, contracts and gRPC adapters, logs and metrics, experiment harness, grid search and baseline comparison | 40 |
| **Total** | | | **160** |

This division balances engineering effort rather than counting screens, requirement IDs, or lines of code. SATSA is deliberately divided: **Thanh implements the search; Hai implements the repeatable evaluator and comparisons; Huy implements approval; An verifies that experimental runs preserve cabin invariants.** Hai does not also own the search algorithm or everyone's testing.

Authority must follow the SRS:

1. Dispatch owns canonical hall-call records and proposes routes.
2. Each Cabin Controller alone accepts or rejects its route and controls its simulated motion and doors.
3. Control Center authorizes privileged actions and recovery; authorization cannot bypass cabin guards.
4. Simulation Engine owns simulation time and deterministic event delivery.
5. SATSA produces an offline recommendation. It never issues door or movement commands.

### Proposed repository layout

Choose the language and frameworks together on Day 1. Prefer one shared backend language unless there is a clear course requirement. Begin with modules communicating in-process, then add gRPC adapters without changing behavior.

```text
contracts/                  Hai: shared types, schemas, .proto files, error codes
src/dispatch/               Thanh: calls, ETA, assignment, policy consumption
src/cabin/                  An: one reusable controller instantiated for five cars
src/control-center/         Huy: authorization, missions, recovery, policy registry
src/simulation/             Hai: clock, event queue, adapters, trace replay
src/optimization/search/    Thanh: SATSA candidate generation and search
src/optimization/evaluate/  Hai: objective, grid search, experiment runner
ui/                         Huy: hall, cabin, fleet, simulation, optimization views
scenarios/                  Hai coordinates; every owner supplies domain fixtures
tests/                      Every owner maintains tests for their own behavior
docs/                       Every owner maintains their design and evidence
```

These are planned directories, not files already implemented. Folder ownership identifies the usual reviewer; it does not prevent others from contributing through reviewed PRs.

## 3. Work packages

Create one issue per package below. Split packages larger than five points into smaller PRs while retaining the package's total estimate. Dependencies refer to the initial usable contract or implementation, so a small reviewed foundation can unblock work before an entire package is finished.

### Thanh - 40 points

| ID | Work package and observable result | Main dependencies | Points |
| --- | --- | --- | ---: |
| TH-01 | Canonical hall-call repository and lifecycle. Validate floors/directions, deduplicate active floor-direction pairs, retain original age, distinguish pending/reserved/assigned/served, and complete pickup separately from drop-off. | HI-03 | 5 |
| TH-02 | Feasible route generation and ETA ranking. Evaluate all eligible passenger cars, use continuous position and existing stops, apply stable tie breaking and the stopping cutoff, and compare insertion delay with the original committed ETA. | HI-03, AN-01, AN-02 | 8 |
| TH-03 | Dispatch-side ownership and reconciliation. Reserve one candidate, confirm only on ACK, distinguish NACK from transport timeout, reconcile accepted route versions before release/reassignment, and preserve ages through faults. | AN-05, HI-03; HI-04 for network checks | 8 |
| TH-04 | Same-direction grouping and aging. Enforce fixed-anchor R/W bounds without waiting to form a group; reevaluate pending calls on fleet changes; protect calls aged at least 60 s. | TH-01, TH-02, HI-02 | 5 |
| TH-05 | Mission candidate selection. Compute medical ETA after onboard drop-offs across all compatible cars; apply G1-first freight eligibility and permitted passenger fallback; return explicit selection failure to Control Center. | HY-05 contract, AN-04, TH-03 | 3 |
| TH-06 | Offline SATSA search. Validate run inputs, project continuous candidates to integer R/W, implement adaptive Gaussian updates, stagnation-triggered seeds and Nelder-Mead refinement, and use Hai's evaluator/cache. Document the adaptation to the cited paper. | HI-05, HI-06 evaluation interface | 8 |
| TH-07 | Dispatch policy activation. Consume only an approved version at a defined safe decision boundary, retain accepted route commitments, and expose active policy version in decisions and replay evidence. | HY-07, HI-02 | 3 |
| | **Total** | | **40** |

### An - 40 points

| ID | Work package and observable result | Main dependencies | Points |
| --- | --- | --- | ---: |
| AN-01 | Independent cabin state and simulated sensor adapters. Represent P1-P4/G1 with position, direction, mode, route/version, door, load, passenger count, freshness and faults; emit timestamped telemetry. | HI-03 | 5 |
| AN-02 | Continuous motion and stopping rules. Advance position at the configured speed, arrive only at valid stops, reject unsafe late insertions and instant reversal, and require valid local movement guards. | AN-01, HI-02 | 8 |
| AN-03 | Door state machine. Implement CLOSED/OPENING/OPEN/CLOSING/OBSTRUCTED, landing-zone checks, dwell, door requests, obstruction reopening, and closed-and-locked movement permission. | AN-01, HI-02 | 5 |
| AN-04 | Boarding, destinations, load and empty handoff. Accept destinations after boarding, track exits, preserve onboard stops, bypass ordinary pickups at FULL_LOAD, hold on overload, and block exclusive handoff on contradictory empty-cabin evidence. | AN-02, AN-03 | 8 |
| AN-05 | Cabin route command receiver. Validate expected version and local feasibility, preserve the previous route on NACK, cache duplicate command results, reject reused IDs with changed payloads, and expose authoritative route/version for reconciliation. | HI-03 | 5 |
| AN-06 | Fault, alarm, fire-input and recovery behavior. Enter FAULT_HOLD safely, retain onboard passengers, prevent between-floor opening, publish alerts, and return to AUTO only after approved readiness checks. Finish the local response policy with Huy at kickoff. | AN-01, AN-03, HY-06 contract | 5 |
| AN-07 | Local MEDICAL/FREIGHT execution. Drain onboard trips, verify stationary and empty handoff, confirm boarding/loading, enforce direct exclusive legs, and handle failed loading, unloading and safety interruptions. | AN-04, AN-05, HY-05, TH-05 | 4 |
| | **Total** | | **40** |

### Huy - 40 points

| ID | Work package and observable result | Main dependencies | Points |
| --- | --- | --- | ---: |
| HY-01 | Application shell and fleet view. Display all five cars independently with position, direction, doors, mode, load warnings, faults and accepted routes. | HI-03 snapshots; mock telemetry initially | 5 |
| HY-02 | Hall and cabin panels. Restrict terminal directions and invalid floors; show pending versus assigned calls; create destinations only after boarding; expose valid door requests and a separate alarm action. | TH-01, AN-03, AN-04 | 6 |
| HY-03 | Simulation console. Start/pause/reset/replay with seed and configuration controls; show session time/state, pending events, ownership, route versions and basic metrics. | HI-02, HI-05 | 4 |
| HY-04 | Control Center role authorization and audit. Enforce permissions in backend handlers, validate grant inputs and material declarations, protect privileged data, and log approved/refused actions without ordinary passenger identity collection. | HI-03, HI-05 audit contract | 5 |
| HY-05 | Medical and freight orchestration. Submit/authorize missions, validate capabilities and material profiles, coordinate reservation/draining/loading/delivery, show held/failed/completed states, and retain unavailable medical missions with alerts. | HY-04, TH-05, AN-07 | 8 |
| HY-06 | Alerts, monitoring and supervisor recovery. Distinguish alarm/medical/fire/fault events, display explicit NO_ELIGIBLE_CAR/UNAVAILABLE, authorize recovery after readiness evidence, and prevent UI actions from bypassing local guards. | AN-06, HI-05 | 6 |
| HY-07 | Optimization view, approval and policy registry. Submit authorized offline runs; display candidate, objective and comparisons; store versioned recommendations and approvals; request safe activation and show the active version. | TH-06, HI-06, TH-07 contract | 6 |
| | **Total** | | **40** |

### Hai - 40 points

| ID | Work package and observable result | Main dependencies | Points |
| --- | --- | --- | ---: |
| HI-01 | Repository and development foundation. Establish the selected build/runtime, consistent local commands, formatting/lint/build/test CI, sample configuration, and a runnable module skeleton. | Day 1 stack decision | 5 |
| HI-02 | Deterministic simulation kernel. Implement event ordering by time/priority/sequence, controllable clock, complete pause, reset, seeded replay, event invalidation and deterministic fault/transport-delay injection. | HI-03 | 8 |
| HI-03 | Shared contracts and in-process adapters. Define DTOs/enums, event envelope, snapshots, route ACK/NACK reasons, reconciliation read contract and optimization evaluation/result interfaces; provide stubs and fixtures early. | Joint Day 1-2 contract review | 3 |
| HI-04 | gRPC/proto3 adapters and telemetry streams. Implement typed dispatch/cabin/Control Center boundaries, distinguish transport failures from application NACKs, and prove retry/delay behavior against the same logical contracts. | HI-03, AN-05, TH-03 | 6 |
| HI-05 | Structured logging, metrics and scenario fixtures. Record required lifecycle/audit data, compute wait and commitment metrics, version traces and seeds, and supply traffic/fault fixtures and isolated evaluator inputs. | HI-02; events from all owners | 6 |
| HI-06 | Research evaluation and comparisons. Implement the fitness function, hard-invariant rejection, cached evaluation, first-idle/directional-sweep baselines, fixed (3,5), exhaustive R/W grid search, and separate held-out evaluation of SATSA results. | HI-02, HI-05, TH-02; TH-06 for final comparison | 8 |
| HI-07 | Reproducible packaging and evidence automation. Provide startup instructions and saved demo scenarios, assemble per-owner acceptance results, record experiment inputs/results and runtime measurements, and verify a clean local setup. | All owners' runnable modules and evidence | 4 |
| | **Total** | | **40** |

### Keeping the workload equal during development

- Re-estimate after the first sprint using actual effort and remaining risk. Equal starting estimates do not guarantee equal working hours.
- Review remaining points, hours spent, blocked time and review duties at every sprint planning session. Keep each person's remaining load within roughly 10% of the team average where practical.
- Transfer a small, independent package or subtask when capacity differs. Record both the owner change and point transfer; do not leave the old owner accountable for transferred work.
- If the simulation foundation becomes the bottleneck, An can take a sensor/fault fixture subtask or Thanh can take an experiment adapter. Transfer equivalent remaining points from their existing queue.
- No person becomes the permanent tester, document writer, or integration fixer. Each author supplies their tests and evidence; Hai only coordinates the shared runner and evidence format.

## 4. Delivery schedule and dependencies

The schedule preserves the SRS's R1-R5 order. R2 and R3 are separate gates inside Sprint 2. The integration steward rotates once per sprint; see [CONTRIBUTING.md](CONTRIBUTING.md) for merge rules.

| Sprint | Thanh | An | Huy | Hai | Exit gate | Steward |
| --- | --- | --- | --- | --- | --- | --- |
| 1: Weeks 1-2 | Hall-call lifecycle; initial ETA and reservation flow | Cabin state, minimal motion/doors and ACK receiver | Fleet shell, basic hall/cabin panels, role model | Build/CI, shared contracts, clock/event queue, in-process adapters | **R1:** 40 floors/five cars, valid/deduplicated calls, pause/reset, repeatable initial trace; minimal contracts exercised | Thanh |
| 2: Weeks 3-4 | Complete ETA, ownership, delay protection, grouping and aging | Complete ordinary trips, doors, load/boarding and initial faults | Complete passenger feedback, console, fault/ownership monitoring | Full replay, logs/metrics, gRPC and lost/delayed-message fixtures | **R2:** one complete safe passenger trip. Then **R3:** grouping, fault isolation and no duplicate ownership | An |
| 3: Weeks 5-6 | Medical selection, freight eligibility, research search design and policy contract | Empty handoff, exclusive mission legs, recovery/failure paths | Roles/audit, medical/freight workflows, unavailable states and recovery | Mission traces, baseline comparisons, objective/grid harness and benchmark inputs | **R4:** medical and freight examples pass, unauthorized actions are rejected, safety still wins | Huy |
| 4: Weeks 7-8 | SATSA search, safe policy consumption, dispatch/algorithm regression | Safety verification on research traces; remaining fault, sensor and exclusive-leg edge cases | Optimization approval/results view, complete UI flows and operator guide | Held-out experiments, optimizer comparison, reproducibility and packaging | **R5:** approved recommendation and safe activation, complete acceptance evidence, tagged reproducible demo | Hai |

Work on final regression and documentation belongs to each person's existing package estimates. An's Sprint 4 work includes checking invariants under generated traces and reviewing the simulator/evaluator boundary; it is part of completing cabin behavior, not an additional 40-point assignment.

**Critical dependency path:** shared contracts -> deterministic kernel and cabin route receiver -> ordinary dispatch/ownership -> resilient mission service -> stable replay/evaluation -> SATSA results -> approved activation.

Prevent sequential blocking with early, small deliveries:

1. By Day 2, merge message types, examples, enums and service stubs so UI and modules can develop against the same fixtures.
2. Agree the evaluation function's input/output interface in Sprint 1; Hai can implement grid search without waiting for SATSA, and Thanh can test search against a deterministic fake objective.
3. Use recorded/mock telemetry for early screens, then replace it with authoritative snapshots. The UI never writes cabin or ownership state directly.
4. Make in-process behavior pass before introducing transport. Route the network and replay paths through the same handlers.
5. Mark stubbed, unimplemented or unexecuted behavior explicitly. A successful mock does not count as integrated acceptance.

## 5. Kickoff decisions and first two days

The following numerical values are **proposed SRS baselines**, pending team sign-off: 2 s/floor, 6 s/served stop, R=3 floors, W=5 s, DELAY_CAP=10 s, AGE_LIMIT=60 s, FULL_LOAD=900 kg and MAX_LOAD=1000 kg. The proposed optimizer domain is R=1..5 and W=0..10, giving 55 integer pairs. Store approved values as versioned configuration rather than scattering constants through modules.

| Decision | Lead prepares; everyone reviews | Required outcome |
| --- | --- | --- |
| Stack, local run commands, repository host and CI | Hai | One reproducible environment; no unnecessary additional services |
| Time/motion/door model | An + Hai | Confirm 2 s/floor and 6 s stop accounting, define door transition timing without accidentally double-counting the worked examples |
| Dispatch policy | Thanh | Confirm R/W, stable tie break, fixed anchor/ETA references, age protection and what counts as materially disadvantaging the anchor |
| Safety and health policy | An | Define sensor freshness, stopping cutoff, load boundaries, readiness checks, alarm response and the scenario-specific simulated fire response |
| Mission policy | Huy + An + Thanh | Per-car capabilities; material/payload limits; loading failure, cancellation/escalation and conflicting medical/freight grants; do not abandon occupied passengers |
| Roles and authentication | Huy | Explicit role/action matrix and backend enforcement; separate user-role changes from mission approval |
| Communication/reconciliation | Hai + Thanh + An | Command lifetime and timeout clock, read-back of authoritative accepted route/version, changed-payload duplicate rejection, stale ACK handling |
| Replay contract | Hai | Event priorities, stable IDs/sequences, versioned initial state and ingress traces; no simulation advancement while paused |
| Optimization study | Thanh + Hai | Obtain/read the cited SATSA paper before implementation; approve mechanisms/adaptation, R/W bounds, lambda/mu, search budget, training/held-out splits and cache keys |
| Evidence semantics | Hai + all owners | Define pickup wait for canonical calls versus virtual passengers, unserved horizon, P95 method, zero-call scenarios, safe activation boundary and evidence storage |

The cited paper is a future implementation input; this plan does not assert that its full algorithm has already been reviewed or reproduced. The SRS's own pseudocode is the initial integration specification.

**Day 1:** all four review the SRS invariants and decisions; Hai prepares the skeleton, Thanh the request/route model, An the cabin guards/states, and Huy the role matrix and screen flow. Each person opens their assigned issues.

**Day 2:** jointly review and merge the first contract PR, add one shared ordinary-trip fixture and a lost-ACK fixture, verify everyone can run the skeleton, and start the first feature PRs. Record unresolved numerical choices with an owner and resolution date before any dependent behavior is accepted.

## 6. Interface contracts agreed before parallel implementation

Hai maintains the shared definitions; the producer and consumer jointly review them. Minimum contracts from SRS Sections 3.3-3.4 and Appendix D are:

| Contract | Producer / handler | Mandatory behavior |
| --- | --- | --- |
| SubmitHallCall | Huy's panel -> Thanh's Dispatch | Origin/direction/event ID/sim time; return canonical request ID and pending/assigned state; do not accept an ordinary destination here |
| RouteProposal / RouteAck | Thanh -> An -> Thanh | Command ID, car ID, expected route version, ordered stops and expiry semantics; versioned ACK or reasoned NACK; unchanged old route on rejection |
| Authoritative route read-back | An -> Thanh through Hai's adapter | Planned supporting interface for reconciliation: accepted command/route/version evidence, so a timeout cannot create a second owner |
| CabinTelemetry / ServiceEvent | An -> Dispatch, Control Center and logs | Time, car, continuous position, direction, door/lock, load/count, mode/faults, route version and verified pickup/exit/delivery events |
| ApproveMission / recovery authorization | Huy -> Dispatch/Cabin | Recorded authorized role, floors/type/material profile and decision ID; no route effect from rejected grants |
| EvaluateConfiguration | Thanh's search -> Hai's evaluator | Versioned scenario package, seeds, R/W and fixed objective settings; feasible result with metrics or explicit hard-invariant rejection |
| OptimizationResult / policy activation | Thanh + Hai -> Huy -> Dispatch | Run/input versions, R/W, score, comparisons and approval; future-boundary activation only |

Use an agreed event envelope with event ID, simulation time, category priority, sequence, source, schema version and correlation IDs. Record transport-delay/loss injections in replay inputs. Wall-clock arrival order, UI refresh and animation frames must not become independent sources of simulation decisions. Network behavior is tested against recorded inputs, not assumed to be deterministic by itself.

## 7. Complete requirement accountability

The following assigns one accountable person to each of the **89 numbered requirements** in the source. Other people still implement their side of a boundary and review it. Requirement count is not the effort estimate.

| Requirement IDs | Accountable owner | Boundary support |
| --- | --- | --- |
| DC-01 to DC-06 | Thanh | An supplies authoritative feasibility/health; Huy supplies panel input |
| CB-01 to CB-05 | An | Thanh supplies routes; Huy supplies cabin interactions |
| CC-01 to CC-04 | Huy | An supplies recovery readiness; Hai supplies telemetry/audit infrastructure |
| MED-01 to MED-03 | Thanh | An confirms route and onboard drain; Huy authorizes |
| MED-04 | Huy | Thanh returns unavailability and reevaluates selection |
| MED-05 | An | Huy confirms mission boarding; Thanh preserves exclusive scheduling |
| FRT-01 | Huy | Thanh checks selection capability; An checks local payload |
| FRT-02 to FRT-04 | An | Huy owns workflow/status and confirmation; Thanh reserves the chosen car |
| SAF-01 to SAF-03 | An | Thanh reconciles and reassigns unpicked calls |
| SAF-04 | Thanh | An and Hai provide accepted-version/idempotency evidence |
| OPT-01 to OPT-03 | Thanh | Huy submits authorized inputs; Hai supplies evaluator interface |
| OPT-04 to OPT-05 | Hai | Thanh supplies search and live policy; An verifies hard safety guards |
| OPT-06 | Huy | Thanh activates at a safe boundary; Hai records versions/results |
| NFR-01 | Huy | Thanh immediately records/attempts assignment; Hai supplies event delivery |
| NFR-02 | Thanh | An keeps committed routes authoritative |
| NFR-03 to NFR-06 | Hai | Every domain owner emits required metrics/evidence |
| SYS-01 | Hai | An instantiates cabin state; Huy renders the fleet |
| SYS-02 | An | Hai maintains independent event routing |
| SYS-03 | Thanh | Huy validates panel input; An validates cabin input |
| SYS-04 | An | Hai supplies only simulated adapters |
| REQ-01 to REQ-04 | Thanh | An supplies accepted routes; Hai supplies transport behavior |
| REQ-05 | An | Thanh records pickup; Hai logs distinct lifecycle events |
| MOVE-01 to MOVE-06 | An | Thanh applies corresponding dispatch-side insertion screens |
| DOOR-01 to DOOR-06 | An | Huy exposes guarded passenger actions |
| SEN-01 to SEN-06 | An | Hai orders timestamped sensor events |
| EMG-01 | Huy | An emits distinct local alarm/fire/fault events |
| EMG-02 | An | Huy receives persistent fault alerts |
| EMG-03 | Thanh | An reports faults and recovery health |
| EMG-04 to EMG-05 | Huy | An gates recovery; Thanh retries eligible medical selection |
| SIM-01 to SIM-05 | Hai | Huy controls sessions; Thanh retains pending requests |
| SIM-06 | An | Hai delivers commands; Thanh retains command identity |
| SIM-07 | Thanh | An exposes accepted version; Hai simulates missing ACK |
| SIM-08 | Hai | An's adapters publish feedback without changing ownership |
| UI-01 to UI-05 | Huy | All owners provide authoritative state |
| LOG-01 to LOG-04 | Hai | All owners emit their own domain/audit events |

### Story coverage

| SRS story | Delivery lead | Required collaborators | Gate |
| --- | --- | --- | --- |
| US-001 Building/fleet | Hai | An, Huy | R1 |
| US-002 Simulation controls/replay | Hai | Huy | R1, extended through R3 |
| US-003 Hall call | Thanh | Huy | R1 |
| US-004 Boarding/destination | An | Huy, Thanh | R2 |
| US-005 Feasible cabin choice | Thanh | An | R2 |
| US-006 Direction grouping | Thanh | An | R3 |
| US-007 Unique ownership | Thanh | An, Hai | R2, fault/timeout verification in R3 |
| US-008 Door interlocks | An | Hai | R2 |
| US-009 Obstruction handling | An | Hai | R2 |
| US-010 Fault and recovery | An | Thanh, Huy, Hai | R3 |
| US-011 Medical mission | Huy | Thanh, An | R4 |
| US-012 Freight mission | Huy | An, Thanh | R4 |
| US-013 Fleet/mission monitoring | Huy | Hai, An | R3, mission extension in R4 |
| US-014 Versioned route protocol | Hai | An, Thanh | R2-R3 |
| US-015 SATSA tuning | Thanh | Hai, An | R5 |
| US-016 Optimized policy approval | Huy | Thanh, Hai | R5 |

## 8. Acceptance gates and evidence

Every package owner writes the tests for their own behavior. Integration checks exercise boundaries with the affected owners. Record test ID, requirement IDs, code revision, input trace/configuration/seed, expected result, actual result, PASS/FAIL/NOT RUN and evidence location. Begin with NOT RUN; only execution changes that status.

| Gate/check | Required observable result | Evidence lead |
| --- | --- | --- |
| R1: model/input | Floors 1-40 and P1-P4/G1 exist; reject floor 0/41, DOWN at 1 and UP at 40; duplicate active calls keep ID and original timestamp | Hai + Thanh |
| R1: clock/replay | PAUSE freezes time, movement, door transitions and scheduled execution; reset/replay of the same initial state, trace, model/policy versions and seed reproduces ordered domain events | Hai |
| R2: ordinary trip, AT-01 | Isolated P1 at 31, 27/DOWN at t=0: pickup t=8; after 6 s pickup stop and selection of 5, destination arrival t=58; final dwell ends t=64; pickup and drop-off are separate events | An + Thanh |
| R2: doors/capacity | Never move before confirmed locked doors; reopen on closing obstruction; reject door-open between floors; at 900 kg bypass new ordinary pickups; above 1000 kg block departure; preserve onboard trips | An |
| R2/R3: ownership, AT-05 | Lost/delayed ACK keeps reservation; retry of same ID/payload returns original result; changed payload with same ID is an error; stale expected version NACKs; no second owner before authoritative reconciliation | Thanh + An + Hai |
| R3: grouping, AT-02 | Scenario P1 at 31 with 27/DOWN at t=0 and 29/DOWN at t=2: safe grouping can reach 29 at t=4 and 27 at t=14, adding 6 s to floor-27 pickup; first assignment attempt is immediate | Thanh |
| R3: cumulative delay | Original commitment t=20 -> candidate t=26 is +6 and may pass; subsequent t=32 is +12 against 20 and must fail, even though it is only +6 against 26 | Thanh |
| R3: resilience | Fault between floors stops motion, keeps doors locked and retains passengers; unpicked external calls are safely reconciled/reassigned with age intact; low load plus nonzero passenger count never proves an empty handoff | An + Thanh |
| R4: medical, AT-03 | Scenario ETAs P1=90 s, P2=20 s, P4=58 s: choose P2 after ACK; exclude maintenance P3 and goods-only G1 for that scenario; no eligible car produces persistent UNAVAILABLE and alert | Thanh + Huy |
| R4: freight, AT-04 | Isolated P1 occupied 27->5, then freight 1->30: passenger arrival t=44, exit complete t=50, material pickup t=58, loading complete t=64, freight arrival t=122; ordinary pickups excluded from direct leg | An + Huy |
| R4: grants/recovery | Unauthorized or invalid grants leave routes unchanged; alarm never self-grants MEDICAL; supervisor approval alone cannot restore an unhealthy cabin; readiness checks and local veto remain necessary | Huy + An |
| R5: optimizer, AT-06 | Validate/project R/W, reject any invariant-breaking run rather than penalize it, compare SATSA with fixed (3,5) and full approved grid, and report held-out metrics using identical scenarios/seeds | Thanh + Hai |
| R5: approval | Unapproved recommendation changes nothing; approved version activates only at the agreed future boundary and does not rewrite accepted routes or commitment references | Huy + Thanh |
| Final: reproducibility | Clean local start, working UI and networked boundaries, saved scenarios, tests/evidence, archived optimizer inputs/results, and a tagged demonstration version | All four; Hai coordinates |

The arithmetic examples use isolated initial fleets and the SRS's simplified timing assumptions. They are test oracles, not measured physical performance. Keep the stated door/dwell accounting consistent with those oracles.

Also cover exact grouping bounds and just-outside bounds; fixed rather than sliding group anchors; exactly 10 s versus greater delay; 899/900/1000/1001 kg; exactly one floor versus less than one floor stopping distance; stale/missing/conflicting sensors; all cars busy; opposite-direction calls; expired commands and late ACKs; simultaneous safety/movement events; mission interruption; medical/freight conflicts; duplicate projected optimizer candidates; empty traffic sets; and no feasible configuration. At age 60 s, test protection/reevaluation, not a guaranteed pickup deadline.

For research, record mean, median, P95 and maximum pickup wait; unserved fraction/count; onboard delay violations; medical pickup and freight completion; dispatch wall time; offline evaluation cost; seeds, scenario/model/policy versions and approval decisions. Report all feasible compared methods on held-out data after selection. Any change based on those results requires a fresh held-out set for an unbiased final evaluation.

## 9. Definition of Done and scope control

A package is Done only when its implementation and relevant success/error/boundary checks are reviewed, acceptance evidence is recorded, requirement/story links are current, the integrated build passes, and run/design notes are updated. A screenshot alone does not demonstrate ownership, safety or replay correctness.

Each person supplies their own module explanation and a short demonstration. The final presentation covers Thanh's dispatch/search, An's cabin/safety, Huy's operator/passenger workflows, and Hai's reproducibility/evaluation. The integration steward coordinates a release rather than inheriting unfinished work from other people.

If the schedule slips, reduce presentation extras first: decorative animation, extra dashboards, optional exports, additional scenario catalogs and optional indexing structures. Keep the SRS's P0 safety/contracts and P1 service/research scope visible. Any removal or deferral of gRPC, mission behavior or SATSA is an explicit scope decision, recorded as deferred or NOT RUN; it is not silently treated as a complete v1.1 implementation.

The merge, review, conflict-resolution and release policy is in [CONTRIBUTING.md](CONTRIBUTING.md). These documents define the proposed process; remote branch protections and CI still need to be configured after the repository and stack decisions.
