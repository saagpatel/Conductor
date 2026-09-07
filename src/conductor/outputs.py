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
import math
from dataclasses import asdict, dataclass, field


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
    session_id: str | None = None
    notes: list[str] = field(default_factory=list)
    # F12: Claude Code's `result.permission_denials` under `--permission-prompts
    # none` -- `{tool_name, tool_use_id, tool_input}` per denial, empty when the
    # envelope carries none (every other fleet, or a claude run with nothing
    # denied). The run still exits 0 with `subtype: success`, so this list is
    # the only signal.
    permission_denials: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "answer_chars": len(self.answer),
            "parsed": self.parsed,
            "status": self.status,
            "error": self.error,
            "session_id": self.session_id,
            "notes": self.notes,
            "permission_denials": self.permission_denials,
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
    if fleet == "script":
        # E6: unlike every other fleet, a script dispatch is priced (at
        # zero) whether or not it printed anything -- checked before the
        # generic empty-stdout short circuit below, which would otherwise
        # leave a silent command's usage as None (unpriced) instead of free.
        return _parse_script(text)
    if not text:
        return FleetOutput()
    if fleet == "codex":
        return _parse_codex(text)
    if fleet == "antigravity":
        return _parse_antigravity(text)
    if fleet == "cursor":
        return _parse_cursor(text)
    if fleet == "claude":
        return _parse_claude(text)
    if fleet == "script":
        return _parse_script(text)
    payload = _last_json_object(text)
    if payload is None:
        return FleetOutput(answer=text, parsed=False)
    return _parse_envelope(fleet, payload, text)


def _parse_script(text: str) -> FleetOutput:
    """A shell command reports no session id and no token usage; its cost
    is fixed at zero rather than estimated, whether or not it printed
    anything -- a command that prints nothing is exactly as free as one
    that prints a page (see budget.py's `free` state). Its whole stdout is
    its answer -- there is no separate "final message" to pick out the way
    a chat model's last reply is, just whatever the command printed."""
    return FleetOutput(answer=text, parsed=bool(text), usage=Usage(cost_usd=0.0))


def _parse_claude(text: str) -> FleetOutput:
    """Claude now streams NDJSON so breakers can see tool calls in flight.

    The final result event keeps the old envelope fields.  Stored transcripts
    from the earlier single-object and JSON-array formats remain readable;
    selecting a result event instead of merely the last object prevents a
    trailing non-result event from hiding the paid answer and usage.
    """
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        raw = [event for event in map(json_line, text.splitlines()) if event is not None]
    if isinstance(raw, list):
        events = [event for event in raw if isinstance(event, dict)]
        results = [event for event in events if event.get("type") == "result"]
        if not results and any(event.get("type") is not None for event in events):
            return _claude_cut_short(events, text)
        payload = (results or events or [None])[-1]
    elif isinstance(raw, dict) and raw.get("type") not in {None, "result"}:
        # A one-event stream is still a stream, not a legacy final envelope.
        # Treating one assistant event as an envelope promotes its raw JSON
        # to an answer and lets an exit-0 truncation look successful.
        return _claude_cut_short([raw], text)
    else:
        payload = raw if isinstance(raw, dict) else None
    if payload is None:
        return FleetOutput(answer=text, parsed=False)
    return _parse_envelope("claude", payload, text)


def _claude_cut_short(events: list[dict], text: str) -> FleetOutput:
    return FleetOutput(
        answer=claude_said(events),
        usage=claude_stream_usage(text),
        parsed=True,
        error="claude stream ended without a result event",
        session_id=_last_stream_id(text, "session_id"),
    )


def claude_init_event(text: str) -> dict | None:
    """The first `system`/`init` event of a Claude stream, carrying the
    session's real `agents` and `tools` lists.

    D3 reads this directly rather than through `parse`'s result envelope: a
    persona instruction can shape the model's first message and never reach
    the final answer at all (the probe's own trap --
    docs/research/2026-09-06-live-probe-inline-agents.md), so the init event
    is the only env-independent evidence that an inline agent was applied.
    """
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        raw = None
    events = (
        [event for event in raw if isinstance(event, dict)]
        if isinstance(raw, list)
        else [event for event in map(json_line, text.splitlines()) if event is not None]
    )
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def agy_init_event(text: str) -> dict | None:
    """The nested `init` object of an Antigravity stream's first `init`
    event, carrying the session's full `tools` list -- E21's only evidence,
    independent of the hooks.json file itself, of which tools a tainted
    dispatch actually had (mirrors `claude_init_event` above for D3).

    The event is `{"event": "init", "conversation_id": ..., "init": {"tools":
    [...], ...}}` -- the tool list sits under the nested `init` key, not at
    the event's own top level (confirmed against a recorded transcript,
    tests/golden/c5-review-fix/runs/20260905T182328Z-antigravity-.../
    stdout.jsonl line 1)."""
    for line in text.splitlines():
        event = json_line(line)
        if event is not None and event.get("event") == "init":
            inner = event.get("init")
            return inner if isinstance(inner, dict) else event
    return None


def claude_said(events: list[dict]) -> str:
    """Text Claude completed before a stream was cut short."""
    parts: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        identity = message.get("id") if isinstance(message, dict) else None
        identity = identity or event.get("uuid")
        if isinstance(identity, str) and identity:
            if identity in seen:
                continue
            seen.add(identity)
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"].strip())
    return "\n\n".join(part for part in parts if part)


def claude_stream_usage(text: str) -> Usage | None:
    """The per-message usage Claude exposed before its final result.

    Claude may repeat one assistant message in the stream. Its message id is
    the stable identity; summing lifecycle copies would overprice a breaker
    kill and turn progress reporting into a second billing bug.
    """
    messages: dict[str, dict] = {}
    anonymous: list[dict] = []
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        raw = None
    events = (
        [event for event in raw if isinstance(event, dict)]
        if isinstance(raw, list)
        else [event for event in map(json_line, text.splitlines()) if event is not None]
    )
    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            continue
        identity = message.get("id") or event.get("uuid")
        if isinstance(identity, str) and identity:
            messages[identity] = usage
        else:
            anonymous.append(usage)
    raw_messages = [*messages.values(), *anonymous]
    if not raw_messages:
        return None
    total: dict[str, int] = {}
    for raw in raw_messages:
        for key, value in raw.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] = total.get(key, 0) + int(value)
    return usage_from_raw("claude", total)


def _parse_antigravity(text: str) -> FleetOutput:
    """agy runs in stream-json mode (one event per line) so its per-step
    usage is visible while it works; the final envelope arrives wrapped as
    `{"event": "result", "result": {...}}`. A transcript with no result
    event was cut short, by conductor's kill or agy's own crash: there is no
    answer, but what the steps reported so far is still priced."""
    session_id = _last_stream_id(text, "conversation_id")
    payload = _last_json_object(text)
    if payload is None:
        return FleetOutput(answer=text, parsed=False)
    event = payload.get("event")
    if event == "result" and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    elif event is not None:
        # The last thing printed was a step, not the result: cut short.
        return FleetOutput(
            answer="", usage=agy_step_usage(text), parsed=True, session_id=session_id
        )
    out = _parse_envelope("antigravity", payload, text)
    out.session_id = session_id
    return out


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
    session_id = _last_stream_id(text, "session_id")
    last = events[-1]
    if last.get("type") == "result" or "result" in last or "is_error" in last:
        out = _parse_envelope("cursor", last, text)
        if said and not out.error:
            out.answer = said
        out.session_id = session_id
        return out
    for ev in reversed(events):
        if ev.get("is_error") is True or ev.get("type") == "error":
            if ev.get("type") == "error" and ev.get("is_error") is not True:
                return FleetOutput(
                    answer=said,
                    parsed=True,
                    status="error",
                    error=str(ev.get("error") or ev.get("message") or "cursor reported error"),
                    session_id=session_id,
                )
            out = _parse_envelope("cursor", ev, text)
            out.answer = said
            out.session_id = session_id
            return out
    return FleetOutput(
        answer=said,
        parsed=True,
        error="cursor stream ended without a result event",
        session_id=session_id,
    )


def _last_stream_id(text: str, key: str) -> str | None:
    """The last non-empty identity in a JSONL stream or wrapped result."""
    found: str | None = None
    for event in (ev for ev in map(json_line, text.splitlines()) if ev is not None):
        candidates = [event]
        if isinstance(event.get("result"), dict):
            candidates.append(event["result"])
        for candidate in candidates:
            value = candidate.get(key)
            if isinstance(value, str) and value:
                found = value
    return found


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

    notes: list[str] = []
    usage = _usage_from_envelope(fleet, payload, notes)
    # On a fleet-reported failure the text is the error, not an answer; it
    # belongs in `error`, not in answer.txt beside a result that is not ok.
    # An envelope with none of the known answer keys degrades to its raw
    # text; one whose answer key is empty said nothing, and reads as such.
    if error:
        answer = ""
    elif not recognized:
        answer = text
    raw_denials = payload.get("permission_denials")
    permission_denials = (
        [d for d in raw_denials if isinstance(d, dict)] if isinstance(raw_denials, list) else []
    )
    return FleetOutput(
        answer=answer,
        usage=usage,
        parsed=True,
        status=status,
        error=error,
        session_id=(
            payload.get("session_id")
            if fleet in {"claude", "cursor"}
            and isinstance(payload.get("session_id"), str)
            and payload.get("session_id")
            else None
        ),
        notes=notes,
        permission_denials=permission_denials,
    )


def _reported_cost(value: object, notes: list[str], field: str) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        notes.append(f"ignored {field}: reported cost must be a finite non-negative number")
        return None
    return float(value)


def _usage_from_envelope(fleet: str, payload: dict, notes: list[str]) -> Usage | None:
    raw = payload.get("usage")
    usage = usage_from_raw(fleet, raw, notes=notes) if isinstance(raw, dict) else None
    if "total_cost_usd" in payload:
        reported = _reported_cost(payload["total_cost_usd"], notes, "total_cost_usd")
        if reported is not None:
            usage = usage or Usage()
            usage.cost_usd = reported
            usage.cost_basis = "reported"
    return usage


def usage_from_raw(fleet: str, raw: dict, *, notes: list[str] | None = None) -> Usage:
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
    usage = Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        thinking_tokens=thinking,
    )
    note_list = notes if notes is not None else []
    for field_name in ("cost_usd", "total_cost_usd"):
        if field_name not in raw:
            continue
        reported = _reported_cost(raw[field_name], note_list, field_name)
        if reported is not None:
            usage.cost_usd = reported
            usage.cost_basis = "reported"
        break
    return usage


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
    session_id: str | None = None
    for ev in events:
        kind = ev.get("type")
        if kind == "thread.started" and isinstance(ev.get("thread_id"), str):
            session_id = ev["thread_id"] or None
        elif kind == "item.completed":
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
    if status != "turn.completed" and error is None:
        error = "codex stream ended without turn.completed"
    return FleetOutput(
        answer=answer,
        usage=usage,
        parsed=True,
        status=status,
        error=error,
        session_id=session_id,
    )


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
        if isinstance(obj, list):
            # Claude Code without user settings prints the whole event list
            # as one JSON array (measured 2026-09-03); the result event is
            # the envelope, wherever it sits.
            dicts = [item for item in obj if isinstance(item, dict)]
            results = [item for item in dicts if item.get("type") == "result"]
            return (results or dicts or [None])[-1]
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
