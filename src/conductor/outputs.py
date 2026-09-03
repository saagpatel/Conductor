"""Normalize each fleet's final output into one shape.

Every fleet reports its answer, its usage, and its own failures differently:
Claude Code and Cursor put the text under `result` and flag failure with
`is_error`; Antigravity puts it under `response` with a `status` of SUCCESS or
ERROR; Codex streams one JSON event per line and never prints a single final
envelope at all. Token fields disagree on casing, on which cache counters
exist, and on whether a cache read is counted inside `input_tokens` or beside
it.

Normalizing here means the orchestrator reads one schema regardless of who did
the work, and means per-dispatch cost is recorded while the evidence is still
in hand. That matters more than it looks: a one-word reply on the claude fleet
measured $0.23 because a headless run writes a fresh prompt cache every spawn,
while the same reply on antigravity moved ~14K input tokens. Startup overhead,
not the work, dominates short dispatches.

Token convention after normalization, which prices.py relies on:
  * `input_tokens` is uncached input only; `cache_read_tokens` sits beside it.
    Anthropic reports it that way natively. OpenAI (`cached_input_tokens`)
    and Google (`cache_read_tokens`) count cached tokens inside the input
    figure, so those are subtracted out. Cursor's `cacheReadTokens` is
    assumed to follow the same inside-the-input convention; every live run
    so far reported zero, so the assumption has not yet cost anything.
  * `output_tokens` includes reasoning. OpenAI counts reasoning inside
    `output_tokens` and reports the reasoning share separately; Antigravity
    reports `thinking_tokens` beside `output_tokens` and Google bills them at
    the output rate, so they are added in. `thinking_tokens` is kept as an
    informational breakdown in both cases.
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
    thinking_tokens: int = 0
    cost_usd: float | None = None
    # "reported" when the fleet printed a dollar figure, "estimated" when
    # conductor priced the tokens itself, None when neither was possible.
    cost_basis: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total_tokens"] = self.total_tokens
        return d

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
    """What a fleet actually said, what it cost to say it, and whether the
    fleet itself thinks the run failed."""

    answer: str = ""
    usage: Usage | None = None
    parsed: bool = False
    status: str | None = None  # the fleet's own status word, when it has one
    error: str | None = None  # the fleet's own failure message, when it has one

    def to_dict(self) -> dict:
        return {
            "answer_chars": len(self.answer),
            "parsed": self.parsed,
            "status": self.status,
            "error": self.error,
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
    if fleet == "codex":
        return _parse_codex(text)
    if fleet == "antigravity":
        return _parse_antigravity(text)
    if fleet == "cursor":
        return _parse_cursor(text)
    payload = _last_json_object(text)
    if payload is None:
        return FleetOutput(answer=text, parsed=False)
    return _parse_envelope(fleet, payload, text)


def _parse_antigravity(text: str) -> FleetOutput:
    """agy runs in stream-json mode (one event per line) so its per-step
    usage is visible while it works; the final envelope arrives wrapped as
    `{"event": "result", "result": {...}}`. A transcript with no result
    event was cut short, by conductor's kill or agy's own crash: there is no
    answer, but what the steps reported so far is still priced."""
    payload = _last_json_object(text)
    if payload is None:
        return FleetOutput(answer=text, parsed=False)
    event = payload.get("event")
    if event == "result" and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    elif event is not None:
        # The last thing printed was a step, not the result: cut short.
        return FleetOutput(answer="", usage=agy_step_usage(text), parsed=True)
    return _parse_envelope("antigravity", payload, text)


def _parse_cursor(text: str) -> FleetOutput:
    """cursor-agent in stream-json mode: `assistant` events carry each message
    the model said, and a final `result` event carries the envelope (usage,
    is_error, and only the last message as `result`). The answer is every
    assistant message joined, so nothing said before a closing remark is
    lost. A lone envelope (the older json format) still parses."""
    events = [ev for ev in map(json_line, text.splitlines()) if ev is not None]
    if not events:
        return FleetOutput(answer=text, parsed=False)
    said = cursor_said(events)
    last = events[-1]
    if last.get("type") == "result" or "result" in last or "is_error" in last:
        out = _parse_envelope("cursor", last, text)
        if said and not out.error:
            out.answer = said
        return out
    return FleetOutput(answer=said, parsed=True)


def cursor_said(events: list[dict]) -> str:
    """Every text block from every assistant event, in order, plus any plan
    the agent filed: in plan mode Cursor delivers its real answer through a
    `createPlan` tool call and says only "auditing..." out loud (a full
    four-finding audit went unread that way, live 2026-09-03)."""
    parts: list[str] = []
    for ev in events:
        if ev.get("type") == "tool_call" and ev.get("subtype") == "completed":
            call = (ev.get("tool_call") or {}).get("createPlanToolCall") or {}
            plan = (call.get("args") or {}).get("plan")
            if isinstance(plan, str) and plan.strip():
                parts.append(plan.strip())
            continue
        if ev.get("type") != "assistant":
            continue
        content = (ev.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"].strip())
    return "\n\n".join(p for p in parts if p)


def agy_step_usage(text: str) -> Usage | None:
    """Running usage from an agy stream-json transcript: the last figure each
    step reported, summed. Steps report their own usage, not a running
    total (three steps of 14.5K, 14.7K, and 15.1K input summed to exactly
    the 44.4K the final result reported; measured 2026-09-03)."""
    per_step: dict[object, dict] = {}
    for line in text.splitlines():
        ev = json_line(line)
        if ev is None or ev.get("event") != "step_update":
            continue
        step = ev.get("step_update")
        if isinstance(step, dict) and isinstance(step.get("usage"), dict):
            per_step[step.get("step_index")] = step["usage"]
    if not per_step:
        return None
    total: dict[str, int] = {}
    for raw in per_step.values():
        for key, value in raw.items():
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + int(value)
    return usage_from_raw("antigravity", total)


# --- single-envelope fleets: claude, cursor, antigravity --------------------


def _parse_envelope(fleet: str, payload: dict, text: str) -> FleetOutput:
    answer = ""
    recognized = False
    structured = payload.get("structured_output")
    if isinstance(structured, (dict, list)):
        # Claude Code with --json-schema: the validated object lives here and
        # `result` may be empty or a prose restatement.
        answer = json.dumps(structured, indent=2)
        recognized = True
    else:
        for key in ("result", "response", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                answer = value.strip()
                recognized = True
                break

    status: str | None = None
    error: str | None = None
    if fleet == "antigravity":
        raw_status = payload.get("status")
        status = str(raw_status) if raw_status is not None else None
        if status and status.upper() != "SUCCESS":
            error = str(payload.get("error") or f"antigravity reported status {status}")
    else:
        subtype = payload.get("subtype")
        status = str(subtype) if subtype is not None else None
        if payload.get("is_error") is True:
            # Claude Code puts the reason in an `errors` list and, on a budget
            # stop, sends no `result` text at all (measured 2026-09-03).
            errors = payload.get("errors")
            listed = "; ".join(str(e) for e in errors) if isinstance(errors, list) else ""
            fallback = str(payload.get("error") or subtype or "fleet reported is_error")
            error = answer or listed or fallback

    usage = _usage_from_envelope(fleet, payload)
    # On a fleet-reported failure the text is the error, not an answer; it
    # belongs in `error`, not in answer.txt beside a result that is not ok.
    # An envelope with none of the known answer keys degrades to its raw
    # text; one whose answer key is empty said nothing, and reads as such.
    if error:
        answer = ""
    elif not recognized:
        answer = text
    return FleetOutput(
        answer=answer,
        usage=usage,
        parsed=True,
        status=status,
        error=error,
    )


def _usage_from_envelope(fleet: str, payload: dict) -> Usage | None:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return None
    usage = usage_from_raw(fleet, raw)
    reported = payload.get("total_cost_usd")
    if isinstance(reported, (int, float)):
        usage.cost_usd = float(reported)
        usage.cost_basis = "reported"
    return usage


def usage_from_raw(fleet: str, raw: dict) -> Usage:
    """One fleet's raw usage object, normalized to the convention above."""
    input_tokens = _int(raw, "input_tokens", "inputTokens")
    output_tokens = _int(raw, "output_tokens", "outputTokens")
    cache_read = _int(raw, "cache_read_input_tokens", "cache_read_tokens", "cacheReadTokens")
    cache_write = _int(raw, "cache_creation_input_tokens", "cache_write_tokens", "cacheWriteTokens")
    thinking = _int(raw, "thinking_tokens", "reasoning_tokens", "reasoningTokens")

    if fleet == "antigravity":
        # Google counts cached tokens inside the prompt count and bills
        # thinking as output.
        input_tokens = max(0, input_tokens - cache_read)
        output_tokens += thinking
    elif fleet == "cursor":
        input_tokens = max(0, input_tokens - cache_read)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        thinking_tokens=thinking,
    )


# --- codex: one JSON event per line -----------------------------------------


def _parse_codex(text: str) -> FleetOutput:
    """`codex exec --json` streams events. The answer is the last completed
    agent_message item; usage arrives on turn.completed; failures arrive as
    `error` or `turn.failed` events. With no parseable event at all (an older
    binary, or --json dropped), the raw text is the answer."""
    events = [ev for ev in map(json_line, text.splitlines()) if ev is not None]
    if not events:
        return FleetOutput(answer=text, parsed=False)

    answer = ""
    usage: Usage | None = None
    status: str | None = None
    error: str | None = None
    for ev in events:
        kind = ev.get("type")
        if kind == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                answer = item["text"].strip()
        elif kind == "turn.completed":
            # A completed turn supersedes any earlier error event: Codex
            # reports retryable stream failures as `error` and then carries
            # on, and a run that recovered is not a failed run.
            status = "turn.completed"
            error = None
            raw = ev.get("usage")
            if isinstance(raw, dict):
                usage = usage_from_codex(raw)
        elif kind in ("turn.failed", "error"):
            status = kind
            err = ev.get("error")
            if isinstance(err, dict):
                error = str(err.get("message") or err)
            else:
                error = str(err or ev.get("message") or kind)
    return FleetOutput(answer=answer, usage=usage, parsed=True, status=status, error=error)


def usage_from_codex(raw: dict) -> Usage:
    total_input = _int(raw, "input_tokens")
    cached = _int(raw, "cached_input_tokens")
    return Usage(
        input_tokens=max(0, total_input - cached),
        output_tokens=_int(raw, "output_tokens"),
        cache_read_tokens=cached,
        cache_write_tokens=_int(raw, "cache_write_input_tokens"),
        thinking_tokens=_int(raw, "reasoning_output_tokens"),
    )


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
        obj = json_line(line)
        if obj is not None:
            return obj
    return None


def json_line(line: str) -> dict | None:
    """One line of a JSONL stream as a dict, or None for anything else."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
