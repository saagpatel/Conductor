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
from test_report import MERGED, _landed_mission, _write_mission, _write_receipt

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


def test_a_missing_breaker_is_left_out_of_the_tool_call_mean_not_counted_as_zero(home: Path):
    """`spend._read_run` sets `tool_calls = 0` when `breaker` is null; the
    vendor/stage mean used to average that 0 in, so a 40-call run plus a
    crash printed `mean_tools 20.0` with no column saying one figure was
    never measured."""
    measured = _write_receipt(
        home,
        "20260101T000000Z-a",
        fleet="claude",
        model="claude-sonnet-5",
        tool_calls=40,
    )
    crash = _write_receipt(
        home,
        "20260101T000000Z-b",
        fleet="claude",
        model="claude-sonnet-5",
        tool_calls=0,
    )
    raw = json.loads((crash / "result.json").read_text())
    raw["breaker"] = None
    (crash / "result.json").write_text(json.dumps(raw))
    row = report(home).vendor_stage[0].to_dict()
    assert row["mean_tool_calls"] == 40.0
    assert row["unknown_tool_calls"] == 1
    assert row["runs"] == 2
    assert "breaker" in json.loads((measured / "result.json").read_text())


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


def test_an_answer_that_is_not_valid_utf8_counts_as_unparsed_not_absent(home: Path):
    """`UnicodeDecodeError` is a ValueError, not an OSError: an interrupted
    write that truncated `answer.txt` mid-sequence used to abort the whole
    report. The sitting still happened; an unreadable file is `unparsed`,
    not a review that never ran."""
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

    assert len(rows) == 1
    assert rows[0].runs == 2
    assert rows[0].unparsed == 1
    assert rows[0].findings == 0


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


def test_an_interrupted_review_is_absent_from_precision_as_well_as_finding_rate(home: Path):
    """Finding rate skips a stop because a partial answer is not a sitting.
    Precision used to add the snapshot's `findings` anyway, so the two
    tables disagreed about whether the review happened. The join is the
    last attempt's `run_id` to that run receipt -- a lane receipt does not
    carry `interrupted`."""
    from test_report import _fix_lane, _write_mission

    run_id = "20260101T000000Z-a"
    _review(home, run_id, "FINDINGS: 3\n", interrupted=True)
    path = home / "runs" / run_id / "result.json"
    raw = json.loads(path.read_text())
    raw["mission"] = "m-int"
    path.write_text(json.dumps(raw))
    _write_receipt(
        home,
        "20260101T000000Z-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m-int",
    )
    _write_mission(
        home,
        "m-int",
        name="m-int",
        ok=False,
        lanes=[
            {
                "name": "review-grok",
                "stage": "review",
                "review": {"verdict": "findings", "findings": 3},
                "attempts": [{"run_id": run_id, "fleet": "cursor", "model": "grok-4.6"}],
            },
            _fix_lane(
                [{"lane": "review-grok", "index": 1, "disposition": "fixed", "reason": "r"}]
            ),
        ],
    )
    _review(home, "20260101T000000Z-c", "FINDINGS: 1\n")

    rpt = report(home)
    rate = next(r for r in rpt.reviewer_finding_rate if r.vendor == "xai")
    assert rate.runs == 1
    assert rate.findings == 1
    assert not [r for r in rpt.reviewer_precision if r.vendor == "xai"]


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
        "items_cost_usd": "0.00",
        "usd_per_item": None,
    }


def test_a_windowed_report_reports_no_per_item_figure(home: Path):
    """`--since` bounds which runs reach a mission row, but `merged` and
    `items` are read from the mission's whole life: dividing the one by the
    other is not a rate. The flag is per mission: a window that actually
    excluded a snapshot run blanks the figure."""
    from conductor.spend import _parse_bound

    _landed_mission(home, "20260101T000000Z-m-p", cost=4.0, items=[1, 2], land=[MERGED])
    _write_receipt(
        home,
        "20251201T000000Z-m-p-early",
        fleet="claude",
        model="claude-sonnet-5",
        stage="fix",
        lane="fix",
        mission="20260101T000000Z-m-p",
        cost=1.0,
    )
    path = home / "missions" / "20260101T000000Z-m-p" / "result.json"
    raw = json.loads(path.read_text())
    raw["lanes"].append(
        {
            "name": "fix",
            "stage": "fix",
            "attempts": [{"run_id": "20251201T000000Z-m-p-early"}],
        }
    )
    path.write_text(json.dumps(raw))

    whole = report(home)
    whole_row = next(r for r in whole.missions if r.mission == "20260101T000000Z-m-p")
    assert whole_row.to_dict()["windowed"] is False
    assert whole_row.to_dict()["usd_per_item"] == "2.50"

    windowed = report(home, since=_parse_bound("2026-01-01", "--since"))
    row = next(r for r in windowed.missions if r.mission == "20260101T000000Z-m-p")

    assert row.items == 2
    assert row.landed_ok == 1
    assert row.usd_per_item() is None
    assert row.to_dict()["windowed"] is True
    assert row.to_dict()["usd_per_item"] is None
    assert windowed.landed.to_dict()["usd_per_item"] is None


def test_a_since_that_cuts_nothing_still_reports_per_item(home: Path):
    """`conductor report --since 2020-01-01` over 2026 receipts used to
    stamp `windowed` on every row and blank rule 2's figure even when the
    window excluded nothing."""
    from conductor.spend import _parse_bound

    _landed_mission(home, "20260101T000000Z-m-p", cost=4.0, items=[1, 2], land=[MERGED])

    rpt = report(home, since=_parse_bound("2020-01-01", "--since"))
    row = next(r for r in rpt.missions if r.mission == "20260101T000000Z-m-p")

    assert row.items == 2
    assert row.landed_ok == 1
    assert row.windowed is False
    assert row.to_dict()["usd_per_item"] == "2.00"
    assert rpt.landed.to_dict()["usd_per_item"] == "2.00"


def test_a_mission_whose_snapshot_run_count_cannot_be_determined_is_windowed(
    home: Path,
):
    """No snapshot means the mission's run count is unknown. Unknown is
    not "not windowed": the figure is refused rather than divided."""
    from conductor.spend import _parse_bound

    _write_receipt(
        home,
        "20260101T000000Z-orphan",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="orphan-no-snapshot",
        cost=4.0,
    )
    rpt = report(home, since=_parse_bound("2020-01-01", "--since"))
    row = next(r for r in rpt.missions if r.mission == "orphan-no-snapshot")
    assert row.windowed is True
    unbounded = report(home)
    orphan = next(r for r in unbounded.missions if r.mission == "orphan-no-snapshot")
    assert orphan.windowed is False


def test_a_parked_mission_is_neither_ok_nor_failed_and_is_out_of_vendor_totals(home: Path):
    """`run_mission` writes ok=False when it parks; gc and the CLI still
    say the sitting is waiting. The report used to print a failed row and
    mix pre-pause runs into vendor/stage as if it had ended. ok is n/a
    (the same blank as any figure it declines to state); unfinished is
    the windowed-style flag that explains why."""
    _write_receipt(
        home,
        "20260101T000000Z-done",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m-done",
        cost=2.0,
    )
    _write_mission(
        home,
        "m-done",
        name="done",
        ok=True,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T000000Z-done"}],
            }
        ],
    )
    _write_receipt(
        home,
        "20260101T000000Z-park",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m-park",
        cost=5.0,
    )
    _write_mission(
        home,
        "m-park",
        name="parked",
        ok=False,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T000000Z-park"}],
            }
        ],
        paused={"kind": "before", "lane": "build", "question": "continue?"},
    )
    rpt = report(home)
    park = next(r for r in rpt.missions if r.mission == "m-park")
    done = next(r for r in rpt.missions if r.mission == "m-done")
    assert park.ok is None
    assert park.unfinished is True
    assert park.to_dict()["ok"] is None
    assert park.to_dict()["unfinished"] is True
    assert park.cost_usd == 5
    assert done.ok is True
    assert done.unfinished is False
    row = next(r for r in rpt.vendor_stage if r.stage == "build")
    assert row.runs == 1
    assert row.cost_usd == 2


def test_an_interrupted_mission_is_neither_ok_nor_failed(home: Path):
    """Interrupted is the same question as parked: gc treats the mission
    as still live. The report does not print it as a finished failure."""
    _write_receipt(
        home,
        "20260101T000000Z-int",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m-int-mission",
        cost=3.0,
    )
    _write_mission(
        home,
        "m-int-mission",
        name="interrupted",
        ok=False,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T000000Z-int"}],
            }
        ],
        interrupted=True,
    )
    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == "m-int-mission")
    assert row.ok is None
    assert row.unfinished is True
    assert rpt.vendor_stage == []


def test_a_pause_that_already_has_an_answer_is_a_finished_sitting(home: Path):
    """The operator already answered (stop). That sitting ended; ok stays
    the receipt's False, and the run belongs in vendor/stage."""
    _write_receipt(
        home,
        "20260101T000000Z-stop",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m-stop",
        cost=1.0,
    )
    _write_mission(
        home,
        "m-stop",
        name="stopped",
        ok=False,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T000000Z-stop"}],
            }
        ],
        paused={"kind": "before", "lane": "build", "question": "continue?", "answer": "stop"},
    )
    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == "m-stop")
    assert row.ok is False
    assert row.unfinished is False
    assert rpt.vendor_stage[0].runs == 1


def test_non_finite_wall_figures_are_blank_not_nan(home: Path):
    """NaN and inf pass `isinstance(..., float)` and used to reach
    `WallClockRow`, where `busy()`/`stretch()` became NaN and `json.dumps`
    wrote a bare NaN."""
    _write_mission(
        home,
        "m-nan",
        name="mission-nan",
        ok=True,
        lanes=[],
        wall={
            "wall_s": float("nan"),
            "paused_s": float("inf"),
            "gate_s": float("-inf"),
            "lanes_s": 400.0,
            "idle_s": -1.0,
            "occupied_s": 1200.0,
            "critical_path_s": 900.0,
            "lead_s": 1.0,
            "concurrency": 2,
        },
    )
    row = report(home).wall_clock[0]
    assert row.wall_s is None
    assert row.paused_s is None
    assert row.gate_s is None
    assert row.lanes_s == 400.0
    assert row.idle_s is None
    assert row.occupied_s == 1200.0
    assert row.busy() is None
    dumped = json.dumps(row.to_dict(), allow_nan=False)
    assert "NaN" not in dumped
    assert "Infinity" not in dumped
