"""optimization/wear.py: the wear study runs end to end and reports every month (D-79)."""

from optimization import wear


def test_D_79_wear_study_writes_a_month_by_month_report(tmp_path):
    month_hours = 0.05  # 3-minute "months": only the machinery is checked here
    assert wear.main(
        ["--month-hours", str(month_hours), "--months", "2", "--seeds", "1", "--workers", "3",
         "--out", str(tmp_path)]
    ) == 0  # fmt: skip
    text = (tmp_path / "wear.md").read_text()
    for kind in wear.KINDS:
        assert f"## {kind}, seed 1" in text
    assert "Months are shortened to 0.05 h" in text
    lines = (tmp_path / "wear_runs.jsonl").read_text().splitlines()
    assert len(lines) == 3
    assert all('"month": 2' in line for line in lines)  # both months closed in every run
    maintained = next(line for line in lines if '"kind": "maintenance"' in line)
    assert '"excluded": ["P3"]' in maintained  # month 2 leaves P3 out (D-75)
