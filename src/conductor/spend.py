"""Summarize durable dispatch receipts without pretending unknown cost is zero."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

from .paths import conductor_home

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RUN_STAMP = re.compile(r"^(\d{8}T\d{6}Z)")


@dataclass(frozen=True)
class Run:
    """The accounting fields from one well-formed dispatch receipt."""

    run_id: str
    created: datetime
    fleet: str
    model: str
    ok: bool
    cost_usd: Decimal | None
    estimated: bool
    tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    tool_calls: int
    dry_run: bool = False  # spawned nothing and spent nothing; not an unpriced run


@dataclass
class Row:
    """One group in the spend ledger; unpriced runs remain a separate count."""

    group: str
    runs: int = 0
    ok: int = 0
    cost_usd: Decimal = Decimal("0")
    estimated_runs: int = 0
    unpriced_runs: int = 0
    tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: int = 0
    dry_runs: int = 0  # counted on the total row only; never spend, never unpriced

    def add(self, run: Run) -> None:
        self.runs += 1
        self.ok += int(run.ok)
        if run.cost_usd is None:
            self.unpriced_runs += 1
        else:
            self.cost_usd += run.cost_usd
        self.estimated_runs += int(run.estimated)
        self.tokens += run.tokens
        self.cache_read_tokens += run.cache_read_tokens
        self.cache_write_tokens += run.cache_write_tokens
        self.tool_calls += run.tool_calls

    def to_dict(self, *, skipped: int | None = None) -> dict[str, str | int | float]:
        row: dict[str, str | int | float] = {
            "group": self.group,
            "runs": self.runs,
            "ok": self.ok,
            "cost_usd": float(self.cost_usd.quantize(Decimal("0.000001"))),
            "estimated_runs": self.estimated_runs,
            "unpriced_runs": self.unpriced_runs,
            "tokens": self.tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "tool_calls": self.tool_calls,
        }
        if skipped is not None:
            row["skipped"] = skipped
            row["dry_runs"] = self.dry_runs
        return row


def _parse_bound(value: str, option: str) -> datetime:
    try:
        if _DATE.fullmatch(value):
            return datetime.combine(date.fromisoformat(value), time.min, tzinfo=UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"error: invalid {option}: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _run_time(run_id: str) -> datetime | None:
    match = _RUN_STAMP.match(run_id)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError
    return Decimal(str(value))


def _read_run(path: Path) -> Run | None:
    try:
        raw: object = json.loads(path.read_text())
        if not isinstance(raw, dict):
            return None
        run_id = raw.get("run_id")
        fleet = raw.get("fleet")
        model = raw.get("model")
        ok = raw.get("ok")
        if not isinstance(run_id, str) or not isinstance(fleet, str) or not isinstance(model, str):
            return None
        if not isinstance(ok, bool):
            return None
        created = _run_time(run_id)
        if created is None:
            return None

        dry_run = raw.get("dry_run") is True
        breaker = raw.get("breaker")
        if breaker is None:
            tool_calls = 0
        elif isinstance(breaker, dict):
            raw_tools = breaker.get("tool_calls", 0)
            if isinstance(raw_tools, bool) or not isinstance(raw_tools, int) or raw_tools < 0:
                return None
            tool_calls = raw_tools
        else:
            return None
        usage = raw.get("usage")
        if usage is None:
            return Run(run_id, created, fleet, model, ok, None, False, 0, 0, 0, tool_calls, dry_run)
        if not isinstance(usage, dict):
            return None
        cost = _number(usage.get("cost_usd"))
        basis = usage.get("cost_basis")
        if basis is not None and basis not in {"reported", "estimated"}:
            return None
        raw_tokens = usage.get("total_tokens")
        if raw_tokens is None:
            tokens = 0
        elif isinstance(raw_tokens, bool) or not isinstance(raw_tokens, int) or raw_tokens < 0:
            return None
        else:
            tokens = raw_tokens
        raw_cache = usage.get("cache_read_tokens", 0)
        if isinstance(raw_cache, bool) or not isinstance(raw_cache, int) or raw_cache < 0:
            return None
        raw_cache_write = usage.get("cache_write_tokens", 0)
        if (
            isinstance(raw_cache_write, bool)
            or not isinstance(raw_cache_write, int)
            or raw_cache_write < 0
        ):
            return None
        return Run(
            run_id,
            created,
            fleet,
            model,
            ok,
            cost,
            basis == "estimated",
            tokens,
            raw_cache,
            raw_cache_write,
            tool_calls,
            dry_run,
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def _mission_runs(data: dict[str, object]) -> set[str]:
    run_ids: set[str] = set()
    lanes = data.get("lanes")
    if isinstance(lanes, list):
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            final = lane.get("final")
            if isinstance(final, str):
                run_ids.add(final)
            elif isinstance(final, dict) and isinstance(final.get("run_id"), str):
                run_ids.add(final["run_id"])
            for key in ("previous_attempts", "attempts"):
                attempts = lane.get(key)
                if not isinstance(attempts, list):
                    continue
                for attempt in attempts:
                    if isinstance(attempt, dict) and isinstance(attempt.get("run_id"), str):
                        run_ids.add(attempt["run_id"])
    previous_collates = data.get("previous_collates")
    if isinstance(previous_collates, list):
        for collate in previous_collates:
            if isinstance(collate, dict):
                run_ids |= _collate_run_ids(collate)
    collate = data.get("collate")
    if isinstance(collate, dict):
        run_ids |= _collate_run_ids(collate)
    # D13: the resolver's own run, and every superseded resolver a rerun
    # retained. Both are absent on a snapshot written before D13.
    resolve = data.get("resolve")
    if isinstance(resolve, dict) and isinstance(resolve.get("run_id"), str):
        run_ids.add(resolve["run_id"])
    previous_resolves = data.get("previous_resolves")
    if isinstance(previous_resolves, list):
        for old_resolve in previous_resolves:
            if isinstance(old_resolve, dict) and isinstance(old_resolve.get("run_id"), str):
                run_ids.add(old_resolve["run_id"])
    return run_ids


def _collate_run_ids(collate: dict[str, object]) -> set[str]:
    """A collate's priced runs: its own run (a prose collate), both order
    runs (a ranking collate's judge 1), and, in a judge sitting (E4), every
    extra judge's own two order runs."""
    run_ids: set[str] = set()
    if isinstance(collate.get("run_id"), str):
        run_ids.add(collate["run_id"])
    orders = collate.get("orders")
    if isinstance(orders, list):
        for order in orders:
            if isinstance(order, dict) and isinstance(order.get("run_id"), str):
                run_ids.add(order["run_id"])
    judges = collate.get("judges")
    if isinstance(judges, list):
        for judge in judges:
            if not isinstance(judge, dict):
                continue
            judge_orders = judge.get("orders")
            if isinstance(judge_orders, list):
                for order in judge_orders:
                    if isinstance(order, dict) and isinstance(order.get("run_id"), str):
                        run_ids.add(order["run_id"])
    return run_ids


def _mission_map(home: Path) -> dict[str, str]:
    association: dict[str, str] = {}
    missions = home / "missions"
    if not missions.is_dir():
        return association
    for result_file in sorted(missions.glob("*/result.json")):
        try:
            raw: object = json.loads(result_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        mission = name if isinstance(name, str) and name else result_file.parent.name
        for run_id in _mission_runs(raw):
            association.setdefault(run_id, mission)
    return association


def _group(run: Run, by: str, missions: dict[str, str]) -> str:
    if by == "day":
        return run.created.date().isoformat()
    if by == "model":
        return run.model
    if by == "mission":
        return missions.get(run.run_id, "(standalone)")
    if by == "run":
        return run.run_id
    return run.fleet


def summarize(
    home: Path, *, since: datetime | None, until: datetime | None, by: str
) -> tuple[list[Row], Row, int]:
    """Read every dispatch receipt once and aggregate the selected window."""
    missions = _mission_map(home)
    groups: dict[str, Row] = {}
    total = Row("total")
    skipped = 0
    runs = home / "runs"
    result_files = sorted(runs.glob("*/result.json")) if runs.is_dir() else []
    for result_file in result_files:
        run = _read_run(result_file)
        if run is None:
            skipped += 1
            continue
        if since is not None and run.created < since:
            continue
        if until is not None and run.created >= until:
            continue
        if run.dry_run:
            # A dry run wrote a receipt with no usage; folding it into
            # "unpriced" would make every rehearsal read as unverified spend.
            total.dry_runs += 1
            continue
        group = _group(run, by, missions)
        groups.setdefault(group, Row(group)).add(run)
        total.add(run)
    rows = sorted(groups.values(), key=lambda row: (-row.cost_usd, row.group))
    return rows, total, skipped


def _print_table(rows: list[Row], total: Row, skipped: int) -> None:
    headings = (
        "group",
        "runs",
        "ok",
        "cost_usd",
        "estimated",
        "unpriced",
        "tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "tool_calls",
    )
    values = [
        (
            row.group,
            str(row.runs),
            str(row.ok),
            f"{row.cost_usd:.4f}",
            str(row.estimated_runs),
            str(row.unpriced_runs),
            str(row.tokens),
            str(row.cache_read_tokens),
            str(row.cache_write_tokens),
            str(row.tool_calls),
        )
        for row in [*rows, total]
    ]
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in values))
        for index in range(len(headings))
    ]
    header = "  ".join(value.ljust(widths[index]) for index, value in enumerate(headings))
    print(header)
    for index, row in enumerate(values):
        rendered = [row[0].ljust(widths[0])]
        rendered.extend(row[column].rjust(widths[column]) for column in range(1, len(row)))
        line = "  ".join(rendered)
        if index == len(values) - 1:
            if total.unpriced_runs:
                line += f"  {total.unpriced_runs} unpriced"
            if skipped:
                line += f"  {skipped} skipped"
            if total.dry_runs:
                line += f"  {total.dry_runs} dry run(s) excluded"
        print(line)


def cmd_spend(args: argparse.Namespace) -> int:
    """Validate the UTC window, then print the durable accounting ledger."""
    try:
        since = _parse_bound(args.since, "--since") if args.since else None
        until = _parse_bound(args.until, "--until") if args.until else None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rows, total, skipped = summarize(conductor_home(), since=since, until=until, by=args.by)
    if args.json:
        output = [row.to_dict() for row in rows]
        output.append(total.to_dict(skipped=skipped))
        print(json.dumps(output, indent=2))
    else:
        _print_table(rows, total, skipped)
    return 0
