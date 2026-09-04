"""Output normalization, against envelopes captured from real runs.

Every fixture below is a real fleet's stdout from a live dispatch on
2026-09-03, trimmed. Synthetic fixtures would only prove the parser matches
my assumption about each envelope; these prove it matches the envelope.
"""

from __future__ import annotations

import json

import pytest

from conductor.outputs import parse

CLAUDE = (
    '{"duration_api_ms":1730,"stop_reason":"end_turn","result":"PONG",'
    '"session_id":"a8ea8138","total_cost_usd":0.231398,'
    '"usage":{"input_tokens":2,"cache_creation_input_tokens":57836,'
    '"cache_read_input_tokens":0,"output_tokens":5}}'
)

CLAUDE_STREAM_JSON = "\n".join(
    [
        '{"type":"system","subtype":"init","session_id":"stream-session"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}',
        '{"type":"user","message":{"content":[{"type":"tool_result","content":"done"}]}}',
        '{"type":"result","subtype":"success","is_error":false,"result":"PONG",'
        '"session_id":"stream-session","total_cost_usd":0.04,'
        '"structured_output":{"answer":"four"},'
        '"usage":{"input_tokens":7,"output_tokens":3}}',
        '{"type":"user","message":{"content":[{"type":"tool_result",'
        '"content":"cleanup complete"}]}}',
    ]
)

CURSOR = (
    '{"type":"result","subtype":"success","is_error":false,"duration_ms":5972,'
    '"result":"PONG","session_id":"f510f063",'
    '"usage":{"inputTokens":23189,"outputTokens":106,"cacheReadTokens":0,'
    '"cacheWriteTokens":0}}'
)

ANTIGRAVITY = (
    '{"conversation_id":"69a75137","response":"PONG\\n",'
    '"usage":{"input_tokens":14435,"output_tokens":2,'
    '"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":14437}}'
)

# agy with --print-timeout 8s on a 25s task: exit 1, and this on stdout.
ANTIGRAVITY_TIMEOUT = (
    '{"conversation_id":"69a75137","status":"ERROR","response":"",'
    '"error":"timeout waiting for response","duration_seconds":2.159818,'
    '"num_turns":1,"usage":{"input_tokens":13826,"output_tokens":310,'
    '"thinking_tokens":195,"cache_read_tokens":0,"total_tokens":14136}}'
)

# codex exec --json, one event per line.
CODEX_JSONL = "\n".join(
    [
        '{"type":"thread.started","thread_id":"01a06560-2a84-7522-9f95-6043c032fabd"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"PONG"}}',
        '{"type":"turn.completed","usage":{"input_tokens":22347,"cached_input_tokens":6912,'
        '"cache_write_input_tokens":0,"output_tokens":6,"reasoning_output_tokens":0}}',
    ]
)

CODEX_BARE = "PONG"


def test_every_fleet_yields_the_same_answer():
    assert parse("claude", CLAUDE).answer == "PONG"
    assert parse("cursor", CURSOR).answer == "PONG"
    assert parse("antigravity", ANTIGRAVITY).answer == "PONG"
    assert parse("codex", CODEX_JSONL).answer == "PONG"


@pytest.mark.parametrize(
    ("fleet", "stream", "session_id"),
    [
        ("claude", CLAUDE, "a8ea8138"),
        ("codex", CODEX_JSONL, "01a06560-2a84-7522-9f95-6043c032fabd"),
        ("antigravity", ANTIGRAVITY, "69a75137"),
        ("cursor", CURSOR, "f510f063"),
    ],
)
def test_every_fleet_parser_yields_its_session_id(fleet, stream, session_id):
    assert parse(fleet, stream).session_id == session_id


def test_claude_cost_and_cache_write_are_captured():
    """The measured surprise worth keeping: a one-word headless reply cost
    $0.23 because the spawn wrote a fresh 57.8K-token prompt cache."""
    out = parse("claude", CLAUDE)
    assert out.usage.cost_usd == 0.231398
    assert out.usage.cost_basis == "reported"
    assert out.usage.cache_write_tokens == 57836
    assert out.usage.output_tokens == 5


def test_claude_stream_json_takes_the_last_result_with_all_final_fields():
    out = parse("claude", CLAUDE_STREAM_JSON)
    assert out.answer == '{\n  "answer": "four"\n}'
    assert out.session_id == "stream-session"
    assert out.usage is not None and out.usage.input_tokens == 7
    assert out.usage.output_tokens == 3 and out.usage.cost_usd == 0.04


def test_a_claude_stream_without_result_fails_closed_and_keeps_partial_usage():
    stream = "\n".join(
        [
            '{"type":"system","subtype":"init","session_id":"stream-session"}',
            '{"type":"assistant","session_id":"stream-session","message":{'
            '"id":"message-1","usage":{"input_tokens":2,"output_tokens":3,'
            '"cache_creation_input_tokens":100},"content":[{"type":"tool_use",'
            '"id":"tool-1","name":"Read","input":{"path":"README.md"}}]}}',
            '{"type":"user","session_id":"stream-session","message":{'
            '"content":[{"type":"tool_result","tool_use_id":"tool-1"}]}}',
        ]
    )
    out = parse("claude", stream)
    assert out.answer == ""
    assert out.error == "claude stream ended without a result event"
    assert out.session_id == "stream-session"
    assert out.usage is not None and out.usage.input_tokens == 2
    assert out.usage.output_tokens == 3 and out.usage.cache_write_tokens == 100


def test_one_claude_assistant_event_is_a_cut_short_stream_not_an_envelope():
    stream = (
        '{"type":"assistant","session_id":"stream-session","message":{'
        '"id":"message-1","usage":{"input_tokens":2,"output_tokens":3},'
        '"content":[{"type":"text","text":"partial"}]}}'
    )
    out = parse("claude", stream)
    assert out.answer == "partial"
    assert out.error == "claude stream ended without a result event"
    assert out.usage is not None and out.usage.total_tokens == 5


def test_camel_case_usage_is_normalized():
    out = parse("cursor", CURSOR)
    assert out.usage.input_tokens == 23189
    assert out.usage.output_tokens == 106
    assert out.usage.cost_usd is None  # Cursor reports no dollar figure
    assert out.usage.cost_basis is None


def test_cursor_cache_reads_are_captured_and_split_out_of_input():
    env = (
        '{"type":"result","result":"ok","session_id":"cursor-s",'
        '"usage":{"inputTokens":1000,"outputTokens":10,"cacheReadTokens":400}}'
    )
    out = parse("cursor", env)
    assert out.usage.input_tokens == 600
    assert out.usage.cache_read_tokens == 400


def test_snake_case_usage_is_normalized():
    out = parse("antigravity", ANTIGRAVITY)
    assert out.usage.input_tokens == 14435
    assert out.usage.total_tokens == 14437


def test_codex_event_stream_yields_answer_and_usage():
    """Codex reports usage only in its event stream. Cached input is counted
    inside input_tokens by OpenAI, so it is split out here."""
    out = parse("codex", CODEX_JSONL)
    assert out.parsed is True
    assert out.status == "turn.completed"
    assert out.error is None
    assert out.usage.input_tokens == 22347 - 6912
    assert out.usage.cache_read_tokens == 6912
    assert out.usage.output_tokens == 6
    assert out.usage.total_tokens == 22347 + 6


def test_codex_failure_events_are_surfaced():
    stream = "\n".join(
        [
            '{"type":"turn.started"}',
            '{"type":"error","message":"stream disconnected before completion"}',
        ]
    )
    out = parse("codex", stream)
    assert out.error == "stream disconnected before completion"
    assert out.status == "error"


def test_a_codex_error_followed_by_a_completed_turn_is_a_recovery():
    """Codex reports retryable stream failures as `error` events and then
    carries on; a run that recovered is not a failed run."""
    stream = "\n".join(
        [
            '{"type":"turn.started"}',
            '{"type":"error","message":"stream disconnected, retrying"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"PONG"}}',
            '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":1}}',
        ]
    )
    out = parse("codex", stream)
    assert out.error is None and out.status == "turn.completed" and out.answer == "PONG"


def test_a_codex_stream_without_turn_completed_fails_closed_but_keeps_the_answer():
    stream = '\n'.join(
        [
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"partial"}}',
        ]
    )
    out = parse("codex", stream)
    assert out.answer == "partial"
    assert out.error == "codex stream ended without turn.completed"


def test_codex_bare_text_still_degrades_to_an_answer():
    """An older binary without --json, or a crash before any event."""
    out = parse("codex", CODEX_BARE)
    assert out.answer == "PONG"
    assert out.parsed is False
    assert out.usage is None


def test_antigravity_timeout_is_an_error_not_an_empty_success():
    """agy's print timeout exits 1 with status ERROR. The status must reach
    the result even if a future version exits 0."""
    out = parse("antigravity", ANTIGRAVITY_TIMEOUT)
    assert out.status == "ERROR"
    assert out.error == "timeout waiting for response"
    assert out.answer == ""
    # Thinking tokens are billed as output by Google; they fold in.
    assert out.usage.output_tokens == 310 + 195
    assert out.usage.thinking_tokens == 195


def test_antigravity_cache_reads_are_split_out_of_input():
    env = (
        '{"status":"SUCCESS","response":"ok","usage":{"input_tokens":1000,'
        '"output_tokens":10,"thinking_tokens":0,"cache_read_tokens":400}}'
    )
    out = parse("antigravity", env)
    assert out.usage.input_tokens == 600
    assert out.usage.cache_read_tokens == 400


def test_cursor_is_error_is_surfaced():
    env = (
        '{"type":"result","subtype":"error_during_execution","is_error":true,'
        '"result":"Workspace trust required","usage":{"inputTokens":1,"outputTokens":1}}'
    )
    out = parse("cursor", env)
    assert out.error == "Workspace trust required"
    assert out.status == "error_during_execution"
    # The error text is not an answer; it must not land in answer.txt.
    assert out.answer == ""


def test_claude_structured_output_becomes_the_answer():
    env = (
        '{"type":"result","subtype":"success","is_error":false,"result":"",'
        '"structured_output":{"answer":"4","confidence":1},"total_cost_usd":0.01,'
        '"usage":{"input_tokens":3,"output_tokens":9}}'
    )
    out = parse("claude", env)
    assert '"answer": "4"' in out.answer
    assert out.error is None


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


def test_claude_event_list_envelope_is_read_from_its_result_event():
    """Without user settings Claude Code prints the event list as one JSON
    array (measured 2026-09-03); the answer and price are in the result event."""
    text = json.dumps(
        [
            {"type": "system", "subtype": "init", "session_id": "earlier"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "OK"}]}},
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "OK",
                "session_id": "result-wins",
                "total_cost_usd": 0.0513,
                "usage": {"input_tokens": 2, "output_tokens": 4, "cache_read_input_tokens": 24096},
            },
        ]
    )
    out = parse("claude", text)
    assert out.answer == "OK" and out.status == "success" and out.error is None
    assert out.session_id == "result-wins"
    assert out.usage is not None and out.usage.cost_usd == 0.0513
    assert out.usage.cache_read_tokens == 24096


@pytest.mark.parametrize(
    ("fleet", "key"), [("antigravity", "conversation_id"), ("cursor", "session_id")]
)
def test_stream_parsers_take_the_last_non_empty_session_id(fleet, key):
    stream = "\n".join(
        [
            json.dumps({key: "first", "type": "assistant"}),
            json.dumps({key: "", "type": "assistant"}),
            json.dumps(
                {
                    key: "last",
                    "type": "result",
                    "status": "SUCCESS",
                    "response": "ok",
                    "result": "ok",
                }
            ),
        ]
    )
    assert parse(fleet, stream).session_id == "last"


@pytest.mark.parametrize(
    ("fleet", "key"), [("antigravity", "conversation_id"), ("cursor", "session_id")]
)
def test_an_empty_stream_session_id_is_none(fleet, key):
    payload = {key: "", "type": "result", "status": "SUCCESS", "result": "ok"}
    assert parse(fleet, json.dumps(payload)).session_id is None


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -0.1, True, "1.25"])
def test_reported_costs_must_be_finite_non_negative_numbers(cost):
    text = json.dumps(
        {
            "result": "PONG",
            "total_cost_usd": cost,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    out = parse("claude", text)
    assert out.usage is not None and out.usage.cost_usd is None
    assert out.notes == [
        "ignored total_cost_usd: reported cost must be a finite non-negative number"
    ]
