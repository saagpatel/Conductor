"""C7: golden-mission regression suite.

Fleets are faked at the argv boundary, as in `test_mission.py`, so recording
and replaying a fixture here costs nothing and needs no CLI installed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from conductor import golden
from conductor import outputs as outputs_mod
from conductor import runner as runner_mod
from conductor.cli import main
from conductor.mission import mission_from_dict, run_mission
from conductor.runner import Result

GOLDEN_DIR = Path(__file__).parent / "golden"


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]


def fake_fleets(monkeypatch: pytest.MonkeyPatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


# --- scrub -------------------------------------------------------------


def test_scrub_replaces_home_cwd_and_user_with_placeholders(tmp_path):
    home = tmp_path / "conductor-home"
    cwd = tmp_path / "repo"
    replacements = golden._placeholder_map(home=home, cwd=str(cwd))
    text = f"working in {cwd}, receipts under {home}, real home {Path.home()}"
    scrubbed = golden.scrub_text(text, replacements)
    assert str(cwd) not in scrubbed
    assert str(home) not in scrubbed
    assert str(Path.home()) not in scrubbed
    assert "<cwd>" in scrubbed and "<home>" in scrubbed and "<user>" in scrubbed


@pytest.mark.parametrize(
    "text",
    [
        "MY_API_TOKEN=abc123def456",
        "AWS_SECRET_ACCESS_KEY=abcdefghijklmnop",
        "DB_PASSWORD=hunter2hunter2",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "key is sk-abcdefghijklmnopqrstuvwx",
        "token xai-abcdefghijklmnopqrstuvwx",
        "token ghp_abcdefghijklmnopqrstuvwx",
        "token AIzaabcdefghijklmnopqrstuvwx",
    ],
)
def test_scrub_redacts_every_secret_pattern(text):
    scrubbed = golden.scrub_text(text, [])
    assert "<redacted>" in scrubbed
    for secret in ("abc123def456", "hunter2hunter2", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"):
        assert secret not in scrubbed


def test_scrub_leaves_a_clean_string_unchanged():
    text = "nothing sensitive here, just prose about a fix"
    assert golden.scrub_text(text, []) == text


def test_scrub_is_idempotent(tmp_path):
    home = tmp_path / "conductor-home"
    cwd = tmp_path / "repo"
    replacements = golden._placeholder_map(home=home, cwd=str(cwd))
    text = f"cwd={cwd} TOKEN=abc123def456ghi789"
    once = golden.scrub_text(text, replacements)
    twice = golden.scrub_text(once, replacements)
    assert once == twice


def test_scrub_json_redacts_a_secret_shaped_key(tmp_path):
    obj = {"api_key": "abc123def456ghi789", "note": "fine"}
    scrubbed = json.loads(golden.scrub_json_text(json.dumps(obj), []))
    assert scrubbed["api_key"] == "<redacted>"
    assert scrubbed["note"] == "fine"


def test_scrub_guard_does_not_read_raw_base64_as_an_env_secret(tmp_path):
    """The base64 alphabet can spell "...KEY=" right before its padding; a
    plain-text scan of the raw run read that as an env secret (E13's export
    hit it on a re-encoded receipt payload). Only the decoded text counts."""
    clean = b"a clean statement with nothing secret in it, padded to len " * 2
    clean = clean[: len(clean) - len(clean) % 3]
    tail = base64.b64decode("KEY=")
    encoded = base64.b64encode(clean + tail).decode()
    assert encoded.endswith("KEY=")
    (tmp_path / "receipt.json").write_text(f'{{"payload": "{encoded}"}}\n')
    assert golden.scrub_guard(tmp_path) == []


def test_scrub_guard_does_not_read_a_signature_ending_in_key_as_an_env_secret(tmp_path):
    """A 44-character DSSE signature is under the 64-character decode
    threshold; it still must not read as `...KEY=` to the plain-text scan."""
    sig = "A" * 40 + "KEY="
    assert len(sig) == 44
    (tmp_path / "receipt.json").write_text(f'{{"sig": "{sig}"}}\n')
    assert golden.scrub_guard(tmp_path) == []


def test_scrub_guard_still_finds_a_long_alphanumeric_secret_value(tmp_path):
    (tmp_path / "env.txt").write_text("MY_SECRET_KEY=" + "a" * 48 + "\n")
    assert golden.scrub_guard(tmp_path) == ["env.txt:1: env secret"]


def test_scrub_guard_decodes_a_base64_blob_and_finds_the_user_home(tmp_path):
    # A DSSE-style payload: real secrets base64-encoded inside a JSON field,
    # the way attestation.json carries its statement. Plain-text scanning
    # never sees these; scrub_guard must decode and re-scan.
    secret = f"cwd was {Path.home()}/repo, API_TOKEN=abc123def456ghi789"
    encoded = base64.b64encode(secret.encode()).decode()
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    (fixture_dir / "blob.json").write_text(json.dumps({"payload": encoded}))

    findings = golden.scrub_guard(fixture_dir)
    assert any("(base64)" in f for f in findings)
    assert any("user home" in f for f in findings)
    assert any("env secret" in f for f in findings)


# --- elision -------------------------------------------------------------


def _parsed_fields(output: outputs_mod.FleetOutput) -> tuple:
    return (
        output.answer,
        output.usage.to_dict() if output.usage else None,
        output.status,
        output.error,
        output.session_id,
    )


def test_elision_preserves_claude_result_and_assistant_text():
    long_text = "A" * 600
    long_input = "B" * 600
    long_result = "C" * 600
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "text", "text": long_text},
                        {"type": "tool_use", "name": "Edit", "input": {"old_string": long_input}},
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": long_result,
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "session_id": "sess-claude-1",
            }
        ),
    ]
    text = "\n".join(lines)
    elided = golden.elide_stream(text)
    obj1, obj2 = (json.loads(line) for line in elided.splitlines())
    assert obj1["message"]["content"][0]["text"] == long_text
    elided_input = obj1["message"]["content"][1]["input"]["old_string"]
    assert elided_input.startswith("<elided 600 chars sha256=")
    assert obj2["result"] == long_result
    before = outputs_mod.parse("claude", text)
    after = outputs_mod.parse("claude", elided)
    assert _parsed_fields(before) == _parsed_fields(after)
    assert before.answer == long_result


def test_elision_preserves_cursor_plan_and_assistant_text():
    long_plan = "P" * 600
    long_text = "Q" * 600
    long_result = "R" * 600
    lines = [
        json.dumps(
            {
                "type": "tool_call",
                "subtype": "completed",
                "tool_call": {"createPlanToolCall": {"args": {"plan": long_plan}}},
            }
        ),
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": long_text}]}}
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": long_result,
                "usage": {"inputTokens": 10, "outputTokens": 5},
                "session_id": "sess-cursor-1",
            }
        ),
    ]
    text = "\n".join(lines)
    elided = golden.elide_stream(text)
    plan_obj, text_obj, result_obj = (json.loads(line) for line in elided.splitlines())
    assert plan_obj["tool_call"]["createPlanToolCall"]["args"]["plan"] == long_plan
    assert text_obj["message"]["content"][0]["text"] == long_text
    assert result_obj["result"] == long_result
    before = outputs_mod.parse("cursor", text)
    after = outputs_mod.parse("cursor", elided)
    assert _parsed_fields(before) == _parsed_fields(after)
    assert before.answer == f"{long_plan}\n\n{long_text}"


def test_elision_preserves_antigravity_response_and_elides_the_rest():
    long_response = "S" * 600
    long_tool_output = "T" * 600
    lines = [
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 0,
                    "usage": {"input_tokens": 5, "output_tokens": 5},
                    "tool_output": long_tool_output,
                },
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "response": long_response,
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                },
                "conversation_id": "sess-agy-1",
            }
        ),
    ]
    text = "\n".join(lines)
    elided = golden.elide_stream(text)
    step_obj, result_obj = (json.loads(line) for line in elided.splitlines())
    assert step_obj["step_update"]["tool_output"].startswith("<elided 600 chars sha256=")
    assert result_obj["result"]["response"] == long_response
    before = outputs_mod.parse("antigravity", text)
    after = outputs_mod.parse("antigravity", elided)
    assert _parsed_fields(before) == _parsed_fields(after)
    assert before.answer == long_response


# --- Result.from_dict --------------------------------------------------


def _base_result(**overrides) -> Result:
    fields = dict(
        run_id="r1",
        fleet="claude",
        model="claude-sonnet-5",
        effort="standard",
        mode="read",
        cwd="/tmp/repo",
        timeout=600,
        exit_code=0,
        timed_out=False,
        duration_s=1.5,
        run_dir="/tmp/runs/r1",
        stdout_path="/tmp/runs/r1/stdout.log",
        stderr_path="/tmp/runs/r1/stderr.log",
        tail="ok",
        spawned=True,
    )
    fields.update(overrides)
    return Result(**fields)


def test_result_from_dict_round_trips():
    r = _base_result()
    assert Result.from_dict(r.to_dict()) == r


def test_result_from_dict_refuses_an_unknown_key():
    d = _base_result().to_dict()
    d["something_new"] = "surprise"
    with pytest.raises(ValueError, match="something_new"):
        Result.from_dict(d)


def test_result_from_dict_defaults_a_missing_newer_field():
    d = _base_result().to_dict()
    del d["cancelled"]
    del d["ok"]
    del d["kind"]
    r = Result.from_dict(d)
    assert r.cancelled is False


def test_result_from_dict_requires_the_fields_without_defaults():
    d = _base_result().to_dict()
    del d["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        Result.from_dict(d)


# --- record then check --------------------------------------------------


def _two_lane_mission_raw(repo: Path) -> dict:
    return {
        "name": "golden-smoke",
        "prompt": "write, then read it back",
        "cwd": str(repo),
        "lanes": [
            {"name": "write", "fleet": "claude", "mode": "write", "commit": "feat: golden"},
            {
                "name": "read",
                "fleet": "cursor",
                "mode": "read",
                "needs": ["write"],
                "prompt": "read the lane answer: {{lanes.write.answer}}",
            },
        ],
    }


def _record_smoke_mission(repo, home, monkeypatch, tmp_path):
    fake_fleets(
        monkeypatch,
        {
            "claude": ["sh", "-c", f"echo x > x.txt && echo '{envelope('done', 0.01)}'"],
            "cursor": say("ok"),
        },
    )
    m = mission_from_dict(_two_lane_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(m, home=home)
    assert result.ok is True
    return Path(result.mission_dir)


def test_record_then_check_is_clean(repo, home, monkeypatch, tmp_path):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    assert golden.scrub_guard(fixture) == []
    assert golden.check(fixture) == []


def test_recorded_fixture_never_contains_a_dot_log_file(repo, home, monkeypatch, tmp_path):
    # The operator's global git excludes drop every `*.log` path, silently,
    # from any `git add`. A fixture file with that extension looks committed
    # (the record-time gate passes against the working tree) but never
    # actually lands in the repo, so a later, fresh checkout replays against
    # a fixture missing its transcripts. No fixture file may use it.
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    log_files = sorted(p.relative_to(fixture) for p in fixture.rglob("*.log"))
    assert log_files == []


def test_recorded_fixture_excludes_attestation_json(repo, home, monkeypatch, tmp_path):
    # attestation.json's DSSE payload is base64 over the real run paths and
    # its signature cannot be verified without the operator's key, so a
    # scrubbed copy would be both unscrubbed and unverifiable. It is never
    # copied into a fixture.
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    run_dirs = list((home / "runs").iterdir())
    assert any((d / "attestation.json").is_file() for d in run_dirs), (
        "test setup: expected the smoke mission to produce a real attestation.json"
    )
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    assert list(fixture.rglob("attestation.json")) == []


def test_check_reports_a_rendered_prompt_difference_after_a_template_edit(
    repo, home, monkeypatch, tmp_path
):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)

    snapshot = json.loads((fixture / "mission.json").read_text())
    for lane in snapshot["lanes"]:
        if lane["name"] == "read":
            lane["attempts"][0]["prompt"] = "please read carefully: {{lanes.write.answer}}"
    (fixture / "mission.json").write_text(json.dumps(snapshot, indent=2))

    diffs = golden.check(fixture)
    assert any("rendered prompt differs from the recording" in line for line in diffs)


def test_check_update_rewrites_a_hand_edited_expected_json(repo, home, monkeypatch, tmp_path):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)

    expected = json.loads((fixture / "expected.json").read_text())
    expected["ok"] = not expected["ok"]
    (fixture / "expected.json").write_text(json.dumps(expected, indent=2))
    assert golden.check(fixture) != []

    golden.check(fixture, update=True)
    assert golden.check(fixture) == []


def test_replay_past_the_recorded_count_is_reported_and_the_mission_still_completes(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"cursor": ["sh", "-c", "exit 3"]})
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"name": "r", "fleet": "cursor", "mode": "read"}],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(m, home=home)
    assert result.ok is False
    mission_dir = Path(result.mission_dir)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)

    # Give the replayed lane a fallback the recording never dispatched.
    snapshot = json.loads((fixture / "mission.json").read_text())
    for lane in snapshot["lanes"]:
        if lane["name"] == "r":
            lane["attempts"].append(dict(lane["attempts"][0]))
    (fixture / "mission.json").write_text(json.dumps(snapshot, indent=2))

    replayed = golden._fresh_replay(fixture)
    assert any(
        "replay dispatched attempt 2 but the recording has 1" in d for d in replayed.differences
    )
    assert replayed.result is not None
    assert replayed.result.mission_id


# --- dispatcher bypasses runner.dispatch --------------------------------


def test_run_mission_with_a_dispatcher_never_calls_runner_dispatch(
    repo, home, monkeypatch, git_out
):
    def boom(*args, **kwargs):
        raise AssertionError("runner.dispatch must not be called when a dispatcher is given")

    monkeypatch.setattr(runner_mod, "dispatch", boom)
    from conductor import mission as mission_mod

    monkeypatch.setattr(mission_mod, "dispatch", boom)

    calls: list[str] = []

    def dispatcher(spec, **kwargs):
        calls.append(kwargs["lane"])
        return Result(
            run_id="fake-run",
            fleet=spec.fleet,
            model="claude-sonnet-5",
            effort=spec.effort,
            mode=spec.mode,
            cwd=str(repo),
            timeout=600,
            exit_code=0,
            timed_out=False,
            duration_s=0.1,
            run_dir=str(home / "runs" / "fake-run"),
            stdout_path="",
            stderr_path="",
            tail="ok",
            spawned=True,
            git_verdict={"checked": True, "no_op": False, "commits_added": 1},
            isolation={"tip_sha": "deadbeef", "clean": True, "branch": "conductor/fake-run"},
            commit={"committed": True, "sha": "deadbeef"},
        )

    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [
            {
                "name": "w",
                "fleet": "claude",
                "mode": "write",
                "commit": "feat: x",
                "branch": "feature/golden",
            }
        ],
    }
    from conductor.mission import mission_from_dict

    m = mission_from_dict(raw, base_dir=repo)
    result = run_mission(m, home=home, dispatcher=dispatcher)
    assert calls == ["w"]
    assert result.lanes[0]["ok"] is True
    assert result.lanes[0]["branch"] == "feature/golden"
    branches = git_out(repo, "branch", "--list")
    assert "feature/golden" not in branches


# --- F8: replay never runs the notify hook --------------------------------


def _record_notify_mission(repo, home, monkeypatch, tmp_path, notify_file: Path) -> Path:
    fake_fleets(monkeypatch, {"claude": say("done", 0.01)})
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "notify": {"command": f"cat >> {notify_file}"},
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is True
    assert [n["event"] for n in result.notifications] == ["end"]
    return Path(result.mission_dir)


def test_replay_records_the_notify_event_without_running_the_command(
    repo, home, monkeypatch, tmp_path
):
    notify_file = tmp_path / "events.jsonl"
    mission_dir = _record_notify_mission(repo, home, monkeypatch, tmp_path, notify_file)
    live_lines = notify_file.read_text().splitlines()
    assert len(live_lines) == 1

    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    assert golden.check(fixture) == []
    # `record` replays once and `check` replays once more: neither ran the
    # hook, or the live run's single line would have company.
    assert notify_file.read_text().splitlines() == live_lines
    expected = json.loads((fixture / "expected.json").read_text())
    assert expected["notifications"] == ["end"]
    replayed = golden._fresh_replay(fixture)
    assert replayed.result.notifications == [
        {"event": "end", "ok": True, "exit_code": 0, "timed_out": False, "error": None}
    ]


def test_replay_notifier_ignores_the_hook_outcome_but_pins_the_event(
    repo, home, monkeypatch, tmp_path
):
    # A hook that fails live (exit 1) is a note on the live result, never on
    # the projection: the fixture pins that `end` reached the hook, not what
    # the operator's machine answered.
    fake_fleets(monkeypatch, {"claude": say("done", 0.01)})
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "notify": {"command": "exit 1"},
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.notifications[0]["ok"] is False
    fixture = golden.record(Path(result.mission_dir), tmp_path / "fixture", home=home)
    assert golden.check(fixture) == []
    assert json.loads((fixture / "expected.json").read_text())["notifications"] == ["end"]


def test_projection_omits_notifications_when_none_fired(repo, home, monkeypatch, tmp_path):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    assert "notifications" not in json.loads((fixture / "expected.json").read_text())


def test_run_mission_notifier_stands_in_for_emit(repo, home, monkeypatch, tmp_path):
    from conductor import notify as notify_mod

    def boom(*args, **kwargs):
        raise AssertionError("notify.emit must not be called when a notifier is given")

    monkeypatch.setattr(notify_mod, "emit", boom)
    fake_fleets(monkeypatch, {"claude": say("done", 0.01)})
    seen: list[tuple[dict, dict]] = []

    def notifier(config, event):
        seen.append((config, event))
        return {"event": event["event"], "ok": True, "exit_code": 0, "timed_out": False}

    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "notify": {"command": "true"},
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, notifier=notifier)
    assert [event["event"] for _, event in seen] == ["end"]
    assert seen[0][0]["command"] == "true"
    assert result.notifications == [
        {"event": "end", "ok": True, "exit_code": 0, "timed_out": False}
    ]


# --- F8: collate, judge, and resolve runs replay offline ---------------------


def _antigravity_envelope(answer: dict) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": json.dumps(answer),
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )


def _codex_stream(text: str) -> str:
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
    done = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return f"{item}\n{done}\n"


def _forbid_live_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    from conductor import mission as mission_mod

    def boom(*args, **kwargs):
        raise AssertionError("a replay dispatched live")

    monkeypatch.setattr(runner_mod, "dispatch", boom)
    monkeypatch.setattr(mission_mod, "dispatch", boom)


def _sitting_mission_raw(repo) -> dict:
    return {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": {
            "fleet": "antigravity",
            "rank": True,
            "judges": [{"fleet": "antigravity", "model": "gemini-3.7-flash"}],
        },
    }


def test_judge_sitting_records_every_judge_run_and_replays_without_a_vendor(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(
        monkeypatch,
        {
            "claude": say("done", 0.01),
            "codex": ["sh", "-c", f"printf '%s' '{_codex_stream('done')}'"],
            "antigravity": [
                "sh",
                "-c",
                f"printf '%s' '{_antigravity_envelope({'strongest': 'a', 'reason': 'r'})}'",
            ],
        },
    )
    result = run_mission(
        mission_from_dict(_sitting_mission_raw(repo), base_dir=tmp_path), home=home
    )
    assert result.ok is True and result.collate["strongest"] == "a"
    judge_runs = [order["run_id"] for order in result.collate["orders"]] + [
        order["run_id"] for judge in result.collate["judges"] for order in judge["orders"]
    ]
    assert len(judge_runs) == 4

    fixture = golden.record(Path(result.mission_dir), tmp_path / "fixture", home=home)
    recorded = {p.name for p in (fixture / "runs").iterdir()}
    assert set(judge_runs) <= recorded
    manifest = json.loads((fixture / "golden.json").read_text())
    assert manifest["fleets"] == ["antigravity", "claude", "codex"]
    expected = json.loads((fixture / "expected.json").read_text())
    assert expected["collate"] == {
        "ok": True,
        "rank": True,
        "strongest": "a",
        "error": None,
        "agreement": "unanimous",
        "votes": {"a": 4, "b": 0},
    }

    _forbid_live_dispatch(monkeypatch)
    assert golden.check(fixture) == []
    replayed = golden._fresh_replay(fixture)
    assert replayed.result.collate["strongest"] == "a"
    assert replayed.result.collate["tally"]["agreement"] == "unanimous"


def test_plain_collate_with_a_merge_conflict_replays_the_recorded_conflict(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(
        monkeypatch,
        {
            "claude": ["sh", "-c", f"echo A > x.txt && echo '{envelope('a', 0.01)}'"],
            "codex": ["sh", "-c", f"echo B > x.txt && printf '%s' '{_codex_stream('b')}'"],
            "antigravity": [
                "sh",
                "-c",
                f"printf '%s' '{_antigravity_envelope({'summary': 'both touch x.txt'})}'",
            ],
        },
    )
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "write", "prompt": "A", "commit": "feat: a"},
            {"name": "b", "fleet": "codex", "mode": "write", "prompt": "B", "commit": "feat: b"},
        ],
        "collate": {"fleet": "antigravity", "instructions": "Summarize."},
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is True
    assert result.collisions["conflicts"]["pairs"] == [
        {"lanes": ["a", "b"], "conflicts": ["x.txt"]}
    ]
    collate_prompt = Path(home, "runs", result.collate["run_id"], "prompt.txt").read_text()
    assert "(conflict: a, b)" in collate_prompt

    fixture = golden.record(Path(result.mission_dir), tmp_path / "fixture", home=home)
    assert result.collate["run_id"] in {p.name for p in (fixture / "runs").iterdir()}
    expected = json.loads((fixture / "expected.json").read_text())
    assert expected["collate"] == {
        "ok": True,
        "rank": None,
        "strongest": None,
        "error": None,
        "agreement": None,
        "votes": None,
    }
    _forbid_live_dispatch(monkeypatch)
    assert golden.check(fixture) == []


def test_projection_omits_collate_and_resolve_when_the_mission_had_none(
    repo, home, monkeypatch, tmp_path
):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    expected = json.loads((fixture / "expected.json").read_text())
    assert "collate" not in expected and "resolve" not in expected


def test_replay_reports_a_recorded_run_with_no_receipt_instead_of_crashing(
    repo, home, monkeypatch, tmp_path
):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    run_dirs = sorted(p for p in (fixture / "runs").iterdir())
    (run_dirs[0] / "result.json").unlink()
    differences = golden.check(fixture)
    assert any("has no result.json in the fixture" in line for line in differences)


def test_scrub_json_text_scrubs_object_keys(tmp_path):
    replacements = golden._placeholder_map(home=tmp_path / "home", cwd=str(tmp_path / "repo"))
    text = json.dumps({"files": {f"{tmp_path / 'repo'}:shared.txt": ["a", "b"]}})
    scrubbed = json.loads(golden.scrub_json_text(text, replacements))
    assert list(scrubbed["files"]) == ["<cwd>:shared.txt"]


def test_record_refuses_a_fixture_the_guard_would_flag(repo, home, monkeypatch, tmp_path):
    # A base64-encoded home path survives every placeholder replacement and
    # only the guard's decoded scan sees it -- exactly what `record` must
    # refuse on its own rather than leave for a separate step.
    payload = (str(Path.home()) + "/leaked").ljust(48, "x")
    while len(payload) % 3:
        payload += "x"
    leak = base64.b64encode(payload.encode()).decode()
    assert len(leak) >= 64 and "=" not in leak
    fake_fleets(monkeypatch, {"claude": say(leak, 0.01)})
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"name": "a", "fleet": "claude"}]}
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    with pytest.raises(golden.GoldenError, match="would leak"):
        golden.record(Path(result.mission_dir), tmp_path / "fixture", home=home)
    assert not (tmp_path / "fixture").exists()


def test_elision_keeps_a_structured_output_answer_whole():
    reason = "r" * 700
    line = json.dumps(
        {
            "type": "result",
            "result": "short",
            "structured_output": {"strongest": "a", "reason": reason},
        }
    )
    elided = json.loads(golden._elide_line(line))
    assert elided["structured_output"]["reason"] == reason


# --- CLI -----------------------------------------------------------------


def test_cli_golden_record_and_check(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    out_dir = tmp_path / "cli-fixture"

    assert main(["golden", "record", mission_dir.name, "--out", str(out_dir)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["fixture"] == str(out_dir)
    assert printed["bytes"] > 0

    assert main(["golden", "check", str(out_dir)]) == 0
    capsys.readouterr()

    snapshot = json.loads((out_dir / "mission.json").read_text())
    for lane in snapshot["lanes"]:
        if lane["name"] == "read":
            lane["attempts"][0]["prompt"] = "changed: {{lanes.write.answer}}"
    (out_dir / "mission.json").write_text(json.dumps(snapshot, indent=2))

    assert main(["golden", "check", str(out_dir)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert any(out_dir.name in line and "rendered prompt differs" in line for line in lines)


def test_cli_golden_record_exits_1_on_refusal(home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["golden", "record", "no-such-mission", "--out", str(tmp_path / "x")]) == 1


# --- committed fixtures --------------------------------------------------


def _fixture_dirs() -> list[Path]:
    if not GOLDEN_DIR.is_dir():
        return []
    return sorted(p.parent for p in GOLDEN_DIR.glob("*/golden.json"))


@pytest.mark.parametrize("fixture_dir", _fixture_dirs(), ids=lambda p: p.name)
def test_committed_fixture_is_clean_and_within_budget(fixture_dir):
    assert golden.check(fixture_dir) == []
    assert golden.scrub_guard(fixture_dir) == []

    total = 0
    manifest = json.loads((fixture_dir / "golden.json").read_text())
    for rel, expected_sha in manifest["files"].items():
        path = fixture_dir / rel
        assert path.is_file(), f"golden.json names {rel} but it is missing"
        data = path.read_bytes()
        total += len(data)
        assert hashlib.sha256(data).hexdigest() == expected_sha, f"{rel} sha256 mismatch"

    present = {
        str(p.relative_to(fixture_dir))
        for p in fixture_dir.rglob("*")
        if p.is_file() and p.name != "golden.json"
    }
    assert present == set(manifest["files"])
    assert total < golden.DEFAULT_MAX_BYTES


# --- E7: a human lane's deliverable through golden -------------------------


def test_recorded_human_lane_deliverable_replays_as_answered(repo, home, monkeypatch, tmp_path):
    """Review finding (Grok, item 3): a human lane's declared deliverable
    lives in the repo, not under the mission directory, so nothing copied it
    into the fixture; `_fresh_replay`'s checkout is empty, so the deliverable
    read at replay time is always missing and the fixture freezes the lane
    as skipped even though the real, recorded run answered it with the file
    present. `expected.json` must match what actually happened, not what a
    replay starved of the file falls back to."""
    from conductor.mission import Mission

    fake_fleets(monkeypatch, {"claude": say("built")})
    raw = {
        "cwd": str(repo),
        "concurrency": 1,
        "lanes": [
            {"name": "build", "fleet": "claude", "prompt": "BUILD"},
            {
                "name": "ask",
                "fleet": "human",
                "prompt": "Approve {{lanes.build.answer}}?",
                "needs": ["build"],
                "deliverable": {"path": "sign-off.txt"},
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is False and first.paused["kind"] == "human"

    (repo / "sign-off.txt").write_text("signed\n")
    snapshot = Mission.from_snapshot(
        json.loads((Path(first.mission_dir) / "mission.json").read_text())
    )
    resumed = run_mission(
        snapshot, home=home, resume_dir=Path(first.mission_dir), answer="approved"
    )
    assert resumed.ok is True

    fixture = golden.record(Path(resumed.mission_dir), tmp_path / "fixture", home=home)
    expected = json.loads((fixture / "expected.json").read_text())
    assert expected["ok"] is True
    ask_lane = next(lane for lane in expected["lanes"] if lane["name"] == "ask")
    assert ask_lane["ok"] is True
    assert golden.check(fixture) == []


# --- README ----------------------------------------------------------------


def test_readme_documents_golden_missions():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("## Golden missions", 1)[1].split("\n## ", 1)[0]
    assert "docs/ROADMAP-2026-09.md" in section and "item C7" in section
    assert "<home>" in section and "<cwd>" in section and "<user>" in section
    assert "TOKEN" in section and "SECRET" in section and "KEY" in section and "PASSWORD" in section
    assert "Bearer <redacted>" in section
    assert "sk-" in section and "xai-" in section and "ghp_" in section and "AIza" in section
    assert "<elided N chars" in section
    assert "conductor golden record" in section
    assert "conductor golden check" in section
    assert "expected.json" in section and "--update" in section
    assert "dispatcher" in section
