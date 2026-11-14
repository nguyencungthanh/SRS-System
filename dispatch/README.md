# Thanh's Dispatch implementation

Scope: TH-01 through TH-06 in `PROJECT_PLAN.md`. Runtime code imports only
`contracts`, the Python standard library, and this package. No dependencies or
shared contracts are changed.

All six work packages are implemented and verified locally on `thanh-dev`.

## Implementation plan and requirement map

| Task | State and behavior | Verification |
| --- | --- | --- |
| TH-01 | Canonical active `(floor, direction)` calls, deterministic IDs, original creation times, explicit PENDING / RESERVED / ASSIGNED / SERVED transitions | Hall-call contract tests and public-interface regressions |
| TH-02 | Latest authoritative telemetry, full proposed routes, fixed absolute commitment ETAs, continuous insertion cutoff, deterministic ETA ranking | AT-01, TC-DELAY-01, exact delay/cutoff/freshness boundaries |
| TH-03 | One in-flight command per car, explicit command lifecycle, ACK confirmation, NACK suppression, timeout followed by route-version reconciliation | AT-05, lost/reordered responses, ownership regressions |
| TH-04 | Immediate grouping under the active R/W policy, original group timestamps, pending-call reevaluation, oldest-call protection | AT-02, exact R/W boundaries, age priority |
| TH-05 | Approved missions, per-car ETA/exclusion explanations, onboard-only drain routes, G1 preference for freight, release of displaced hall calls after acceptance | AT-03, AT-04, mission eligibility and reservation regressions |
| TH-06 | COMMITMENT, FIRST_IDLE and SWEEP behind the same factory; approved policy affects future decisions | Baseline contract tests, distinct policy behavior, policy activation and replay |

The controller reacts to one delivered message at a time and emits immutable
contract messages. Time comes exclusively from `handle(message, now)`. Timers
carry command IDs so that resolved commands ignore late timeout events.

## Run

From the repository root, after the setup in the main README:

```bash
python -m pytest tests/contract/test_dispatch.py tests/dispatch
python -m pytest
ruff check .
ruff format --check .
python -m app.headless --scenario AT-01 --dispatch real --cabin fake --cc fake
python -m app.headless --scenario AT-03 --dispatch real --cabin fake --cc fake
```

Integration here uses the existing fake Cabin and Control Center. Their owners'
real modules and An's evaluator/optimizer are separate work packages.

## AI development notes

- Tool: Codex, with three development/review agents coordinated by a primary agent.
- Work split: pure route planning; message controller and ownership; independent
  regression tests/review. The primary agent implemented mission estimates and
  integrated the results.
- AI initially allowed a pickup event at the wrong origin to complete a call and
  allowed a duplicate old event to remove a newer call's deduplication entry.
  Review added owner/origin/lifecycle guards and regressions for both cases.
- Review also corrected delayed fault-release reconciliation, pending medical
  priority, mission ETA projection, and new commitment references after delayed
  proposal delivery. Existing commitment references remain fixed.
- ACKs initially synthesized a route update over an old physical sample. This was
  removed: ownership is confirmed, and the next proposal waits for authoritative
  telemetry at the accepted version, preserving the continuous insertion cutoff.

## Verification recorded on 14 November 2026

| Check | Result |
| --- | --- |
| Dispatch contracts and own tests | 92 passed, including all 34 contract cases |
| Full repository suite | 257 passed, 63 skipped |
| Fake-mode suite | 270 passed, 50 skipped |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed; 57 files checked |
| Engine acceptance and saved-trace tests | AT-01, AT-02, AT-03, AT-04, AT-05 lost ACK; three identical replays |
| Additional headless checks | AT-05 unsafe served; DEMO served all 12 calls |
| Command-line saved trace and replay | AT-03 and AT-05 lost ACK: IDENTICAL |

The 63 full-suite skips are the Cabin and Control Center contracts, whose real
packages do not yet exist. Dispatch contracts have no skips. Acceptance integration
uses real Dispatch with the shared fake Cabin and Control Center; validation with
their future real implementations remains part of the team's integration phase.

The implementation is ready for team PR review and merge from `thanh-dev`.
