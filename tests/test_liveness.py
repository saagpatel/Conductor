"""Liveness tests: tool-idle breaker, per-run heartbeat, and run reporting."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.breakers import Breaker
from conductor.cli import LIVENESS_STALE_S, build_parser, main
from conductor.fleets import DispatchRefused, Spec
from conductor.mission import Mission, MissionInvalid, mission_from_dict
from conductor.runner import dispatch
from docs import doc_section


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


def test_idle_breaker_trips_when_stdout_grows_without_tool_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "stdout.log"
    path.write_text("")
    now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)

    breaker = Breaker(
        "codex", path, stall_s=None, loop_limit=None, max_tool_calls=None, idle_s=5
    )
    assert breaker.to_dict()["last_tool_call_age_s"] == 0.0

    # Output grows with non-tool lines
    now = 1002.0
    msg = _line({"type": "item.completed", "item": {"type": "agent_message", "text": "thinking"}})
    path.write_text(msg + "\n")
    assert breaker.check() is None
    assert breaker.to_dict()["last_tool_call_age_s"] == 2.0
    assert breaker.to_dict()["last_output_age_s"] == 0.0

    now = 1004.0
    with path.open("a") as f:
        f.write(
            _line(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "still thinking"},
                }
            )
            + "\n"
        )
    assert breaker.check() is None
    assert breaker.to_dict()["last_tool_call_age_s"] == 4.0

    # Reaching 5.5s without any tool calls trips the idle breaker
    now = 1005.5
    with path.open("a") as f:
        f.write(
            _line(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "working"},
                }
            )
            + "\n"
        )
    reason = "idle: no tool call for 5s"
    assert breaker.check() == reason
    assert breaker.to_dict()["tripped"] == reason
    assert breaker.to_dict()["last_tool_call_age_s"] == 5.5


def test_idle_breaker_does_not_trip_while_tool_calls_keep_arriving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "stdout.log"
    path.write_text("")
    now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)

    breaker = Breaker(
        "codex", path, stall_s=None, loop_limit=None, max_tool_calls=None, idle_s=5
    )

    now = 1003.0
    with path.open("a") as f:
        f.write(_codex_command("cmd-1", "id-1") + "\n")
    assert breaker.check() is None
    assert breaker.to_dict()["tool_calls"] == 1
    assert breaker.to_dict()["last_tool_call_age_s"] == 0.0

    now = 1006.0  # 6s since start, but only 3s since last tool call
    with path.open("a") as f:
        f.write(_codex_command("cmd-2", "id-2") + "\n")
    assert breaker.check() is None
    assert breaker.to_dict()["tool_calls"] == 2
    assert breaker.to_dict()["last_tool_call_age_s"] == 0.0

    now = 1009.0  # 9s since start, 3s since last tool call
    with path.open("a") as f:
        f.write(_codex_command("cmd-3", "id-3") + "\n")
    assert breaker.check() is None
    assert breaker.to_dict()["tool_calls"] == 3
    assert breaker.to_dict()["last_tool_call_age_s"] == 0.0

    # 5.5s after the last tool call at 1009.0, idle breaker trips
    now = 1014.5
    with path.open("a") as f:
        f.write(
            _line(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "idle text"},
                }
            )
            + "\n"
        )
    assert breaker.check() == "idle: no tool call for 5s"
    assert breaker.to_dict()["last_tool_call_age_s"] == 5.5


def test_stall_check_evaluated_before_idle_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "stdout.log"
    path.write_text("")
    now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)

    breaker = Breaker(
        "codex", path, stall_s=2, loop_limit=None, max_tool_calls=None, idle_s=5
    )
    now = 1003.0
    assert breaker.check() == "stalled: no output for 2s"


def test_idle_timeout_validation_and_disabling_at_all_layers(tmp_path: Path):
    defaults = Spec(fleet="codex", prompt="x", cwd=str(tmp_path))
    assert defaults.tool_idle_timeout is None

    Spec(fleet="codex", prompt="x", cwd=str(tmp_path), tool_idle_timeout=0).validate()
    Spec(fleet="codex", prompt="x", cwd=str(tmp_path), tool_idle_timeout=None).validate()
    Spec(fleet="codex", prompt="x", cwd=str(tmp_path), tool_idle_timeout=10).validate()

    msg = r"tool_idle_timeout must be positive; 0 or null disables"
    with pytest.raises(DispatchRefused, match=msg):
        Spec(fleet="codex", prompt="x", cwd=str(tmp_path), tool_idle_timeout=-1).validate()

    with pytest.raises(DispatchRefused, match=msg):
        Spec(fleet="codex", prompt="x", cwd=str(tmp_path), tool_idle_timeout=True).validate()

    parser = build_parser()
    assert parser.parse_args(["dispatch", "--fleet", "codex", "x"]).tool_idle_timeout is None
    parsed_zero = parser.parse_args(
        ["dispatch", "--fleet", "codex", "--tool-idle-timeout", "0", "x"]
    )
    assert parsed_zero.tool_idle_timeout == 0
    parsed_val = parser.parse_args(
        ["dispatch", "--fleet", "codex", "--tool-idle-timeout", "25", "x"]
    )
    assert parsed_val.tool_idle_timeout == 25

    with pytest.raises(MissionInvalid, match=msg):
        mission_from_dict(
            {"prompt": "x", "tool_idle_timeout": -5, "lanes": [{"fleet": "codex"}]},
            base_dir=tmp_path,
        )

    mission = mission_from_dict(
        {
            "prompt": "x",
            "tool_idle_timeout": 45,
            "lanes": [
                {
                    "name": "lane-inherit",
                    "fleet": "codex",
                    "fallback": [{"fleet": "claude"}],
                },
                {
                    "name": "lane-override",
                    "fleet": "codex",
                    "tool_idle_timeout": 15,
                },
                {
                    "name": "lane-disabled",
                    "fleet": "codex",
                    "tool_idle_timeout": None,
                },
                {
                    "name": "lane-zero-disabled",
                    "fleet": "codex",
                    "tool_idle_timeout": 0,
                },
            ],
        },
        base_dir=tmp_path,
    )
    l1_pri, l1_fb = mission.lanes[0].attempts
    assert l1_pri.tool_idle_timeout == 45
    assert l1_fb.tool_idle_timeout == 45
    assert mission.lanes[1].attempts[0].tool_idle_timeout == 15
    assert mission.lanes[2].attempts[0].tool_idle_timeout is None
    assert mission.lanes[3].attempts[0].tool_idle_timeout == 0

    snapshot = mission.to_dict()
    assert snapshot["lanes"][0]["attempts"][0]["tool_idle_timeout"] == 45
    assert snapshot["lanes"][1]["attempts"][0]["tool_idle_timeout"] == 15
    assert snapshot["lanes"][2]["attempts"][0]["tool_idle_timeout"] is None
    resumed = Mission.from_snapshot(snapshot)
    assert resumed.lanes[0].attempts[0].tool_idle_timeout == 45
    assert resumed.lanes[1].attempts[0].tool_idle_timeout == 15
    assert resumed.lanes[2].attempts[0].tool_idle_timeout is None


def test_dispatch_writes_liveness_json_and_idle_kill_receipt(
    repo, home, fake_fleet, monkeypatch
):
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
    res = dispatch(Spec(fleet="claude", prompt="test", cwd=str(repo)), home=home)
    assert res.ok is True

    run_dir = Path(res.run_dir)
    liveness_path = run_dir / "liveness.json"
    assert liveness_path.is_file()
    live = json.loads(liveness_path.read_text())

    assert isinstance(live["at"], str) and live["at"].endswith("Z")
    assert isinstance(live["elapsed_s"], (int, float))
    assert isinstance(live["pid"], int)
    assert isinstance(live["stdout_bytes"], int)
    assert "spend_usd" in live
    assert live["tool_calls"] == 1
    assert isinstance(live["last_output_age_s"], float)
    assert isinstance(live["last_tool_call_age_s"], float)
    assert live["tripped"] is None

    result_json = json.loads((run_dir / "result.json").read_text())
    assert "last_tool_call_age_s" in result_json["breaker"]
    assert isinstance(result_json["breaker"]["last_tool_call_age_s"], float)

    # Tool-idle kill: stdout grows with non-tool lines and no tool call arrives
    non_tool = _line(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}}
    )
    fake_fleet(["sh", "-c", f"echo '{non_tool}'; sleep 0.1; echo '{non_tool}'; sleep 60"])
    monkeypatch.setattr(runner_mod, "POLL_S", 0.1)
    gate_marker = repo / "gate-must-not-run"
    idle_res = dispatch(
        Spec(
            fleet="claude",
            prompt="idle-test",
            cwd=str(repo),
            timeout=10,
            stall_timeout=0,
            loop_limit=0,
            tool_idle_timeout=1,
        ),
        home=home,
        test_command=f"touch {gate_marker}",
    )
    assert idle_res.ok is False
    assert idle_res.error is not None
    assert idle_res.error.startswith("idle:")
    assert idle_res.error == "idle: no tool call for 1s; process group killed"
    assert idle_res.breaker is not None
    assert idle_res.breaker["tripped"] == "idle: no tool call for 1s"
    assert "last_tool_call_age_s" in idle_res.breaker
    assert isinstance(idle_res.breaker["last_tool_call_age_s"], float)
    assert idle_res.breaker["last_tool_call_age_s"] >= 1.0
    assert not gate_marker.exists()


def test_conductor_runs_reports_running_silent_and_incomplete(home, monkeypatch, capsys):
    runs_dir = home / "runs"
    runs_dir.mkdir(parents=True)

    fresh_dir = runs_dir / "20260905T010000Z-fresh"
    fresh_dir.mkdir()
    fresh_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    (fresh_dir / "liveness.json").write_text(
        json.dumps(
            {
                "at": fresh_at,
                "elapsed_s": 5.0,
                "pid": os.getpid(),
                "stdout_bytes": 120,
                "spend_usd": 0.015,
                "tool_calls": 3,
                "last_output_age_s": 0.5,
                "last_tool_call_age_s": 1.5,
                "tripped": None,
            }
        )
    )

    silent_dir = runs_dir / "20260905T005000Z-silent"
    silent_dir.mkdir()
    past = datetime.now(UTC) - timedelta(seconds=LIVENESS_STALE_S + 15)
    silent_at = past.isoformat().replace("+00:00", "Z")
    (silent_dir / "liveness.json").write_text(
        json.dumps(
            {
                "at": silent_at,
                "elapsed_s": 120.0,
                "pid": os.getpid(),
                "stdout_bytes": 40,
                "spend_usd": None,
            }
        )
    )

    # 2026-09-08: `status` used to come from the heartbeat age alone, so a
    # run whose process was gone read as `running` for LIVENESS_STALE_S and,
    # if its `at` was missing or malformed, forever. The pid decides first
    # now, so a dead pid is its own state whatever the heartbeat says --
    # which is why the silent fixture above carries a live pid.
    dead_dir = runs_dir / "20260905T004500Z-dead"
    dead_dir.mkdir()
    (dead_dir / "liveness.json").write_text(
        json.dumps(
            {
                "at": fresh_at,
                "elapsed_s": 60.0,
                "pid": 9999999,
                "stdout_bytes": 40,
                "spend_usd": None,
            }
        )
    )

    incomplete_dir = runs_dir / "20260905T004000Z-incomplete"
    incomplete_dir.mkdir()

    completed_dir = runs_dir / "20260905T003000Z-completed"
    completed_dir.mkdir()
    (completed_dir / "liveness.json").write_text(
        json.dumps(
            {
                "at": fresh_at,
                "elapsed_s": 10.0,
                "pid": os.getpid(),
                "stdout_bytes": 200,
                "spend_usd": None,
            }
        )
    )
    (completed_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": completed_dir.name,
                "ok": True,
                "fleet": "claude",
                "model": "sonnet",
                "session_id": "sess-1",
                "exit_code": 0,
                "duration_s": 10.0,
            }
        )
    )

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["runs"]) == 0
    rows = json.loads(capsys.readouterr().out)
    by_id = {r["run_id"]: r for r in rows}

    fresh_row = by_id[fresh_dir.name]
    assert fresh_row["status"] == "running"
    assert fresh_row["heartbeat_age_s"] < 5.0
    assert fresh_row["elapsed_s"] == 5.0
    assert fresh_row["pid"] == os.getpid()
    assert fresh_row["pid_alive"] is True
    assert fresh_row["spend_usd"] == 0.015
    assert fresh_row["tool_calls"] == 3
    assert fresh_row["last_output_age_s"] == 0.5
    assert fresh_row["last_tool_call_age_s"] == 1.5

    silent_row = by_id[silent_dir.name]
    assert silent_row["status"] == "silent"
    assert silent_row["heartbeat_age_s"] >= LIVENESS_STALE_S
    assert silent_row["elapsed_s"] == 120.0
    assert silent_row["pid"] == os.getpid()
    assert silent_row["pid_alive"] is True
    assert silent_row["spend_usd"] is None
    assert silent_row["tool_calls"] is None
    assert silent_row["last_output_age_s"] is None
    assert silent_row["last_tool_call_age_s"] is None

    # A fresh heartbeat does not make a gone process running.
    dead_row = by_id[dead_dir.name]
    assert dead_row["status"] == "dead"
    assert dead_row["pid_alive"] is False
    assert dead_row["heartbeat_age_s"] < 5.0

    incomplete_row = by_id[incomplete_dir.name]
    assert incomplete_row == {"run_id": incomplete_dir.name, "status": "incomplete"}

    completed_row = by_id[completed_dir.name]
    assert completed_row["ok"] is True
    assert "status" not in completed_row

    # Directory state must be completely untouched
    assert (fresh_dir / "liveness.json").is_file()
    assert (silent_dir / "liveness.json").is_file()
    assert incomplete_dir.is_dir() and not any(incomplete_dir.iterdir())
    assert (completed_dir / "result.json").is_file()
    assert (completed_dir / "liveness.json").is_file()


def test_readme_documents_tool_idle_breaker_and_liveness():
    section = doc_section("Per-dispatch caps")
    compact = " ".join(section.split())

    assert "tool-idle breaker" in compact
    assert "--tool-idle-timeout" in compact
    assert "tool_idle_timeout" in compact
    assert "idle: no tool call for {idle_s}s" in compact
    assert "read lane thinking through a long review" in compact
    assert "off by default" in compact

    assert "### Liveness" in section
    assert "liveness.json" in compact
    assert "every poll tick" in compact
    for field in (
        "at",
        "elapsed_s",
        "pid",
        "stdout_bytes",
        "spend_usd",
        "tool_calls",
        "last_output_age_s",
        "last_tool_call_age_s",
        "tripped",
    ):
        assert field in compact
    assert "running" in compact
    assert "silent" in compact
    assert "incomplete" in compact
    assert "`silent` is a flag" in compact
    assert "nothing is reclaimed" in compact
    assert "never modifies, moves, reclaims, or deletes a run directory" in compact
