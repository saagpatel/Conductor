"""Summarize durable dispatch receipts without pretending unknown cost is zero."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Iterable
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


def _optional_count(value: object) -> int:
    """An optional non-negative integer counter off a receipt.

    JSON null (and a missing key, whose caller passes None) is a missing
    count, not a malformed run: it reads as 0. A bool, a non-int, or a
    negative value is a wrong type and refuses the receipt -- `_read_run`
    already treats ValueError as skip. A JSON float (`13028437.0`) is a
    non-int and stays a refusal: the write path (`outputs.usable_int`)
    already coerces integral floats to int before the receipt is stored,
    so a float still on disk is not a conductor-written count.
    """
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError
    return value


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
            tool_calls = _optional_count(breaker.get("tool_calls"))
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
        tokens = _optional_count(usage.get("total_tokens"))
        cache_read = _optional_count(usage.get("cache_read_tokens"))
        cache_write = _optional_count(usage.get("cache_write_tokens"))
        return Run(
            run_id,
            created,
            fleet,
            model,
            ok,
            cost,
            basis == "estimated",
            tokens,
            cache_read,
            cache_write,
            tool_calls,
            dry_run,
        )
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Effect:
    """One paid dispatch a mission snapshot or a lane receipt named.

    `kind` is `"attempt"` (a lane's own dispatch), `"collate"` (a collate's
    own run), `"order"` (one of a collate's judge orders), or `"resolve"`
    (the cross-vendor resolver). `lane` and `stage` are set only for an
    `"attempt"`; every other kind carries neither, since a collate, order,
    or resolve is not itself a lane. `superseded` is True for an entry a
    rerun replaced (`previous_attempts`, `previous_collates`, or
    `previous_resolves`), so a caller that only wants the live picture can
    filter it out. `record` is whatever summary object the receipt carried
    for that run, so a caller can price it (`cost_usd`, `unpriced`) even
    once the run's own directory under `runs/` is gone; it is `{}` for a
    bare `final` string, which carries no summary of its own.
    """

    run_id: str
    kind: str
    lane: str | None
    stage: str | None
    superseded: bool
    record: dict


def effects(snapshot: dict | None = None, lanes: Iterable[dict] = ()) -> list[Effect]:
    """Every paid dispatch a mission snapshot and/or a set of lane receipts
    named, as one `Effect` per distinct `run_id` (first occurrence wins, in
    encounter order).

    This is the versioned adapter every caller that discovers a mission's
    paid run ids -- `resume`'s spend accounting, the ledger `report`, and
    `export` -- reads instead of walking the receipt shape by hand,
    because each generation of receipt added a new place a paid dispatch
    could hide and a hand-written walk had to be told about it separately:

    - lane `attempts` and a lane's `final` run: every generation.
    - `previous_attempts` (an attempt a rerun superseded): every generation.
    - `previous_collates` and a collate's `orders` (a ranking collate's two
      judge runs) and a judge sitting's extra `judges[].orders`: added E4.
    - `resolve` (the cross-vendor resolver) and `previous_resolves` (a rerun's
      superseded resolvers): added D13.

    `snapshot` is a mission's `result.json` shape: its `lanes` (each lane's
    `final` as a bare string or as a dict with `run_id`, then
    `attempts`, then `previous_attempts`), then `collate` and
    `previous_collates`, then `resolve` and `previous_resolves`. `lanes` is an
    iterable of lane-receipt dicts (`lanes/<name>.json`, carrying `name`,
    `stage`, `attempts`, `previous_attempts`), each walked the same way as
    a snapshot lane. Every key above may be absent on an older receipt;
    a missing key is simply skipped, never an error.
    """
    found: dict[str, Effect] = {}

    def add(run_id: object, kind: str, lane: str | None, stage: str | None,
            superseded: bool, record: object) -> None:
        if isinstance(run_id, str) and run_id not in found:
            found[run_id] = Effect(
                run_id, kind, lane, stage, superseded, record if isinstance(record, dict) else {}
            )

    def walk_lane(lane_data: object) -> None:
        if not isinstance(lane_data, dict):
            return
        lane_name = lane_data.get("name")
        lane_name = lane_name if isinstance(lane_name, str) else None
        stage = lane_data.get("stage")
        stage = stage if isinstance(stage, str) else None
        final = lane_data.get("final")
        if isinstance(final, str):
            add(final, "attempt", lane_name, stage, False, {})
        elif isinstance(final, dict):
            add(final.get("run_id"), "attempt", lane_name, stage, False, final)
        # Current first, then the superseded ones -- the order `walk_collate`
        # and `resolve` / `previous_resolves` already use. Reversed, an
        # attempt kept across a resume (the same run_id in both lists) was
        # registered from `previous_attempts` and, first occurrence winning,
        # read as superseded for the rest of the mission's life.
        for key, superseded in (("attempts", False), ("previous_attempts", True)):
            attempts = lane_data.get(key)
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if isinstance(attempt, dict):
                    add(attempt.get("run_id"), "attempt", lane_name, stage, superseded, attempt)

    def walk_collate(collate: object, superseded: bool) -> None:
        if not isinstance(collate, dict):
            return
        add(collate.get("run_id"), "collate", None, None, superseded, collate)
        orders = collate.get("orders")
        if isinstance(orders, list):
            for order in orders:
                if isinstance(order, dict):
                    add(order.get("run_id"), "order", None, None, superseded, order)
        judges = collate.get("judges")
        if isinstance(judges, list):
            for judge in judges:
                if not isinstance(judge, dict):
                    continue
                judge_orders = judge.get("orders")
                if isinstance(judge_orders, list):
                    for order in judge_orders:
                        if isinstance(order, dict):
                            add(order.get("run_id"), "order", None, None, superseded, order)

    if isinstance(snapshot, dict):
        snapshot_lanes = snapshot.get("lanes")
        if isinstance(snapshot_lanes, list):
            for lane_data in snapshot_lanes:
                walk_lane(lane_data)
        # Current first, then the superseded ones -- the order `resolve` and
        # `previous_resolves` below already use. Reversed, a collate kept
        # across a resume (the same run_id in both lists) was registered from
        # `previous_collates` and, first occurrence winning, read as
        # superseded for the rest of the mission's life (2026-09-08 review).
        walk_collate(snapshot.get("collate"), False)
        previous_collates = snapshot.get("previous_collates")
        if isinstance(previous_collates, list):
            for collate in previous_collates:
                walk_collate(collate, True)
        resolve = snapshot.get("resolve")
        if isinstance(resolve, dict):
            add(resolve.get("run_id"), "resolve", None, None, False, resolve)
        previous_resolves = snapshot.get("previous_resolves")
        if isinstance(previous_resolves, list):
            for old_resolve in previous_resolves:
                if isinstance(old_resolve, dict):
                    add(old_resolve.get("run_id"), "resolve", None, None, True, old_resolve)

    for lane_data in lanes:
        walk_lane(lane_data)

    return list(found.values())


def mission_run_ids(data: dict[str, object]) -> set[str]:
    """Every run id a mission's `result.json` snapshot (`data`) named as
    paid for: lane attempts and their final run, every collate's own run and
    order runs (a judge sitting's extra judges included), and the resolver's
    run plus every superseded resolver a rerun kept. W9: public so `export`
    can union it with what lane receipts and the receipt chain separately
    name, catching a judge-order or resolver run that no lane attempt does.
    """
    return {effect.run_id for effect in effects(data)}


# Kept as an alias: every internal caller predates the public name above.
_mission_runs = mission_run_ids


def _collate_run_ids(collate: dict[str, object]) -> set[str]:
    """A collate's priced runs: its own run (a prose collate), both order
    runs (a ranking collate's judge 1), and, in a judge sitting (E4), every
    extra judge's own two order runs."""
    return {effect.run_id for effect in effects({"collate": collate})}


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
