# AI log - An

Raw material for the "how we used AI" half of the presentation. One entry per loop:
**key features -> plan -> An corrects -> code -> log.** Keep it honest, including mistakes.

Tool: Claude Code (Claude Opus 5.5) in the Claude desktop app.

---

## 14/11/2026 - AN-01 `contracts/` (F1-F5, F10), contract v1

**Asked:** read PROJECT_PLAN, CONTRIBUTING, DECISIONS and the SRS; list the key features of
`contracts/` for review; then a plan; code only after the plan is approved.

**AI did**

- Extracted the SRS PDF to text and read 3.3, 3.4, 4.1-4.9, 7.1-7.9, 9.2-9.3, App. B and D.
- Key features: found 10 gaps in the plan's message table (G1-G10), e.g. no way for a pure
  `handle()` to wait (motion, doors, ACK timeout), no shape for a route stop, no loading /
  unloading confirmation, no initial cabin state for AT-03, R/W stored as global constants
  although the optimizer runs 55 configurations in one process.
- Plan: file order, field list per message, routing, priority, self-tests. An approved it as is.
- Code: `contracts/` with 28 messages, value types, enums, config + `Policy`, deterministic
  `IdGen`, JSON codec, routing + priority, module protocols + factory signatures, UI snapshot
  shapes + REST table + `fake_snapshot()`. 108 self-tests in `tests/contract/test_contracts.py`,
  including the SRS oracles recomputed from `config.py` (AT-01 8/58/64, AT-02 4/14,
  TC-DELAY-01, AT-03 90/20/58, AT-04 44/50/58/64/122).
- DECISIONS.md: new rows D-08, D-09, D-16..D-19, D-25..D-28, D-36, D-37 (Proposed), D-15 changed.
  PROJECT_PLAN message table, snapshot shape and REST table synced with the code.

**AI got wrong**

- Worked in the wrong folder (`~/Downloads/SRS-System`, an older clone) and read outdated
  versions of the plan, then created a branch there. An caught it from a screenshot of the
  real folder.
- Ran `git commit` and created a branch after An said to leave git alone and push once at the
  end. An had to ask for it to be undone.
- First draft of the tests referenced samples by list index (`SAMPLES[16]`), which breaks
  silently when the list changes. Replaced with named constants before the first run.
- Changed one shape vs. the approved plan without asking: `RouteVersionReport` answers about
  the queried `command_id` (`seen`, `accepted`) instead of `last_command_id`. Flagged in D-17
  for team review.

**An corrected**

- Pointed the AI to the right repository.
- No git at all until everything is done; one push at the end.
- Reviewed the result and approved the unrequested `RouteVersionReport` change and the four
  extra REST endpoints (`/mission/confirm`, `/fault/clear`, `/maintenance`, `/alert/ack`).

**Result**

- `pytest`: 108 passed. `ruff check` and `ruff format --check`: clean.
- Environment: `.venv` on Python 3.14.4 (plan requires >= 3.12); versions pinned in
  `requirements.txt`.
- Not committed (push once at the end). Rows marked Proposed in DECISIONS.md wait for the
  team review.

---

## 14/11/2026 - AN-01 `fakes/` (F6) + minimal bus (F8 part 1)

**Asked:** key features for `fakes/`, then a plan, then code. An approved both without changes.

**AI did**

- Key features: found G11 (who sends which mission status), G12 (one alarm press could give
  two ALARM alerts), G13 (who boards passengers in random traffic, deferred to the engine
  loop). While planning, found G14: a pure `handle()` module has no way to start itself, so
  the engine now sends `Timer(key="start")` at t=0.
- Code: `simulation/bus.py` (queue by time/priority/sequence, msg_id stamping, rejects
  modules that emit a wrong `t_sim` or a pre-set `msg_id`), `FakeCabin`, `FakeDispatch`,
  `FakeControlCenter`, 29 new tests. All-fake run reproduces AT-01 (8 / 58 / 64 s) and an
  AT-03-style mission (P2 chosen, P3/G1 excluded with reasons, completed at 82 s); two runs
  give byte-identical logs (NFR-05).
- DECISIONS: D-29 new; D-08, D-16, D-18, D-19 extended.

**AI got wrong**

- First version of `_stop_motion` invalidated every pending timer, so a fault during the door
  dwell would have left the door open forever. Caught while reviewing its own code before the
  tests ran.
- Added a contract rule An had not seen in the plan: `route_version` changes on every stop-list
  change, including a served stop (D-16). Flagged for review.
- Two lines over the 100-char limit (fixed by `ruff format`).

**Checked:** all 137 tests passed on the first run, which looked too easy, so the AI broke the
fakes on purpose (wrong dwell, cabin also raising ALARM, no stale check). The matching tests
failed each time.

**Result:** `pytest` 137 passed; `ruff` clean. Not committed.

**An decided:** keep D-16 (`route_version` bumps on every stop-list change, served stops included).

---

## 14/11/2026 - AN-01 contract tests (F7)

**Asked:** key features for the per-module contract tests, then a plan, then code. An approved
both without changes.

**AI did**

- Key features: found G15-G19 (what R/W actually restrict, a missing door-button message, when
  mission events fire, the role matrix, what a valid freight request is). While planning the
  fault test it found one more: how a faulted car hands its hall calls back (D-55).
- Contract: new message `DoorRequest` + `/door`; DECISIONS D-50..D-55 (Proposed).
- Harness: `tests/conftest.py` (`--fakes`, `--fakes-all`, `importorskip` so `main` stays green
  before the modules exist), `tests/contract/helpers.py` (fleets from the SRS examples,
  `ScriptedCabin` that loses ACKs / drops / NACKs on purpose, invariant checks for single
  ownership, door interlocks and command-id consistency).
- Tests: `test_dispatch.py` (34), `test_cabin.py` (35), `test_control_center.py` (28), each named
  after its SRS / decision ID, oracles straight from SRS 4.9 and 9.2.
- Fakes updated to D-52 (wait for LOADED / UNLOADED) and D-55 (release hall calls on fault).

**AI got wrong (and how it was caught)**

- `--fakes-all` hung: the fake dispatch re-offered a call to the car that had just NACKed it,
  forever, at the same instant. Fixed the fake, and the bus now raises `ContractViolation` on
  more than 10 000 deliveries at one instant, so a real module with the same bug fails a test
  instead of hanging it.
- Running the `fake_ok` tests against the fakes exposed two bugs in the AI's own tests: a
  telemetry window that included samples from before the button press, and a "redelivered"
  proposal rebuilt with a new `expiry_ts` (so it was not the same payload). Both fixed.
- Every remaining `--fakes-all` failure (41) was read one by one and traced to behaviour the
  fakes deliberately skip, not to a broken test.

**Limits:** the 50 tests not marked `fake_ok` have never run against a real module. Owners
should report any test that looks wrong to An instead of changing it.

**Result:** `pytest` 140 passed, 97 skipped (waiting for dispatch/, cabin/, control_center/);
`pytest --fakes` 187 passed; `ruff` clean. Not committed.

---

## 14/11/2026 - AN-01 engine, app, AI context (F8, F9)

**Asked:** key features for the full engine (F8) and the AI context (F9), then a plan, then
code. An approved both without changes.

**AI did**

- Key features: G13 (who moves the virtual passengers) plus five new gaps: losing / delaying a
  message for AT-05, the interface between `app/` and Huy's `api/`, the trace format, real vs
  fake by default, and `delay_violations` that only Dispatch can know (D-56..D-61).
- Code: `simulation/engine.py` (RUNNING / PAUSED / STOPPED, reset, input trace, JSON-lines log,
  replay by SHA-256, snapshot assembly, wait metrics), `simulation/passengers.py` (virtual
  passengers as an ordinary module), network drop/delay rules in the bus, `scenarios/`
  (AT-01..AT-05 and DEMO), `app/` (`python -m app`, `python -m app.headless`, `--replay`,
  real-time `SimRunner` implementing `contracts/runtime.py`), 25 tests in `tests/engine/`.
- Docs: `AI_CONTEXT.md` (shared rules, module interface, one section per coder with SRS
  sections, decisions and the traps their tests check, a starter prompt) and `README.md`.
  Every README command was run before writing it down.

**AI got wrong (and how it was caught)**

- The first replay hash included operator events (start / pause), so a run that was paused
  could never replay as IDENTICAL. Caught while designing the replay test; operator events
  are now excluded from the hash (they change *when* things run, not *what* is decided).
- `telemetry_age_s = inf` before the first telemetry would have produced `Infinity`, which a
  browser cannot parse as JSON. Now `None`; the test uses `json.dumps(..., allow_nan=False)`.
- With fakes, AT-05-unsafe never finished: the fake dispatch kept assigning a car whose
  sensors were invalid. The fake now excludes such cars, as DC-02 requires.
- `--cabin real` without `cabin/` printed a raw traceback; now a one-line message.
- All 25 new tests passed on the first run, so the AI broke the code on purpose three times
  (pause ignored, operator events hashed, no load limit for boarding); each was caught.

**Result:** `pytest` 165 passed, 97 skipped; `pytest --fakes` 212 passed; `ruff` clean;
`python -m app.headless --scenario AT-01` prints 8 / 58 / 64 s with all fakes; replay
IDENTICAL. Not committed.
