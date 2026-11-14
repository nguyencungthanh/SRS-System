# Contributing - rules for the coding team

> **What this file is:** the team's working rules: folder ownership, setup commands, how to change the contract, Git branches and merging, how to work with AI, the PR template and code style.
> **Use it to:** check what you may touch and how to get your code into `main` safely. Read it before your first PR. For *what* to build and *who* builds it, see [PROJECT_PLAN.md](PROJECT_PLAN.md).

For **An, Thanh, Hai, Huy**. The goal of these rules is that four people, each working with their own AI, can build their parts **independently** and merge them without breaking each other.

## 1. Golden rules

1. **Edit only your own folders.** See PROJECT_PLAN.md, Section 5.
2. **Never import another person's module.** Talk only through `contracts/` messages.
3. **`contracts/` belongs to An.** If you need a change, ask; do not edit it (Section 4).
4. **Your module is done when its contract tests pass,** plus your own unit tests.
5. **`main` always runs.** `pytest` must be green on `main` after every merge.
6. **Do not merge code you cannot explain,** even if your AI wrote it.
7. **If the SRS is unclear, do not guess and do not let the AI guess.** Write it in `DECISIONS.md` and tell the team.

## 2. Folder ownership

| Folder | Owner | Others may |
|---|---|---|
| `contracts/`, `fakes/`, `tests/contract/`, `tests/engine/` | An | read only |
| `simulation/`, `app/`, `optimization/`, `scenarios/` | An | read only |
| `dispatch/`, `tests/dispatch/` | Thanh | read only |
| `cabin/`, `tests/cabin/` | Hai | read only |
| `control_center/`, `api/`, `web/`, `tests/control_center/` | Huy | read only |
| `README.md`, `DECISIONS.md`, `requirements.txt` | An merges; anyone proposes | open a PR |

A PR that touches a folder you do not own is rejected, even a one-line fix. Tell the owner instead.

## 3. Setup and everyday commands

```bash
git clone <repo-url> && cd <repo>
python3 -m venv .venv && source .venv/bin/activate   # needs Python >= 3.12
pip install -r requirements.txt
```

| Purpose | Command |
|---|---|
| All tests | `pytest` |
| Only your contract tests | `pytest tests/contract/test_cabin.py` |
| Contract tests that also run on the fakes | `pytest --fakes` |
| Lint + format | `ruff check . && ruff format .` |
| Run with your module real, others fake | `python -m app.headless --scenario AT-01 --dispatch fake --cabin real --cc fake` |
| Headless acceptance scenario | `python -m app.headless --scenario AT-01` |
| Web UI | `python -m app`, then open `http://localhost:8000` |

These commands are confirmed in [README.md](README.md). If one does not work, tell An.

## 4. Contract freeze and change requests

The contract is everything in `contracts/` plus the shape of the fakes and the contract tests. It is **frozen** after the team review.

To change it:

1. Message An with **what you need, why, and which message, enum or test it affects.**
2. An decides, edits `contracts/`, bumps `CONTRACT_VERSION` in `contracts/__init__.py`, updates the fakes and contract tests, and merges.
3. An posts in the group chat: "Contract vN: <change>. Affects: <who>."
4. Affected people pull `main` and adapt.

Until An answers, keep working with the current contract. Write your code so the change will be small.

Changes that are always allowed without asking: **adding** an optional field with a default, or **adding** a new enum value nobody else uses yet. Renaming or removing anything always needs the steps above.

## 5. Branches and merging

Only one long-lived branch exists: **`main`**. There is no `develop`.

- **Branch name:** `<name>/<task-id>-<short-desc>`, lowercase. Example: `hai/HA-03-door-state-machine`.
- **Start from fresh main:**

  ```bash
  git switch main && git pull --ff-only
  git switch -c hai/HA-03-door-state-machine
  ```

- **Keep PRs small:** one package or part of a package. Open the PR as soon as its tests pass; do not batch the whole day into one PR.
- **Before asking for review:** merge the latest `main` into your branch (`git fetch && git merge origin/main`) and run `pytest` and `ruff check .`.
- **Review:** one approval from **anyone else** on the team. Reviewers check that the PR stays inside the author's folders and that tests pass; they do not redesign it. Review promptly so nobody is blocked. An must review anything touching `contracts/` or `app/` wiring.
- **Merge:** squash merge into `main`, with the task ID in the title. Delete the branch afterwards.
- **Never:** push directly to `main`, force-push a shared branch, or disable or skip a failing test to get green.
- **Conflicts:** they should be rare, because nobody shares folders. If you hit one outside your folder, stop and ask that folder's owner.

### Integration and code freeze

| Phase | Rule |
|---|---|
| Building modules | Normal PRs into `main` |
| Integration | An integrates. Owners only merge **fixes** for failures An reports, and reply quickly. |
| Stabilising | Bug fixes only, no new features |
| Code freeze | An tags `v1.0-demo`. Anything not working goes into the README as known issues or deferred. |

## 6. Working with your AI

Each person uses their own AI tool. The rules below keep the four outputs compatible and give the presenters material for the "how we used AI" half of the grade.

1. **Always start with the same context.** Paste in `AI_CONTEXT.md`, the relevant `contracts/` files, your contract test file, and the SRS sections listed for your package in PROJECT_PLAN.md.
2. **Plan, then code.** Ask the AI for a plan first. Read it, fix what is wrong, and only then let it write code.
3. **Tests come from the SRS, not from the AI's code.** Contract tests are written by An from SRS numbers. Do not let your AI edit them to make them pass. If you think a test is wrong, tell An.
4. **The AI only edits your folders.** Reject any AI change to `contracts/`, `fakes/`, `tests/contract/` or someone else's folder.
5. **Do not accept new dependencies** the AI suggests. The stack is locked (PROJECT_PLAN.md, Section 2).
6. **Write AI notes in every PR.** Two or three lines are enough. The presenters will collect these, so keep the notes honest and concrete, including the AI's mistakes.

## 7. PR description template

```text
Task: HA-03 door state machine          Owner: Hai
SRS IDs: DOOR-01..05, SAF-02
What changed: <1-3 lines, observable behaviour>
Contract tests: pytest tests/contract/test_cabin.py -k DOOR  PASS / FAIL / NOT RUN
Own tests: tests/cabin/test_door.py  PASS
Ran with: python -m app --cabin=real (others fake)  OK / problem: ...
Contract change needed?: no / yes -> asked An

AI notes:
- Tool: <ChatGPT / Claude / Copilot / ...>
- AI did: <e.g. generated the state machine from SRS 7.4 + Figure 8>
- AI got wrong: <e.g. allowed opening while moving; ignored the landing-zone check>
- I fixed: <what you changed and why>
```

## 8. Code style

- **Python >= 3.12** with type hints on public functions; `ruff format` decides formatting.
- **No real time or randomness in core logic.** Use the `now` passed to `handle()`. Randomness only through the seeded RNG the engine provides. Never call `time.time()`, `sleep` or unseeded `random` in `dispatch/`, `cabin/`, `control_center/` or `simulation/`.
- **No magic numbers.** Every SRS constant comes from `contracts/config.py`.
- **State machines are explicit.** Use an enum state plus a transition function or table, not scattered booleans.
- **Reasons are values.** Every NACK, rejection and exclusion carries a reason enum, so logs and UI can show why.
- **Name tests after the SRS ID.** Pattern: `test_<ID with _ instead of ->_<what it checks>`. Examples: `test_DOOR_04_obstruction_reopens`, `test_AT_01_pickup_at_8s`, `test_TC_DELAY_01_rejects_32_vs_20`. This applies to contract tests and your own tests. Run one requirement with `pytest -k DOOR_04`. This keeps traceability automatic.
