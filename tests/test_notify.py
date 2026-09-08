"""E12: notifications -- an opt-in shell hook fired at three settle
boundaries (pause, end, breaker). A notification is a note, never a
verdict: it never changes `ok`, an exit code, or a pause.
"""

from __future__ import annotations

import json
import os
import time
import tracemalloc
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission
from conductor.notify import emit
from docs import doc_section


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def _ok_argv(spec) -> list[str]:
    return ["sh", "-c", f"echo '{envelope('ok')}'"]


def _codex_event(command: str, item_id: str) -> str:
    item = {"id": item_id, "type": "command_execution", "command": command}
    return json.dumps({"type": "item.completed", "item": item}, separators=(",", ":"))


# --- item 1: validation ------------------------------------------------------


def test_notify_refused_when_not_an_object(tmp_path):
    with pytest.raises(MissionInvalid, match="notify must be an object"):
        mission_from_dict(
            {"prompt": "x", "lanes": [{"fleet": "codex"}], "notify": "x"}, base_dir=tmp_path
        )


def test_notify_refused_unknown_field(tmp_path):
    with pytest.raises(MissionInvalid, match="unknown field"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex"}],
                "notify": {"command": "x", "bogus": 1},
            },
            base_dir=tmp_path,
        )


def test_notify_refused_empty_command(tmp_path):
    with pytest.raises(MissionInvalid, match="command must be a non-empty string"):
        mission_from_dict(
            {"prompt": "x", "lanes": [{"fleet": "codex"}], "notify": {"command": ""}},
            base_dir=tmp_path,
        )


def test_notify_refused_unknown_event(tmp_path):
    with pytest.raises(MissionInvalid, match="unknown event 'bogus'"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex"}],
                "notify": {"command": "x", "events": ["bogus"]},
            },
            base_dir=tmp_path,
        )


def test_notify_refused_non_positive_timeout(tmp_path):
    with pytest.raises(MissionInvalid, match="timeout must be positive"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex"}],
                "notify": {"command": "x", "timeout": 0},
            },
            base_dir=tmp_path,
        )


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), "inf", "nan"])
def test_notify_refused_non_finite_timeout(tmp_path, bad):
    """`timeout <= 0` is False for NaN and infinity, so `{"timeout": "inf"}`
    loaded and `proc.wait(timeout=inf)` never timed out."""
    with pytest.raises(MissionInvalid, match="timeout must be positive"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex"}],
                "notify": {"command": "x", "timeout": bad},
            },
            base_dir=tmp_path,
        )


def test_notify_events_and_timeout_default(tmp_path):
    mission = mission_from_dict(
        {"prompt": "x", "lanes": [{"fleet": "codex"}], "notify": {"command": "x"}},
        base_dir=tmp_path,
    )
    assert mission.notify == {
        "command": "x",
        "events": ["pause", "end", "breaker"],
        "timeout": 10.0,
    }


def test_notify_snapshot_round_trips(tmp_path):
    mission = mission_from_dict(
        {
            "prompt": "x",
            "notify": {"command": "cat", "events": ["pause", "breaker"], "timeout": 5},
            "lanes": [{"fleet": "codex", "name": "a"}],
        },
        base_dir=tmp_path,
    )
    raw = mission.to_dict()
    reloaded = Mission.from_snapshot(raw)
    assert reloaded.notify == {"command": "cat", "events": ["pause", "breaker"], "timeout": 5.0}
    assert reloaded.to_dict() == raw


# --- item 2: emit -------------------------------------------------------------


def test_emit_runs_command_with_stdin_json_and_env_vars(tmp_path):
    stdin_file = tmp_path / "stdin.json"
    env_file = tmp_path / "env.txt"
    command = (
        f"cat > {stdin_file}; "
        f'printf \'%s\\n%s\' "$CONDUCTOR_EVENT" "$CONDUCTOR_MISSION" > {env_file}'
    )
    event = {"event": "end", "mission_id": "m-1", "ok": True}
    result = emit({"command": command, "timeout": 5}, event, cwd=str(tmp_path))
    assert result == {"event": "end", "ok": True, "exit_code": 0, "timed_out": False, "error": None}
    assert json.loads(stdin_file.read_text()) == event
    assert env_file.read_text() == "end\nm-1"


def test_emit_non_zero_exit_is_ok_false_with_exit_code(tmp_path):
    result = emit(
        {"command": "echo boom >&2; exit 3", "timeout": 5},
        {"event": "pause", "mission_id": "m"},
        cwd=str(tmp_path),
    )
    assert result["ok"] is False
    assert result["exit_code"] == 3
    assert result["timed_out"] is False
    assert "3" in result["error"] and "boom" in result["error"]


def test_emit_timeout_is_ok_false_and_timed_out(tmp_path):
    result = emit(
        {"command": "sleep 5", "timeout": 0.2},
        {"event": "end", "mission_id": "m"},
        cwd=str(tmp_path),
    )
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "0.2" in result["error"]


def test_emit_timeout_kills_the_whole_process_group(tmp_path):
    # W10 (astra): `subprocess.run`'s timeout kills only the shell, so a
    # child the notify command spawned kept running after the mission moved
    # on. The command now leads its own process group, killed whole.
    pid_file = tmp_path / "child.pid"
    command = f"sh -c 'echo $$ > {pid_file}; sleep 30' & sleep 30"
    result = emit(
        {"command": command, "timeout": 1},
        {"event": "end", "mission_id": "m"},
        cwd=str(tmp_path),
    )
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    child_pid = int(pid_file.read_text().strip())
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, f"child {child_pid} outlived the notify timeout"
        time.sleep(0.05)


def test_emit_failure_error_is_a_bounded_tail_of_a_huge_output(tmp_path):
    # W10 (astra): `capture_output` read the whole of a command's output
    # into memory for a 500-character tail. Several megabytes now cost a
    # bounded read of the file's end, and the reason stays the cap plus its
    # `exit N: ` prefix.
    line = "x" * 1023
    command = (
        f"echo HEAD-MARKER; yes '{line}' | head -c 4000000; echo TAIL-MARKER; exit 4"
    )
    tracemalloc.start()
    try:
        result = emit(
            {"command": command, "timeout": 30},
            {"event": "end", "mission_id": "m"},
            cwd=str(tmp_path),
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result["ok"] is False
    assert result["exit_code"] == 4
    assert result["timed_out"] is False
    assert result["error"].startswith("exit 4: ")
    # The tail, not the head: 4 MB of output later, the end is what a reason
    # about a failure needs.
    assert "TAIL-MARKER" in result["error"]
    assert "HEAD-MARKER" not in result["error"]
    assert len(result["error"]) <= 520
    # And the 4 MB never became a Python string.
    assert peak < 1_000_000, f"emit held {peak} bytes of a 4 MB output"


def test_emit_missing_command_is_ok_false(tmp_path):
    result = emit(
        {"command": "conductor-notify-command-that-does-not-exist-xyz", "timeout": 5},
        {"event": "end", "mission_id": "m"},
        cwd=str(tmp_path),
    )
    assert result["ok"] is False
    assert result["timed_out"] is False
    assert result["exit_code"] == 127


def test_emit_never_raises_on_non_utf8_output(tmp_path):
    # Review finding (grok): `text=True` with no `errors=` decodes with the
    # strict handler, so a command that writes invalid UTF-8 raised
    # `UnicodeDecodeError` out of `emit` -- not `OSError`, so the spec's "it
    # never raises" did not hold and a noisy notify command could abort the
    # mission instead of recording `ok: false`.
    result = emit(
        {"command": r"printf '\xff\xfe'", "timeout": 5},
        {"event": "end", "mission_id": "m"},
        cwd=str(tmp_path),
    )
    assert result["ok"] is True


def test_emit_appends_a_newline_so_two_events_are_two_jsonl_lines(tmp_path):
    # Review finding (grok): `emit` sends the event with no trailing
    # newline, so the README's own example (`cat >> events.jsonl`, "the
    # event as one line of JSON on stdin") concatenates a second event onto
    # the first line instead of appending a new one.
    events_file = tmp_path / "events.jsonl"
    config = {"command": f"cat >> {events_file}", "timeout": 5}
    emit(config, {"event": "pause", "mission_id": "m"}, cwd=str(tmp_path))
    emit(config, {"event": "end", "mission_id": "m"}, cwd=str(tmp_path))
    lines = events_file.read_text().splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["pause", "end"]


# --- item 3: emission points through a mission --------------------------------


def test_pause_sends_pause_event_but_not_end(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", _ok_argv)
    notify_file = tmp_path / "events.jsonl"
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "pause": {"before": ["b"]},
            "notify": {"command": f"cat >> {notify_file}"},
            "lanes": [
                {"name": "a", "fleet": "claude"},
                {"name": "b", "fleet": "claude", "needs": ["a"]},
            ],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.paused["kind"] == "lane"
    assert [n["event"] for n in result.notifications] == ["pause"]
    lines = [json.loads(text) for text in notify_file.read_text().splitlines()]
    assert [line["event"] for line in lines] == ["pause"]
    assert lines[0]["lane"] == "b" and lines[0]["mission_id"] == result.mission_id


def test_end_event_carries_lanes_and_cost(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('ok', cost=0.25)}'"]
    )
    notify_file = tmp_path / "events.jsonl"
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "notify": {"command": f"cat >> {notify_file}"},
            "lanes": [{"name": "a", "fleet": "claude"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    assert [n["event"] for n in result.notifications] == ["end"]
    lines = [json.loads(text) for text in notify_file.read_text().splitlines()]
    assert lines == [
        {
            "event": "end",
            "mission_id": result.mission_id,
            "ok": True,
            "name": mission.name,
            "cost_usd": result.cost_usd,
            "lanes": [{"name": "a", "ok": True, "kind": None}],
        }
    ]
    report = Path(result.report_path).read_text()
    assert "## Notifications" in report and "- end: ok" in report
    result_doc = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert result_doc["notifications"] == result.notifications


def test_breaker_trip_emits_breaker_event(repo, home, monkeypatch, tmp_path):
    one = _codex_event("one", "one")
    two = _codex_event("two", "two")
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"printf '%s\\n%s' '{one}' '{two}'"]
    )
    notify_file = tmp_path / "events.jsonl"
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "max_tool_calls": 1,
            "notify": {"command": f"cat >> {notify_file}"},
            "lanes": [{"fleet": "codex", "name": "a"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    lane = result.lanes[0]
    assert lane["breaker"] is not None and lane["ok"] is False
    assert [n["event"] for n in result.notifications] == ["breaker", "end"]
    assert result.notifications[0]["ok"] is True
    events = [json.loads(text) for text in notify_file.read_text().splitlines()]
    assert events[0] == {
        "event": "breaker",
        "mission_id": result.mission_id,
        "lane": "a",
        "breaker": lane["breaker"],
        "run_id": lane["attempts"][-1]["run_id"],
        "cost_usd": lane["cost_usd"],
    }


def test_events_filter_suppresses_unlisted_events(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", _ok_argv)
    notify_file = tmp_path / "events.jsonl"
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "notify": {"command": f"cat >> {notify_file}", "events": ["pause"]},
            "lanes": [{"name": "a", "fleet": "claude"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    assert result.notifications == []
    assert not notify_file.exists()
    assert "## Notifications" not in Path(result.report_path).read_text()


def test_failing_notify_command_never_changes_ok(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", _ok_argv)
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "notify": {"command": "exit 7"},
            "lanes": [{"name": "a", "fleet": "claude"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    assert len(result.notifications) == 1
    note = result.notifications[0]
    assert note["ok"] is False and note["exit_code"] == 7
    report = Path(result.report_path).read_text()
    assert "## Notifications" in report and "- end: failed: exit 7" in report
    result_doc = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert result_doc["ok"] is True
    assert result_doc["notifications"][0]["exit_code"] == 7


def test_dry_run_emits_nothing(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", _ok_argv)
    notify_file = tmp_path / "events.jsonl"
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "notify": {"command": f"cat >> {notify_file}"},
            "lanes": [{"name": "a", "fleet": "claude"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home, dry_run=True)
    assert result.notifications == []
    assert not notify_file.exists()


# --- item 4: documentation -----------------------------------------------------


def test_readme_documents_notifications():
    section = doc_section("#### Notifications")
    compact = " ".join(section.split())
    assert '"events": ["pause", "end", "breaker"]' in compact
    assert "CONDUCTOR_EVENT" in compact and "CONDUCTOR_MISSION" in compact
    assert "never changes `ok`, an exit code, or a pause" in compact
    assert "Not sent while the mission is parked" in compact
