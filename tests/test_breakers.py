"""Progress breakers stop silence, repeated work, and unbounded tool churn."""

from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.breakers import Breaker, tool_events
from conductor.cli import build_parser, main
from conductor.fleets import DispatchRefused, Spec
from conductor.mission import mission_from_dict, run_mission
from conductor.runner import dispatch
from docs import doc_section


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True)
    return sha256(raw.encode()).hexdigest()[:12]


def _line(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"))


def _codex_command(command: str, identity: str | None = None) -> str:
    return _line(
        {
            "type": "item.completed",
            "item": {
                "id": identity or command,
                "type": "command_execution",
                "command": command,
            },
        }
    )


def test_tool_events_normalize_every_fleet_fixture_shape():
    codex = "\n".join(
        [
            _line(
                {
                    "type": "item.started",
                    "item": {
                        "id": "item-1",
                        "type": "command_execution",
                        "command": "pytest -q",
                    },
                }
            ),
            _line(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item-1",
                        "type": "command_execution",
                        "command": "pytest -q",
                    },
                }
            ),
            _line(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "file_change",
                        "changes": [{"path": "z.py"}, {"path": "a.py"}],
                    },
                }
            ),
            _line(
                {
                    "type": "item.completed",
                    "item": {"type": "mcp_tool_call", "server": "github", "tool": "search"},
                }
            ),
        ]
    )
    assert tool_events("codex", codex) == [
        "cmd:pytest -q",
        "edit:a.py,z.py",
        "mcp:github.search",
    ]

    claude_input = {"command": "pytest -q", "timeout": 30}
    claude = _line(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "name": "Bash", "input": claude_input},
                ]
            },
        }
    )
    assert tool_events("claude", claude) == [f"Bash:{_digest(claude_input)}"]

    agy_args = {"path": "README.md", "line_end": 20}
    agy = "\n".join(
        [
            _line(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conversation-1",
                        "step_index": 3,
                        "state": "RUNNING",
                        "step_type": "tool",
                        "tool_name": "view_file",
                        "tool_info": {"name": "view_file", "parameters": agy_args},
                    },
                }
            ),
            _line(
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conversation-1",
                        "step_index": 3,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "view_file",
                        "tool_info": {
                            "name": "view_file",
                            "parameters": agy_args,
                            "output": "contents",
                        },
                    },
                }
            ),
            _line(
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 4,
                        "state": "DONE",
                        "usage": {"input_tokens": 10},
                    },
                }
            ),
        ]
    )
    assert tool_events("antigravity", agy) == [f"view_file:{_digest(agy_args)}"]

    cursor_args = {"plan": "inspect"}
    cursor_call = {
        "toolCallId": "tool-1",
        "createPlanToolCall": {"args": cursor_args},
    }
    cursor = "\n".join(
        _line({"type": "tool_call", "subtype": subtype, "tool_call": cursor_call})
        for subtype in ("started", "completed")
    )
    assert tool_events("cursor", cursor) == [f"createPlan:{_digest(cursor_args)}"]


def test_breaker_reads_only_new_complete_lines(tmp_path: Path):
    path = tmp_path / "stdout.log"
    first = _codex_command("one")
    second = _codex_command("two")
    path.write_bytes((first + "\n" + second[:12]).encode())
    breaker = Breaker(
        "codex", path, stall_s=None, loop_limit=None, max_tool_calls=None
    )
    assert breaker.check() is None
    assert breaker.to_dict()["tool_calls"] == 1
    with path.open("ab") as output:
        output.write((second[12:] + "\n").encode())
    assert breaker.check() is None
    assert breaker.to_dict()["tool_calls"] == 2
    # A third poll must not re-read either prior line.
    assert breaker.check() is None
    assert breaker.to_dict()["tool_calls"] == 2


def test_breaker_deduplicates_a_call_completed_on_a_later_poll(tmp_path: Path):
    path = tmp_path / "stdout.log"
    started = _line(
        {
            "type": "item.started",
            "item": {"id": "item-1", "type": "command_execution", "command": "pytest"},
        }
    )
    completed = _line(
        {
            "type": "item.completed",
            "item": {"id": "item-1", "type": "command_execution", "command": "pytest"},
        }
    )
    path.write_text(started + "\n")
    breaker = Breaker("codex", path, stall_s=None, loop_limit=None, max_tool_calls=None)
    assert breaker.check() is None and breaker.to_dict()["tool_calls"] == 1
    with path.open("a") as output:
        output.write(completed + "\n")
    assert breaker.check() is None and breaker.to_dict()["tool_calls"] == 1


def test_final_check_consumes_an_unterminated_last_event(tmp_path: Path):
    path = tmp_path / "stdout.log"
    path.write_text(_codex_command("one") + "\n" + _codex_command("two"))
    breaker = Breaker("codex", path, stall_s=None, loop_limit=None, max_tool_calls=1)
    assert breaker.check() is None and breaker.to_dict()["tool_calls"] == 1
    assert breaker.check(final=True) == "tool budget hit: 2 tool calls"
    assert breaker.check() == "tool budget hit: 2 tool calls"
    assert breaker.to_dict()["tool_calls"] == 2


def test_dispatch_checks_an_unterminated_final_event_after_exit(repo, home, fake_fleet):
    first = _codex_command("one")
    second = _codex_command("two")
    fake_fleet(["sh", "-c", f"printf '%s\\n%s' '{first}' '{second}'"])
    result = dispatch(
        Spec(
            fleet="codex",
            prompt="final event",
            cwd=str(repo),
            stall_timeout=0,
            loop_limit=0,
            max_tool_calls=1,
        ),
        home=home,
    )
    reason = "tool budget hit: 2 tool calls"
    assert result.error == f"{reason}; process group killed"
    assert result.breaker is not None and result.breaker["tool_calls"] == 2


def test_healthy_dispatch_receipt_still_reports_tool_progress(repo, home, fake_fleet):
    tool_input = {"path": "README.md"}
    assistant = _line(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": tool_input}]
            },
        }
    )
    result_event = _line(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    fake_fleet(["sh", "-c", f"echo '{assistant}'; echo '{result_event}'"])
    result = dispatch(Spec(fleet="claude", prompt="healthy", cwd=str(repo)), home=home)
    assert result.ok is True
    assert result.breaker is not None
    assert result.breaker["tool_calls"] == 1 and result.breaker["tripped"] is None
    assert result.summary()["tool_calls"] == 1 and result.summary()["breaker"] is None
    disabled = dispatch(
        Spec(
            fleet="claude",
            prompt="disabled",
            cwd=str(repo),
            stall_timeout=0,
            loop_limit=0,
            max_tool_calls=0,
        ),
        home=home,
    )
    assert disabled.ok is True and disabled.breaker is None


def test_loop_breaker_trips_only_on_identical_tail(tmp_path: Path):
    path = tmp_path / "stdout.log"
    path.write_text(
        "\n".join(_codex_command("same", f"item-{n}") for n in range(6)) + "\n"
    )
    repeated = Breaker("codex", path, stall_s=None, loop_limit=6, max_tool_calls=None)
    assert repeated.check() == "looping: cmd:same repeated 6 times"

    path.write_text("\n".join(_codex_command(f"cmd-{n}") for n in range(6)) + "\n")
    distinct = Breaker("codex", path, stall_s=None, loop_limit=6, max_tool_calls=None)
    assert distinct.check() is None

    # Successive edits to one file are progress: Codex reports only the path.
    edits_stream = [
        _line(
            {
                "type": "item.completed",
                "item": {"id": f"e{n}", "type": "file_change", "changes": [{"path": "big.py"}]},
            }
        )
        for n in range(6)
    ]
    path.write_text("\n".join(edits_stream) + "\n")
    edits = Breaker("codex", path, stall_s=None, loop_limit=6, max_tool_calls=None)
    assert edits.check() is None
    assert edits.to_dict()["tool_calls"] == 6


@pytest.mark.parametrize(("count", "reason"), [(3, None), (4, "tool budget hit: 4 tool calls")])
def test_tool_budget_trips_at_n_plus_one(tmp_path: Path, count: int, reason: str | None):
    path = tmp_path / f"{count}.log"
    path.write_text("\n".join(_codex_command(f"cmd-{n}") for n in range(count)) + "\n")
    breaker = Breaker("codex", path, stall_s=None, loop_limit=None, max_tool_calls=3)
    assert breaker.check() == reason


def _rollout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    day = tmp_path / "codex-home" / "sessions" / "2026" / "09" / "03"
    day.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    return day / "rollout-2026-09-03T00-00-00-thread-1.jsonl"


def test_stall_dispatch_is_killed_priced_receipted_and_never_gated(
    repo, home, fake_fleet, monkeypatch, tmp_path, capsys
):
    rollout = _rollout(tmp_path, monkeypatch)
    started = _line({"type": "thread.started", "thread_id": "thread-1"})
    usage = _line(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 10_000, "output_tokens": 1_000}
                },
            },
        }
    )
    fake_fleet(
        [
            "sh",
            "-c",
            f"echo '{started}'; echo '{usage}' > '{rollout}'; echo awake; sleep 60",
        ]
    )
    monkeypatch.setattr(runner_mod, "POLL_S", 0.2)
    gate_marker = repo / "gate-ran"
    began = time.monotonic()
    result = dispatch(
        Spec(
            fleet="codex",
            model="terra",
            prompt="stall",
            cwd=str(repo),
            timeout=20,
            stall_timeout=2,
            loop_limit=0,
            cap_usd=10.0,
        ),
        home=home,
        test_command=f"touch {gate_marker}",
    )
    assert time.monotonic() - began < 8
    reason = "stalled: no output for 2s"
    assert result.error == f"{reason}; process group killed"
    assert result.breaker is not None and result.breaker["tripped"] == reason
    assert result.breaker["tool_calls"] == 0
    assert isinstance(result.breaker["last_output_age_s"], float)
    assert result.ok is False and result.tests is None and not gate_marker.exists()
    assert result.usage is not None and result.usage["cost_usd"] > 0
    assert result.budget is not None and result.budget["exceeded"] is True
    assert result.summary()["breaker"] == reason

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["runs"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["tool_calls"] == 0


def test_a_claude_breaker_kill_is_priced_from_streamed_message_usage(
    repo, home, fake_fleet, monkeypatch
):
    assistant = _line(
        {
            "type": "assistant",
            "session_id": "claude-session",
            "message": {
                "id": "message-1",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "cache_creation_input_tokens": 10_000,
                },
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"path": "README.md"},
                    }
                ],
            },
        }
    )
    fake_fleet(["sh", "-c", f"echo '{assistant}'; sleep 60"])
    monkeypatch.setattr(runner_mod, "POLL_S", 0.2)
    result = dispatch(
        Spec(
            fleet="claude",
            model="sonnet",
            prompt="loop",
            cwd=str(repo),
            timeout=20,
            stall_timeout=0,
            loop_limit=1,
        ),
        home=home,
    )
    assert result.error is not None and result.error.startswith("looping: Read:")
    assert result.usage is not None and result.usage["cost_usd"] > 0
    assert result.usage["cost_basis"] == "estimated"


def test_spec_cli_and_mission_breaker_values_validate_and_zero_disables(tmp_path: Path):
    defaults = Spec(fleet="codex", prompt="x", cwd=str(tmp_path))
    assert (defaults.stall_timeout, defaults.loop_limit, defaults.max_tool_calls) == (900, 6, None)
    Spec(
        fleet="codex",
        prompt="x",
        cwd=str(tmp_path),
        stall_timeout=0,
        loop_limit=0,
        max_tool_calls=0,
    ).validate()
    for key in ("stall_timeout", "loop_limit", "max_tool_calls"):
        with pytest.raises(DispatchRefused, match=rf"{key} must be positive; 0 or null disables"):
            Spec(fleet="codex", prompt="x", cwd=str(tmp_path), **{key: -1}).validate()

    args = build_parser().parse_args(
        [
            "dispatch",
            "--fleet",
            "codex",
            "--stall-timeout",
            "0",
            "--loop-limit",
            "0",
            "--max-tool-calls",
            "0",
            "x",
        ]
    )
    assert (args.stall_timeout, args.loop_limit, args.max_tool_calls) == (0, 0, 0)

    mission = mission_from_dict(
        {
            "prompt": "x",
            "stall_timeout": 30,
            "loop_limit": 4,
            "max_tool_calls": 9,
            "lanes": [
                {
                    "fleet": "codex",
                    "loop_limit": 2,
                    "fallback": [{"fleet": "claude"}],
                }
            ],
        },
        base_dir=tmp_path,
    )
    primary, fallback = mission.lanes[0].attempts
    assert (primary.stall_timeout, primary.loop_limit, primary.max_tool_calls) == (30, 2, 9)
    assert (fallback.stall_timeout, fallback.loop_limit, fallback.max_tool_calls) == (30, 2, 9)

    disabled = mission_from_dict(
        {
            "prompt": "x",
            "stall_timeout": None,
            "loop_limit": None,
            "max_tool_calls": None,
            "lanes": [{"fleet": "codex"}],
        },
        base_dir=tmp_path,
    ).lanes[0].attempts[0]
    assert (disabled.stall_timeout, disabled.loop_limit, disabled.max_tool_calls) == (
        None,
        None,
        None,
    )


def test_looping_mission_primary_falls_back_and_reports_tools_and_spend(
    repo, home, monkeypatch, tmp_path, capsys
):
    repeated = "; ".join(
        f"echo '{_codex_command('same', f'item-{n}')}'" for n in range(6)
    )
    claude = _line(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "fallback",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    by_fleet = {
        "codex": ["sh", "-c", repeated],
        "claude": ["sh", "-c", f"echo '{claude}'"],
    }
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])
    mission = mission_from_dict(
        {
            "name": "breaker-fallback",
            "prompt": "x",
            "cwd": str(repo),
            "stall_timeout": 0,
            "loop_limit": 6,
            "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude"}]}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    lane = result.lanes[0]
    reason = "looping: cmd:same repeated 6 times"
    assert lane["ok"] is True and len(lane["attempts"]) == 2
    assert lane["attempts"][0]["breaker"] == reason
    assert lane["attempts"][0]["failure"] == f"{reason}; process group killed"
    assert lane["tool_calls"] == 6 and lane["breaker"] is None
    report = Path(result.report_path).read_text()
    assert "| tokens | tools |" in report
    assert f"- breaker: {reason}" in report

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    spend = json.loads(capsys.readouterr().out)
    assert spend[-1]["tool_calls"] == 6


def test_readme_documents_breaker_defaults_disabling_pricing_and_claude_streaming():
    section = doc_section("#### Breakers")
    compact = " ".join(section.split())
    assert "900" in compact and "6 identical" in compact and "off by default" in compact
    assert "--stall-timeout 0" in compact and "--loop-limit 0" in compact
    assert "--max-tool-calls 0" in compact
    assert "priced from the watcher's last reading" in compact
    assert "stream-json --verbose" in compact
