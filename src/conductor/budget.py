"""Per-dispatch dollar caps.

Only Claude Code can cap its own spend (`--max-budget-usd`; it stops with
`subtype: error_max_budget_usd`). No other fleet has a budget flag, so for
them conductor watches whatever running usage the fleet leaks while it works
and kills the process group the moment the estimate crosses the cap:

  * Codex prints usage on stdout only once, at the end (`turn.completed`),
    but appends a `token_count` event with cumulative totals to its session
    rollout file (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl`)
    after every model response: 52 of them across one 12-minute run,
    measured 2026-09-03. The thread id is the first event on stdout.
  * Antigravity in `stream-json` mode prints a `step_update` carrying that
    step's own usage after every model response; the running total is the
    sum of the last figure each step reported.
  * Cursor reports usage once, in its final result. Its cap can only be
    checked after the fact, and the result says so.
  * Claude reports per-message usage in assistant stream events. Its native
    dollar cap remains authoritative; conductor tails those messages only so
    a breaker-killed run still has a best available price.

The check runs on conductor's own wait loop every `POLL_S` seconds, so a
watched fleet overshoots by at most one model response plus one poll. The
same tail prices a run that conductor killed for any reason: without it a
timed-out Codex dispatch would land in the ledger as `cost_usd: null`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import prices
from .outputs import Usage, agy_step_usage, claude_stream_usage, json_line, usage_from_codex

POLL_S = 2.0


@dataclass
class Budget:
    """One dispatch's cap and what became of it."""

    cap_usd: float | None
    enforcement: str  # "native" | "watcher" | "post-hoc" | "none", from the fleet registry
    exceeded: bool = False
    unpriced: bool = False  # a cap was set and no figure ever arrived: unenforced
    # E6: a script dispatch's own budget block -- priced at zero and
    # verified, never unpriced, and carrying no cap to enforce. False on
    # every other fleet, whether or not a cap_usd was ever set.
    free: bool = False
    observed_usd: float | None = None  # the figure the verdict was based on
    # E24: cap_grace_usd from the dispatch's own Spec, carried here so
    # settle() has it without a second argument; None means no grace band.
    grace_usd: float | None = None
    # E24: how much of the grace band a finished run actually drew on.
    # Zero when the cost never left cap_usd; capped at grace_usd itself
    # once the cost clears the whole band (the band cannot absorb more than
    # it holds); None when no grace was set or the cost is unknown.
    grace_used: float | None = None

    def to_dict(self) -> dict:
        # E24: grace_usd/grace_used are omitted, not written null, on a
        # dispatch that never set a grace band -- every receipt written
        # before this field existed stays byte-identical in shape.
        data = asdict(self)
        if self.grace_usd is None:
            del data["grace_usd"]
            del data["grace_used"]
        return data

    def settle(
        self,
        cost_usd: float | None,
        *,
        killed: bool,
        fleet_status: str | None,
        interrupted: bool = False,
    ) -> None:
        """The verdict, once the run is over and priced.

        A fleet that stopped itself on its own budget flag is over the cap
        whatever its reported figure says; so is a run the watcher killed.
        A run that comes back with no figure at all was never capped by
        anything, and must not read as within budget; it is flagged
        `unpriced` and the runner fails it closed. A run conductor
        interrupted was stopped by something other than its cap; it is not
        over budget, and coming back unpriced is no evidence about the cap
        either way.

        E24: with `grace_usd` set, the real ceiling is `cap_usd + grace_usd`
        -- a run that finishes inside that band is not over budget and is
        not failed. The native stop (`error_max_budget_usd`) already fires at
        that same combined figure (fleets.build_argv folds grace into the
        one flag Claude Code takes), so it still means over budget here.
        """
        self.observed_usd = cost_usd
        self.unpriced = cost_usd is None and not killed and not interrupted
        ceiling = (
            self.cap_usd
            if self.grace_usd is None or self.cap_usd is None
            else self.cap_usd + self.grace_usd
        )
        self.exceeded = (
            killed
            or fleet_status == "error_max_budget_usd"
            or (cost_usd is not None and ceiling is not None and cost_usd > ceiling)
        )
        if self.grace_usd is None or cost_usd is None:
            self.grace_used = None
        else:
            self.grace_used = min(max(0.0, cost_usd - self.cap_usd), self.grace_usd)


def codex_sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "sessions"


class Watcher:
    """Follows one running dispatch's usage; says when it crosses the cap.

    Built for every codex, antigravity, and Claude dispatch, cap or not,
    because the usage it collects is also the only price a killed run can
    get. Claude's watcher never enforces its cap: the CLI's native dollar cap
    remains authoritative. Cursor leaks nothing mid-run, so its poll() is
    always None.
    """

    def __init__(
        self,
        fleet: str,
        model_id: str,
        stdout_path: Path,
        cap_usd: float | None,
        table: dict[str, prices.Price] | None = None,
    ) -> None:
        self.cap_usd = cap_usd
        self.model_id = model_id
        self.table = table if table is not None else prices.load_prices()
        self.usage: Usage | None = None
        self._source: _CodexRollout | _AgySteps | _ClaudeMessages | None
        if fleet == "codex":
            self._source = _CodexRollout(stdout_path, since=datetime.now(UTC))
        elif fleet == "antigravity":
            self._source = _AgySteps(stdout_path)
        elif fleet == "claude":
            self._source = _ClaudeMessages(stdout_path)
        else:
            self._source = None

    def poll(self) -> Usage | None:
        """The latest running usage, priced, or None if nothing has landed."""
        if self._source is None:
            return None
        usage = self._source.poll()
        if usage is not None:
            usage.cost_usd = prices.estimate(
                self.model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                table=self.table,
            )
            usage.cost_basis = "estimated" if usage.cost_usd is not None else None
            self.usage = usage
        return self.usage

    def over_cap(self) -> bool:
        if self.cap_usd is None:
            return False
        usage = self.poll()
        return usage is not None and usage.cost_usd is not None and usage.cost_usd > self.cap_usd


class _Tail:
    """Incremental line reader over a file another process is appending to."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._offset = 0
        self._partial = b""

    def lines(self) -> list[str]:
        try:
            with self.path.open("rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
        except OSError:
            return []
        if not chunk:
            return []
        self._offset += len(chunk)
        parts = (self._partial + chunk).split(b"\n")
        self._partial = parts.pop()
        return [p.decode(errors="replace") for p in parts]


_CODEX_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


class _CodexRollout:
    """Cumulative usage from the session rollout Codex writes as it works.

    The totals are cumulative over the whole thread, so a resumed thread's
    rollout opens with everything the earlier dispatch already paid for.
    Counting that again tripped a $5 fix lane two seconds in with the $7.93
    its build lane had spent (2026-09-04). Every token_count stamped before
    `since` (this dispatch's spawn) is the baseline; the run's own usage is
    the cumulative figure less that baseline.
    """

    def __init__(self, stdout_path: Path, *, since: datetime | None = None) -> None:
        self._stdout = _Tail(stdout_path)
        self._thread_id: str | None = None
        self._rollout: _Tail | None = None
        self._since = since
        self._baseline: dict | None = None
        self.usage: Usage | None = None

    def poll(self) -> Usage | None:
        if self._rollout is None:
            self._locate()
            if self._rollout is None:
                return self.usage
        for line in self._rollout.lines():
            ev = json_line(line)
            payload = (ev or {}).get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            total = (payload.get("info") or {}).get("total_token_usage")
            if not isinstance(total, dict):
                continue
            if self._before_spawn((ev or {}).get("timestamp")):
                self._baseline = total
                continue
            self.usage = usage_from_codex(self._less_baseline(total))
        return self.usage

    def _before_spawn(self, stamp: object) -> bool:
        if self._since is None or not isinstance(stamp, str):
            return False  # an unstamped line cannot be placed; count it
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when < self._since

    def _less_baseline(self, total: dict) -> dict:
        if not self._baseline:
            return total
        return {
            key: max(0, int(total.get(key) or 0) - int(self._baseline.get(key) or 0))
            for key in _CODEX_USAGE_KEYS
        }

    def _locate(self) -> None:
        if self._thread_id is None:
            for line in self._stdout.lines():
                ev = json_line(line)
                if ev and ev.get("type") == "thread.started" and ev.get("thread_id"):
                    self._thread_id = str(ev["thread_id"])
                    break
        if self._thread_id is None:
            return
        # Day-bucketed by local date; the file may appear a beat after the
        # thread.started event, so a miss here is retried on the next poll.
        hits = sorted(codex_sessions_dir().glob(f"*/*/*/rollout-*-{self._thread_id}.jsonl"))
        if hits:
            self._rollout = _Tail(hits[-1])


class _AgySteps:
    """Running usage from agy's stream-json step updates."""

    def __init__(self, stdout_path: Path) -> None:
        self._tail = _Tail(stdout_path)
        self._seen: list[str] = []
        self.usage: Usage | None = None

    def poll(self) -> Usage | None:
        new = self._tail.lines()
        if new:
            self._seen.extend(new)
            self.usage = agy_step_usage("\n".join(self._seen))
        return self.usage


class _ClaudeMessages:
    """Running usage from Claude assistant events before its final result."""

    def __init__(self, stdout_path: Path) -> None:
        self._tail = _Tail(stdout_path)
        self._seen: list[str] = []
        self.usage: Usage | None = None

    def poll(self) -> Usage | None:
        new = self._tail.lines()
        if new:
            self._seen.extend(new)
            self.usage = claude_stream_usage("\n".join(self._seen))
        return self.usage
