"""The ledger report (E11): AGENTS.md's Shape A rules as numbers, computed
from the same durable receipts `conductor spend` reads, joined to a mission
snapshot when an older receipt carries no stage of its own."""

from __future__ import annotations

import json
from pathlib import Path

from conductor.cli import main
from conductor.report import WallClockRow, report
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
    empty.write_text("NO_FINDINGS\n")
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


def test_report_disposition_naming_an_unknown_lane_is_counted_not_dropped(home: Path):
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
    # older mission, or one still awaiting its fix) contributes nothing --
    # "findings written" without a paired fix is not a precision figure.
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
    assert not any(r.vendor == "google" for r in rpt.reviewer_precision)


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
    # A green Claude build lost at its cap (rule 10): no gate ran, so
    # runner._gate_passed reads that as passed, the case the rule names.
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
    assert rpt.rules.cap_losses["build"] == 1
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
        "missions",
        "rules",
        "skipped",
        "wall_clock",
    ]
    assert list(payload["rules"].keys()) == ["review", "cap_losses"]
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
