"""Normalize each fleet's final output into one shape.

Every fleet reports its answer and its usage differently: Claude Code and
Cursor put the text under `result`, Antigravity under `response`, and Codex
prints bare text with no envelope at all. Token fields disagree on casing and
on which cache counters exist.

Normalizing here means the orchestrator reads one schema regardless of who did
the work, and means per-dispatch cost is recorded while the evidence is still
in hand. That matters more than it looks: a one-word reply on the claude fleet
measured $0.23 because a headless run writes a fresh prompt cache every spawn,
while the same reply on antigravity moved ~14K input tokens. Startup overhead,
not the work, dominates short dispatches.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


@dataclass
class FleetOutput:
    """What a fleet actually said, plus what it cost to say it."""

    answer: str = ""
    usage: Usage | None = None
    parsed: bool = False

    def to_dict(self) -> dict:
        return {
            "answer_chars": len(self.answer),
            "parsed": self.parsed,
            "usage": self.usage.to_dict() if self.usage else None,
        }


def _int(d: dict, *keys: str) -> int:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def parse(fleet: str, stdout: str) -> FleetOutput:
    """Best-effort: a fleet that changes its envelope must degrade to the raw
    text, never to an exception in the middle of an unattended run."""
    text = stdout.strip()
    if not text:
        return FleetOutput()

    # Codex prints the final message as bare text (conductor also asks it to
    # write that message to a file via -o).
    if fleet == "codex":
        return FleetOutput(answer=text, parsed=True)

    payload = _last_json_object(text)
    if payload is None:
        return FleetOutput(answer=text, parsed=False)

    answer = ""
    for key in ("result", "response", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            answer = value.strip()
            break

    raw_usage = payload.get("usage")
    usage = None
    if isinstance(raw_usage, dict):
        usage = Usage(
            input_tokens=_int(raw_usage, "input_tokens", "inputTokens"),
            output_tokens=_int(raw_usage, "output_tokens", "outputTokens"),
            cache_read_tokens=_int(
                raw_usage, "cache_read_input_tokens", "cache_read_tokens", "cacheReadTokens"
            ),
            cache_write_tokens=_int(
                raw_usage,
                "cache_creation_input_tokens",
                "cache_write_tokens",
                "cacheWriteTokens",
            ),
            cost_usd=(
                float(payload["total_cost_usd"])
                if isinstance(payload.get("total_cost_usd"), (int, float))
                else None
            ),
        )

    return FleetOutput(answer=answer or text, usage=usage, parsed=True)


def _last_json_object(text: str) -> dict | None:
    """Fleets that stream events emit one JSON object per line and put the
    result last; fleets that don't emit a single object. Take the last object
    that parses, so both shapes work."""
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
