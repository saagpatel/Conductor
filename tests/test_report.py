"""The ledger report (E11): AGENTS.md's Shape A rules as numbers, computed
from the same durable receipts `conductor spend` reads, joined to a mission
snapshot when an older receipt carries no stage of its own."""

from __future__ import annotations

import json
from pathlib import Path

from conductor.cli import main
from conductor.report import report
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
        "usage": {"cost_usd": cost, "cost_basis": basis, "total_tokens": tokens},
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


def _write_mission(home: Path, mission_id: str, *, name: str, ok: bool, lanes: list[dict]) -> None:
    mission_dir = home / "missions" / mission_id
    mission_dir.mkdir(parents=True)
    (mission_dir / "result.json").write_text(json.dumps({"name": name, "ok": ok, "lanes": lanes}))


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
    joined_run = next(r for r in rpt.missions if r.mission == "m-old")
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
    empty = home / "runs" / "empty-answer.txt"
    empty.parent.mkdir(parents=True)
    empty.write_text("NO_FINDINGS\n")
    found = home / "runs" / "found-answer.txt"
    found.write_text("file.py:12: off-by-one\n")

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
    assert row.rate() == 0.5


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
    row = next(r for r in rpt.missions if r.mission == "mission-x")
    assert row.cost_usd == 2
    assert row.ok is True
    assert row.lanes == 2
    assert row.capped is True


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
        "missions",
        "rules",
        "skipped",
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
