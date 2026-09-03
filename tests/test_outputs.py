"""Output normalization, against envelopes captured from real runs.

Every fixture below is a real fleet's stdout from a live dispatch on
2026-09-03, trimmed. Synthetic fixtures would only prove the parser matches
my assumption about each envelope; these prove it matches the envelope.
"""

from __future__ import annotations

from conductor.outputs import parse

CLAUDE = (
    '{"duration_api_ms":1730,"stop_reason":"end_turn","result":"PONG",'
    '"session_id":"a8ea8138","total_cost_usd":0.231398,'
    '"usage":{"input_tokens":2,"cache_creation_input_tokens":57836,'
    '"cache_read_input_tokens":0,"output_tokens":5}}'
)

CURSOR = (
    '{"type":"result","subtype":"success","is_error":false,"duration_ms":5972,'
    '"result":"PONG","session_id":"f510f063",'
    '"usage":{"inputTokens":23189,"outputTokens":106,"cacheReadTokens":0,'
    '"cacheWriteTokens":0}}'
)

ANTIGRAVITY = (
    '{"response":"PONG\\n","usage":{"input_tokens":14435,"output_tokens":2,'
    '"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":14437}}'
)

CODEX = "PONG"


def test_every_fleet_yields_the_same_answer():
    assert parse("claude", CLAUDE).answer == "PONG"
    assert parse("cursor", CURSOR).answer == "PONG"
    assert parse("antigravity", ANTIGRAVITY).answer == "PONG"
    assert parse("codex", CODEX).answer == "PONG"


def test_claude_cost_and_cache_write_are_captured():
    """The measured surprise worth keeping: a one-word headless reply cost
    $0.23 because the spawn wrote a fresh 57.8K-token prompt cache."""
    out = parse("claude", CLAUDE)
    assert out.usage.cost_usd == 0.231398
    assert out.usage.cache_write_tokens == 57836
    assert out.usage.output_tokens == 5


def test_camel_case_usage_is_normalized():
    out = parse("cursor", CURSOR)
    assert out.usage.input_tokens == 23189
    assert out.usage.output_tokens == 106
    assert out.usage.cost_usd is None  # Cursor reports no dollar figure


def test_snake_case_usage_is_normalized():
    out = parse("antigravity", ANTIGRAVITY)
    assert out.usage.input_tokens == 14435
    assert out.usage.total_tokens == 14437


def test_codex_bare_text_needs_no_envelope():
    out = parse("codex", CODEX)
    assert out.parsed is True
    assert out.usage is None


def test_streamed_events_take_the_last_object():
    stream = "\n".join(
        [
            '{"type":"assistant","text":"thinking out loud"}',
            '{"type":"result","result":"FINAL","usage":{"inputTokens":10,"outputTokens":2}}',
        ]
    )
    out = parse("cursor", stream)
    assert out.answer == "FINAL"
    assert out.usage.input_tokens == 10


def test_an_unrecognized_envelope_degrades_to_raw_text():
    """A fleet that changes its output shape must not crash a 3am run."""
    out = parse("cursor", "not json at all")
    assert out.parsed is False
    assert out.answer == "not json at all"
    assert out.usage is None


def test_empty_output_is_not_an_error():
    out = parse("claude", "   ")
    assert out.answer == ""
    assert out.parsed is False
