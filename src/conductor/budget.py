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

The check runs on conductor's own wait loop every `POLL_S` seconds, so a
watched fleet overshoots by at most one model response plus one poll. The
same tail prices a run that conductor killed for any reason: without it a
timed-out Codex dispatch would land in the ledger as `cost_usd: null`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import prices
from .outputs import Usage, agy_step_usage, json_line, usage_from_codex

POLL_S = 2.0


@dataclass
class Budget:
    """One dispatch's cap and what became of it."""

    cap_usd: float
    enforcement: str  # "native" | "watcher" | "post-hoc", from the fleet registry
    exceeded: bool = False
    unpriced: bool = False  # a cap was set and no figure ever arrived: unenforced
    observed_usd: float | None = None  # the figure the verdict was based on

    def to_dict(self) -> dict:
        return asdict(self)

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
        """
        self.observed_usd = cost_usd
        self.unpriced = cost_usd is None and not killed and not interrupted
        self.exceeded = (
            killed
            or fleet_status == "error_max_budget_usd"
            or (cost_usd is not None and cost_usd > self.cap_usd)
        )


def codex_sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "sessions"


class Watcher:
    """Follows one running dispatch's usage; says when it crosses the cap.

    Built for every codex and antigravity dispatch, cap or not, because the
    usage it collects is also the only price a killed run can get. With no
    cap it never fires. Other fleets leak nothing mid-run; for them poll()
    is always None.
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
        self._source: _CodexRollout | _AgySteps | None
        if fleet == "codex":
            self._source = _CodexRollout(stdout_path)
        elif fleet == "antigravity":
            self._source = _AgySteps(stdout_path)
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


class _CodexRollout:
    """Cumulative usage from the session rollout Codex writes as it works."""

    def __init__(self, stdout_path: Path) -> None:
        self._stdout = _Tail(stdout_path)
        self._thread_id: str | None = None
        self._rollout: _Tail | None = None
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
            if isinstance(total, dict):
                self.usage = usage_from_codex(total)
        return self.usage

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
