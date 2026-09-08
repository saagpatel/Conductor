"""What the report does with a figure it cannot compute.

Every check here answers one question: does the report say "unknown", or
does it quietly say zero? A cold review on 2026-09-08 found six places
where an absent, unreadable, or non-finite figure read as `0.0` and was
then divided, averaged, or printed as if it were measured.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_report import MERGED, _landed_mission, _write_receipt

from conductor.report import report


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


def _review(home: Path, run_id: str, answer: str, **extra: object) -> None:
    directory = _write_receipt(
        home,
        run_id,
        fleet="cursor",
        model="grok-4.6",
        stage="review",
        lane="review-grok",
        mode="read",
        answer_path=str(home / "runs" / run_id / "answer.txt"),
    )
    (directory / "answer.txt").write_bytes(answer.encode())
    if extra:
        path = directory / "result.json"
        raw = json.loads(path.read_text())
        raw.update(extra)
        path.write_text(json.dumps(raw))


def test_a_non_finite_duration_is_unknown_not_a_zero_second_run(home: Path):
    """json round-trips NaN and Infinity, and both survive an `int | float`
    check; either one in a group turns its mean and median into NaN."""
    for run_id, duration in (
        ("20260101T000000Z-a", 4.0),
        ("20260101T000000Z-b", float("nan")),
        ("20260101T000000Z-c", float("inf")),
        ("20260101T000000Z-d", -1.0),
    ):
        _write_receipt(
            home,
            run_id,
            fleet="claude",
            model="claude-sonnet-5",
            duration_s=duration,
        )
    row = report(home).vendor_stage[0]
    assert row.to_dict()["mean_duration_s"] == 4.0
    assert row.to_dict()["median_duration_s"] == 4.0
    assert row.to_dict()["unknown_durations"] == 3


def test_a_missing_duration_is_left_out_of_the_mean_not_counted_as_zero(home: Path):
    directory = _write_receipt(
        home, "20260101T000000Z-a", fleet="claude", model="claude-sonnet-5", duration_s=10.0
    )
    raw = json.loads((directory / "result.json").read_text())
    del raw["duration_s"]
    (directory / "result.json").write_text(json.dumps(raw))
    _write_receipt(
        home, "20260101T000000Z-b", fleet="claude", model="claude-sonnet-5", duration_s=10.0
    )
    row = report(home).vendor_stage[0].to_dict()
    assert row["mean_duration_s"] == 10.0
    assert row["unknown_durations"] == 1


def test_input_tokens_reported_as_a_float_still_count_against_the_cache_rate(home: Path):
    """A vendor that writes the count as a JSON float used to read as 0,
    which drops it out of the denominator and inflates the hit rate."""
    directory = _write_receipt(
        home,
        "20260101T000000Z-a",
        fleet="claude",
        model="claude-sonnet-5",
        cache_read_tokens=50,
        input_tokens=0,
    )
    path = directory / "result.json"
    raw = json.loads(path.read_text())
    raw["usage"]["input_tokens"] = 50.0
    path.write_text(json.dumps(raw))
    assert report(home).vendor_stage[0].cache_pct() == 50.0


def test_an_answer_that_is_not_valid_utf8_is_skipped_not_a_crash(home: Path):
    """`UnicodeDecodeError` is a ValueError, not an OSError: an interrupted
    write that truncated `answer.txt` mid-sequence used to abort the whole
    report."""
    directory = _write_receipt(
        home,
        "20260101T000000Z-a",
        fleet="cursor",
        model="grok-4.6",
        stage="review",
        lane="review-grok",
        mode="read",
        answer_path=str(home / "runs" / "20260101T000000Z-a" / "answer.txt"),
    )
    (directory / "answer.txt").write_bytes(b"NO_FINDINGS\n\xff\xfe")
    _review(home, "20260101T000000Z-b", "NO_FINDINGS\n")

    rows = report(home).reviewer_finding_rate

    assert [row.runs for row in rows] == [1]


def test_a_mission_snapshot_that_is_not_valid_utf8_is_skipped_not_a_crash(home: Path):
    mission_dir = home / "missions" / "20260101T000000Z-m"
    mission_dir.mkdir(parents=True)
    (mission_dir / "result.json").write_bytes(b'{"ok": true, "\xff": 1}')
    _write_receipt(home, "20260101T000000Z-a", fleet="claude", model="claude-sonnet-5")

    assert report(home).vendor_stage[0].runs == 1


def test_an_interrupted_review_is_not_scored_as_a_completed_sitting(home: Path):
    """An interrupt can flush a partial answer; reading it as a review turns
    a stop into a finished sitting in rule 7's own finding rate."""
    _review(home, "20260101T000000Z-a", "FINDINGS: 3\n", interrupted=True)
    _review(home, "20260101T000000Z-b", "FINDINGS: 2\n", cancelled=True)
    _review(home, "20260101T000000Z-c", "FINDINGS: 1\n")

    rows = report(home).reviewer_finding_rate

    assert len(rows) == 1
    assert rows[0].runs == 1
    assert rows[0].findings == 1


def test_a_landed_mission_with_an_unpriced_run_reports_no_per_item_figure(home: Path):
    """`cost_usd` skips a run with no price, so the mission's cost is a
    lower bound; dividing it by the items it landed reads as a cheap
    mission rather than an unknown one."""
    _landed_mission(home, "20260101T000000Z-m-p", cost=4.0, items=[1, 2], land=[MERGED])
    _write_receipt(
        home,
        "20260101T000000Z-m-p-fix",
        fleet="claude",
        model="claude-sonnet-5",
        stage="fix",
        lane="fix",
        mission="20260101T000000Z-m-p",
        cost=None,
        basis=None,
    )

    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == "20260101T000000Z-m-p")

    assert row.unpriced_runs == 1
    assert row.usd_per_item() is None
    assert row.to_dict()["unpriced_runs"] == 1
    # And the mission is out of the aggregate's division on the same grounds.
    assert rpt.landed.to_dict() == {
        "missions": 1,
        "cost_usd": "4.00",
        "with_items": 0,
        "items": 0,
        "usd_per_item": None,
    }


def test_a_windowed_report_reports_no_per_item_figure(home: Path):
    """`--since` bounds which runs reach a mission row, but `merged` and
    `items` are read from the mission's whole life: dividing the one by the
    other is not a rate."""
    from conductor.spend import _parse_bound

    _landed_mission(home, "20260101T000000Z-m-p", cost=4.0, items=[1, 2], land=[MERGED])

    whole = report(home)
    assert next(r for r in whole.missions).usd_per_item() is not None

    windowed = report(home, since=_parse_bound("2020-01-01", "--since"))
    row = next(r for r in windowed.missions if r.mission == "20260101T000000Z-m-p")

    assert row.items == 2
    assert row.landed_ok == 1
    assert row.usd_per_item() is None
    assert windowed.landed.to_dict()["usd_per_item"] is None
