"""E11: the ledger report -- what Shape A's ten rules (AGENTS.md) look like
as numbers, computed from the same durable receipts `conductor spend` reads.

Every run receipt is read once (`spend._read_run` supplies the accounting
fields already validated there; this module only adds the fields a rule
needs: which pipeline stage and mission lane made the dispatch, its
duration, its error kind, and whether its gate passed). A receipt written
before E11 carries no `stage`, `lane`, or `mission` field at all; for those,
`_scan_missions` joins the run back to its mission snapshot the same way
`spend._mission_map` does, but keeps the lane name and declared stage too,
not just the mission name.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import fleets
from .paths import conductor_home
from .runner import _gate_passed as _runner_gate_passed
from .spend import Run as _SpendRun
from .spend import _collate_run_ids, _parse_bound
from .spend import _read_run as _spend_read_run

REVIEW_STAGE = "review"
NO_FINDINGS = "NO_FINDINGS"


def _money(value: Decimal) -> str:
    """Two-decimal string, per the task's rendering rule for every dollar
    figure this report prints or emits as JSON."""
    return f"{value:.2f}"


def _cell(value: object) -> str:
    return "n/a" if value is None else str(value)


def _vendor(fleet: str, model: str) -> str:
    """The vendor behind a receipt's resolved model id.

    A receipt stores the concrete id the CLI was given (`claude-opus-5`,
    `gemini-3.8-flash-high`, ...), not the abstract model name `fleets.Model`
    is keyed by, so lookup matches against every effort-resolved id a
    registered model can take. An unregistered fleet, or a model id that
    matches none of them (an older receipt, a since-removed model), reports
    the fleet name itself -- never raises, since a malformed old receipt
    must not stop the whole report.
    """
    registered = fleets.FLEETS.get(fleet)
    if registered is None:
        return fleet
    for candidate in registered.models:
        if model == candidate.name or model in candidate.resolve.values():
            return candidate.vendor
    return fleet


@dataclass(frozen=True)
class Run(_SpendRun):
    """`spend.Run`'s accounting fields, plus what a rule needs: stage, lane,
    mission (from the receipt, or joined from a mission snapshot when the
    receipt predates them), duration, error kind, mode, whether an answer
    was written, and whether the gate passed."""

    stage: str | None = None
    lane: str | None = None
    mission: str | None = None
    duration_s: float = 0.0
    kind: str | None = None
    mode: str | None = None
    answer_path: str | None = None
    gate_passed: bool = True


def _str_field(raw: dict, key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _scan_missions(
    home: Path,
) -> tuple[dict[str, tuple[str, str | None, str | None]], dict[str, dict[str, object]]]:
    """One pass over every mission snapshot: `run_id -> (mission, lane, stage)`
    for every attempt and collate dispatch it named, and `mission -> {"ok",
    "lanes"}` read from the snapshot's own top-level `ok` and lane count.

    A lane attempt joins to its declared name and stage; a collate or
    `previous_collates` run (`spend._collate_run_ids`) joins to the mission
    alone, since neither is a lane and carries no stage of its own.

    `mission` here is the snapshot directory's own name (`result_file.parent.
    name`) -- the same `mission_id` `mission.py`'s dispatch_one stamps
    directly onto a staged lane's live receipt (`dispatch(spec, lane=...,
    mission=mission_id, ...)`), never the snapshot's separate, friendlier
    `name` field. A staged lane's receipt already carries `stage`, so it is
    never joined at all; keying this map by anything other than the id that
    receipt already carries would only ever match an unrelated, joined
    (unstaged) run and never the staged one, splitting one mission's runs
    across two group keys and leaving the id-keyed row's `ok`/`lanes` unset.
    """
    join: dict[str, tuple[str, str | None, str | None]] = {}
    meta: dict[str, dict[str, object]] = {}
    missions_dir = home / "missions"
    if not missions_dir.is_dir():
        return join, meta
    for result_file in sorted(missions_dir.glob("*/result.json")):
        try:
            raw: object = json.loads(result_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        mission = result_file.parent.name
        lanes = raw.get("lanes")
        lane_list = lanes if isinstance(lanes, list) else []
        ok = raw.get("ok")
        salvage_dir = result_file.parent / "salvage"
        salvaged = len(list(salvage_dir.glob("*.json"))) if salvage_dir.is_dir() else 0
        meta[mission] = {
            "ok": ok if isinstance(ok, bool) else None,
            "lanes": len(lane_list),
            "salvaged": salvaged,
        }
        for lane_raw in lane_list:
            if not isinstance(lane_raw, dict):
                continue
            lane_name = _str_field(lane_raw, "name")
            stage = _str_field(lane_raw, "stage")
            for key in ("previous_attempts", "attempts"):
                attempts = lane_raw.get(key)
                if not isinstance(attempts, list):
                    continue
                for attempt in attempts:
                    if isinstance(attempt, dict) and isinstance(attempt.get("run_id"), str):
                        join.setdefault(attempt["run_id"], (mission, lane_name, stage))
        collates: list[object] = []
        collate = raw.get("collate")
        if isinstance(collate, dict):
            collates.append(collate)
        previous_collates = raw.get("previous_collates")
        if isinstance(previous_collates, list):
            collates.extend(previous_collates)
        for one_collate in collates:
            if isinstance(one_collate, dict):
                for run_id in _collate_run_ids(one_collate):
                    join.setdefault(run_id, (mission, None, None))
    return join, meta


def _read_run(path: Path, join: dict[str, tuple[str, str | None, str | None]]) -> Run | None:
    base = _spend_read_run(path)
    if base is None:
        return None
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    stage = _str_field(raw, "stage")
    lane = _str_field(raw, "lane")
    mission = _str_field(raw, "mission")
    if stage is None:
        joined = join.get(base.run_id)
        if joined is not None:
            mission, lane, stage = joined

    duration = raw.get("duration_s")
    duration_s = (
        float(duration)
        if isinstance(duration, int | float) and not isinstance(duration, bool)
        else 0.0
    )
    gate_passed = _runner_gate_passed(raw.get("tests"), raw.get("test_surface"))

    return Run(
        run_id=base.run_id,
        created=base.created,
        fleet=base.fleet,
        model=base.model,
        ok=base.ok,
        cost_usd=base.cost_usd,
        estimated=base.estimated,
        tokens=base.tokens,
        cache_read_tokens=base.cache_read_tokens,
        cache_write_tokens=base.cache_write_tokens,
        tool_calls=base.tool_calls,
        dry_run=base.dry_run,
        stage=stage,
        lane=lane,
        mission=mission,
        duration_s=duration_s,
        kind=_str_field(raw, "kind"),
        mode=_str_field(raw, "mode"),
        answer_path=_str_field(raw, "answer_path"),
        gate_passed=gate_passed,
    )


@dataclass
class VendorStageRow:
    vendor: str
    stage: str | None
    runs: int = 0
    ok: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    unpriced_runs: int = 0
    cap_misses: int = 0
    gate_failures: int = 0
    _durations: list[float] = field(default_factory=list)
    _tool_calls: list[int] = field(default_factory=list)

    def add(self, run: Run) -> None:
        self.runs += 1
        self.ok += int(run.ok)
        if run.cost_usd is None:
            self.unpriced_runs += 1
        else:
            self.cost_usd += run.cost_usd
        if run.kind == "cap":
            self.cap_misses += 1
        if run.kind == "gate":
            self.gate_failures += 1
        self._durations.append(run.duration_s)
        self._tool_calls.append(run.tool_calls)

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "stage": self.stage,
            "runs": self.runs,
            "ok": self.ok,
            "cost_usd": _money(self.cost_usd),
            "unpriced_runs": self.unpriced_runs,
            "mean_duration_s": (
                round(statistics.mean(self._durations), 1) if self._durations else None
            ),
            "median_duration_s": (
                round(statistics.median(self._durations), 1) if self._durations else None
            ),
            "cap_misses": self.cap_misses,
            "gate_failures": self.gate_failures,
            "mean_tool_calls": (
                round(statistics.mean(self._tool_calls), 1) if self._tool_calls else None
            ),
        }


@dataclass
class ErrorKindRow:
    kind: str
    count: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "count": self.count, "cost_usd": _money(self.cost_usd)}


@dataclass
class ReviewerFindingRow:
    vendor: str
    runs: int = 0
    findings: int = 0

    def rate(self) -> float | None:
        return round(self.findings / self.runs, 3) if self.runs else None

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "runs": self.runs,
            "findings": self.findings,
            "rate": self.rate(),
        }


@dataclass
class MissionRow:
    mission: str
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    ok: bool | None = None
    lanes: int = 0
    capped: bool = False
    # E23: salvage receipts under this mission's `salvage/` directory, 0 when
    # it is absent -- `conductor salvage` never dispatches, so this is the
    # only place a salvage shows up in the report at all.
    salvaged: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "mission": self.mission,
            "cost_usd": _money(self.cost_usd),
            "ok": self.ok,
            "lanes": self.lanes,
            "capped": self.capped,
            "salvaged": self.salvaged,
        }


@dataclass
class Rules:
    """The figures behind AGENTS.md rules 7 (reviewer cap misses and finding
    rate, per vendor) and 10 (a green Claude build or fix run lost at its
    cap), so the lead can compare the prose against the receipts directly."""

    review: list[dict[str, object]]
    cap_losses: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {"review": self.review, "cap_losses": self.cap_losses}


@dataclass
class Report:
    vendor_stage: list[VendorStageRow]
    error_kinds: list[ErrorKindRow]
    reviewer_finding_rate: list[ReviewerFindingRow]
    missions: list[MissionRow]
    rules: Rules
    skipped: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor_stage": [row.to_dict() for row in self.vendor_stage],
            "error_kinds": [row.to_dict() for row in self.error_kinds],
            "reviewer_finding_rate": [row.to_dict() for row in self.reviewer_finding_rate],
            "missions": [row.to_dict() for row in self.missions],
            "rules": self.rules.to_dict(),
            "skipped": self.skipped,
        }


def _is_no_findings(answer_path: str) -> bool | None:
    try:
        text = Path(answer_path).read_text()
    except OSError:
        return None
    return text.strip() == NO_FINDINGS


def _build_report(
    rows: list[Run], skipped: int, mission_meta: dict[str, dict[str, object]]
) -> Report:
    vendor_stage: dict[tuple[str, str | None], VendorStageRow] = {}
    error_kinds: dict[str, ErrorKindRow] = {}
    reviewer: dict[str, ReviewerFindingRow] = {}
    missions: dict[str, MissionRow] = {}

    for run in rows:
        vendor = _vendor(run.fleet, run.model)
        key = (vendor, run.stage)
        vendor_stage.setdefault(key, VendorStageRow(vendor=vendor, stage=run.stage)).add(run)

        if run.kind is not None:
            kind_row = error_kinds.setdefault(run.kind, ErrorKindRow(kind=run.kind))
            kind_row.count += 1
            if run.cost_usd is not None:
                kind_row.cost_usd += run.cost_usd

        if run.stage == REVIEW_STAGE and run.answer_path:
            found = _is_no_findings(run.answer_path)
            if found is not None:
                row = reviewer.setdefault(vendor, ReviewerFindingRow(vendor=vendor))
                row.runs += 1
                row.findings += int(not found)

        if run.mission is not None:
            mission_row = missions.setdefault(run.mission, MissionRow(mission=run.mission))
            if run.cost_usd is not None:
                mission_row.cost_usd += run.cost_usd
            if run.kind == "cap":
                mission_row.capped = True

    for name, mission_row in missions.items():
        meta = mission_meta.get(name, {})
        mission_row.ok = meta.get("ok") if isinstance(meta.get("ok"), bool) else None
        mission_row.lanes = meta.get("lanes", 0) if isinstance(meta.get("lanes"), int) else 0
        mission_row.salvaged = (
            meta.get("salvaged", 0) if isinstance(meta.get("salvaged"), int) else 0
        )

    vendor_stage_rows = sorted(
        vendor_stage.values(), key=lambda r: (-r.cost_usd, r.vendor, r.stage or "")
    )
    error_kind_rows = sorted(error_kinds.values(), key=lambda r: (-r.count, r.kind))
    reviewer_rows = sorted(reviewer.values(), key=lambda r: r.vendor)
    mission_rows = sorted(missions.values(), key=lambda r: (-r.cost_usd, r.mission))

    review_vendors = sorted({r.vendor for r in vendor_stage_rows if r.stage == REVIEW_STAGE})
    review_rules: list[dict[str, object]] = []
    for vendor in review_vendors:
        cap_misses = next(
            r.cap_misses
            for r in vendor_stage_rows
            if r.vendor == vendor and r.stage == REVIEW_STAGE
        )
        finding_row = reviewer.get(vendor)
        rate = finding_row.rate() if finding_row is not None else None
        review_rules.append(
            {
                "vendor": vendor,
                "cap_misses": cap_misses,
                "finding_rate": rate if rate is not None else "n/a",
            }
        )

    cap_losses: dict[str, object] = {}
    for stage in ("build", "fix"):
        matches = [r for r in rows if _vendor(r.fleet, r.model) == "anthropic" and r.stage == stage]
        if not matches:
            cap_losses[stage] = "n/a"
        else:
            cap_losses[stage] = sum(1 for r in matches if r.kind == "cap" and r.gate_passed)

    return Report(
        vendor_stage=vendor_stage_rows,
        error_kinds=error_kind_rows,
        reviewer_finding_rate=reviewer_rows,
        missions=mission_rows,
        rules=Rules(review=review_rules, cap_losses=cap_losses),
        skipped=skipped,
    )


def report(home: Path, *, since: datetime | None = None, until: datetime | None = None) -> Report:
    """Read every dispatch receipt once and compute the ledger report."""
    join, mission_meta = _scan_missions(home)
    runs_dir = home / "runs"
    result_files = sorted(runs_dir.glob("*/result.json")) if runs_dir.is_dir() else []
    rows: list[Run] = []
    skipped = 0
    for result_file in result_files:
        run = _read_run(result_file, join)
        if run is None:
            skipped += 1
            continue
        if since is not None and run.created < since:
            continue
        if until is not None and run.created >= until:
            continue
        if run.dry_run:
            # A dry run spent nothing; see spend.summarize's own comment.
            continue
        rows.append(run)
    return _build_report(rows, skipped, mission_meta)


def _print_section(title: str, headings: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    print(title)
    if not rows:
        print("  (no data)")
        print()
        return
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        for index in range(len(headings))
    ]
    print("  " + "  ".join(headings[index].ljust(widths[index]) for index in range(len(headings))))
    for row in rows:
        print("  " + "  ".join(row[index].ljust(widths[index]) for index in range(len(row))))
    print()


def _print_report(rpt: Report) -> None:
    _print_section(
        "Vendor and stage",
        (
            "vendor",
            "stage",
            "runs",
            "ok",
            "cost_usd",
            "unpriced",
            "mean_s",
            "median_s",
            "cap_misses",
            "gate_failures",
            "mean_tools",
        ),
        [
            (
                d["vendor"],
                d["stage"] or "(none)",
                _cell(d["runs"]),
                _cell(d["ok"]),
                d["cost_usd"],
                _cell(d["unpriced_runs"]),
                _cell(d["mean_duration_s"]),
                _cell(d["median_duration_s"]),
                _cell(d["cap_misses"]),
                _cell(d["gate_failures"]),
                _cell(d["mean_tool_calls"]),
            )
            for d in (row.to_dict() for row in rpt.vendor_stage)
        ],
    )
    _print_section(
        "Error kinds",
        ("kind", "count", "cost_usd"),
        [
            (d["kind"], _cell(d["count"]), d["cost_usd"])
            for d in (row.to_dict() for row in rpt.error_kinds)
        ],
    )
    _print_section(
        "Reviewer finding rate (stage=review, answer written, not NO_FINDINGS)",
        ("vendor", "runs", "findings", "rate"),
        [
            (d["vendor"], _cell(d["runs"]), _cell(d["findings"]), _cell(d["rate"]))
            for d in (row.to_dict() for row in rpt.reviewer_finding_rate)
        ],
    )
    _print_section(
        "Missions",
        ("mission", "cost_usd", "ok", "lanes", "capped", "salvaged"),
        [
            (
                d["mission"],
                d["cost_usd"],
                _cell(d["ok"]),
                _cell(d["lanes"]),
                _cell(d["capped"]),
                _cell(d["salvaged"]),
            )
            for d in (row.to_dict() for row in rpt.missions)
        ],
    )
    print("Rules: AGENTS.md 7 (review cap misses and finding rate) and 10 (a green Claude")
    print("build or fix run lost at its cap)")
    _print_section(
        "  rule 7: review vendors",
        ("vendor", "cap_misses", "finding_rate"),
        [
            (item["vendor"], _cell(item["cap_misses"]), _cell(item["finding_rate"]))
            for item in rpt.rules.review
        ],
    )
    _print_section(
        "  rule 10: Claude runs capped after their gate passed",
        ("stage", "count"),
        [(stage, _cell(count)) for stage, count in rpt.rules.cap_losses.items()],
    )
    if rpt.skipped:
        print(f"{rpt.skipped} receipt(s) skipped (malformed or unreadable)")


def cmd_report(args: argparse.Namespace) -> int:
    """Validate the UTC window, then print the ledger report."""
    try:
        since = _parse_bound(args.since, "--since") if args.since else None
        until = _parse_bound(args.until, "--until") if args.until else None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rpt = report(conductor_home(), since=since, until=until)
    if args.json:
        print(json.dumps(rpt.to_dict(), indent=2))
    else:
        _print_report(rpt)
    return 0
