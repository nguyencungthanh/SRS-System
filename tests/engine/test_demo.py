"""app/demo.py: the explainer is recorded from real runs and points at real code."""

import json
import re

import pytest

from app import demo

pytestmark = pytest.mark.skipif(
    demo.module_set()[0].kinds != {"dispatch": "real", "cabin": "real", "cc": "real"},
    reason="the demo explains the team's modules; fakes have other functions",
)


@pytest.fixture(scope="module")
def data():
    return demo.build()


def story(data, key):
    return next(s for s in data["stories"] if s["key"] == key)


def panels(s, kind):
    return [p for h in s["handlings"] for p in h["inside"] if p["kind"] == kind]


def test_every_explained_function_exists():
    index = demo.source_index(demo.traced_files())
    names = {name for _, name in index}
    assert set(demo.EXPLAIN) <= names, set(demo.EXPLAIN) - names
    assert demo.WATCHED <= names


def test_the_cabin_checklist_is_read_from_validate():
    checks = demo.proposal_checks()
    assert [c["n"] for c in checks] == list(range(1, 9))
    assert "VERSION_MISMATCH" in checks[0]["reasons"]
    assert "FULL_LOAD" in checks[6]["reasons"]


def test_every_story_reaches_all_its_key_moments(data):
    # record() raises SystemExit if a caption finds no event, so building is the check.
    for s in data["stories"]:
        captions = [e for e in s["events"] if e.get("caption")]
        assert len(captions) == len(next(x for x in demo.STORIES if x.key == s["key"]).captions)
        assert all(not e["minor"] for e in captions)


def test_recorded_decisions_match_the_srs_oracles(data):
    (at01,) = [p for p in panels(story(data, "hall-call"), "eta") if p["ref"] == "HC-0001"][:1]
    assert at01["chosen"] == "P1"
    assert {r["car"]: r.get("eta") for r in at01["rows"]}["P1"] == 8.0  # AT-01
    grouped = [p for p in panels(story(data, "grouping"), "eta") if p["ref"] == "HC-0002"][0]
    p1 = next(r for r in grouped["rows"] if r["car"] == "P1")
    (budget,) = p1["budget"]
    assert (budget["reference"], budget["new"]) == (8.0, 14.0)  # AT-02: +6 s <= DELAY_CAP
    medical = panels(story(data, "medical"), "mission")[0]
    etas = {r["car"]: (r["eta"], r["excluded"]) for r in medical["rows"]}
    assert medical["chosen"] == "P2" and etas["P2"] == (20.0, None)  # AT-03
    assert etas["P3"][1] == "MAINTENANCE" and etas["G1"][1] is not None
    hold = panels(story(data, "crowd"), "hold")
    assert hold and hold[0]["load"] > 1000.0  # SEN-03
    breakdown = story(data, "breakdown")
    fns = {c["fn"] for h in breakdown["handlings"] for c in h["calls"]}
    assert "Dispatch._mission_released" in fns  # D-62 is on screen


def test_the_committed_recording_points_at_real_lines():
    text = demo.OUT.read_text()
    recorded = json.loads(text[text.index("{") : text.rindex("}") + 1])
    stale = []
    for s in recorded["stories"]:
        for h in s["handlings"]:
            for c in h["calls"]:
                line = (demo.ROOT / c["file"]).read_text().splitlines()[c["line"] - 1]
                if not re.search(rf"def {re.escape(c['fn'].split('.')[-1])}\(", line):
                    stale.append(f"{c['file']}:{c['line']} {c['fn']}")
    assert not stale, f"web/demo/stories.js is stale, run python -m app.demo: {stale[:5]}"
