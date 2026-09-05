"""C7: golden-mission regression suite.

Fleets are faked at the argv boundary, as in `test_mission.py`, so recording
and replaying a fixture here costs nothing and needs no CLI installed.
"""

from __future__ import annotations

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
