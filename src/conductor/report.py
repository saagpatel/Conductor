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
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import fleets, spend
from . import verdicts as verdicts_mod
from .paths import conductor_home
from .runner import _gate_passed as _runner_gate_passed
from .spend import Run as _SpendRun
from .spend import _number, _parse_bound, _run_time
from .spend import _read_run as _spend_read_run

BUILD_STAGE = "build"
REVIEW_STAGE = "review"
FIX_STAGE = "fix"
DISPOSITIONS = ("fixed", "refused", "already", "wording")


def _money(value: Decimal) -> str:
    """Two-decimal string, per the task's rendering rule for every dollar
    figure this report prints or emits as JSON."""
    return f"{value:.2f}"


def _cell(value: object) -> str:
    return "n/a" if value is None else str(value)


_WALL_FIGURES = (
    "wall_s",
    "paused_s",
    "gate_s",
    "lanes_s",
    "idle_s",
    # W8: blank on every receipt written before these shipped.
    "occupied_s",
    "critical_path_s",
    "lead_s",
)


def _wall_figures(wall: dict | None) -> dict[str, float | int | None]:
    """F2: one mission's `wall` block, as `WallClockRow`'s own keyword
    arguments -- every figure blank (never 0) on a receipt that predates
    the block, or where a single figure was never computable."""
    out: dict[str, float | int | None] = {}
    for key in _WALL_FIGURES:
        value = wall.get(key) if isinstance(wall, dict) else None
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        out[key] = float(value) if numeric else None
    # F15 item 6: an int, not a float, and blank (not 0) on a receipt that
    # predates this field -- `WallClockRow.busy` reads that blank as "unknown
    # concurrency", never as 1.
    concurrency = wall.get("concurrency") if isinstance(wall, dict) else None
    out["concurrency"] = (
        concurrency
        if isinstance(concurrency, int) and not isinstance(concurrency, bool)
        else None
    )
    return out


def _unmatched_vendor(fleet: str) -> str:
    """The vendor-column key for a receipt whose model id matches none of
    that fleet's registered ids. The fleet name is still the most useful
    thing to say; the `unmatched:` prefix keeps it from sharing a cell with
    a registered vendor (`cursor` is both a fleet and a vendor, and so is
    `script`). Never raises: an old receipt must not stop the report.
    """
    return f"unmatched:{fleet}"


def _vendor(fleet: str, model: str) -> str:
    """The vendor behind a receipt's resolved model id.

    A receipt stores the concrete id the CLI was given (`claude-opus-5`,
    `gemini-3.8-flash-high`, ...), not the abstract model name `fleets.Model`
    is keyed by, so lookup matches against every effort-resolved id a
    registered model can take. An unregistered fleet, or a model id that
    matches none of them (an older receipt, a since-removed model), reports
    `unmatched:<fleet>` -- never the bare fleet name, which can be a
    registered vendor, and never raises, since a malformed old receipt
    must not stop the whole report.
    """
    registered = fleets.FLEETS.get(fleet)
    if registered is None:
        return _unmatched_vendor(fleet)
    for candidate in registered.models:
        if model == candidate.name or model in candidate.resolve.values():
            return candidate.vendor
    return _unmatched_vendor(fleet)


@dataclass(frozen=True)
class Run(_SpendRun):
    """`spend.Run`'s accounting fields, plus what a rule needs: stage, lane,
    mission (from the receipt, or joined from a mission snapshot when the
    receipt predates them), duration, error kind, mode, whether an answer
    was written, and whether the gate passed."""

    stage: str | None = None
    lane: str | None = None
    mission: str | None = None
    # None when the receipt carries no usable figure -- a missing, bool, or
    # non-finite `duration_s`. 0.0 is a real answer ("it took no time"), so
    # coercing the unknown to it pulled every mean and median toward zero
    # (2026-09-08 review).
    duration_s: float | None = None
    # F2: the receipt's own `usage.input_tokens` -- the "cache" column's
    # denominator, cache_read_tokens (already on spend.Run) over this.
    input_tokens: int = 0
    kind: str | None = None
    mode: str | None = None
    answer_path: str | None = None
    gate_passed: bool = True
    # D21: whether a gate actually ran. `gate_passed` is True when none did
    # (runner._gate_passed reads "nothing to fail" as not-failed), which is
    # right for `ok` and wrong for "capped after its gate passed": a
    # watcher-killed run never reaches the gate at all.
    gate_ran: bool = False
    # A run conductor itself stopped. Neither is a sitting that finished, so
    # neither is scored as a review: an interrupt can leave a partial
    # `answer.txt` behind, and reading it as a completed review is exactly
    # the false green rule 7's figures are read for (2026-09-08 review).
    interrupted: bool = False
    cancelled: bool = False
    # False when the receipt carries no `breaker` block (a crash or
    # spawn-failure writes null). `tool_calls` is then unknown, not zero --
    # VendorStageRow leaves it out of the mean the same way it leaves an
    # unknown duration out (2026-09-08 review).
    tool_calls_known: bool = True
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

    A lane attempt joins to its declared name and stage; every other effect
    `spend.effects` finds (a collate, a collate order, or a resolve) joins to
    the mission alone, since none of them is a lane and none carries a stage
    of its own.

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
        except (OSError, ValueError):
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
        land_files = sorted(land_dir.glob("*.json")) if land_dir.is_dir() else []
        landed = len(land_files)
        # Review item 2: `landed` counts every receipt `conductor land`
        # wrote, refusals and dry runs included (the F18 mission carries
        # three for one merge). `landed_ok` is the merges that happened.
        landed_ok = sum(1 for path in land_files if _land_merged(path))
        review_lanes: dict[str, dict[str, object]] = {}
        fix_dispositions: list[object] | None = None
        meta[mission] = {
            "ok": ok if isinstance(ok, bool) else None,
            "lanes": len(lane_list),
            "salvaged": salvaged,
            "landed": landed,
            "landed_ok": landed_ok,
            # Review item 2: the spec items the build lane's evidence map
            # names (`evidence.json`, Phase H item 6), read from the copy
            # the runner captured after the gates (W4); None on a mission
            # with no build lane, no map, or a map that did not parse.
            "items": None,
            "review_lanes": review_lanes,
            "fix_dispositions": fix_dispositions,
            # F15 item 3: summed over every `stage: fix` lane on this
            # mission, whether or not it recorded a `dispositions` list.
            "fix_dispositions_malformed": 0,
            # F2: None on a receipt that predates the wall block -- the
            # report lists that mission with blanks, never skips it.
            "wall": raw.get("wall") if isinstance(raw.get("wall"), dict) else None,
        }
        for effect in spend.effects(raw):
            if effect.kind == "attempt":
                join.setdefault(effect.run_id, (mission, effect.lane, effect.stage))
            else:
                join.setdefault(effect.run_id, (mission, None, None))
        for lane_raw in lane_list:
            if not isinstance(lane_raw, dict):
                continue
            lane_name = _str_field(lane_raw, "name")
            stage = _str_field(lane_raw, "stage")
            if stage == BUILD_STAGE and meta[mission]["items"] is None:
                meta[mission]["items"] = _evidence_items(lane_raw)
            if stage == REVIEW_STAGE and isinstance(lane_raw.get("review"), dict):
                attempts = lane_raw.get("attempts")
                last = attempts[-1] if isinstance(attempts, list) and attempts else None
                fleet = last.get("fleet") if isinstance(last, dict) else None
                model = last.get("model") if isinstance(last, dict) else None
                if isinstance(fleet, str) and lane_name:
                    findings = lane_raw["review"].get("findings")
                    findings_parsed = isinstance(findings, int) and not isinstance(findings, bool)
                    raw_items = lane_raw["review"].get("items")
                    last_run_id = last.get("run_id") if isinstance(last, dict) else None
                    review_lanes[lane_name] = {
                        "vendor": _vendor(fleet, model if isinstance(model, str) else ""),
                        "findings": findings if findings_parsed else 0,
                        # F15 item 2: an unparsed verdict is not a finding
                        # source (0 findings would otherwise silently count
                        # as "zero findings, correctly") and its lane must
                        # not have a fix lane's dispositions tallied against
                        # it either -- see the precision loop below.
                        "unparsed": not findings_parsed,
                        # F15 mission 2 item 4: each {"index", "file", "line",
                        # "confidence"} the review lane's FINDING: lines
                        # parsed to; [] on a receipt that predates item 1.
                        "items": raw_items if isinstance(raw_items, list) else [],
                        # Join to the last attempt's run receipt: a lane
                        # receipt does not carry `interrupted`/`cancelled`.
                        "run_id": last_run_id if isinstance(last_run_id, str) else None,
                    }
            if stage == FIX_STAGE:
                malformed = lane_raw.get("dispositions_malformed")
                if isinstance(malformed, int) and not isinstance(malformed, bool):
                    meta[mission]["fix_dispositions_malformed"] += malformed
                if isinstance(lane_raw.get("dispositions"), list):
                    # F15 latent item: a mission with more than one `stage:
                    # fix` lane keeps every lane's dispositions, not the
                    # last one's -- the list is per mission, so extend it.
                    fix_dispositions = (fix_dispositions or []) + lane_raw["dispositions"]
                    meta[mission]["fix_dispositions"] = fix_dispositions
    return join, meta


def _land_merged(path: Path) -> bool:
    """A `conductor land` receipt for a merge that happened: `ok` true, not
    a dry run, not the already-merged answer. A refusal receipt carries
    `refused` and none of these keys.

    `dry_run` missing is a real merge, matching `land.py` (a missing key
    is `is not True` there). Live receipts always write the key; a
    truncated or hand-written one may not, and the two surfaces must
    still agree about that file.
    """
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    return (
        raw.get("ok") is True
        and raw.get("dry_run") is not True
        and raw.get("already_merged") is not True
    )


def _evidence_items(lane_raw: dict) -> int | None:
    """How many spec items the build lane's evidence map names, from the
    final attempt's captured deliverable (`deliverable_path`, the copy the
    runner judged) when the runner recorded it as parsed and ok. None when
    the receipt predates the map or the copy is unreadable; never 0 for
    "unknown", since 0 would divide a landed cost by nothing."""
    attempts = lane_raw.get("attempts")
    last = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(last, dict):
        return None
    state = last.get("deliverable")
    copy = last.get("deliverable_path")
    if not isinstance(state, dict) or state.get("ok") is not True or not isinstance(copy, str):
        return None
    try:
        raw: object = json.loads(Path(copy).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        return None
    return len(raw["items"])


def _gate_ran(tests: object, surface: object) -> bool:
    """D21: whether this receipt's own gate, or the clean gate that replaces
    it, actually ran -- the same choice `runner._gate_passed` makes between
    the two blocks, asked about the run rather than the verdict. A run
    killed at its cap never reaches either (`runner.dispatch` gates only
    when `error is None`), so its `gate_passed: True` means "not checked"."""
    surface_dict = surface if isinstance(surface, dict) else {}
    clean = surface_dict.get("clean_gate")
    clean_dict = clean if isinstance(clean, dict) else {}
    counted = clean_dict if clean_dict.get("ran") else tests
    return bool(isinstance(counted, dict) and counted.get("ran"))


def _read_run(path: Path, join: dict[str, tuple[str, str | None, str | None]]) -> Run | None:
    base = _spend_read_run(path)
    if base is None:
        return None
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, ValueError):
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
    duration_s: float | None = None
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        number = float(duration)
        # NaN and inf both survive `isinstance`, and json round-trips both;
        # either one poisons `statistics.mean` for the whole group.
        if math.isfinite(number) and number >= 0:
            duration_s = number
    gate_passed = _runner_gate_passed(raw.get("tests"), raw.get("test_surface"))
    gate_ran = _gate_ran(raw.get("tests"), raw.get("test_surface"))

    usage = raw.get("usage")
    raw_input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    input_tokens = 0
    if isinstance(raw_input_tokens, int | float) and not isinstance(raw_input_tokens, bool):
        # A vendor that reports the count as a JSON float used to read as 0,
        # which drops it out of `cache_pct`'s denominator and inflates the
        # hit rate -- the very error that column was rewritten to avoid.
        if math.isfinite(raw_input_tokens) and raw_input_tokens >= 0:
            input_tokens = int(raw_input_tokens)

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
        gate_ran=gate_ran,
        interrupted=raw.get("interrupted") is True,
        cancelled=raw.get("cancelled") is True,
        tool_calls_known=raw.get("breaker") is not None,
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
    # F2: the "cache" column -- the prompt cache hit rate over every run in
    # this vendor/stage group: cache reads over everything the model was
    # given (uncached input plus cache reads plus cache writes), the same
    # figure `report.md` prints per mission. The first shape divided reads
    # by uncached input alone and printed millions of percent.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_tokens: int = 0
    # Runs in this group whose receipt carried no usable `duration_s`. They
    # are out of the mean and median entirely rather than counted as
    # zero-second runs, so the column says what it measured.
    unknown_durations: int = 0
    # Runs whose receipt carried no `breaker` block. Same shape: out of
    # `mean_tool_calls` rather than averaged in as zero.
    unknown_tool_calls: int = 0
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
        if run.duration_s is None:
            self.unknown_durations += 1
        else:
            self._durations.append(run.duration_s)
        if not run.tool_calls_known:
            self.unknown_tool_calls += 1
        else:
            self._tool_calls.append(run.tool_calls)
        self.cache_read_tokens += run.cache_read_tokens
        self.cache_write_tokens += run.cache_write_tokens
        self.input_tokens += run.input_tokens

    def cache_pct(self) -> float | None:
        given = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        if not given:
            return None
        return round(self.cache_read_tokens / given * 100, 1)

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
            "unknown_durations": self.unknown_durations,
            "cache_pct": self.cache_pct(),
            "cap_misses": self.cap_misses,
            "gate_failures": self.gate_failures,
            "mean_tool_calls": (
                round(statistics.mean(self._tool_calls), 1) if self._tool_calls else None
            ),
            "unknown_tool_calls": self.unknown_tool_calls,
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
    # Reviews whose parsed verdict reported at least one finding; `rate` is
    # this over the parsed reviews (a fraction, never above 1), while
    # `findings` is the total count those reviews reported.
    with_findings: int = 0

    def rate(self) -> float | None:
        parsed = self.runs - self.unparsed
        return round(self.with_findings / parsed, 3) if parsed else None

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
    # F15 item 2: review lanes on this vendor whose verdict did not parse
    # (findings is not an int) -- excluded from `findings` and from every
    # disposition count above, since a fix lane's dispositions against an
    # unparsed verdict are not real dispositions against real findings.
    unparsed: int = 0
    # W7/W11: the selection this row's `precision` actually rests on --
    # distinct missions whose dispositions contributed to it, and `stage:
    # review` lanes on this vendor that parsed but received no landed
    # disposition of their own, whether their mission recorded no
    # dispositions at all or recorded some naming only other lanes (so they
    # count in `reviewer_finding_rate`, never here, and were previously
    # invisible on this table).
    missions: int = 0
    undispositioned: int = 0
    # F15 mission 2 item 4: the confidence (1-10) of every review item whose
    # (lane, index) a fix lane's disposition matched -- refused and fixed
    # kept apart so a calibration line can compare them.
    refused_confidences: list[int] = field(default_factory=list)
    fixed_confidences: list[int] = field(default_factory=list)

    def total(self) -> int:
        return self.fixed + self.refused + self.already + self.wording

    def precision(self) -> float | None:
        denom = self.fixed + self.refused
        if self.total() < 3 or denom == 0:
            return None
        return round(self.fixed / denom, 3)

    def corrected_rate(self) -> float | None:
        """`fixed` over `findings`: how much of what this vendor reported
        actually landed a fix, not just how it split once refused too."""
        if not self.findings:
            return None
        return round(self.fixed / self.findings, 3)

    def basis(self) -> str:
        """W7: `precision` is fixer agreement over a selection, not a truth
        figure -- this spells the selection's two denominators out in
        prose, the same numbers as the `missions` and `undispositioned`
        columns."""
        return (
            f"fixer agreement over {self.missions} mission(s) with dispositions; "
            f"{self.undispositioned} review lane(s) not dispositioned"
        )

    def calibration(self) -> dict[str, object]:
        """Mean confidence (1 decimal) of the findings this vendor's own
        reported confidence, split by what the fix lane did with them --
        `matched` is the total count either mean was computed over."""

        def mean(values: list[int]) -> float | None:
            return round(float(statistics.mean(values)), 1) if values else None

        return {
            "refused_mean": mean(self.refused_confidences),
            "fixed_mean": mean(self.fixed_confidences),
            "matched": len(self.refused_confidences) + len(self.fixed_confidences),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor": self.vendor,
            "findings": self.findings,
            "fixed": self.fixed,
            "refused": self.refused,
            "already": self.already,
            "wording": self.wording,
            "unparsed": self.unparsed,
            "missions": self.missions,
            "undispositioned": self.undispositioned,
            "precision": self.precision(),
            "refused_confidences": list(self.refused_confidences),
            "fixed_confidences": list(self.fixed_confidences),
            "calibration": self.calibration(),
            "corrected_rate": self.corrected_rate(),
            "basis": self.basis(),
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
    # Review item 2: of those, the merges that happened (`_land_merged`).
    landed_ok: int = 0
    # Review item 2: spec items the build lane's evidence map names; None
    # when the mission carries no parsed map.
    items: int | None = None
    # Runs of this mission that spawned but carried no price. `cost_usd` is
    # then a lower bound, not the mission's cost (2026-09-08 review).
    unpriced_runs: int = 0
    # True when `--since`/`--until` narrowed the runs that reached this row.
    # `landed_ok` and `items` are read from the mission's own directory, over
    # its whole life, so dividing a windowed cost by them is not a rate.
    windowed: bool = False

    def usd_per_item(self) -> Decimal | None:
        """The mission's whole cost over the items it landed: AGENTS.md
        rule 2's "about a dollar per spec item" as a measured figure. None
        unless a merge happened, the map is known, every run of the mission
        carried a price, and the report's window holds the whole mission --
        an unknown is reported as unknown, never divided."""
        if not self.landed_ok or not self.items:
            return None
        if self.unpriced_runs or self.windowed:
            return None
        return self.cost_usd / self.items

    def to_dict(self) -> dict[str, object]:
        per_item = self.usd_per_item()
        return {
            "mission": self.mission,
            "cost_usd": _money(self.cost_usd),
            "ok": self.ok,
            "lanes": self.lanes,
            "capped": self.capped,
            "salvaged": self.salvaged,
            "landed": self.landed,
            "landed_ok": self.landed_ok,
            "items": self.items,
            "unpriced_runs": self.unpriced_runs,
            "windowed": self.windowed,
            "usd_per_item": _money(per_item) if per_item is not None else None,
        }


@dataclass
class LandedRow:
    """Review item 2: cost per landed item across the report's missions.
    `missions` and `cost_usd` cover every mission with a merge that
    happened; `usd_per_item` divides `items_cost_usd` -- the cost of the
    subset whose build lane left a parsed evidence map, every run carried
    a price, and the report's window holds the whole mission (`with_items`)
    -- by the items those maps name, so a mission without a map neither
    inflates nor deflates it. A mission whose own cost is a lower bound
    -- an unpriced run, or a report window that cut some of its runs --
    is out of the division on the same grounds (2026-09-08 review).
    `with_items` is that subset, not "has a map".
    """

    missions: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    with_items: int = 0
    items: int = 0
    items_cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))

    def usd_per_item(self) -> Decimal | None:
        return self.items_cost_usd / self.items if self.items else None

    def to_dict(self) -> dict[str, object]:
        per_item = self.usd_per_item()
        return {
            "missions": self.missions,
            "cost_usd": _money(self.cost_usd),
            "with_items": self.with_items,
            "items": self.items,
            "items_cost_usd": _money(self.items_cost_usd),
            "usd_per_item": _money(per_item) if per_item is not None else None,
        }


@dataclass
class WallClockRow:
    """F2: one mission's wall clock, straight off its own `result.json`
    `wall` block -- `busy` (lanes_s / (wall_s * concurrency)) is computed
    here, not stored, since the figures it divides are always read together.

    F15 item 6: `lanes_s` is a sum across every lane, so a mission that ran
    more than one lane at once legitimately pushes it past `wall_s` on its
    own -- dividing by `wall_s` alone always read that as `busy` above 1.0.
    `concurrency` is None on a receipt that predates this field, and `busy`
    follows it blank rather than assume 1.

    W8: `lanes_s` and `gate_s` are lane-work sums, so `busy` measures how
    hard the lanes worked, not how the elapsed time was spent. `occupied_s`
    (the union of the attempt intervals), `critical_path_s` (the longest
    chain through the lane graph), and `lead_s` (elapsed time that was
    neither paused nor occupied) are the decomposition, and `stretch`
    (`wall_s / critical_path_s`) is the one figure to read for how much
    longer the mission took than its own floor. `busy` keeps its old meaning
    exactly, so an old receipt still reads the same."""

    mission: str
    wall_s: float | None = None
    paused_s: float | None = None
    gate_s: float | None = None
    lanes_s: float | None = None
    idle_s: float | None = None
    concurrency: int | None = None
    occupied_s: float | None = None
    critical_path_s: float | None = None
    lead_s: float | None = None

    def busy(self) -> float | None:
        if not self.wall_s or self.lanes_s is None or not self.concurrency:
            return None
        return round(self.lanes_s / (self.wall_s * self.concurrency), 3)

    def stretch(self) -> float | None:
        """W8: how many times its own critical path the mission actually
        took. 1.0 is a mission that never waited on anything but its longest
        chain; blank when either figure is missing or the path is zero."""
        if self.wall_s is None or not self.critical_path_s:
            return None
        return round(self.wall_s / self.critical_path_s, 3)

    def to_dict(self) -> dict[str, object]:
        return {
            "mission": self.mission,
            "wall_s": self.wall_s,
            "paused_s": self.paused_s,
            "gate_s": self.gate_s,
            "lanes_s": self.lanes_s,
            "idle_s": self.idle_s,
            "concurrency": self.concurrency,
            "busy": self.busy(),
            "occupied_s": self.occupied_s,
            "critical_path_s": self.critical_path_s,
            "lead_s": self.lead_s,
            "stretch": self.stretch(),
        }


@dataclass
class Rules:
    """The figures behind AGENTS.md rules 7 (reviewer cap misses and finding
    rate, per vendor) and 10 (a Claude build or fix run lost at its cap), so
    the lead can compare the prose against the receipts directly.

    D21: `cap_losses[stage]` is `"n/a"` when the stage has no Claude run at
    all, else `{"gate_passed", "gate_failed", "gate_not_run", "total"}` over
    that stage's capped runs. The three cohorts are kept apart because a run
    killed at its cap usually never reaches its gate, and counting "no gate
    ran" as passed read not-checked as green -- rule 10's dollar is about a
    run that had already earned its verdict."""

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
    # Receipts too malformed to have a usable timestamp (the directory
    # name did not parse as a run id). They cannot be bounded by `--since`
    # / `--until`, so they are never folded into `skipped` -- that figure
    # is the same window as every other table.
    skipped_unwindowable: int = 0
    wall_clock: list[WallClockRow] = field(default_factory=list)
    # F15 item 3: totals the per-vendor `reviewer_precision` rows cannot
    # carry -- a disposition naming a lane that is not a review lane on its
    # mission, and a fix lane's own malformed `DISPOSITION:` lines.
    dispositions_unknown_lane: int = 0
    dispositions_malformed: int = 0
    # D20: a disposition superseded by a later one for the same (mission,
    # reviewer lane, finding index), and a disposition whose index names no
    # finding the review lane reported. Neither is counted as a disposition
    # against a finding.
    dispositions_duplicate: int = 0
    dispositions_unmatched: int = 0
    # Review item 2: the aggregate behind the Missions table's per-mission
    # `usd_per_item`.
    landed: LandedRow = field(default_factory=LandedRow)

    def to_dict(self) -> dict[str, object]:
        return {
            "vendor_stage": [row.to_dict() for row in self.vendor_stage],
            "error_kinds": [row.to_dict() for row in self.error_kinds],
            "reviewer_finding_rate": [row.to_dict() for row in self.reviewer_finding_rate],
            "reviewer_precision": [row.to_dict() for row in self.reviewer_precision],
            "dispositions_unknown_lane": self.dispositions_unknown_lane,
            "dispositions_malformed": self.dispositions_malformed,
            "dispositions_duplicate": self.dispositions_duplicate,
            "dispositions_unmatched": self.dispositions_unmatched,
            "missions": [row.to_dict() for row in self.missions],
            "landed": self.landed.to_dict(),
            "rules": self.rules.to_dict(),
            "skipped": self.skipped,
            "skipped_unwindowable": self.skipped_unwindowable,
            "wall_clock": [row.to_dict() for row in self.wall_clock],
        }


def _is_no_findings(answer_path: str) -> dict | None:
    """F1: a thin call to `verdicts.review_verdict` over the answer text --
    the parser conductor now trusts instead of `text.strip() ==
    "NO_FINDINGS"`, which a reviewer's own narration before its verdict
    line defeated.

    None means the file could not be read or decoded. The finding-rate
    loop counts that as `unparsed` (the verdict did not parse), not as a
    sitting that never happened -- an unreadable `answer.txt` is the
    strongest unparsed case, and dropping it made vendor/stage and the
    finding-rate table disagree about how many reviews ran.
    """
    try:
        text = Path(answer_path).read_text()
    except (OSError, ValueError):
        # An interrupted write leaves `answer.txt` truncated mid-UTF-8, and
        # `UnicodeDecodeError` is a ValueError, not an OSError: one such file
        # used to abort the whole report instead of being skipped.
        return None
    return verdicts_mod.review_verdict(text)


def _matched_confidence(items: object, index: object) -> int | None:
    """F15 mission 2 item 4: the review item whose own `index` matches a
    fix lane disposition's `index`, on the same lane -- its confidence, or
    None when nothing matches (a disposition against an old-style review
    with no `items`, or an index the review never numbered)."""
    if not isinstance(items, list) or type(index) is not int:
        return None
    for entry in items:
        if isinstance(entry, dict) and entry.get("index") == index:
            confidence = entry.get("confidence")
            if isinstance(confidence, int) and not isinstance(confidence, bool):
                return confidence
            return None
    return None


def _matches_a_finding(items: object, index: object) -> bool:
    """D20: whether a disposition's `index` names a finding the review lane
    actually parsed. A review lane with no parsed `items` at all (a receipt
    that predates them) cannot answer the question, so every disposition
    against it still counts -- the check applies only where there is
    something to match against."""
    if not isinstance(items, list) or not items:
        return True
    return any(
        isinstance(entry, dict) and type(index) is int and entry.get("index") == index
        for entry in items
    )


def _review_sitting_stopped(info: dict, stopped_ids: set[str]) -> bool:
    """Whether this review lane's last attempt is a run conductor stopped.

    A lane receipt does not carry `interrupted`/`cancelled`; the join is
    the last attempt's `run_id` to the run receipt the finding rate already
    skipped. Precision uses the same sittings so the two tables cannot
    disagree about whether the review happened.
    """
    run_id = info.get("run_id")
    return isinstance(run_id, str) and run_id in stopped_ids


def _unique_dispositions(
    mission: str, fix_dispositions: object, *, counts: dict[str, int]
) -> list[dict]:
    """D20: one entry per (mission, reviewer lane, finding index), keeping
    the last one a fix lane recorded -- a fix lane that restates a
    disposition, or two fix lanes on one mission that both name the same
    finding, used to count once per copy (one finding with three copies of
    its fixed disposition read as `fixed 3`, `corrected rate 3.0`).

    Malformed entries are dropped here as before. An entry carrying no
    integer index identifies no finding, so it cannot be deduplicated and is
    returned as its own row; `counts["duplicate"]` counts every entry a
    later one superseded."""
    if not isinstance(fix_dispositions, list):
        return []
    keyed: dict[tuple[str, str, int], dict] = {}
    unkeyed: list[dict] = []
    for item in fix_dispositions:
        if not isinstance(item, dict):
            continue
        lane_name = item.get("lane")
        disposition = item.get("disposition")
        if disposition not in DISPOSITIONS or not isinstance(lane_name, str):
            continue
        index = item.get("index")
        if type(index) is not int:
            unkeyed.append(item)
            continue
        key = (mission, lane_name, index)
        if key in keyed:
            counts["duplicate"] += 1
        keyed[key] = item
    return list(keyed.values()) + unkeyed


def _build_report(
    rows: list[Run],
    skipped: int,
    mission_meta: dict[str, dict[str, object]],
    *,
    windowed: bool = False,
    skipped_unwindowable: int = 0,
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

        # A run conductor stopped is not a review that happened: an
        # interrupt can leave a partial `answer.txt`, and scoring it reads a
        # stop as a completed sitting in rule 7's own figures.
        if (
            run.stage == REVIEW_STAGE
            and run.answer_path
            and not run.interrupted
            and not run.cancelled
        ):
            verdict = _is_no_findings(run.answer_path)
            row = reviewer.setdefault(vendor, ReviewerFindingRow(vendor=vendor))
            row.runs += 1
            if verdict is None or verdict["verdict"] == "unparsed":
                row.unparsed += 1
            else:
                row.findings += verdict["findings"]
                if verdict["findings"]:
                    row.with_findings += 1

        if run.mission is not None:
            mission_row = missions.setdefault(run.mission, MissionRow(mission=run.mission))
            if run.cost_usd is not None:
                mission_row.cost_usd += run.cost_usd
            elif not run.dry_run:
                # A dry run spent nothing and is not unpriced. Anything else
                # without a price leaves this mission's cost a lower bound,
                # and `usd_per_item` refuses to divide a lower bound.
                mission_row.unpriced_runs += 1
            if run.kind == "cap":
                mission_row.capped = True

    precision: dict[str, ReviewerPrecisionRow] = {}
    # F15 item 3: a disposition naming a lane that is not a review lane on
    # its own mission (typo, or a lane that never ran as `stage: review`) is
    # well-formed but has nowhere to land in `precision` -- counted here
    # instead of vanishing silently.
    dispositions_unknown_lane = 0
    # D20: `duplicate` is a repeated (mission, reviewer lane, finding index)
    # -- only the last entry for a key is counted -- and `unmatched` is a
    # well-formed disposition whose index names no finding the review lane
    # reported. Both were previously counted as ordinary dispositions.
    disposition_counts = {"duplicate": 0, "unmatched": 0}
    # Finding rate skips a run conductor stopped; precision joins the
    # snapshot's last attempt to that same receipt so the two tables name
    # the same sittings.
    stopped_review_ids = {
        r.run_id for r in rows if (r.interrupted or r.cancelled) and r.stage == REVIEW_STAGE
    }
    for name, mission_row in missions.items():
        meta = mission_meta.get(name, {})
        mission_row.ok = meta.get("ok") if isinstance(meta.get("ok"), bool) else None
        mission_row.lanes = meta.get("lanes", 0) if isinstance(meta.get("lanes"), int) else 0
        mission_row.salvaged = (
            meta.get("salvaged", 0) if isinstance(meta.get("salvaged"), int) else 0
        )
        mission_row.landed = meta.get("landed", 0) if isinstance(meta.get("landed"), int) else 0
        mission_row.landed_ok = (
            meta.get("landed_ok", 0) if isinstance(meta.get("landed_ok"), int) else 0
        )
        items = meta.get("items")
        known = isinstance(items, int) and not isinstance(items, bool)
        mission_row.items = items if known else None
        mission_row.windowed = windowed

        review_lanes = meta.get("review_lanes")
        review_lanes = review_lanes if isinstance(review_lanes, dict) else {}
        fix_dispositions = meta.get("fix_dispositions")
        # W7: no fix lane recorded dispositions on this mission (no fix lane
        # at all, or one that ran without a `dispositions` list) -- every
        # parsed review lane here would otherwise vanish from the precision
        # table with no trace; tally it as undispositioned instead so the
        # selection the table rests on is visible. It still counts in
        # `reviewer_finding_rate`, which reads runs directly, not this join.
        if fix_dispositions is None:
            for info in review_lanes.values():
                # W11's guard applies here too: a lane that reported nothing
                # was never left out of anything, on a mission with no fix
                # lane just as on one whose dispositions named other lanes.
                if info.get("unparsed") or not info.get("findings"):
                    continue
                if _review_sitting_stopped(info, stopped_review_ids):
                    continue
                row = precision.setdefault(
                    info["vendor"], ReviewerPrecisionRow(vendor=info["vendor"])
                )
                row.undispositioned += 1
            continue
        if not review_lanes:
            continue
        # F1 item 4: only a mission with both a review lane whose verdict
        # parsed and a fix lane that recorded dispositions (even an empty
        # list -- the field's presence is what "with dispositions" means)
        # joins a disposition's named reviewer lane back to its vendor.
        for info in review_lanes.values():
            if _review_sitting_stopped(info, stopped_review_ids):
                continue
            row = precision.setdefault(info["vendor"], ReviewerPrecisionRow(vendor=info["vendor"]))
            if info.get("unparsed"):
                row.unparsed += 1
            else:
                row.findings += info["findings"]
        # W7: `missions` counts a mission for a vendor only once one of its
        # dispositions actually lands against that vendor's row below --
        # not merely for having a review lane on the mission -- so it names
        # the selection `precision` is actually scored over, never an
        # inflated one a vendor's own findings had no say in.
        mission_vendors: set[str] = set()
        # W11: a lane that received at least one matched disposition is
        # dispositioned -- tracked per lane name, not per vendor, since two
        # lanes on one vendor on one mission can differ (one named, one not).
        dispositioned_lanes: set[str] = set()
        for item in _unique_dispositions(name, fix_dispositions, counts=disposition_counts):
            lane_name = item["lane"]
            disposition = item["disposition"]
            info = review_lanes.get(lane_name)
            if info is None:
                dispositions_unknown_lane += 1
                continue
            if _review_sitting_stopped(info, stopped_review_ids):
                continue
            if info.get("unparsed"):
                continue
            # D20: a disposition whose index names no finding the review lane
            # actually reported is not a disposition against a finding --
            # counted as unmatched and left out of every per-vendor tally, so
            # `fixed` can never exceed `findings` (a `corrected_rate` above
            # 1.0 was reachable from one bad index alone).
            if not _matches_a_finding(info.get("items"), item.get("index")):
                disposition_counts["unmatched"] += 1
                continue
            row = precision.setdefault(info["vendor"], ReviewerPrecisionRow(vendor=info["vendor"]))
            setattr(row, disposition, getattr(row, disposition) + 1)
            mission_vendors.add(info["vendor"])
            dispositioned_lanes.add(lane_name)
            if disposition in ("fixed", "refused"):
                confidence = _matched_confidence(info.get("items"), item.get("index"))
                if confidence is not None:
                    target = (
                        row.fixed_confidences
                        if disposition == "fixed"
                        else row.refused_confidences
                    )
                    target.append(confidence)
        for vendor in mission_vendors:
            precision[vendor].missions += 1
        # W11: a parsed review lane that named no landed disposition on a
        # mission that recorded dispositions (for other lanes, or none at
        # all here) is undispositioned too -- previously only a mission with
        # no fix lane at all reached this column, so a vendor whose lane
        # parsed but went unnamed was counted in `findings` and nowhere else.
        # A lane that reported zero findings has nothing a disposition could
        # ever name, so it is not "left out" the way an unnamed lane with
        # findings is; skip it rather than inflate `basis()` with a lane a
        # fix lane could never have dispositioned.
        for lane_name, info in review_lanes.items():
            if info.get("unparsed") or not info.get("findings"):
                continue
            if _review_sitting_stopped(info, stopped_review_ids):
                continue
            if lane_name in dispositioned_lanes:
                continue
            row = precision.setdefault(info["vendor"], ReviewerPrecisionRow(vendor=info["vendor"]))
            row.undispositioned += 1

    precision_rows = sorted(precision.values(), key=lambda r: r.vendor)
    # F15 item 3: every fix lane's own `dispositions_malformed` count, summed
    # across the same missions the rest of this report's tables are scoped
    # to -- independent of whether that mission has a review lane at all,
    # unlike `dispositions_unknown_lane` above.
    dispositions_malformed = sum(
        meta.get("fix_dispositions_malformed", 0)
        for name, meta in mission_meta.items()
        if isinstance(meta.get("fix_dispositions_malformed"), int)
        and (not windowed or name in missions)
    )

    vendor_stage_rows = sorted(
        vendor_stage.values(), key=lambda r: (-r.cost_usd, r.vendor, r.stage or "")
    )
    error_kind_rows = sorted(error_kinds.values(), key=lambda r: (-r.count, r.kind))
    reviewer_rows = sorted(reviewer.values(), key=lambda r: r.vendor)
    mission_rows = sorted(missions.values(), key=lambda r: (-r.cost_usd, r.mission))
    landed = LandedRow()
    for row in mission_rows:
        if not row.landed_ok:
            continue
        landed.missions += 1
        landed.cost_usd += row.cost_usd
        # `usd_per_item`, not `items`: a mission whose cost is a lower bound
        # (an unpriced run) or whose runs were cut by the report's window is
        # out of the division, the same way one with no map already was.
        if row.usd_per_item() is not None:
            landed.with_items += 1
            landed.items += row.items
            landed.items_cost_usd += row.cost_usd

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
    # Rule 10 is the Claude *fleet*'s cap (AGENTS.md: every Claude cap),
    # not `_vendor == "anthropic"`. A since-renamed model id still ran on
    # that fleet; selecting by vendor made vendor/stage show a cap miss
    # that this table called "no Claude run at all". A run whose fleet is
    # not `claude` is not claimed.
    grace: dict[str, object] = {}
    for stage in ("build", "fix"):
        matches = [r for r in rows if r.fleet == "claude" and r.stage == stage]
        if not matches:
            cap_losses[stage] = "n/a"
        else:
            # D21: a cap loss is only rule 10's case when a gate actually ran
            # and passed. A watcher-killed run never gets that far, and
            # counting its "no gate ran" as passed read not-checked as green.
            capped = [r for r in matches if r.kind == "cap"]
            unmatched_vendor_runs = sum(
                1 for r in matches if _vendor(r.fleet, r.model) != "anthropic"
            )
            cohort: dict[str, object] = {
                "gate_passed": sum(1 for r in capped if r.gate_ran and r.gate_passed),
                "gate_failed": sum(1 for r in capped if r.gate_ran and not r.gate_passed),
                "gate_not_run": sum(1 for r in capped if not r.gate_ran),
                "total": len(capped),
            }
            if unmatched_vendor_runs:
                # The fleet is Claude; the model id did not resolve to
                # anthropic. Surfaced rather than silently claimed as that
                # vendor, or dropped from the table that exists to count
                # these cap misses.
                cohort["unmatched_vendor_runs"] = unmatched_vendor_runs
            cap_losses[stage] = cohort
        grace_total = sum((r.grace_used for r in matches if r.grace_used), Decimal("0"))
        grace[stage] = {
            "used_usd": _money(grace_total),
            "runs": sum(1 for r in matches if r.finished_in_band),
        }
    cap_losses["grace"] = grace

    # F2: one row per mission this report included -- every mission on disk
    # when the report is unwindowed, and only missions with a run inside
    # `--since`/`--until` when those are set, the same bound as every other
    # table. A mission recorded before this field existed still gets its
    # row when it is in that set, blanks and all.
    wall_rows = [
        WallClockRow(mission=name, **_wall_figures(mission_meta[name].get("wall")))
        for name in sorted(mission_meta)
        if not windowed or name in missions
    ]

    return Report(
        vendor_stage=vendor_stage_rows,
        error_kinds=error_kind_rows,
        reviewer_finding_rate=reviewer_rows,
        reviewer_precision=precision_rows,
        dispositions_unknown_lane=dispositions_unknown_lane,
        dispositions_malformed=dispositions_malformed,
        dispositions_duplicate=disposition_counts["duplicate"],
        dispositions_unmatched=disposition_counts["unmatched"],
        missions=mission_rows,
        landed=landed,
        rules=Rules(review=review_rules, cap_losses=cap_losses),
        skipped=skipped,
        skipped_unwindowable=skipped_unwindowable,
        wall_clock=wall_rows,
    )


def report(home: Path, *, since: datetime | None = None, until: datetime | None = None) -> Report:
    """Read every dispatch receipt once and compute the ledger report."""
    join, mission_meta = _scan_missions(home)
    runs_dir = home / "runs"
    result_files = sorted(runs_dir.glob("*/result.json")) if runs_dir.is_dir() else []
    rows: list[Run] = []
    skipped = 0
    skipped_unwindowable = 0
    for result_file in result_files:
        run = _read_run(result_file, join)
        if run is None:
            # `_read_run` failed; the directory name is the only stamp we
            # can still window on. A receipt whose name is not a run id
            # cannot be bounded by `--since`/`--until`, so it is counted
            # separately rather than silently included in or dropped from
            # `skipped`.
            created = _run_time(result_file.parent.name)
            if created is None:
                skipped_unwindowable += 1
                continue
            if since is not None and created < since:
                continue
            if until is not None and created >= until:
                continue
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
    return _build_report(
        rows,
        skipped,
        mission_meta,
        windowed=since is not None or until is not None,
        skipped_unwindowable=skipped_unwindowable,
    )


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
        "Reviewer precision (fixer agreement -- findings fixed vs. refused, over "
        "missions with a fix lane's dispositions)",
        (
            "vendor",
            "findings",
            "fixed",
            "refused",
            "already",
            "wording",
            "unparsed",
            "precision",
            "missions",
            "undispositioned",
        ),
        [
            (
                d["vendor"],
                _cell(d["findings"]),
                _cell(d["fixed"]),
                _cell(d["refused"]),
                _cell(d["already"]),
                _cell(d["wording"]),
                _cell(d["unparsed"]),
                _cell(d["precision"]),
                _cell(d["missions"]),
                _cell(d["undispositioned"]),
            )
            for d in (row.to_dict() for row in rpt.reviewer_precision)
        ],
    )
    # F15 mission 2 item 4: one calibration line per vendor, right under the
    # precision table it refines -- confidence of what the fix lane refused
    # versus what it fixed, plus the corrected finding rate (fixed/findings).
    for row in rpt.reviewer_precision:
        d = row.to_dict()
        calib = d["calibration"]
        refused_mean = _cell(calib["refused_mean"])
        fixed_mean = _cell(calib["fixed_mean"])
        corrected = _cell(d["corrected_rate"])
        print(
            f"  {row.vendor}: refused mean confidence {refused_mean} over "
            f"{len(row.refused_confidences)}, fixed mean confidence {fixed_mean} over "
            f"{len(row.fixed_confidences)}, corrected finding rate {corrected}"
        )
        # W7: precision is fixer agreement, not truth -- name the selection
        # it rests on right under the table, in the same numbers as the
        # `missions`/`undispositioned` columns.
        print(f"  {row.vendor}: {d['basis']}")
    print(
        f"  dispositions naming an unknown lane: {rpt.dispositions_unknown_lane}"
        f"  |  malformed disposition lines: {rpt.dispositions_malformed}"
        f"  |  duplicate dispositions: {rpt.dispositions_duplicate}"
        f"  |  dispositions naming no reported finding: {rpt.dispositions_unmatched}"
    )
    print()
    _print_section(
        "Missions",
        (
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
        ),
        [
            (
                d["mission"],
                d["cost_usd"],
                _cell(d["ok"]),
                _cell(d["lanes"]),
                _cell(d["capped"]),
                _cell(d["salvaged"]),
                _cell(d["landed"]),
                _cell(d["landed_ok"]),
                _cell(d["items"]),
                _cell(d["unpriced_runs"]),
                _cell(d["usd_per_item"]),
            )
            for d in (row.to_dict() for row in rpt.missions)
        ],
    )
    landed = rpt.landed.to_dict()
    if landed["missions"]:
        per_item = landed["usd_per_item"]
        print(
            f"  Landed: {landed['missions']} mission(s), ${landed['cost_usd']}; "
            f"{landed['with_items']} fully priced, unwindowed, with an evidence map "
            f"naming {landed['items']} item(s) (${landed['items_cost_usd']}): "
            + (f"${per_item} per landed item" if per_item is not None else "no per-item figure")
        )
    else:
        print("  Landed: no mission in this window merged")
    print()
    _print_section(
        "Wall clock",
        (
            "mission",
            "wall_s",
            "paused_s",
            "gate_s",
            "lanes_s",
            "idle_s",
            "concurrency",
            "busy",
            "occupied_s",
            "critical_path_s",
            "lead_s",
            "stretch",
        ),
        [
            (
                d["mission"],
                _cell(d["wall_s"]),
                _cell(d["paused_s"]),
                _cell(d["gate_s"]),
                _cell(d["lanes_s"]),
                _cell(d["idle_s"]),
                _cell(d["concurrency"]),
                _cell(d["busy"]),
                _cell(d["occupied_s"]),
                _cell(d["critical_path_s"]),
                _cell(d["lead_s"]),
                _cell(d["stretch"]),
            )
            for d in (row.to_dict() for row in rpt.wall_clock)
        ],
    )
    print("Rules: AGENTS.md 7 (review cap misses and finding rate) and 10 (a Claude build")
    print("or fix run lost at its cap, split by what its own gate actually did)")
    _print_section(
        "  rule 7: review vendors",
        ("vendor", "cap_misses", "finding_rate"),
        [
            (item["vendor"], _cell(item["cap_misses"]), _cell(item["finding_rate"]))
            for item in rpt.rules.review
        ],
    )
    _print_section(
        "  rule 10: Claude runs capped, by what their own gate did",
        ("stage", "gate passed", "gate failed", "gate not run", "total"),
        [
            (
                stage,
                _cell(cohorts["gate_passed"]) if isinstance(cohorts, dict) else _cell(cohorts),
                _cell(cohorts["gate_failed"]) if isinstance(cohorts, dict) else _cell(cohorts),
                _cell(cohorts["gate_not_run"]) if isinstance(cohorts, dict) else _cell(cohorts),
                _cell(cohorts["total"]) if isinstance(cohorts, dict) else _cell(cohorts),
            )
            for stage, cohorts in rpt.rules.cap_losses.items()
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
    if rpt.skipped_unwindowable:
        print(
            f"{rpt.skipped_unwindowable} receipt(s) skipped "
            "(malformed or unreadable, no usable timestamp to window)"
        )


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
