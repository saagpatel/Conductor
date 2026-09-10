"""E14: cost forecast and warn -- before a mission dispatches anything,
compare each lane's cap against what the same vendor and stage has actually
cost on this machine, and warn (never refuse) when the cap sits under the
history. Costs come from the same durable receipts `conductor report` reads
(`report._read_run` and `report._scan_missions`, reused rather than a second
reader); nothing here is estimated from tokens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from . import report as report_mod
from .fleets import model_vendor

if TYPE_CHECKING:
    from .mission import Mission

# The cap policy uses nearest-rank p80, which is the maximum at n=3/4.
# Three runs are a sample floor, not protection against an outlier.
_WARN_PERCENTILE = 80
_MIN_RUNS_FOR_PERCENTILES = 3


def _normalize_since(since: datetime | None) -> datetime | None:
    if since is None:
        return None
    if since.tzinfo is None:
        return since.replace(tzinfo=UTC)
    return since.astimezone(UTC)


@dataclass
class _HistoryStats:
    costs: list[Decimal] = field(default_factory=list)
    unpriced_runs: int = 0
    estimated_runs: int = 0
    zero_cost_runs: int = 0


def _scan_history(
    home: Path, *, since: datetime | None = None
) -> dict[tuple[str, str | None], _HistoryStats]:
    join, _ = report_mod._scan_missions(home)
    runs_dir = home / "runs"
    result_files = sorted(runs_dir.glob("*/result.json")) if runs_dir.is_dir() else []
    since_utc = _normalize_since(since)
    grouped: dict[tuple[str, str | None], _HistoryStats] = {}
    for result_file in result_files:
        run = report_mod._read_run(result_file, join)
        if run is None or run.dry_run:
            continue
        if since_utc is not None and run.created < since_utc:
            continue
        joined = join.get(run.run_id)
        if joined is not None and joined[1] is None:
            # Known auxiliary dispatch (collate, order, resolve)
            continue
        vendor = report_mod._vendor(run.fleet, run.model)
        key = (vendor, run.stage)
        stats = grouped.setdefault(key, _HistoryStats())
        if run.estimated:
            stats.estimated_runs += 1
        if run.cost_usd is None:
            stats.unpriced_runs += 1
        else:
            stats.costs.append(run.cost_usd)
            if run.cost_usd == Decimal("0"):
                stats.zero_cost_runs += 1
    return grouped


def history(
    home: Path, *, since: datetime | None = None
) -> dict[tuple[str, str | None], list[Decimal]]:
    """The priced, non-dry-run cost of every dispatch receipt under
    `home/runs`, excluding known auxiliary effects and grouped by
    (vendor, stage). Standalone dispatches remain eligible."""
    scanned = _scan_history(home, since=since)
    return {k: v.costs for k, v in scanned.items() if v.costs}


def _percentile(costs: list[Decimal], pct: int) -> Decimal:
    """Nearest-rank percentile over the sorted costs. Integer ceiling
    division (`pct` and the count are both whole numbers) rather than
    `math.ceil(pct / 100 * n)`, which can overshoot by one rank on a value
    floating-point cannot represent exactly (0.8 * 5 rounds up, not to 4.0)."""
    ordered = sorted(costs)
    n = len(ordered)
    rank = -(-(pct * n) // 100)
    index = max(0, min(n - 1, rank - 1))
    return ordered[index]


@dataclass(frozen=True)
class LaneForecast:
    lane: str
    vendor: str
    stage: str | None
    cap_usd: float | None
    runs: int
    median_usd: Decimal | None
    p80_usd: Decimal | None
    warn: bool
    unpriced_runs: int = 0
    estimated_runs: int = 0
    zero_cost_runs: int = 0
    lane_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "lane": self.lane,
            "vendor": self.vendor,
            "stage": self.stage,
            "cap_usd": self.cap_usd,
            "runs": self.runs,
            "median_usd": float(self.median_usd) if self.median_usd is not None else None,
            "p80_usd": float(self.p80_usd) if self.p80_usd is not None else None,
            "warn": self.warn,
            "unpriced_runs": self.unpriced_runs,
            "estimated_runs": self.estimated_runs,
            "zero_cost_runs": self.zero_cost_runs,
        }


@dataclass(frozen=True)
class Forecast:
    lanes: list[LaneForecast] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "lanes": [lane.to_dict() for lane in self.lanes],
            "warnings": self.warnings,
        }


def forecast(mission: Mission, home: Path, *, since: datetime | None = None) -> Forecast:
    """One `LaneForecast` per dispatched lane (human and script lanes carry
    no cost history worth comparing, and are skipped), and one warning per
    lane whose cap sits under its vendor-and-stage's 80th percentile with at
    least three runs on record. The dispatch itself is never refused or
    delayed by what this finds."""
    hist_stats = _scan_history(home, since=since)
    lanes: list[LaneForecast] = []
    warnings: list[str] = []
    for idx, lane in enumerate(mission.lanes):
        if lane.human or lane.script:
            continue
        primary = lane.attempts[1 if lane.cascaded else 0]
        vendor = model_vendor(primary.fleet, primary.model)
        stage = lane.stage
        stats = hist_stats.get((vendor, stage))
        costs = stats.costs if stats is not None else []
        runs = len(costs)
        unpriced_runs = stats.unpriced_runs if stats is not None else 0
        estimated_runs = stats.estimated_runs if stats is not None else 0
        zero_cost_runs = stats.zero_cost_runs if stats is not None else 0
        cap_usd = primary.cap_usd
        median_usd: Decimal | None = None
        p80_usd: Decimal | None = None
        if runs >= _MIN_RUNS_FOR_PERCENTILES:
            median_usd = _percentile(costs, 50)
            p80_usd = _percentile(costs, _WARN_PERCENTILE)
        warn = (
            cap_usd is not None
            and p80_usd is not None
            and Decimal(str(cap_usd)) < p80_usd
        )
        stage_label = stage if stage is not None else "unstaged"
        if warn:
            warnings.append(
                f"lane '{lane.name}': cap ${cap_usd:.2f} is under the ${p80_usd:.2f} "
                f"80th percentile of {runs} {vendor} {stage_label} runs"
            )
        if unpriced_runs > 0:
            run_str = f"{unpriced_runs} run" if unpriced_runs == 1 else f"{unpriced_runs} runs"
            warnings.append(
                f"lane '{lane.name}': matching {vendor} {stage_label} history has "
                f"{run_str} with unknown prices"
            )
        lanes.append(
            LaneForecast(
                lane=lane.name,
                vendor=vendor,
                stage=stage,
                cap_usd=cap_usd,
                runs=runs,
                median_usd=median_usd,
                p80_usd=p80_usd,
                warn=warn,
                unpriced_runs=unpriced_runs,
                estimated_runs=estimated_runs,
                zero_cost_runs=zero_cost_runs,
                lane_index=idx,
            )
        )
    return Forecast(lanes=lanes, warnings=warnings)


def apply_caps(raw: dict, fc: Forecast, *, enabled: bool = True) -> list[dict[str, object]]:
    """F17: raise every warned lane's cap to its p80 rounded up to the next
    whole dollar, raise the mission budget by the same difference, and record
    the arithmetic under `raw["caps"]` for every lane that has a cap. With
    `enabled` false nothing moves and the receipt records the p80 that was
    declined. Returns one row per lane actually raised, for the launcher to
    print."""
    by_name = {lane.lane: lane for lane in fc.lanes}
    by_index = {lane.lane_index: lane for lane in fc.lanes if lane.lane_index is not None}
    raised_rows: list[dict[str, object]] = []
    caps_block: dict[str, dict[str, object]] = {}
    previous = raw.get("caps") if isinstance(raw.get("caps"), dict) else {}
    for index, raw_lane in enumerate(raw.get("lanes") or []):
        if not isinstance(raw_lane, dict) or raw_lane.get("fleet") in {"human", "script"}:
            continue
        name = str(raw_lane.get("name") or "")
        row = by_name.get(name) if name else by_index.get(index)
        if not name and row is not None:
            name = row.lane
        cap = raw_lane.get("cap_usd")
        if cap is None and row is not None:
            cap = row.cap_usd
        if cap is None:
            continue
        cap = float(cap)
        p80 = float(row.p80_usd) if row is not None and row.p80_usd is not None else None
        runs = row.runs if row is not None else 0
        old = previous.get(name)
        # Preserve the original arithmetic on a repeated application, but
        # do not carry its basis over an operator's subsequent cap edit.
        old = old if isinstance(old, dict) and old.get("cap_usd") == cap else {}
        rule_2 = old.get("rule_2_usd", cap)
        basis = old.get("basis", "rule 2")
        written = cap
        if enabled and row is not None and row.warn and row.p80_usd is not None:
            written = max(cap, float(math.ceil(row.p80_usd)))
            if written > cap:
                raw_lane["cap_usd"] = written
                budget = raw.get("max_cost_usd")
                if budget is not None:
                    raw["max_cost_usd"] = round(float(budget) + written - cap, 2)
                basis = "forecast p80"
                raised_rows.append(
                    {"lane": name, "old_usd": cap, "cap_usd": written, "p80_usd": p80, "runs": runs}
                )
        caps_block[name] = {
            "rule_2_usd": rule_2,
            "forecast_p80_usd": p80,
            "forecast_runs": runs,
            "cap_usd": written,
            "basis": basis,
        }
    raw["caps"] = caps_block
    return raised_rows
