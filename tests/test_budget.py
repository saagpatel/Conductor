"""Per-dispatch dollar caps.

The failure these prevent: one runaway dispatch on a fleet with no budget
flag spending the week's budget before the wall-clock timeout notices. Each
fleet is capped the way it allows, and a cap conductor cannot enforce is
refused rather than silently dropped.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from conductor.budget import _Tail
from conductor.fleets import DispatchRefused, Spec, build_argv
from conductor.outputs import parse
from conductor.runner import dispatch


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test cap", cwd=str(repo))
    base.update(kw)
    return Spec(**base)


# --- claude: the fleet caps itself ------------------------------------------


def test_claude_gets_its_own_budget_flag():
    argv = build_argv(Spec(fleet="claude", prompt="x", cwd="/tmp", cap_usd=0.25))
    assert argv[argv.index("--max-budget-usd") + 1] == "0.25"
    argv = build_argv(Spec(fleet="claude", prompt="x", cwd="/tmp", cap_usd=5.0))
    assert argv[argv.index("--max-budget-usd") + 1] == "5"
    assert "--max-budget-usd" not in build_argv(Spec(fleet="claude", prompt="x", cwd="/tmp"))


def test_a_claude_budget_stop_is_read_as_over_cap(repo, home, fake_fleet):
    """The envelope Claude Code printed live 2026-09-03 with --max-budget-usd
    0.01: exit 1, no `result` text, the reason under `errors`."""
    envelope = {
        "type": "result",
        "subtype": "error_max_budget_usd",
        "is_error": True,
        "errors": ["Reached maximum budget ($0.01)"],
        "total_cost_usd": 0.23684,
        "usage": {"input_tokens": 2, "output_tokens": 364, "cache_creation_input_tokens": 58299},
    }
    fake_fleet(["sh", "-c", f"echo '{json.dumps(envelope)}'; exit 1"])
    result = dispatch(spec_for(repo, cap_usd=0.01), home=home)
    assert result.budget == {
        "cap_usd": 0.01,
        "enforcement": "native",
        "exceeded": True,
        "unpriced": False,
        "observed_usd": 0.23684,
    }
    assert result.fleet_error == "Reached maximum budget ($0.01)"
    assert result.answer_path is None
    assert result.summary()["failure"] == "over budget: $0.2368 against a $0.0100 cap"
    assert result.summary()["over_cap"] is True


# --- codex: conductor tails the session rollout -----------------------------


def _codex_home(tmp_path: Path, monkeypatch) -> Path:
    day = tmp_path / "codex-home" / "sessions" / "2026" / "09" / "03"
    day.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    return day / "rollout-2026-09-03T00-00-00-thread-1.jsonl"


def _token_count(**usage: int) -> str:
    return json.dumps(
        {
            "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": usage}},
        }
    )


STARTED = json.dumps({"type": "thread.started", "thread_id": "thread-1"})


def test_codex_is_killed_when_its_rollout_prices_over_the_cap(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    """Codex prints usage on stdout only at the end; its rollout file is the
    running figure. 1M output tokens on terra is $12 against a $1 cap."""
    rollout = _codex_home(tmp_path, monkeypatch)
    heavy = _token_count(input_tokens=1000, cached_input_tokens=0, output_tokens=1_000_000)
    fake_fleet(["sh", "-c", f"echo '{STARTED}'; echo '{heavy}' > '{rollout}'; sleep 60"])
    started = time.monotonic()
    result = dispatch(
        spec_for(repo, fleet="codex", model="terra", cap_usd=1.0, timeout=50), home=home
    )
    assert time.monotonic() - started < 20
    assert result.ok is False and result.timed_out is False
    assert result.error.startswith("budget cap hit: $12.0")
    assert result.budget["exceeded"] is True and result.budget["enforcement"] == "watcher"
    assert result.usage["output_tokens"] == 1_000_000
    assert result.usage["cost_basis"] == "estimated"
    assert result.answer_path is None
    assert result.summary()["failure"] == result.error


def test_a_codex_run_under_the_cap_is_left_alone(repo, home, fake_fleet, monkeypatch, tmp_path):
    rollout = _codex_home(tmp_path, monkeypatch)
    light = _token_count(input_tokens=1000, cached_input_tokens=0, output_tokens=100)
    done = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "finished"},
        }
    )
    turn = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 100}}
    )
    script = (
        f"echo '{STARTED}'; echo '{light}' > '{rollout}'; sleep 3; echo '{done}'; echo '{turn}'"
    )
    fake_fleet(["sh", "-c", script])
    result = dispatch(spec_for(repo, fleet="codex", model="terra", cap_usd=1.0), home=home)
    assert result.ok is True
    assert result.budget["exceeded"] is False
    assert result.budget["observed_usd"] == result.usage["cost_usd"]
    assert Path(result.answer_path).read_text() == "finished"


def test_a_timed_out_codex_run_is_still_priced_from_its_rollout(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    """No cap at all: the watcher still runs, because without it a killed
    Codex dispatch lands in the ledger as cost_usd: null."""
    rollout = _codex_home(tmp_path, monkeypatch)
    usage = _token_count(input_tokens=10_000, cached_input_tokens=0, output_tokens=1000)
    fake_fleet(["sh", "-c", f"echo '{STARTED}'; echo '{usage}' > '{rollout}'; sleep 60"])
    result = dispatch(spec_for(repo, fleet="codex", model="terra", timeout=3), home=home)
    assert result.timed_out is True
    assert result.budget is None
    assert result.usage["input_tokens"] == 10_000 and result.usage["cost_usd"] > 0
    assert result.usage["cost_basis"] == "estimated"


def test_a_codex_watcher_with_no_rollout_falls_back_to_a_post_run_verdict(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nowhere"))
    turn = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1_000_000}}
    )
    fake_fleet(["sh", "-c", f"echo '{STARTED}'; echo '{turn}'"])
    result = dispatch(spec_for(repo, fleet="codex", model="terra", cap_usd=1.0), home=home)
    assert result.exit_code == 0 and result.ok is False
    assert result.budget["exceeded"] is True
    assert result.summary()["failure"].startswith("over budget: $12.0")
    assert any("saw no running usage" in n for n in result.verdict["notes"])


# --- antigravity: conductor tails the stream-json steps ---------------------


def _step(index: int, **usage: int) -> str:
    return json.dumps(
        {
            "event": "step_update",
            "step_update": {"step_index": index, "state": "DONE", "usage": usage},
        }
    )


def _agy_result(response: str, **usage: int) -> str:
    return json.dumps(
        {"event": "result", "result": {"status": "SUCCESS", "response": response, "usage": usage}}
    )


def test_antigravity_runs_in_stream_json_so_its_steps_are_visible():
    argv = build_argv(Spec(fleet="antigravity", prompt="x", cwd="/tmp"))
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_antigravity_is_killed_when_its_steps_price_over_the_cap(repo, home, fake_fleet):
    """3M input tokens on gemini-3.8-flash is $2.25 against a $1 cap."""
    steps = [_step(1, input_tokens=1_500_000, output_tokens=10), _step(3, input_tokens=1_500_000)]
    fake_fleet(["sh", "-c", f"echo '{steps[0]}'; echo '{steps[1]}'; sleep 60"])
    result = dispatch(spec_for(repo, fleet="antigravity", cap_usd=1.0, timeout=50), home=home)
    assert result.ok is False and result.timed_out is False
    assert result.error.startswith("budget cap hit: $2.25")
    assert result.usage["input_tokens"] == 3_000_000
    assert result.answer_path is None


def test_antigravity_stream_result_is_unwrapped_and_steps_sum_to_it():
    """Steps report their own usage, not a running total, and a step may
    report more than once; the last figure per step counts."""
    lines = [
        _step(1, input_tokens=90, output_tokens=1),
        _step(1, input_tokens=100, output_tokens=5),
        _step(3, input_tokens=200, output_tokens=6, thinking_tokens=4),
    ]
    finished = parse("antigravity", "\n".join(lines + [_agy_result("DONE\n", input_tokens=300)]))
    assert finished.answer == "DONE" and finished.status == "SUCCESS"
    assert finished.usage.input_tokens == 300

    cut_short = parse("antigravity", "\n".join(lines))
    assert cut_short.answer == "" and cut_short.error is None and cut_short.parsed
    assert cut_short.usage.input_tokens == 300
    assert cut_short.usage.output_tokens == 11 + 4  # thinking bills as output
    assert cut_short.usage.thinking_tokens == 4


# --- cursor: usage arrives once, at the end ---------------------------------


def test_cursor_is_judged_after_the_run_because_it_reports_usage_once(repo, home, fake_fleet):
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,"result":"PONG",'
        '"usage":{"inputTokens":1000000,"outputTokens":1000000}}'
    )
    fake_fleet(["sh", "-c", f"echo '{envelope}'"])
    over = dispatch(spec_for(repo, fleet="cursor", model="composer-2.5", cap_usd=1.0), home=home)
    assert over.exit_code == 0 and over.ok is False
    assert over.budget == {
        "cap_usd": 1.0,
        "enforcement": "post-hoc",
        "exceeded": True,
        "unpriced": False,
        "observed_usd": 3.0,
    }
    assert over.summary()["failure"] == "over budget: $3.0000 against a $1.0000 cap"
    assert Path(over.answer_path).read_text() == "PONG"  # the work is kept, the verdict is not ok

    under = dispatch(spec_for(repo, fleet="cursor", model="composer-2.5", cap_usd=5.0), home=home)
    assert under.ok is True
    assert under.budget["exceeded"] is False and under.summary()["over_cap"] is False


# --- refusals ---------------------------------------------------------------


def test_a_cap_conductor_cannot_enforce_is_refused_before_spawn(monkeypatch, tmp_path):
    with pytest.raises(DispatchRefused, match="positive"):
        Spec(fleet="codex", prompt="x", cwd="/tmp", cap_usd=0).validate()
    # `--cap-usd inf` parses; no finite spend ever exceeds it (Codex review).
    with pytest.raises(DispatchRefused, match="finite"):
        Spec(fleet="codex", prompt="x", cwd="/tmp", cap_usd=float("inf")).validate()
    monkeypatch.setenv("CONDUCTOR_HOME", str(tmp_path))
    (tmp_path / "prices.json").write_text(
        json.dumps({"composer-2.5": None, "claude-sonnet-5": None})
    )
    with pytest.raises(DispatchRefused, match="unpriced"):
        Spec(fleet="cursor", model="composer-2.5", prompt="x", cwd="/tmp", cap_usd=1.0).validate()
    # Claude Code caps itself; it needs no price from conductor.
    Spec(fleet="claude", model="sonnet", prompt="x", cwd="/tmp", cap_usd=1.0).validate()


# --- the tail ---------------------------------------------------------------


def test_the_tail_reads_only_new_lines_and_holds_a_partial_one(tmp_path):
    path = tmp_path / "f.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":')
    tail = _Tail(path)
    assert tail.lines() == ['{"a":1}']
    with path.open("ab") as fh:
        fh.write(b"2}\n")
    assert tail.lines() == ['{"b":2}']
    assert tail.lines() == []
    assert _Tail(tmp_path / "missing").lines() == []
