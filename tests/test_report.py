"""The ledger report (E11): AGENTS.md's Shape A rules as numbers, computed
from the same durable receipts `conductor spend` reads, joined to a mission
snapshot when an older receipt carries no stage of its own."""

from __future__ import annotations

import json
from pathlib import Path

from conductor.cli import main
from conductor.report import ReviewerPrecisionRow, WallClockRow, report
from conductor.runner import Result


def _write_receipt(
    home: Path,
    run_id: str,
    *,
    fleet: str,
    model: str,
    ok: bool = True,
    cost: float | None = 1.0,
    basis: str | None = "reported",
    tokens: int = 10,
    tool_calls: int = 0,
    duration_s: float = 5.0,
    mode: str = "write",
    stage: str | None = None,
    lane: str | None = None,
    mission: str | None = None,
    kind: str | None = None,
    answer_path: str | None = None,
    tests: dict | None = None,
    test_surface: dict | None = None,
    dry_run: bool = False,
    cache_read_tokens: int = 0,
    input_tokens: int = 0,
) -> Path:
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    raw: dict[str, object] = {
        "run_id": run_id,
        "fleet": fleet,
        "model": model,
        "ok": ok,
        "mode": mode,
        "duration_s": duration_s,
        "usage": {
            "cost_usd": cost,
            "cost_basis": basis,
            "total_tokens": tokens,
            "cache_read_tokens": cache_read_tokens,
            "input_tokens": input_tokens,
        },
        "breaker": {"tool_calls": tool_calls, "tripped": None},
        "interrupted": False,
        "dry_run": dry_run,
    }
    for key, value in (
        ("stage", stage),
        ("lane", lane),
        ("mission", mission),
        ("kind", kind),
        ("answer_path", answer_path),
        ("tests", tests),
        ("test_surface", test_surface),
    ):
        if value is not None:
            raw[key] = value
    (directory / "result.json").write_text(json.dumps(raw))
    return directory


def _write_mission(
    home: Path,
    mission_id: str,
    *,
    name: str,
    ok: bool,
    lanes: list[dict],
    collate: dict | None = None,
    wall: dict | None = None,
) -> None:
    mission_dir = home / "missions" / mission_id
    mission_dir.mkdir(parents=True)
    payload: dict[str, object] = {"name": name, "ok": ok, "lanes": lanes}
    if collate is not None:
        payload["collate"] = collate
    if wall is not None:
        payload["wall"] = wall
    (mission_dir / "result.json").write_text(json.dumps(payload))


def test_report_groups_by_vendor_and_stage_with_join_for_missing_stage(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-claude-new",
        fleet="claude",
        model="claude-sonnet-5",
        cost=1.0,
        stage="build",
        lane="build",
        mission="m-new",
    )
    _write_receipt(
        home,
        "20260101T010000Z-claude-old",
        fleet="claude",
        model="claude-sonnet-5",
        cost=2.0,
    )
    _write_mission(
        home,
        "20260101T020000Z-m-old",
        name="m-old",
        ok=True,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T010000Z-claude-old"}],
            }
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.vendor_stage if r.vendor == "anthropic" and r.stage == "build")
    assert row.runs == 2
    assert row.cost_usd == 3
    # The join resolves to the snapshot directory's own name -- the same
    # identity a staged lane's live receipt carries directly (mission_id,
    # not the snapshot's separate "name" field; see
    # test_report_mission_identity_matches_what_dispatch_stamps_on_the_receipt).
    joined_run = next(r for r in rpt.missions if r.mission == "20260101T020000Z-m-old")
    assert joined_run.cost_usd == 2


def test_report_counts_cap_misses_and_gate_failures(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-agy-cap",
        fleet="antigravity",
        model="gemini-3.8-flash-high",
        ok=False,
        kind="cap",
        stage="review",
    )
    _write_receipt(
        home,
        "20260101T010000Z-cursor-gate",
        fleet="cursor",
        model="cursor-grok-4.6-medium",
        ok=False,
        kind="gate",
        stage="fix",
    )
    rpt = report(home)
    review_row = next(r for r in rpt.vendor_stage if r.vendor == "google" and r.stage == "review")
    assert review_row.cap_misses == 1
    assert review_row.gate_failures == 0
    fix_row = next(r for r in rpt.vendor_stage if r.vendor == "xai" and r.stage == "fix")
    assert fix_row.gate_failures == 1
    assert fix_row.cap_misses == 0
    kind_counts = {row.kind: row.count for row in rpt.error_kinds}
    assert kind_counts == {"cap": 1, "gate": 1}


def test_report_reviewer_finding_rate_no_findings_and_non_empty(home: Path):
    # F1: the reviewer narrates before its verdict now, and only the final
    # line -- NO_FINDINGS or FINDINGS: N -- is tallied; a "found" answer
    # with no final marker is unparsed, not counted as a finding.
    empty = home / "runs" / "empty-answer.txt"
    empty.parent.mkdir(parents=True)
    # The narration is the point: `NO_FINDINGS` on the last line after a
    # paragraph of preamble is what the old `text.strip() == "NO_FINDINGS"`
    # check read as a finding. A fixture that is only the bare token would
    # pass against that old check too (2026-09-08 audit).
    empty.write_text(
        "I read the diff and the two modules it touches.\n"
        "Nothing here meets the bar.\n\n"
        "NO_FINDINGS\n"
    )
    found = home / "runs" / "found-answer.txt"
    found.write_text("file.py:12: off-by-one\nFINDINGS: 1\n")

    _write_receipt(
        home,
        "20260101T000000Z-claude-r1",
        fleet="claude",
        model="claude-opus-5",
        stage="review",
        answer_path=str(empty),
    )
    _write_receipt(
        home,
        "20260101T010000Z-claude-r2",
        fleet="claude",
        model="claude-opus-5",
        stage="review",
        answer_path=str(found),
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_finding_rate if r.vendor == "anthropic")
    assert row.runs == 2
    assert row.findings == 1
    assert row.unparsed == 0
    assert row.rate() == 0.5


def test_report_reviewer_finding_rate_excludes_unparsed_from_rate(home: Path):
    narrated = home / "runs" / "narrated.txt"
    narrated.parent.mkdir(parents=True)
    narrated.write_text("looks fine to me, no further comment\n")

    _write_receipt(
        home,
        "20260101T000000Z-claude-narrated",
        fleet="claude",
        model="claude-opus-5",
        stage="review",
        answer_path=str(narrated),
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_finding_rate if r.vendor == "anthropic")
    assert row.runs == 1
    assert row.unparsed == 1
    assert row.findings == 0
    assert row.rate() is None


def _review_lane(name: str, *, fleet: str, model: str, findings: int) -> dict:
    return {
        "name": name,
        "stage": "review",
        "review": {"verdict": "findings", "findings": findings},
        "attempts": [{"fleet": fleet, "model": model}],
    }


def _fix_lane(dispositions: list[dict]) -> dict:
    return {"name": "fix", "stage": "fix", "dispositions": dispositions}


def test_report_reviewer_precision_table_over_two_missions(home: Path):
    # F1 item 4: two missions, each with a "review-gemini" lane and a fix
    # lane whose dispositions name it. The vendor comes from the review
    # lane's own final attempt, joined by lane name through the mission
    # snapshot -- not from the fix lane, which never names a fleet at all.
    _write_receipt(
        home,
        "20260101T000000Z-m1-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m1",
    )
    _write_mission(
        home,
        "m1",
        name="mission-one",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=2
            ),
            _fix_lane(
                [
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-gemini", "index": 2, "disposition": "refused", "reason": "r2"},
                ]
            ),
        ],
    )
    _write_receipt(
        home,
        "20260101T010000Z-m2-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m2",
    )
    _write_mission(
        home,
        "m2",
        name="mission-two",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r3"}]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.findings == 3
    assert row.fixed == 2
    assert row.refused == 1
    assert row.already == 0
    assert row.wording == 0
    assert row.precision() == round(2 / 3, 3)


def test_report_reviewer_precision_counts_an_unparsed_review_verdict_separately(home: Path):
    # F15 item 2: an unparsed verdict is not a finding source (0 findings
    # must not silently pass as "reviewed, found nothing") and its lane's
    # dispositions must not be tallied as fixed/refused either.
    _write_receipt(
        home,
        "20260101T000000Z-m5-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m5",
    )
    _write_mission(
        home,
        "m5",
        name="mission-five",
        ok=True,
        lanes=[
            {
                "name": "review-gemini",
                "stage": "review",
                "review": {"verdict": "unparsed", "findings": None},
                "attempts": [{"fleet": "antigravity", "model": "gemini-3.7-flash"}],
            },
            _fix_lane(
                [
                    {
                        "lane": "review-gemini",
                        "index": 1,
                        "disposition": "fixed",
                        "reason": "r1",
                    },
                    {
                        "lane": "review-gemini",
                        "index": 2,
                        "disposition": "refused",
                        "reason": "r2",
                    },
                ]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.findings == 0
    assert row.fixed == 0
    assert row.refused == 0
    assert row.unparsed == 1
    # W7: this mission's fix lane recorded dispositions, but neither one
    # could land against the unparsed review lane (skipped above) -- its
    # dispositions did not contribute to google's row, so this mission must
    # not count toward `missions`. A genuine old-shape receipt (fix lane
    # present, nothing landed) reads as 0 here rather than failing, the
    # same requirement spec item 3(c) asks for.
    assert row.missions == 0
    assert row.undispositioned == 0


def test_report_disposition_naming_an_unknown_lane_is_counted_not_dropped(
    home: Path, monkeypatch, capsys
):
    # F15 item 3: a well-formed disposition naming a lane that never ran as
    # `stage: review` on this mission used to vanish with no counter.
    _write_receipt(
        home,
        "20260101T000000Z-m6-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m6",
    )
    _write_mission(
        home,
        "m6",
        name="mission-six",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-typo", "index": 1, "disposition": "fixed", "reason": "r2"},
                ]
            ),
        ],
    )
    rpt = report(home)
    assert rpt.dispositions_unknown_lane == 1
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.fixed == 1
    # Grok, F15 mission 1: the printed report is the ledger the lead reads,
    # so the counter has to survive there too, not only on the JSON object.
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    printed = capsys.readouterr().out
    assert "dispositions naming an unknown lane: 1" in printed
    assert "malformed disposition lines: 0" in printed


def test_report_sums_dispositions_malformed_across_fix_lanes(home: Path):
    # F15 item 3: `dispositions_malformed` lives on each fix lane's own
    # receipt (verdicts.dispositions_malformed's count of skipped lines);
    # the report was not printing or summing it anywhere.
    _write_receipt(
        home,
        "20260101T000000Z-m7-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m7",
    )
    _write_mission(
        home,
        "m7",
        name="mission-seven",
        ok=True,
        lanes=[
            {
                "name": "fix",
                "stage": "fix",
                "dispositions": [],
                "dispositions_malformed": 2,
            },
        ],
    )
    rpt = report(home)
    assert rpt.dispositions_malformed == 2


# --- F15 mission 2 item 4: calibration and corrected finding rate ----------


def test_report_calibration_matches_confidences_to_dispositions(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-m8-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m8",
    )
    _write_mission(
        home,
        "m8",
        name="mission-eight",
        ok=True,
        lanes=[
            {
                "name": "review-gemini",
                "stage": "review",
                "review": {
                    "verdict": "findings",
                    "findings": 2,
                    "items": [
                        {"index": 1, "file": "a.py", "line": 1, "confidence": 9},
                        {"index": 2, "file": "b.py", "line": 2, "confidence": 4},
                    ],
                },
                "attempts": [{"fleet": "antigravity", "model": "gemini-3.7-flash"}],
            },
            _fix_lane(
                [
                    {"lane": "review-gemini", "index": 1, "disposition": "refused", "reason": "r1"},
                    {"lane": "review-gemini", "index": 2, "disposition": "fixed", "reason": "r2"},
                ]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.calibration() == {"refused_mean": 9.0, "fixed_mean": 4.0, "matched": 2}
    assert row.corrected_rate() == 0.5
    assert row.findings == 2
    d = row.to_dict()
    assert d["calibration"] == {"refused_mean": 9.0, "fixed_mean": 4.0, "matched": 2}
    assert d["corrected_rate"] == 0.5
    assert d["refused_confidences"] == [9]
    assert d["fixed_confidences"] == [4]


def test_report_calibration_ignores_a_disposition_with_no_matching_item(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-m9-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m9",
    )
    _write_mission(
        home,
        "m9",
        name="mission-nine",
        ok=True,
        lanes=[
            # An old-style review receipt: no "items" key at all.
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.fixed == 1
    assert row.refused_confidences == [] and row.fixed_confidences == []
    assert row.calibration() == {"refused_mean": None, "fixed_mean": None, "matched": 0}
    # findings=1, fixed=1: corrected_rate is a straight fixed/findings ratio,
    # unaffected by whether any confidence matched.
    assert row.corrected_rate() == 1.0


def test_report_prints_a_calibration_line_per_vendor(home: Path, monkeypatch, capsys):
    _write_receipt(
        home,
        "20260101T000000Z-m10-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m10",
    )
    _write_mission(
        home,
        "m10",
        name="mission-ten",
        ok=True,
        lanes=[
            {
                "name": "review-gemini",
                "stage": "review",
                "review": {
                    "verdict": "findings",
                    "findings": 1,
                    "items": [{"index": 1, "file": "a.py", "line": 1, "confidence": 7}],
                },
                "attempts": [{"fleet": "antigravity", "model": "gemini-3.7-flash"}],
            },
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    printed = capsys.readouterr().out
    assert (
        "google: refused mean confidence n/a over 0, fixed mean confidence 7.0 over 1, "
        "corrected finding rate 1.0" in printed
    )


# --- F2: wall clock on the ledger -------------------------------------------


def test_report_vendor_stage_cache_column_is_the_prompt_cache_hit_rate(home: Path):
    """Reads over everything given to the model (uncached input, reads,
    writes): the same hit rate report.md prints per mission. The first
    shape divided reads by uncached input alone and printed 300% here and
    millions of percent on a real day."""
    _write_receipt(
        home,
        "20260101T000000Z-claude-cached",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        cache_read_tokens=60,
        input_tokens=20,
    )
    rpt = report(home)
    row = next(r for r in rpt.vendor_stage if r.vendor == "anthropic" and r.stage == "build")
    assert row.cache_pct() == 75.0
    assert row.to_dict()["cache_pct"] == 75.0


def test_report_vendor_stage_cache_column_is_blank_with_no_input_tokens(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-claude-nothing",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
    )
    rpt = report(home)
    row = next(r for r in rpt.vendor_stage if r.vendor == "anthropic" and r.stage == "build")
    assert row.cache_pct() is None


def test_report_wall_clock_table_lists_every_mission_blanks_for_missing_wall(home: Path):
    _write_mission(
        home,
        "m-new",
        name="mission-new",
        ok=True,
        lanes=[],
        wall={
            "launched_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T01:00:00+00:00",
            "wall_s": 3600.0,
            "paused_s": 3000.0,
            "gate_s": 60.0,
            "lanes_s": 400.0,
            "idle_s": 5.0,
            "concurrency": 2,
        },
    )
    # A mission recorded before F2 shipped: no "wall" key at all.
    _write_mission(home, "m-old", name="mission-old", ok=True, lanes=[])
    rpt = report(home)
    rows = {row.mission: row for row in rpt.wall_clock}
    assert set(rows) == {"m-new", "m-old"}

    fresh = rows["m-new"].to_dict()
    assert fresh["wall_s"] == 3600.0
    assert fresh["paused_s"] == 3000.0
    assert fresh["gate_s"] == 60.0
    assert fresh["lanes_s"] == 400.0
    assert fresh["idle_s"] == 5.0
    assert fresh["concurrency"] == 2
    assert fresh["busy"] == round(400.0 / (3600.0 * 2), 3)

    old = rows["m-old"].to_dict()
    assert old == {
        "mission": "m-old",
        "wall_s": None,
        "paused_s": None,
        "gate_s": None,
        "lanes_s": None,
        "idle_s": None,
        "concurrency": None,
        "busy": None,
        # W8: a receipt that predates these three carries no figure for
        # them either, and `stretch` follows `critical_path_s` blank.
        "occupied_s": None,
        "critical_path_s": None,
        "lead_s": None,
        "stretch": None,
    }


def test_wall_clock_row_busy_divides_by_wall_s_times_concurrency():
    row = WallClockRow(mission="m", wall_s=12.0, lanes_s=30.0, concurrency=3)
    assert row.busy() == round(30.0 / (12.0 * 3), 3)


def test_wall_clock_row_busy_is_blank_without_concurrency():
    row = WallClockRow(mission="m", wall_s=12.0, lanes_s=30.0)
    assert row.busy() is None


def test_readme_documents_wall_clock_in_the_report_section():
    readme = Path(__file__).parents[1] / "README.md"
    raw_section = readme.read_text().split("#### Ledger report", 1)[1].split("\n### ", 1)[0]
    section = " ".join(raw_section.split())
    assert "**Wall clock**" in section
    assert "launched_at" in section and "paused_s" in section and "idle_s" in section
    assert "concurrency" in section
    assert "lanes_s / (wall_s * concurrency)" in section
    assert "cache_read_tokens" in section and "input_tokens" in section


def test_report_reviewer_precision_blank_under_three_dispositions(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-m3-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m3",
    )
    _write_mission(
        home,
        "m3",
        name="mission-three",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.total() == 1
    assert row.precision() is None


def test_report_reviewer_precision_skips_a_mission_with_no_fix_dispositions(home: Path):
    # A review lane with no fix lane recording `dispositions` at all (an
    # older mission, or one still awaiting its fix) contributes no
    # findings/fixed/refused -- "findings written" without a paired fix is
    # not a precision figure. W7: it is no longer invisible, though -- it
    # still surfaces as `undispositioned` on the vendor's row.
    _write_receipt(
        home,
        "20260101T000000Z-m4-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m4",
    )
    _write_mission(
        home,
        "m4",
        name="mission-four",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=4
            ),
            {"name": "fix", "stage": "fix"},
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.findings == 0
    assert row.total() == 0
    assert row.missions == 0
    assert row.undispositioned == 1


def test_report_reviewer_precision_missions_and_undispositioned_columns(home: Path):
    # W7: one vendor's row spans a mission with dispositions and one
    # without -- `missions` counts only the first, `undispositioned` only
    # the second, and a different vendor's own row is unaffected by either.
    _write_receipt(
        home,
        "20260101T000000Z-m5-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m5",
    )
    _write_mission(
        home,
        "m5",
        name="mission-five",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    _write_receipt(
        home,
        "20260101T010000Z-m6-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m6",
    )
    _write_mission(
        home,
        "m6",
        name="mission-six",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=2
            ),
            # No fix lane at all: this mission recorded no dispositions.
        ],
    )
    _write_receipt(
        home,
        "20260101T020000Z-m7-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m7",
    )
    _write_mission(
        home,
        "m7",
        name="mission-seven",
        ok=True,
        lanes=[
            _review_lane(
                "review-grok", fleet="cursor", model="cursor-grok-4.6-medium", findings=1
            ),
            _fix_lane(
                [{"lane": "review-grok", "index": 1, "disposition": "refused", "reason": "r2"}]
            ),
        ],
    )
    rpt = report(home)
    google_row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert google_row.missions == 1
    assert google_row.undispositioned == 1
    xai_row = next(r for r in rpt.reviewer_precision if r.vendor == "xai")
    assert xai_row.missions == 1
    assert xai_row.undispositioned == 0


def test_report_reviewer_precision_undispositioned_when_dispositions_name_only_the_other_lane(
    home: Path,
):
    # W11: a mission recorded dispositions, but every one of them names
    # vendor A's lane -- vendor B's lane parsed and got no matched
    # disposition of its own, so it must count as undispositioned too, not
    # vanish the way it did before (counted in `findings`, nowhere else).
    _write_receipt(
        home,
        "20260101T000000Z-w11a-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="w11a",
    )
    _write_mission(
        home,
        "w11a",
        name="mission-w11a",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _review_lane(
                "review-grok", fleet="cursor", model="cursor-grok-4.6-medium", findings=2
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    rpt = report(home)
    google_row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert google_row.undispositioned == 0
    assert google_row.missions == 1
    xai_row = next(r for r in rpt.reviewer_precision if r.vendor == "xai")
    assert xai_row.undispositioned == 1
    assert xai_row.missions == 0
    assert xai_row.findings == 2


def test_report_reviewer_precision_undispositioned_both_vendors_with_no_dispositions(
    home: Path,
):
    # W7's pinned behaviour, now with two vendors on the same mission: no
    # fix lane recorded dispositions at all, so both parsed review lanes
    # count as undispositioned, not just the one vendor the older tests
    # exercised.
    _write_receipt(
        home,
        "20260101T000000Z-w11b-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="w11b",
    )
    _write_mission(
        home,
        "w11b",
        name="mission-w11b",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _review_lane(
                "review-grok", fleet="cursor", model="cursor-grok-4.6-medium", findings=2
            ),
            # No fix lane at all: this mission recorded no dispositions.
        ],
    )
    rpt = report(home)
    google_row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert google_row.undispositioned == 1
    xai_row = next(r for r in rpt.reviewer_precision if r.vendor == "xai")
    assert xai_row.undispositioned == 1


def test_report_reviewer_precision_one_vendor_two_lanes_one_dispositioned(home: Path):
    # W11: two review lanes on the same vendor on one mission -- one named
    # by a disposition, one not. Tracked per lane name, so the undispositioned
    # lane still counts even though its vendor's other lane was dispositioned.
    _write_receipt(
        home,
        "20260101T000000Z-w11c-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="w11c",
    )
    _write_mission(
        home,
        "w11c",
        name="mission-w11c",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini-1", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _review_lane(
                "review-gemini-2", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini-1", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    rpt = report(home)
    google_row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert google_row.undispositioned == 1
    assert google_row.missions == 1


def test_report_reviewer_precision_zero_findings_lane_is_not_undispositioned(home: Path):
    # W11 peer review (Opus): a review lane that reported NO_FINDINGS has
    # nothing a disposition could ever name, so a mission whose fix lane
    # dispositioned only the other vendor's findings must not read the
    # zero-findings vendor as undispositioned too -- it was never left out
    # of anything, unlike an unnamed lane that actually reported findings.
    _write_receipt(
        home,
        "20260101T000000Z-w11d-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="w11d",
    )
    _write_mission(
        home,
        "w11d",
        name="mission-w11d",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=0
            ),
            _review_lane(
                "review-grok", fleet="cursor", model="cursor-grok-4.6-medium", findings=2
            ),
            _fix_lane(
                [
                    {"lane": "review-grok", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-grok", "index": 2, "disposition": "fixed", "reason": "r2"},
                ]
            ),
        ],
    )
    rpt = report(home)
    google_row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert google_row.undispositioned == 0
    assert google_row.findings == 0
    xai_row = next(r for r in rpt.reviewer_precision if r.vendor == "xai")
    assert xai_row.undispositioned == 0
    assert xai_row.missions == 1


def test_report_reviewer_precision_zero_findings_lane_not_undispositioned_without_fix_lane(
    home: Path,
):
    # The no-fix-lane branch used to count every parsed lane, zero findings
    # included, while the with-dispositions branch (W11) skipped them: the
    # same NO_FINDINGS lane read as undispositioned on one mission shape and
    # not on the other. Both branches now apply the same guard.
    _write_receipt(
        home,
        "20260101T000000Z-w11e-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="w11e",
    )
    _write_mission(
        home,
        "w11e",
        name="mission-w11e",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=0
            ),
            _review_lane(
                "review-grok", fleet="cursor", model="cursor-grok-4.6-medium", findings=2
            ),
            # No fix lane at all.
        ],
    )
    rpt = report(home)
    # No disposition and no counted lane ever touched the google vendor, so
    # it has no row at all; a row that did exist would have to read zero.
    google_rows = [r for r in rpt.reviewer_precision if r.vendor == "google"]
    assert all(r.undispositioned == 0 for r in google_rows)
    xai_row = next(r for r in rpt.reviewer_precision if r.vendor == "xai")
    assert xai_row.undispositioned == 1


def test_report_reviewer_precision_basis_and_columns_in_json_and_printed(
    home: Path, monkeypatch, capsys
):
    # W7: the basis sentence and the two new columns are visible in both
    # the JSON payload and the printed table, not just on the dataclass.
    _write_receipt(
        home,
        "20260101T000000Z-m8-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m8",
    )
    _write_mission(
        home,
        "m8",
        name="mission-eight",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    row = next(r for r in payload["reviewer_precision"] if r["vendor"] == "google")
    assert row["missions"] == 1
    assert row["undispositioned"] == 0
    assert row["basis"] == (
        "fixer agreement over 1 mission(s) with dispositions; 0 review lane(s) not dispositioned"
    )

    assert main(["report"]) == 0
    printed = capsys.readouterr().out
    assert "missions" in printed
    assert "undispositioned" in printed
    assert (
        "google: fixer agreement over 1 mission(s) with dispositions; 0 review lane(s) not "
        "dispositioned" in printed
    )


def test_report_reviewer_precision_heading_labels_fixer_agreement_not_truth(
    home: Path, monkeypatch, capsys
):
    # W7 item 2: the table itself must not read as a truth figure -- the
    # heading, not just the `basis` line under it, has to say this is
    # fixer agreement.
    _write_receipt(
        home,
        "20260101T000000Z-m9-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m9",
    )
    _write_mission(
        home,
        "m9",
        name="mission-nine",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=1
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    printed = capsys.readouterr().out
    heading = next(line for line in printed.splitlines() if line.startswith("Reviewer precision"))
    assert "fixer agreement" in heading
    # W7 item 2 again, from the other side: the heading must not offer the
    # figure as truth, correctness, or accuracy. Asserting only that "fixer
    # agreement" appears left "fixer agreement is reviewer accuracy" green
    # (2026-09-08 audit).
    lowered = heading.lower()
    assert not any(word in lowered for word in ("truth", "true", "correct", "accura", "valid"))
    assert "findings fixed vs. refused" in heading


def test_reviewer_precision_row_defaults_missions_and_undispositioned_to_zero():
    # W7: a row built the way every review-only vendor (no dispositions at
    # all) or old code path builds it -- never assigning `missions` or
    # `undispositioned` -- reads as 0 rather than failing.
    row = ReviewerPrecisionRow(vendor="google")
    assert row.missions == 0
    assert row.undispositioned == 0
    d = row.to_dict()
    assert d["missions"] == 0
    assert d["undispositioned"] == 0


def test_report_mission_rows_with_capped_lane(home: Path):
    _write_receipt(
        home,
        "20260101T000000Z-claude-build",
        fleet="claude",
        model="claude-sonnet-5",
        cost=1.5,
    )
    _write_receipt(
        home,
        "20260101T010000Z-claude-review",
        fleet="claude",
        model="claude-opus-5",
        cost=0.5,
        ok=False,
        kind="cap",
    )
    _write_mission(
        home,
        "20260101T020000Z-mission-x",
        name="mission-x",
        ok=True,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T000000Z-claude-build"}],
            },
            {
                "name": "review",
                "stage": "review",
                "attempts": [{"run_id": "20260101T010000Z-claude-review"}],
            },
        ],
    )
    rpt = report(home)
    # Both receipts here are joined (neither sets `stage`/`mission` directly),
    # so they resolve to the snapshot directory's own name, matching what a
    # staged lane's live receipt would carry directly.
    row = next(r for r in rpt.missions if r.mission == "20260101T020000Z-mission-x")
    assert row.cost_usd == 2
    assert row.ok is True
    assert row.lanes == 2
    assert row.capped is True


def test_report_mission_identity_matches_what_dispatch_stamps_on_the_receipt(home: Path):
    """A staged lane's live receipt carries `mission=mission_id` (the
    timestamped directory name -- mission.py's dispatch_one passes exactly
    that). A collate dispatch never gets a `mission=` kwarg at all, so its
    receipt is only ever recovered through the snapshot join. Both must
    resolve to the SAME mission group, keyed on the identity `report.py`
    actually has on a live receipt -- the directory id -- not the
    snapshot's separate, friendlier `name` field, or one mission silently
    splits into two rows and the id-keyed row never receives its own
    snapshot's `ok`/`lanes`.
    """
    mission_id = "20260101T020000Z-parser-refactor"
    _write_receipt(
        home,
        "20260101T000000Z-claude-build",
        fleet="claude",
        model="claude-sonnet-5",
        cost=1.0,
        stage="build",
        lane="build",
        mission=mission_id,
    )
    _write_receipt(
        home,
        "20260101T010000Z-claude-collate",
        fleet="claude",
        model="claude-haiku-4-5",
        cost=0.5,
    )
    _write_mission(
        home,
        mission_id,
        name="parser-refactor",
        ok=True,
        lanes=[
            {
                "name": "build",
                "stage": "build",
                "attempts": [{"run_id": "20260101T000000Z-claude-build"}],
            }
        ],
        collate={"run_id": "20260101T010000Z-claude-collate"},
    )
    rpt = report(home)
    assert len(rpt.missions) == 1
    row = rpt.missions[0]
    assert row.mission == mission_id
    assert row.cost_usd == 1.5
    assert row.ok is True
    assert row.lanes == 1


def test_report_rules_section_reports_n_a_when_no_data(home: Path):
    # D21: a Claude build killed at its cap before any gate ran. Its
    # receipt's `gate_passed` is True only because nothing ran, so it lands
    # in the `gate_not_run` cohort, never in `gate_passed` -- rule 10's
    # dollar is about a run that had already earned its verdict.
    _write_receipt(
        home,
        "20260101T000000Z-claude-buildcap",
        fleet="claude",
        model="claude-sonnet-5",
        ok=False,
        kind="cap",
        stage="build",
    )
    # A review-stage cap miss with no answer file: cap_misses is real,
    # finding_rate has no data to compute from.
    _write_receipt(
        home,
        "20260101T010000Z-claude-reviewcap",
        fleet="claude",
        model="claude-opus-5",
        ok=False,
        kind="cap",
        stage="review",
    )
    rpt = report(home)
    assert rpt.rules.cap_losses["build"] == {
        "gate_passed": 0,
        "gate_failed": 0,
        "gate_not_run": 1,
        "total": 1,
    }
    assert rpt.rules.cap_losses["fix"] == "n/a"
    review_entry = next(r for r in rpt.rules.review if r["vendor"] == "anthropic")
    assert review_entry["cap_misses"] == 1
    assert review_entry["finding_rate"] == "n/a"


def test_report_json_key_order(home: Path, monkeypatch, capsys):
    _write_receipt(
        home,
        "20260101T000000Z-claude-a",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload.keys()) == [
        "vendor_stage",
        "error_kinds",
        "reviewer_finding_rate",
        "reviewer_precision",
        "dispositions_unknown_lane",
        "dispositions_malformed",
        "dispositions_duplicate",
        "dispositions_unmatched",
        "missions",
        "landed",
        "rules",
        "skipped",
        "wall_clock",
    ]
    assert list(payload["rules"].keys()) == ["review", "cap_losses"]
    assert list(payload["landed"].keys()) == [
        "missions",
        "cost_usd",
        "with_items",
        "items",
        "usd_per_item",
    ]
    assert payload["landed"] == {
        "missions": 0,
        "cost_usd": "0.00",
        "with_items": 0,
        "items": 0,
        "usd_per_item": None,
    }
    row = payload["vendor_stage"][0]
    assert list(row.keys()) == [
        "vendor",
        "stage",
        "runs",
        "ok",
        "cost_usd",
        "unpriced_runs",
        "mean_duration_s",
        "median_duration_s",
        "unknown_durations",
        "cache_pct",
        "cap_misses",
        "gate_failures",
        "mean_tool_calls",
    ]
    assert row["cost_usd"] == "1.00"


def test_report_rejects_a_bad_since_with_exit_two(home: Path, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report", "--since", "not-a-date"]) == 2
    assert "invalid --since" in capsys.readouterr().err


def test_result_from_dict_defaults_new_fields_and_to_dict_writes_them():
    raw = {
        "run_id": "20260101T000000Z-claude-x",
        "fleet": "claude",
        "model": "claude-sonnet-5",
        "effort": "standard",
        "mode": "write",
        "cwd": "/tmp/x",
        "timeout": 60,
        "exit_code": 0,
        "timed_out": False,
        "duration_s": 1.0,
        "run_dir": "/tmp/x/run",
        "stdout_path": "/tmp/x/run/stdout.log",
        "stderr_path": "/tmp/x/run/stderr.log",
        "tail": "",
    }
    result = Result.from_dict(raw)
    assert result.stage is None
    assert result.lane is None
    assert result.mission is None
    written = result.to_dict()
    assert written["stage"] is None
    assert written["lane"] is None
    assert written["mission"] is None


def test_report_wall_clock_table_is_bounded_by_since_like_every_other_table(home: Path):
    from datetime import UTC, datetime

    _write_mission(home, "m-old", name="mission-old", ok=True, lanes=[])
    _write_mission(home, "m-new", name="mission-new", ok=True, lanes=[])
    _write_receipt(
        home,
        "20260201T000000Z-claude-recent",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m-new",
    )
    rpt = report(home, since=datetime(2026, 1, 15, tzinfo=UTC))
    assert {row.mission for row in rpt.wall_clock} == {"m-new"}
    unbounded = report(home)
    assert {row.mission for row in unbounded.wall_clock} == {"m-new", "m-old"}


def test_report_keeps_every_fix_lanes_dispositions_on_a_multi_fix_mission(home: Path):
    """F15 latent item: `fix_dispositions` was a per-mission single slot
    overwritten per fix lane, so a second `stage: fix` lane's dispositions
    never reached the precision table."""
    _write_receipt(
        home,
        "20260101T000000Z-m7-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="m7",
    )
    second_fix = _fix_lane(
        [{"lane": "review-gemini", "index": 2, "disposition": "refused", "reason": "r2"}]
    ) | {"name": "fix-2"}
    _write_mission(
        home,
        "m7",
        name="mission-seven",
        ok=True,
        lanes=[
            _review_lane(
                "review-gemini", fleet="antigravity", model="gemini-3.7-flash", findings=2
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"}]
            ),
            second_fix,
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.fixed == 1
    assert row.refused == 1


# --- D20: one count per finding ------------------------------------------


def _review_lane_with_items(name: str, *, fleet: str, model: str, items: list[dict]) -> dict:
    return {
        "name": name,
        "stage": "review",
        "review": {"verdict": "findings", "findings": len(items), "items": items},
        "attempts": [{"fleet": fleet, "model": model}],
    }


def test_report_counts_a_repeated_disposition_once(home: Path):
    # D20: one finding plus three copies of its fixed disposition read as
    # fixed 3, precision 1.0, corrected rate 3.0 -- a quality metric above
    # its own denominator.
    _write_receipt(
        home,
        "20260101T000000Z-d20a-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="d20a",
    )
    _write_mission(
        home,
        "d20a",
        name="mission-d20a",
        ok=True,
        lanes=[
            _review_lane_with_items(
                "review-gemini",
                fleet="antigravity",
                model="gemini-3.7-flash",
                items=[{"index": 1, "file": "a.py", "line": 1, "confidence": 8}],
            ),
            _fix_lane(
                [
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                ]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.fixed == 1
    assert row.findings == 1
    assert row.corrected_rate() == 1.0
    assert row.fixed_confidences == [8]
    assert rpt.dispositions_duplicate == 2
    assert rpt.dispositions_unmatched == 0


def test_report_keeps_the_last_disposition_for_one_finding(home: Path):
    # A fix lane that restates its own disposition settles on the last one.
    _write_receipt(
        home,
        "20260101T000000Z-d20b-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="d20b",
    )
    _write_mission(
        home,
        "d20b",
        name="mission-d20b",
        ok=True,
        lanes=[
            _review_lane_with_items(
                "review-gemini",
                fleet="antigravity",
                model="gemini-3.7-flash",
                items=[{"index": 1, "file": "a.py", "line": 1, "confidence": 8}],
            ),
            _fix_lane(
                [
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-gemini", "index": 1, "disposition": "refused", "reason": "r2"},
                ]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert (row.fixed, row.refused) == (0, 1)
    assert rpt.dispositions_duplicate == 1


def test_report_counts_a_disposition_naming_no_reported_finding_as_unmatched(home: Path):
    # D20: `index: 99` against a review lane that reported one finding is
    # not a disposition against a finding.
    _write_receipt(
        home,
        "20260101T000000Z-d20c-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="d20c",
    )
    _write_mission(
        home,
        "d20c",
        name="mission-d20c",
        ok=True,
        lanes=[
            _review_lane_with_items(
                "review-gemini",
                fleet="antigravity",
                model="gemini-3.7-flash",
                items=[{"index": 1, "file": "a.py", "line": 1, "confidence": 8}],
            ),
            _fix_lane(
                [{"lane": "review-gemini", "index": 99, "disposition": "fixed", "reason": "r1"}]
            ),
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.reviewer_precision if r.vendor == "google")
    assert row.fixed == 0
    assert row.corrected_rate() == 0.0
    assert rpt.dispositions_unmatched == 1
    assert rpt.dispositions_duplicate == 0


def test_report_prints_the_duplicate_and_unmatched_counts(home: Path, monkeypatch, capsys):
    _write_receipt(
        home,
        "20260101T000000Z-d20d-build",
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        mission="d20d",
    )
    _write_mission(
        home,
        "d20d",
        name="mission-d20d",
        ok=True,
        lanes=[
            _review_lane_with_items(
                "review-gemini",
                fleet="antigravity",
                model="gemini-3.7-flash",
                items=[{"index": 1, "file": "a.py", "line": 1, "confidence": 8}],
            ),
            _fix_lane(
                [
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "r1"},
                    {"lane": "review-gemini", "index": 99, "disposition": "fixed", "reason": "r2"},
                ]
            ),
        ],
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    printed = capsys.readouterr().out
    assert "duplicate dispositions: 1" in printed
    assert "dispositions naming no reported finding: 1" in printed


def test_report_cap_loss_counts_a_gate_that_ran_and_passed(home: Path):
    # D21: the case rule 10's dollar is actually about -- the gate ran, it
    # passed, and the run was then lost at its cap. Beside it, a capped run
    # whose gate ran and failed, so the two never share a cohort.
    _write_receipt(
        home,
        "20260101T000000Z-claude-gated-cap",
        fleet="claude",
        model="claude-sonnet-5",
        ok=False,
        kind="cap",
        stage="build",
        tests={"ran": True, "exit_code": 0, "timed_out": False, "interrupted": False},
    )
    _write_receipt(
        home,
        "20260101T010000Z-claude-failed-cap",
        fleet="claude",
        model="claude-sonnet-5",
        ok=False,
        kind="cap",
        stage="build",
        tests={"ran": True, "exit_code": 1, "timed_out": False, "interrupted": False},
    )
    rpt = report(home)
    assert rpt.rules.cap_losses["build"] == {
        "gate_passed": 1,
        "gate_failed": 1,
        "gate_not_run": 0,
        "total": 2,
    }


def test_report_prints_the_three_cap_loss_cohorts(home: Path, monkeypatch, capsys):
    _write_receipt(
        home,
        "20260101T000000Z-claude-uncapped-gate",
        fleet="claude",
        model="claude-sonnet-5",
        ok=False,
        kind="cap",
        stage="build",
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    printed = capsys.readouterr().out
    assert "rule 10: Claude runs capped, by what their own gate did" in printed
    assert "gate not run" in printed


def test_wall_clock_row_carries_occupied_critical_path_lead_and_stretch(home: Path):
    """W8: the three new figures come straight off the `wall` block, and
    `stretch` (`wall_s / critical_path_s`) is computed here the way `busy`
    is -- how much longer the mission took than its own longest chain."""
    _write_mission(
        home,
        "m-w8",
        name="mission-w8",
        ok=True,
        lanes=[],
        wall={
            "launched_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T01:00:00+00:00",
            "wall_s": 3600.0,
            "paused_s": 600.0,
            "gate_s": 60.0,
            "lanes_s": 400.0,
            "idle_s": 5.0,
            "concurrency": 2,
            "occupied_s": 1200.0,
            "critical_path_s": 900.0,
            "lead_s": 1800.0,
        },
    )
    row = {r.mission: r for r in report(home).wall_clock}["m-w8"].to_dict()
    assert row["occupied_s"] == 1200.0
    assert row["critical_path_s"] == 900.0
    assert row["lead_s"] == 1800.0
    assert row["stretch"] == 4.0
    # `busy` keeps exactly its old definition, off the lane-work sums.
    assert row["busy"] == round(400.0 / (3600.0 * 2), 3)


def test_wall_clock_row_stretch_is_blank_without_a_critical_path():
    assert WallClockRow(mission="m", wall_s=12.0).stretch() is None
    assert WallClockRow(mission="m", wall_s=12.0, critical_path_s=0.0).stretch() is None
    assert WallClockRow(mission="m", critical_path_s=4.0).stretch() is None


def test_report_prints_the_new_wall_clock_columns(home: Path, capsys, monkeypatch):
    _write_mission(
        home,
        "m-w8",
        name="mission-w8",
        ok=True,
        lanes=[],
        wall={
            "wall_s": 3600.0,
            "paused_s": 600.0,
            "gate_s": 60.0,
            "lanes_s": 400.0,
            "idle_s": 5.0,
            "concurrency": 2,
            "occupied_s": 1200.0,
            "critical_path_s": 900.0,
            "lead_s": 1800.0,
        },
    )
    # And a receipt from before W8: its three columns print blank, not 0.
    _write_mission(home, "m-old", name="mission-old", ok=True, lanes=[])
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if "critical_path_s" in line)
    for column in ("occupied_s", "critical_path_s", "lead_s", "stretch"):
        assert column in header
    row = next(line for line in out.splitlines() if line.strip().startswith("m-w8"))
    assert "1200.0" in row and "900.0" in row and "1800.0" in row and "4.0" in row
    old_row = next(line for line in out.splitlines() if line.strip().startswith("m-old"))
    assert old_row.split().count("n/a") >= 4


def test_report_json_emits_the_new_wall_clock_figures(home: Path, capsys, monkeypatch):
    _write_mission(
        home,
        "m-w8",
        name="mission-w8",
        ok=True,
        lanes=[],
        wall={
            "wall_s": 3600.0,
            "paused_s": 600.0,
            "gate_s": 60.0,
            "lanes_s": 400.0,
            "idle_s": 5.0,
            "concurrency": 2,
            "occupied_s": 1200.0,
            "critical_path_s": 900.0,
            "lead_s": 1800.0,
        },
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    row = payload["wall_clock"][0]
    assert row["occupied_s"] == 1200.0
    assert row["critical_path_s"] == 900.0
    assert row["lead_s"] == 1800.0
    assert row["stretch"] == 4.0


# --- review item 2: cost per landed item ------------------------------------


def _write_land_receipt(home: Path, mission_id: str, stamp: str, **fields: object) -> None:
    land_dir = home / "missions" / mission_id / "land"
    land_dir.mkdir(parents=True, exist_ok=True)
    (land_dir / f"build-{stamp}.json").write_text(json.dumps({"lane": "build", **fields}))


def _write_evidence(home: Path, run_id: str, items: object) -> str:
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    copy = run_dir / "deliverable"
    copy.write_text(json.dumps({"items": items}))
    return str(copy)


def _build_lane(run_id: str, *, copy: str | None, deliverable_ok: bool = True) -> dict:
    attempt: dict[str, object] = {"run_id": run_id}
    if copy is not None:
        attempt["deliverable"] = {"path": "evidence.json", "ok": deliverable_ok, "parsed": True}
        attempt["deliverable_path"] = copy
    return {"name": "build", "stage": "build", "attempts": [attempt]}


def _landed_mission(
    home: Path, mission_id: str, *, cost: float, items: object, land: list[dict]
) -> None:
    run_id = f"{mission_id}-build"
    _write_receipt(
        home,
        run_id,
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        lane="build",
        mission=mission_id,
        cost=cost,
    )
    copy = _write_evidence(home, run_id, items) if items is not None else None
    lanes = [_build_lane(run_id, copy=copy)]
    _write_mission(home, mission_id, name=mission_id, ok=True, lanes=lanes)
    for index, fields in enumerate(land):
        _write_land_receipt(home, mission_id, f"2026010{index}T000000000000Z", **fields)


MERGED = {"ok": True, "dry_run": False, "already_merged": False}


def test_report_landed_counts_only_merges_that_happened(home: Path):
    # The F18 mission on record carries three land receipts for one merge:
    # a refusal (`refused`, no other keys), a dry run, and the merge.
    _landed_mission(
        home,
        "20260101T000000Z-m-three-receipts",
        cost=4.0,
        items=[1, 2, 3, 4],
        land=[
            {"refused": "lane 'fix' has no branch on its receipt"},
            {"ok": True, "dry_run": True, "already_merged": False},
            MERGED,
            {"ok": False, "dry_run": False, "already_merged": True},
        ],
    )
    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == "20260101T000000Z-m-three-receipts")
    assert row.landed == 4
    assert row.landed_ok == 1
    assert row.items == 4
    assert row.to_dict()["usd_per_item"] == "1.00"


def test_report_landed_aggregate_divides_only_missions_with_an_evidence_map(home: Path):
    _landed_mission(home, "20260101T000000Z-m-a", cost=6.0, items=[1, 2, 3], land=[MERGED])
    # Merged, but its build lane predates the evidence map: counted in the
    # landed cost, left out of the per-item division.
    _landed_mission(home, "20260101T000000Z-m-b", cost=10.0, items=None, land=[MERGED])
    # An evidence map on a mission that never merged is not a landed item.
    _landed_mission(home, "20260101T000000Z-m-c", cost=8.0, items=[1, 2], land=[])
    # A map with no items is unknown, not a free landing.
    _landed_mission(home, "20260101T000000Z-m-d", cost=2.0, items=[], land=[MERGED])
    rpt = report(home)
    by_name = {r.mission: r for r in rpt.missions}
    assert by_name["20260101T000000Z-m-a"].to_dict()["usd_per_item"] == "2.00"
    assert by_name["20260101T000000Z-m-b"].items is None
    assert by_name["20260101T000000Z-m-b"].usd_per_item() is None
    assert by_name["20260101T000000Z-m-c"].landed_ok == 0
    assert by_name["20260101T000000Z-m-c"].usd_per_item() is None
    assert by_name["20260101T000000Z-m-d"].items == 0
    assert by_name["20260101T000000Z-m-d"].usd_per_item() is None
    assert rpt.landed.to_dict() == {
        "missions": 3,
        "cost_usd": "18.00",
        "with_items": 1,
        "items": 3,
        "usd_per_item": "2.00",
    }


def test_report_evidence_items_need_a_parsed_ok_copy(home: Path):
    run_id = "20260101T000000Z-m-e-build"
    _write_receipt(
        home,
        run_id,
        fleet="claude",
        model="claude-sonnet-5",
        stage="build",
        lane="build",
        mission="20260101T000000Z-m-e",
        cost=3.0,
    )
    copy = _write_evidence(home, run_id, [1, 2, 3])
    _write_mission(
        home,
        "20260101T000000Z-m-e",
        name="m-e",
        ok=True,
        lanes=[_build_lane(run_id, copy=copy, deliverable_ok=False)],
    )
    _write_land_receipt(home, "20260101T000000Z-m-e", "20260101T000000000000Z", **MERGED)
    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == "20260101T000000Z-m-e")
    assert row.items is None
    assert rpt.landed.to_dict()["with_items"] == 0
    # An unreadable copy is unknown too, never a crash.
    Path(copy).write_text("{not json")
    _write_mission_lanes = home / "missions" / "20260101T000000Z-m-e" / "result.json"
    payload = json.loads(_write_mission_lanes.read_text())
    payload["lanes"][0]["attempts"][0]["deliverable"]["ok"] = True
    _write_mission_lanes.write_text(json.dumps(payload))
    again = next(r for r in report(home).missions if r.mission == "20260101T000000Z-m-e")
    assert again.items is None


def test_report_prints_landed_columns_and_summary_line(home: Path, monkeypatch, capsys):
    _landed_mission(home, "20260101T000000Z-m-p", cost=5.0, items=[1, 2], land=[MERGED])
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if line.strip().startswith("mission "))
    assert header.split() == [
        "mission",
        "cost_usd",
        "ok",
        "lanes",
        "capped",
        "salvaged",
        "landed",
        "merged",
        "items",
        "unpriced",
        "usd_per_item",
    ]
    row = next(line for line in out.splitlines() if "20260101T000000Z-m-p" in line)
    assert row.split()[-4:] == ["1", "2", "0", "2.50"]
    assert (
        "Landed: 1 mission(s), $5.00; 1 with an evidence map naming 2 item(s): "
        "$2.50 per landed item" in out
    )


def test_report_prints_no_merge_when_nothing_landed(home: Path, monkeypatch, capsys):
    _write_receipt(
        home, "20260101T000000Z-claude-a", fleet="claude", model="claude-sonnet-5", stage="build"
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["report"]) == 0
    assert "Landed: no mission in this window merged" in capsys.readouterr().out


def test_readme_documents_cost_per_landed_item():
    readme = Path(__file__).parents[1] / "README.md"
    raw_section = readme.read_text().split("#### Ledger report", 1)[1].split("\n### ", 1)[0]
    section = " ".join(raw_section.split())
    assert "`merged`" in section and "`items`" in section and "`usd_per_item`" in section
    assert "evidence map" in section
    assert "per landed item" in section
    assert "AGENTS.md rule 2" in section
