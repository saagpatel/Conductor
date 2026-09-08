"""Re-parse stored stdout with the current parser and correct the ledger.

A parser convention change leaves every receipt already on disk under the old
convention, so `spend`, `report`, the rolling ceiling, and `forecast` read a
mix. This command owns the catch-up: re-parse each run's `stdout.log`, compare
that to the stored `usage`, and report or rewrite the difference.

Two refusals, which are the point of the command:

1. A `cost_basis: "reported"` cost is never recomputed. The vendor printed a
   dollar figure; that figure is evidence. Token counters on such a receipt
   may be corrected; `cost_usd` may not.
2. A receipt whose token counters did not move is not touched at all, even
   when `prices.estimate` now returns a different figure. Re-price what the
   *parser* changed. Never let a price-table revision walk backwards through
   stored history.
"""

from __future__ import annotations

import argparse
import json
import math
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import prices
from .outputs import Usage, parse
from .paths import conductor_home
from .report import _print_section

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
)

SKIP_UNREADABLE = "unreadable"
SKIP_DRY_RUN = "dry_run"
SKIP_NO_USAGE = "no_usage"
SKIP_NO_PARSED_USAGE = "no_parsed_usage"
SKIP_TOKENS_UNCHANGED = "tokens_unchanged"

SKIP_REASONS = (
    SKIP_UNREADABLE,
    SKIP_DRY_RUN,
    SKIP_NO_USAGE,
    SKIP_NO_PARSED_USAGE,
    SKIP_TOKENS_UNCHANGED,
)

_LARGEST = 10


@dataclass
class FleetDelta:
    """Token and cost deltas for one fleet over the moved set."""

    receipts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    old_cost_usd: float = 0.0
    new_cost_usd: float = 0.0

    def add(self, move: Move) -> None:
        self.receipts += 1
        for name in TOKEN_FIELDS:
            setattr(self, name, getattr(self, name) + move.token_delta[name])
        if move.old_cost_usd is not None:
            self.old_cost_usd += move.old_cost_usd
        if move.new_cost_usd is not None:
            self.new_cost_usd += move.new_cost_usd

    def to_dict(self) -> dict[str, int | float]:
        return {
            "receipts": self.receipts,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "thinking_tokens": self.thinking_tokens,
            "old_cost_usd": round(self.old_cost_usd, 6),
            "new_cost_usd": round(self.new_cost_usd, 6),
        }


@dataclass(frozen=True)
class Move:
    """One receipt whose parser-derived token counters differ from storage."""

    run_id: str
    fleet: str
    path: Path
    raw: dict
    usage: dict
    token_delta: dict[str, int]
    old_tokens: dict[str, int]
    new_tokens: dict[str, int]
    new_total_tokens: int
    old_cost_usd: float | None
    new_cost_usd: float | None
    new_price: dict | None
    estimated: bool

    @property
    def cost_delta(self) -> float:
        old = 0.0 if self.old_cost_usd is None else self.old_cost_usd
        new = 0.0 if self.new_cost_usd is None else self.new_cost_usd
        return new - old

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "fleet": self.fleet,
            "input_tokens": self.token_delta["input_tokens"],
            "output_tokens": self.token_delta["output_tokens"],
            "cache_read_tokens": self.token_delta["cache_read_tokens"],
            "cache_write_tokens": self.token_delta["cache_write_tokens"],
            "thinking_tokens": self.token_delta["thinking_tokens"],
            "old_cost_usd": self.old_cost_usd,
            "new_cost_usd": self.new_cost_usd,
            "delta_usd": round(self.cost_delta, 6),
        }


@dataclass
class Summary:
    """What a reprice pass scanned, skipped, and would move (or did)."""

    scanned: int = 0
    apply: bool = False
    archive: str | None = None
    skipped: dict[str, int] = field(
        default_factory=lambda: {reason: 0 for reason in SKIP_REASONS}
    )
    moves: list[Move] = field(default_factory=list)

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    def to_dict(self) -> dict[str, object]:
        fleets: dict[str, FleetDelta] = {}
        old_cost = 0.0
        new_cost = 0.0
        for move in self.moves:
            fleets.setdefault(move.fleet, FleetDelta()).add(move)
            if move.old_cost_usd is not None:
                old_cost += move.old_cost_usd
            if move.new_cost_usd is not None:
                new_cost += move.new_cost_usd
        largest = sorted(
            self.moves,
            key=lambda move: (
                -abs(move.cost_delta),
                -abs(move.token_delta["input_tokens"]),
                move.run_id,
            ),
        )[:_LARGEST]
        return {
            "scanned": self.scanned,
            "moved": len(self.moves),
            "apply": self.apply,
            "archive": self.archive,
            "skipped": dict(self.skipped),
            "fleets": {name: delta.to_dict() for name, delta in sorted(fleets.items())},
            "old_cost_usd": round(old_cost, 6),
            "new_cost_usd": round(new_cost, 6),
            "largest": [move.to_dict() for move in largest],
        }


def _counter(value: object) -> int:
    """A stored token counter, or 0 when the key is missing or null."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _tokens_from_stored(usage: dict) -> dict[str, int]:
    return {name: _counter(usage.get(name)) for name in TOKEN_FIELDS}


def _tokens_from_parsed(usage: Usage) -> dict[str, int]:
    return {name: int(getattr(usage, name)) for name in TOKEN_FIELDS}


def _cost_number(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _candidate_runs(home: Path) -> list[Path]:
    runs = home / "runs"
    if not runs.is_dir():
        return []
    found: list[Path] = []
    for result_file in sorted(runs.glob("*/result.json")):
        if (result_file.parent / "stdout.log").is_file():
            found.append(result_file.parent)
    return found


def _load_receipt(path: Path) -> tuple[dict, str] | str:
    """Return `(raw, fleet)` or a skip reason."""
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return SKIP_UNREADABLE
    if not isinstance(raw, dict):
        return SKIP_UNREADABLE
    if raw.get("dry_run") is True:
        return SKIP_DRY_RUN
    fleet = raw.get("fleet")
    model = raw.get("model")
    if not isinstance(fleet, str) or not isinstance(model, str):
        return SKIP_UNREADABLE
    usage = raw.get("usage")
    if usage is None:
        return SKIP_NO_USAGE
    if not isinstance(usage, dict):
        return SKIP_UNREADABLE
    return raw, fleet


def _read_stdout(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _new_cost(
    move_estimated: bool, model: str, parsed: Usage, stored: dict
) -> tuple[float | None, dict | None]:
    """The cost a rewrite may carry: estimated from new tokens, else the stored figure.

    Enforces refusal 1: a reported cost is returned unchanged, never
    recomputed from the stream or from `prices.estimate`.
    """
    stored_cost = _cost_number(stored.get("cost_usd"))
    stored_price = stored.get("price") if isinstance(stored.get("price"), dict) else None
    if not move_estimated:
        return stored_cost, stored_price
    estimated = prices.estimate(
        model,
        input_tokens=parsed.input_tokens,
        output_tokens=parsed.output_tokens,
        cache_read_tokens=parsed.cache_read_tokens,
        cache_write_tokens=parsed.cache_write_tokens,
    )
    return estimated, prices.basis(model)


def _plan_move(run_dir: Path) -> Move | str:
    """A Move, or the skip reason that kept this receipt off the moved set.

    Refusal 2 lives here: identical token counters return
    `tokens_unchanged` even when `prices.estimate` would now differ.
    """
    result_file = run_dir / "result.json"
    loaded = _load_receipt(result_file)
    if isinstance(loaded, str):
        return loaded
    raw, fleet = loaded
    stdout = _read_stdout(run_dir / "stdout.log")
    if stdout is None:
        return SKIP_UNREADABLE
    parsed = parse(fleet, stdout).usage
    if parsed is None:
        return SKIP_NO_PARSED_USAGE
    usage = raw["usage"]
    model = raw["model"]
    if not isinstance(usage, dict) or not isinstance(model, str):
        return SKIP_UNREADABLE
    old_tokens = _tokens_from_stored(usage)
    new_tokens = _tokens_from_parsed(parsed)
    if old_tokens == new_tokens:
        return SKIP_TOKENS_UNCHANGED
    estimated = usage.get("cost_basis") == "estimated"
    new_cost, new_price = _new_cost(estimated, model, parsed, usage)
    token_delta = {name: new_tokens[name] - old_tokens[name] for name in TOKEN_FIELDS}
    run_id = raw.get("run_id")
    return Move(
        run_id=run_id if isinstance(run_id, str) else run_dir.name,
        fleet=fleet,
        path=result_file,
        raw=raw,
        usage=usage,
        token_delta=token_delta,
        old_tokens=old_tokens,
        new_tokens=new_tokens,
        new_total_tokens=parsed.total_tokens,
        old_cost_usd=_cost_number(usage.get("cost_usd")),
        new_cost_usd=new_cost,
        new_price=new_price,
        estimated=estimated,
    )


def _archive_receipts(home: Path, *, now: datetime) -> Path:
    """Tar every `result.json` under `runs/` beside that directory."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = home / f"runs-receipts.backup-{stamp}.tgz"
    if archive.exists():
        stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        archive = home / f"runs-receipts.backup-{stamp}.tgz"
    runs = home / "runs"
    with tarfile.open(archive, "w:gz") as tar:
        for result_file in sorted(runs.glob("*/result.json")):
            tar.add(result_file, arcname=str(result_file.relative_to(home)))
    return archive


def _write_move(move: Move) -> None:
    """Rewrite only the usage counters, and estimated cost, matching runner.py."""
    for name in TOKEN_FIELDS:
        move.usage[name] = move.new_tokens[name]
    move.usage["total_tokens"] = move.new_total_tokens
    if move.estimated:
        move.usage["cost_usd"] = move.new_cost_usd
        move.usage["price"] = move.new_price
    # runner.py: write_text(json.dumps(result.to_dict(), indent=2)) -- no newline.
    move.path.write_text(json.dumps(move.raw, indent=2))


def reprice(
    home: Path,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> Summary:
    """Scan `$CONDUCTOR_HOME/runs` and report or rewrite parser-moved usage."""
    summary = Summary(apply=apply)
    planned: list[Move] = []
    for run_dir in _candidate_runs(home):
        summary.scanned += 1
        outcome = _plan_move(run_dir)
        if isinstance(outcome, str):
            summary.skipped[outcome] += 1
            continue
        planned.append(outcome)
    summary.moves = planned
    if apply and planned:
        archive = _archive_receipts(home, now=now or datetime.now(UTC))
        summary.archive = str(archive)
        for move in planned:
            _write_move(move)
    return summary


def _print_summary(summary: Summary) -> None:
    payload = summary.to_dict()
    action = "rewritten" if summary.apply else "would move"
    print(
        f"scanned {summary.scanned}  {action} {len(summary.moves)}"
        f"  skipped {summary.skipped_total}"
    )
    if summary.archive is not None:
        print(f"archive {summary.archive}")
    skip_rows = [
        (reason, str(count)) for reason, count in summary.skipped.items() if count
    ]
    _print_section("Skipped", ("reason", "count"), skip_rows)
    fleets = payload["fleets"]
    if not isinstance(fleets, dict):
        fleets = {}
    fleet_rows = [
        (
            name,
            str(row["receipts"]),
            str(row["input_tokens"]),
            str(row["output_tokens"]),
            str(row["cache_read_tokens"]),
            str(row["cache_write_tokens"]),
            str(row["thinking_tokens"]),
            f"{row['old_cost_usd']:.4f}",
            f"{row['new_cost_usd']:.4f}",
        )
        for name, row in fleets.items()
        if isinstance(row, dict)
    ]
    _print_section(
        "Per-fleet token deltas",
        (
            "fleet",
            "receipts",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "thinking_tokens",
            "old_cost_usd",
            "new_cost_usd",
        ),
        fleet_rows,
    )
    print(
        f"moved-set cost  {payload['old_cost_usd']:.4f} -> {payload['new_cost_usd']:.4f}"
    )
    print()
    largest = payload["largest"]
    if not isinstance(largest, list):
        largest = []
    largest_rows = [
        (
            str(row.get("run_id", "")),
            str(row.get("fleet", "")),
            str(row.get("input_tokens", "")),
            f"{row.get('old_cost_usd'):.4f}"
            if isinstance(row.get("old_cost_usd"), int | float)
            else "-",
            f"{row.get('new_cost_usd'):.4f}"
            if isinstance(row.get("new_cost_usd"), int | float)
            else "-",
            f"{row.get('delta_usd'):.4f}"
            if isinstance(row.get("delta_usd"), int | float)
            else "-",
        )
        for row in largest
        if isinstance(row, dict)
    ]
    _print_section(
        "Largest corrections",
        ("run_id", "fleet", "d_input", "old_cost_usd", "new_cost_usd", "delta_usd"),
        largest_rows,
    )


def cmd_reprice(args: argparse.Namespace) -> int:
    """Dry-run the ledger correction, or apply it after an explicit flag."""
    summary = reprice(conductor_home(), apply=bool(getattr(args, "apply", False)))
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        _print_summary(summary)
    return 0
