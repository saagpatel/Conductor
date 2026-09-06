"""E9: a rolling spend ceiling read from the run receipts under
`<home>/runs`, independent of any one mission's own ledger. A mission's own
`max_cost_usd` bounds what that mission alone may spend; this bounds what
conductor, across every mission and every standalone dispatch, has spent
recently -- the guard an unattended launch needs before it ever opens a
worktree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .spend import _read_run

USD_PER_HOUR = 10.0
USD_PER_DAY = 25.0


@dataclass(frozen=True)
class RollingSpend:
    hour_usd: float
    day_usd: float
    unpriced_hour: int
    unpriced_day: int
    runs_hour: int
    runs_day: int


def rolling_spend(home: Path, *, now: datetime | None = None) -> RollingSpend:
    """Sum every well-formed run receipt under `home/runs` whose run time
    (`spend._run_time`) falls in the last 60 minutes and the last 24 hours.
    A run with no priced usage (including a free E6 script run, which is
    priced at exactly $0 and so never lands here) counts toward the window's
    run count and its unpriced count, never toward its dollars. A dry run
    spent nothing and is not a dispatch at all -- excluded outright, the same
    as `spend.summarize` excludes it."""
    now = now or datetime.now(UTC)
    hour_ago = now - timedelta(minutes=60)
    day_ago = now - timedelta(hours=24)
    hour_usd = 0.0
    day_usd = 0.0
    unpriced_hour = 0
    unpriced_day = 0
    runs_hour = 0
    runs_day = 0
    runs_dir = Path(home) / "runs"
    if runs_dir.is_dir():
        for result_file in runs_dir.glob("*/result.json"):
            run = _read_run(result_file)
            if run is None or run.dry_run or run.created < day_ago:
                continue
            runs_day += 1
            if run.cost_usd is None:
                unpriced_day += 1
            else:
                day_usd += float(run.cost_usd)
            if run.created >= hour_ago:
                runs_hour += 1
                if run.cost_usd is None:
                    unpriced_hour += 1
                else:
                    hour_usd += float(run.cost_usd)
    return RollingSpend(
        hour_usd=hour_usd,
        day_usd=day_usd,
        unpriced_hour=unpriced_hour,
        unpriced_day=unpriced_day,
        runs_hour=runs_hour,
        runs_day=runs_day,
    )
