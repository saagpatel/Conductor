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
from . import verdicts as verdicts_mod
from .paths import conductor_home
from .runner import _gate_passed as _runner_gate_passed
from .spend import Run as _SpendRun
from .spend import _collate_run_ids, _number, _parse_bound
from .spend import _read_run as _spend_read_run

REVIEW_STAGE = "review"
FIX_STAGE = "fix"
DISPOSITIONS = ("fixed", "refused", "already", "wording")


def _money(value: Decimal) -> str:
    """Two-decimal string, per the task's rendering rule for every dollar
    figure this report prints or emits as JSON."""
    return f"{value:.2f}"


def _cell(value: object) -> str:
    return "n/a" if value is None else str(value)


_WALL_FIGURES = ("wall_s", "paused_s", "gate_s", "lanes_s", "idle_s")


def _wall_figures(wall: dict | None) -> dict[str, float | None]:
    """F2: one mission's `wall` block, as `WallClockRow`'s own keyword
    arguments -- every figure blank (never 0) on a receipt that predates
    the block, or where a single figure was never computable."""
    out: dict[str, float | None] = {}
    for key in _WALL_FIGURES:
        value = wall.get(key) if isinstance(wall, dict) else None
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        out[key] = float(value) if numeric else None
    return out


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
    # F2: the receipt's own `usage.input_tokens` -- the "cache" column's
    # denominator, cache_read_tokens (already on spend.Run) over this.
    input_tokens: int = 0
    kind: str | None = None
    mode: str | None = None
    answer_path: str | None = None
    gate_passed: bool = True
    # E24: how much of the grace band this run drew on, and whether it
    # finished inside the band (grace used, and not over budget) -- the
    # two figures rule 10 reports per stage.
    grace_used: Decimal | None = None
    finished_in_band: bool = False


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

    F1: `meta[mission]` also carries `review_lanes` (`{lane_name: {"vendor",
    "findings"}}`, from every `stage: review` lane's own `review` verdict
    and its final attempt's fleet/model) and `fix_dispositions` (the
    `dispositions` list from a `stage: fix` lane, or None when no fix lane
    on this mission recorded one) -- the join `_build_report`'s reviewer
    precision table needs between a disposition's named reviewer lane and
    that lane's vendor, without a second pass over the same file.
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
        # F7: `conductor land` receipts, same shape as `salvaged` above --
        # `land` never dispatches a fleet either, so this is the only place
        # a land shows up in the report at all.
        land_dir = result_file.parent / "land"
        landed = len(list(land_dir.glob("*.json"))) if land_dir.is_dir() else 0
        review_lanes: dict[str, dict[str, object]] = {}
        fix_dispositions: list[object] | None = None
        meta[mission] = {
            "ok": ok if isinstance(ok, bool) else None,
            "lanes": len(lane_list),
            "salvaged": salvaged,
            "landed": landed,
            "review_lanes": review_lanes,
            "fix_dispositions": fix_dispositions,
            # F2: None on a receipt that predates the wall block -- the
            # report lists that mission with blanks, never skips it.
            "wall": raw.get("wall") if isinstance(raw.get("wall"), dict) else None,
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
            if stage == REVIEW_STAGE and isinstance(lane_raw.get("review"), dict):
                attempts = lane_raw.get("attempts")
                last = attempts[-1] if isinstance(attempts, list) and attempts else None
                fleet = last.get("fleet") if isinstance(last, dict) else None
                model = last.get("model") if isinstance(last, dict) else None
                if isinstance(fleet, str) and lane_name:
                    findings = lane_raw["review"].get("findings")
                    review_lanes[lane_name] = {
                        "vendor": _vendor(fleet, model if isinstance(model, str) else ""),
                        "findings": findings if isinstance(findings, int) else 0,
                    }
            if stage == FIX_STAGE and isinstance(lane_raw.get("dispositions"), list):
                fix_dispositions = lane_raw["dispositions"]
                meta[mission]["fix_dispositions"] = fix_dispositions
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

    usage = raw.get("usage")
    raw_input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    input_tokens = (
        raw_input_tokens
        if isinstance(raw_input_tokens, int) and not isinstance(raw_input_tokens, bool)
        else 0
    )

    budget = raw.get("budget")
    grace_used: Decimal | None = None
    finished_in_band = False
    if isinstance(budget, dict):
        try:
            grace_used = _number(budget.get("grace_used"))
        except ValueError:
            grace_used = None
        finished_in_band = (
            grace_used is not None and grace_used > 0 and budget.get("exceeded") is False
        )

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
        input_tokens=input_tokens,
        kind=_str_field(raw, "kind"),
        mode=_str_field(raw, "mode"),
        answer_path=_str_field(raw, "answer_path"),
        gate_passed=gate_passed,
        grace_used=grace_used,
        finished_in_band=finished_in_band,
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
    # F2: the "cache" column -- cache_read_tokens over input_tokens, summed
    # across every run in this vendor/stage group, as a percentage.
    cache_read_tokens: int = 0
    input_tokens: int = 0
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
        self.cache_read_tokens += run.cache_read_tokens
        self.input_tokens += run.input_tokens

    def cache_pct(self) -> float | None:
        if not self.input_tokens:
            return None
        return round(self.cache_read_tokens / self.input_tokens * 100, 1)

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
            "cache_pct": self.cache_pct(),
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
    # F1: `runs` whose final line did not parse as `NO_FINDINGS` or
    # `FINDINGS: N` -- narration `report._is_no_findings` used to read as a
    # finding. Included in `runs`, excluded from `rate`'s denominator.
    unparsed: int = 0

    def rate(self) -> float | None:
        parsed = self.runs - self.unparsed
        return round(self.findings / parsed, 3) if parsed else None

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "runs": self.runs,
            "findings": self.findings,
            "unparsed": self.unparsed,
            "rate": self.rate(),
        }


@dataclass
class ReviewerPrecisionRow:
    """F1: item 4 -- per reviewer vendor, over missions that have both a
    review lane and a fix lane with dispositions, how many of that
    reviewer's findings the fix lane fixed versus refused as wrong.
    `precision` is blank (n/a) under three total dispositions: three
    dispositions is not enough to read as a rate."""

    vendor: str
    findings: int = 0
    fixed: int = 0
    refused: int = 0
    already: int = 0
    wording: int = 0

    def total(self) -> int:
        return self.fixed + self.refused + self.already + self.wording

    def precision(self) -> float | None:
        denom = self.fixed + self.refused
        if self.total() < 3 or denom == 0:
            return None
        return round(self.fixed / denom, 3)

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "findings": self.findings,
            "fixed": self.fixed,
            "refused": self.refused,
            "already": self.already,
            "wording": self.wording,
            "precision": self.precision(),
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
    # F7: land receipts under this mission's `land/` directory, 0 when absent.
    landed: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "mission": self.mission,
            "cost_usd": _money(self.cost_usd),
            "ok": self.ok,
            "lanes": self.lanes,
            "capped": self.capped,
            "salvaged": self.salvaged,
            "landed": self.landed,
        }


@dataclass
class WallClockRow:
    """F2: one mission's wall clock, straight off its own `result.json`
    `wall` block -- `busy` (lanes_s / wall_s) is computed here, not stored,
    since the two figures it divides are always read together."""

    mission: str
    wall_s: float | None = None
    paused_s: float | None = None
    gate_s: float | None = None
    lanes_s: float | None = None
    idle_s: float | None = None

    def busy(self) -> float | None:
        if not self.wall_s or self.lanes_s is None:
            return None
        return round(self.lanes_s / self.wall_s, 3)

    def to_dict(self) -> dict[str, object]:
        return {
            "mission": self.mission,
            "wall_s": self.wall_s,
            "paused_s": self.paused_s,
            "gate_s": self.gate_s,
            "lanes_s": self.lanes_s,
            "idle_s": self.idle_s,
            "busy": self.busy(),
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
    reviewer_precision: list[ReviewerPrecisionRow]
    missions: list[MissionRow]
    rules: Rules
    skipped: int = 0
    wall_clock: list[WallClockRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor_stage": [row.to_dict() for row in self.vendor_stage],
            "error_kinds": [row.to_dict() for row in self.error_kinds],
            "reviewer_finding_rate": [row.to_dict() for row in self.reviewer_finding_rate],
            "reviewer_precision": [row.to_dict() for row in self.reviewer_precision],
            "missions": [row.to_dict() for row in self.missions],
            "rules": self.rules.to_dict(),
            "skipped": self.skipped,
            "wall_clock": [row.to_dict() for row in self.wall_clock],
        }


def _is_no_findings(answer_path: str) -> dict | None:
    """F1: a thin call to `verdicts.review_verdict` over the answer text --
    the parser conductor now trusts instead of `text.strip() ==
    "NO_FINDINGS"`, which a reviewer's own narration before its verdict
    line defeated."""
    try:
        text = Path(answer_path).read_text()
    except OSError:
        return None
    return verdicts_mod.review_verdict(text)


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
            verdict = _is_no_findings(run.answer_path)
            if verdict is not None:
                row = reviewer.setdefault(vendor, ReviewerFindingRow(vendor=vendor))
                row.runs += 1
                if verdict["verdict"] == "unparsed":
                    row.unparsed += 1
                else:
                    row.findings += verdict["findings"]

        if run.mission is not None:
            mission_row = missions.setdefault(run.mission, MissionRow(mission=run.mission))
            if run.cost_usd is not None:
                mission_row.cost_usd += run.cost_usd
            if run.kind == "cap":
                mission_row.capped = True

    precision: dict[str, ReviewerPrecisionRow] = {}
    for name, mission_row in missions.items():
        meta = mission_meta.get(name, {})
        mission_row.ok = meta.get("ok") if isinstance(meta.get("ok"), bool) else None
        mission_row.lanes = meta.get("lanes", 0) if isinstance(meta.get("lanes"), int) else 0
        mission_row.salvaged = (
            meta.get("salvaged", 0) if isinstance(meta.get("salvaged"), int) else 0
        )
        mission_row.landed = meta.get("landed", 0) if isinstance(meta.get("landed"), int) else 0

        # F1 item 4: only a mission with both a review lane whose verdict
        # parsed and a fix lane that recorded dispositions (even an empty
        # list -- the field's presence is what "with dispositions" means)
        # joins a disposition's named reviewer lane back to its vendor.
        review_lanes = meta.get("review_lanes")
        fix_dispositions = meta.get("fix_dispositions")
        if not review_lanes or not isinstance(review_lanes, dict) or fix_dispositions is None:
            continue
        for info in review_lanes.values():
            row = precision.setdefault(info["vendor"], ReviewerPrecisionRow(vendor=info["vendor"]))
            row.findings += info["findings"]
        for item in fix_dispositions:
            if not isinstance(item, dict):
                continue
            lane_name = item.get("lane")
            disposition = item.get("disposition")
            info = review_lanes.get(lane_name) if isinstance(lane_name, str) else None
            if info is None or disposition not in DISPOSITIONS:
                continue
            row = precision.setdefault(info["vendor"], ReviewerPrecisionRow(vendor=info["vendor"]))
            setattr(row, disposition, getattr(row, disposition) + 1)

    precision_rows = sorted(precision.values(), key=lambda r: r.vendor)

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
    # E24: rule 10's other half -- how much of the grace band each Claude
    # build/fix stage actually drew on, and how many of its runs finished
    # inside the band instead of being lost the way rule 10's dollar names.
    grace: dict[str, object] = {}
    for stage in ("build", "fix"):
        matches = [r for r in rows if _vendor(r.fleet, r.model) == "anthropic" and r.stage == stage]
        if not matches:
            cap_losses[stage] = "n/a"
        else:
            cap_losses[stage] = sum(1 for r in matches if r.kind == "cap" and r.gate_passed)
        grace_total = sum((r.grace_used for r in matches if r.grace_used), Decimal("0"))
        grace[stage] = {
            "used_usd": _money(grace_total),
            "runs": sum(1 for r in matches if r.finished_in_band),
        }
    cap_losses["grace"] = grace

    # F2: every mission this pass ever saw (`_scan_missions` reads every
    # `result.json` under `home/missions`, not just the ones with a run in
    # the window `rows` was filtered to), so a mission recorded before this
    # field existed still gets its row, blanks and all.
    wall_rows = [
        WallClockRow(mission=name, **_wall_figures(mission_meta[name].get("wall")))
        for name in sorted(mission_meta)
    ]

    return Report(
        vendor_stage=vendor_stage_rows,
        error_kinds=error_kind_rows,
        reviewer_finding_rate=reviewer_rows,
        reviewer_precision=precision_rows,
        missions=mission_rows,
        rules=Rules(review=review_rules, cap_losses=cap_losses),
        skipped=skipped,
        wall_clock=wall_rows,
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
            "cache",
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
                _cell(d["cache_pct"]) + ("%" if d["cache_pct"] is not None else ""),
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
        "Reviewer finding rate (stage=review, final line NO_FINDINGS or FINDINGS: N)",
        ("vendor", "runs", "findings", "unparsed", "rate"),
        [
            (
                d["vendor"],
                _cell(d["runs"]),
                _cell(d["findings"]),
                _cell(d["unparsed"]),
                _cell(d["rate"]),
            )
            for d in (row.to_dict() for row in rpt.reviewer_finding_rate)
        ],
    )
    _print_section(
        "Reviewer precision (findings fixed vs. refused, over missions with a fix "
        "lane's dispositions)",
        ("vendor", "findings", "fixed", "refused", "already", "wording", "precision"),
        [
            (
                d["vendor"],
                _cell(d["findings"]),
                _cell(d["fixed"]),
                _cell(d["refused"]),
                _cell(d["already"]),
                _cell(d["wording"]),
                _cell(d["precision"]),
            )
            for d in (row.to_dict() for row in rpt.reviewer_precision)
        ],
    )
    _print_section(
        "Missions",
        ("mission", "cost_usd", "ok", "lanes", "capped", "salvaged", "landed"),
        [
            (
                d["mission"],
                d["cost_usd"],
                _cell(d["ok"]),
                _cell(d["lanes"]),
                _cell(d["capped"]),
                _cell(d["salvaged"]),
                _cell(d["landed"]),
            )
            for d in (row.to_dict() for row in rpt.missions)
        ],
    )
    _print_section(
        "Wall clock",
        ("mission", "wall_s", "paused_s", "gate_s", "lanes_s", "idle_s", "busy"),
        [
            (
                d["mission"],
                _cell(d["wall_s"]),
                _cell(d["paused_s"]),
                _cell(d["gate_s"]),
                _cell(d["lanes_s"]),
                _cell(d["idle_s"]),
                _cell(d["busy"]),
            )
            for d in (row.to_dict() for row in rpt.wall_clock)
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
        [
            (stage, _cell(count))
            for stage, count in rpt.rules.cap_losses.items()
            if stage != "grace"
        ],
    )
    _print_section(
        "  rule 10: grace band usage (E24)",
        ("stage", "used_usd", "runs finished in band"),
        [
            (stage, info["used_usd"], _cell(info["runs"]))
            for stage, info in (rpt.rules.cap_losses.get("grace") or {}).items()
        ],
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
