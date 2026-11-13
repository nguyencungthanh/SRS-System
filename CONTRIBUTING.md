# Collaboration and branch merge rules

These are proposed working rules for Thanh, An, Huy and Hai. They accompany the implementation plan and the Smart Elevator System SRS v1.1, especially Sections 3.4, 7.1-7.7 and 9.5. Branch protection, CI and hosting settings have not been configured by this document.

## 1. Branches and allowed merge paths

| Branch | Purpose | Allowed incoming changes |
|---|---|---|
| `main` | Stable, tested demonstrations and releases | Reviewed release PR from `develop`, or reviewed urgent hotfix PR |
| `develop` | Shared integration of completed work | Reviewed feature/fix/documentation PR, or reviewed back-merge from `main` |
| `feat/<name>/<task-id>-<description>` | One feature or independently reviewable part of a task | Synchronization from `develop`; submit a PR to `develop` |
| `fix/<name>/<task-id>-<description>` | A regular defect fix | Synchronization from `develop`; submit a PR to `develop` |
| `docs/<name>/<task-id>-<description>` | Documentation changes | Synchronization from `develop`; submit a PR to `develop` |
| `hotfix/<name>/<task-id>-<description>` | An urgent defect in a released demonstration | Start from `main`; submit a PR to `main`, then back-merge `main` into `develop` |

Example: `feat/thanh/TH-01-hall-call-repository`. Use lowercase names: `thanh`, `an`, `huy`, `hai`.

Do not create permanent personal integration branches. Everyone integrates through `develop`. Do not merge one person's feature branch directly into another person's feature branch. Merge the small prerequisite contract or implementation into `develop` first, then synchronize dependent branches.

No direct pushes to `main` or `develop`, self-approval, force pushes, or rewriting published history. During initial repository setup, the team must agree on the first bootstrap commit and create `develop` from it before enabling these rules. This one-time bootstrap is not a release exemption for subsequent work.

## 2. Start and synchronize work

Create an issue before implementation. Record its owner, task ID, SRS requirement IDs, acceptance condition, dependencies and estimated points. Keep one active implementation task per person where possible. Split large tasks into PRs that can be reviewed independently; aim to open a draft PR within two working days.

After repository bootstrap, start from current integration code:

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feat/thanh/TH-01-hall-call-repository
```

Before requesting final review, synchronize the feature branch using a merge:

```bash
git fetch origin
git switch feat/thanh/TH-01-hall-call-repository
git merge origin/develop
```

Resolve any conflicts, run the required checks, commit the resolution if needed, and push the feature branch. Do not run a destructive reset to make a conflict disappear. Do not rebase or force-push a branch that has already been published.

If a dependency is not ready, work against the agreed interface and a clearly identified test double. A test double is not evidence of completed integration. Unfinished behavior must not appear as a working feature in a release.

## 3. Review and approval

Every PR needs at least one approval from a person other than its author. A change affecting another module must be reviewed by that module's owner, or by a recorded acting owner if the usual owner is the author or is unavailable.

The following changes require **two non-author approvals**, including a reviewer responsible for the affected boundary:

- Cabin motion permission, doors, load, faults, passenger draining or recovery guards.
- Request ownership, route versions, ACK/NACK, retries, reconciliation or command idempotency.
- Shared message contracts, protocol adapters or deterministic simulation ordering and time.
- Mission authorization, privileged roles, capability checks or operator audit records.
- Committed ETA protection, mission priority, or approval and activation of policy configurations.
- Any release or hotfix into `main`.

Default routing:

| Author / area | First reviewer | Additional reviewer for critical changes |
|---|---|---|
| Thanh: dispatch and SATSA search | An | Hai; include Huy for mission authorization or policy activation |
| An: cabin execution and safety | Thanh | Huy for recovery/mission behavior, or Hai for simulation/transport behavior |
| Huy: UI and Control Center | Hai | Thanh for dispatch/policy changes, or An for cabin recovery and handoff |
| Hai: simulation, contracts, transport and evaluation | Huy | An for physical-model events, or Thanh for ownership/dispatch contracts |

For a change crossing several modules, request all affected owners. Two approvals are a minimum, not a replacement for the relevant expertise. The author never counts as an approver. A steward who authored the PR must have another team member perform the merge.

Any code or contract change after approval requires the affected reviewer to review again. Run checks again on the revised candidate. If `develop` advances in a way that affects the PR, synchronize and rerun the relevant checks before merging.

## 4. Merge procedure

### Feature, fix or documentation PR into `develop`

1. Confirm the task dependencies have merged and the PR describes the behavior now implemented.
2. Synchronize with current `develop` and resolve conflicts with affected owners.
3. Pass the required checks and obtain the required independent approvals.
4. The sprint's integration steward verifies the checklist and uses **squash merge** into `develop`. Use a clear final commit message containing the task ID.
5. Run the integration smoke check on `develop`; attach its result to the task. If a regression appears, fix it immediately or revert the offending merge through a reviewed PR.
6. Mark the task Done only when its acceptance evidence exists. Delete the remote feature branch after a successful merge.

The steward coordinates merges; the steward does not redesign every feature or provide an additional mandatory approval. Reviewers may approve independently while the steward handles the queue.

### Release from `develop` into `main`

1. Select a release candidate commit on `develop` and pause unrelated integration merges while validating it.
2. Run release acceptance, relevant end-to-end checks, reproducibility checks and required research experiments for that release's scope.
3. Open a release PR from `develop` to `main`, listing included requirements, evidence, known limitations and any unimplemented scope.
4. Obtain two non-author approvals and verify checks against the exact candidate being merged.
5. Use a **merge commit**, preserving ancestry. Do not squash or rebase the release PR.
6. Tag the resulting `main` commit, record the run instructions and smoke-test the tagged version. Bring `main` back into `develop` through a reviewed merge when needed to retain a common history.

Do not claim an R1-R4 release implements R5 optimization. The final full-system release must pass all agreed mandatory requirements. A failed safety, ownership or authorization check blocks the affected release.

### Urgent hotfix

Start a `hotfix/...` branch from current `main`. Keep the change limited to the released defect, add a regression check, obtain two independent approvals and merge it into `main` with a merge commit. Tag the corrected release. Immediately open and complete a reviewed **merge of `main` into `develop`**, preserving ancestry, before normal release promotion resumes. Feature authors then synchronize from `develop`.

## 5. Conflict and shared-contract rules

The PR author resolves conflicts and asks the owner of the other changed area to review the resolution. Do not select "ours" or "theirs" for an entire file without checking the intended behavior. If the author is unavailable, record who has taken ownership before changing the branch.

For shared messages, configuration, event schemas or generated clients:

1. Write down the old contract, proposed change, affected producers/consumers and compatibility or migration plan.
2. Ask Hai plus all affected module owners to review the contract before implementation diverges.
3. Merge the shared contract and minimal compatible adapters first; then merge the consuming features.
4. Regenerate clients from the approved source instead of editing generated files manually.
5. Update contract fixtures, replay compatibility information and requirement traceability together.

No module may bypass the agreed authority boundaries: Dispatch owns canonical calls; Cabin alone accepts routes and permits local execution; Control Center authorizes privileged actions; the simulation engine orders events; SATSA produces offline recommendations.

## 6. Required checks and evidence

During Sprint 1, record the actual setup, formatting, build and test commands in the repository README and implement CI for them. Until a check exists, report it as `NOT RUN`; do not report planned checks as passing. A behavior cannot be marked implemented Done without its relevant checks.

For each implementation PR, run:

- Formatting/static checks and build/type checks appropriate to the chosen stack.
- Meaningful unit checks for changed behavior, including rejection and error paths.
- Affected producer/consumer contract checks and integration checks.
- The small invariant suite once available: single owner, idempotent route commands, locked-door movement, fixed cumulative delay reference and deterministic replay.
- Affected UI workflow checks when visible behavior changes.

Keep the routine PR suite small enough for frequent integration. Run full scenario suites at release gates. For R5, also archive SATSA versus fixed-policy and exhaustive-grid results, fixed seeds, separate training/held-out traces, invalid candidates and configuration approval/activation evidence. Long optimizer experiments need not run for unrelated documentation or UI PRs.

Never disable an invariant check to make a merge pass. Fix an invalid test with an explanation and independent review, or fix the implementation. Record software-simulation evidence without making physical elevator certification claims.

## 7. PR checklist

Include this information in every PR description:

```text
Task ID and owner:
SRS requirement IDs / user stories:
Behavior changed and observable acceptance condition:
Dependencies already merged:
Shared contract/configuration changes and affected owners:
How to run and test:
Checks: PASS / FAIL / NOT RUN, with command, commit and evidence link:
Deterministic scenario IDs, seeds and model/policy versions, if relevant:
Error paths and rejected operations covered:
Screenshots or trace excerpts, if useful:
Known limitations and any remaining work:
Required reviewers and approval status:
```

For a documentation-only PR, mark implementation fields `not applicable` with a short reason. A checked box without supporting evidence is not an acceptance result.

## 8. Equal coordination duties

| Two-week sprint | Integration steward |
|---|---|
| Sprint 1 | Thanh |
| Sprint 2 | An |
| Sprint 3 | Huy |
| Sprint 4 | Hai |

Each steward maintains the merge queue, coordinates the sprint demonstration and records integration results. Everyone implements, tests, reviews and documents their own area. All four contribute to final acceptance and present their own work; release preparation is shared rather than assigned entirely to the final steward.
