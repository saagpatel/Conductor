"""E14: cost forecast and warn -- before a mission dispatches anything,
compare each lane's cap against what the same vendor and stage has actually
cost on this machine, and warn (never refuse) when the cap sits under the
history. Costs come from the same durable receipts `conductor report` reads
(`report._read_run` and `report._scan_missions`, reused rather than a second
reader); nothing here is estimated from tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from . import report as report_mod

if TYPE_CHECKING:
    from .mission import Mission

# AGENTS.md rule 7 names the 80th percentile as the figure a cap is judged
# against; the three-run floor keeps a single unlucky run from reading as a
# trend.
_WARN_PERCENTILE = 80
_MIN_RUNS_FOR_PERCENTILES = 3


def history(
    home: Path, *, since: datetime | None = None
) -> dict[tuple[str, str | None], list[Decimal]]:
    """The priced, non-dry-run cost of every dispatch receipt under
    `home/runs`, grouped by (vendor, stage) exactly the way `conductor
    report` groups its vendor-and-stage rows."""
    join, _ = report_mod._scan_missions(home)
    runs_dir = home / "runs"
    result_files = sorted(runs_dir.glob("*/result.json")) if runs_dir.is_dir() else []
    grouped: dict[tuple[str, str | None], list[Decimal]] = {}
    for result_file in result_files:
        run = report_mod._read_run(result_file, join)
        if run is None or run.dry_run or run.cost_usd is None:
            continue
        if since is not None and run.created < since:
            continue
        vendor = report_mod._vendor(run.fleet, run.model)
        grouped.setdefault((vendor, run.stage), []).append(run.cost_usd)
    return grouped


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
    hist = history(home, since=since)
    lanes: list[LaneForecast] = []
    warnings: list[str] = []
    for lane in mission.lanes:
        if lane.human or lane.script:
            continue
        primary = lane.attempts[0]
        vendor = report_mod._vendor(primary.fleet, primary.model or "")
        stage = lane.stage
        costs = hist.get((vendor, stage), [])
        runs = len(costs)
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
        if warn:
            warnings.append(
                f"lane '{lane.name}': cap ${cap_usd:.2f} is under the ${p80_usd:.2f} "
                f"80th percentile of {runs} {vendor} {stage} runs"
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
            )
        )
    return Forecast(lanes=lanes, warnings=warnings)
