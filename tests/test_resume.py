"""Fleet sessions are receipts, and resume is guarded lineage rather than a hint."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import build_parser, main
from conductor.fleets import DispatchRefused, Spec, build_argv
from conductor.mission import MissionInvalid, mission_from_dict, run_mission
from conductor.runner import dispatch


def test_resume_argv_is_exact_for_every_fleet():
    common = {"prompt": "fix it", "cwd": "/repo", "resume": "SESSION"}
    assert build_argv(Spec(fleet="claude", **common)) == [
        "claude",
        "-p",
        "fix it",
        "--model",
        "claude-sonnet-5",
        "--effort",
        "medium",
        "--output-format",
        "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--setting-sources",
        "project",
        "--permission-mode",
        "plan",
        "--resume",
        "SESSION",
    ]
    assert build_argv(Spec(fleet="codex", model="sol", **common)) == [
        "codex",
        "-C",
        "/repo",
        "--sandbox",
        "read-only",
        "exec",
        "resume",
        "SESSION",
        "-m",
        "gpt-5.6-sol",
        "-c",
        "approval_policy=never",
        "-c",
        "model_reasoning_effort=medium",
        "--skip-git-repo-check",
        "--json",
        "fix it",
    ]
    assert build_argv(Spec(fleet="antigravity", **common)) == [
        "agy",
        "-p",
        "fix it",
        "--add-dir",
        "/repo",
        "--model",
        "gemini-3.8-flash-medium",
        "--effort",
        "medium",
        "--output-format",
        "stream-json",
        "--print-timeout",
        "595s",
        "--mode",
        "plan",
        "--sandbox",
        "--conversation",
        "SESSION",
    ]
    assert build_argv(Spec(fleet="cursor", **common)) == [
        "cursor-agent",
        "-p",
        "fix it",
        "--model",
        "cursor-grok-4.6-medium",
        "--output-format",
        "stream-json",
        "--mode",
        "plan",
        "--trust",
        "--resume",
        "SESSION",
    ]


def test_codex_resume_keeps_schema_and_output_flags_after_json(tmp_path: Path):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    argv = build_argv(
        Spec(
            fleet="codex",
            prompt="x",
            cwd="/repo",
            resume="S",
            schema=str(schema),
            last_message="/tmp/answer",
        )
    )
    assert argv[-6:] == [
        "--json",
        "--output-schema",
        str(schema),
        "-o",
        "/tmp/answer",
        "x",
    ]


def test_resume_dispatch_accepts_only_the_requested_session(repo, home, fake_fleet):
    fake_fleet(session_id="same")
    result = dispatch(
        Spec(fleet="claude", prompt="x", cwd=str(repo), resume="same"), home=home
    )
    assert result.ok is True
    assert result.session_id == "same"
    assert result.resumed == {"requested": "same", "ok": True, "session_id": "same"}
    assert result.summary()["session_id"] == "same"
    assert "resumed session same" in result.git_verdict["notes"]
    saved = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert saved["session_id"] == "same" and saved["resumed"]["ok"] is True


@pytest.mark.parametrize(("reported", "got"), [("different", "different"), (None, "none")])
def test_resume_dispatch_rejects_a_different_or_missing_session(
    repo, home, fake_fleet, reported, got
):
    fake_fleet(session_id=reported)
    result = dispatch(
        Spec(fleet="claude", prompt="x", cwd=str(repo), resume="wanted"), home=home
    )
    expected = f"resume failed: fleet reported session {got}, requested wanted"
    assert result.ok is False
    assert result.error == expected
    assert result.summary()["failure"] == expected
    assert result.resumed == {"requested": "wanted", "ok": False, "session_id": reported}
    assert expected in result.git_verdict["notes"]


def test_resume_guard_preserves_an_earlier_dispatch_error(repo, home, fake_fleet):
    fake_fleet(argv=["/definitely/missing/fleet"])
    result = dispatch(
        Spec(fleet="claude", prompt="x", cwd=str(repo), resume="wanted"), home=home
    )

    guard = "resume failed: fleet reported session none, requested wanted"
    assert result.error is not None and result.error.startswith("cannot spawn claude:")
    assert result.resumed == {"requested": "wanted", "ok": False, "session_id": None}
    assert guard in result.git_verdict["notes"]


def test_runs_lists_the_recorded_session_id(home, monkeypatch, capsys):
    directory = home / "runs" / "20260903T000000Z-codex-x"
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "run_id": directory.name,
                "ok": True,
                "fleet": "codex",
                "model": "gpt-5.6-sol",
                "session_id": "S",
                "exit_code": 0,
                "duration_s": 1,
            }
        )
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["runs"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["session_id"] == "S"


def test_cli_parses_resume_and_refuses_an_empty_value(repo, capsys):
    args = build_parser().parse_args(
        ["dispatch", "--fleet", "codex", "--cwd", str(repo), "--resume", "S", "x"]
    )
    assert args.resume == "S"
    assert main(
        ["dispatch", "--fleet", "codex", "--cwd", str(repo), "--resume", "", "x"]
    ) == 3
    assert "resume must be a non-empty session id" in capsys.readouterr().err
    with pytest.raises(DispatchRefused, match="non-empty session id"):
        Spec(fleet="codex", prompt="x", cwd=str(repo), resume="  ").validate()


def _stream(fleet: str, session_id: str | None, *, cached: bool = False) -> str:
    if fleet == "codex":
        events = []
        if session_id is not None:
            events.append({"type": "thread.started", "thread_id": session_id})
        events += [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "ok"},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100 if cached else 10,
                    "cached_input_tokens": 40 if cached else 0,
                    "output_tokens": 1,
                },
            },
        ]
        return "\n".join(json.dumps(event) for event in events)
    payload: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "usage": {"inputTokens": 10, "outputTokens": 1},
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return json.dumps(payload)


def _mission_fleets(monkeypatch, session_for) -> None:
    """Run shell stand-ins while preserving the translated argv in argv.json."""

    def fake_build(spec: Spec) -> list[str]:
        session_id = session_for(spec)
        output = _stream(spec.fleet, session_id, cached=spec.prompt.startswith("BUILD"))
        if spec.prompt.startswith("BUILD"):
            action = "printf 'v1\\n' > built.txt"
        elif spec.prompt.startswith("FIX"):
            action = "printf 'fixed\\n' > fixed.txt"
        else:
            action = ":"
        return [
            "sh",
            "-c",
            f"{action}; printf '%s\\n' {shlex.quote(output)}",
            "fake-fleet",
            *build_argv(spec),
        ]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)


def _resume_mission(repo: Path, *, fix_fleet: str = "codex", fallback: bool = False) -> dict:
    fix: dict[str, object] = {
        "name": "fix",
        "fleet": fix_fleet,
        "mode": "write",
        "prompt": "FIX the build",
        "base": "build",
        "resume": "build",
        "commit": "fix",
    }
    if fallback:
        fix["fallback"] = [{"fleet": "codex"}]
    return {
        "cwd": str(repo),
        "concurrency": 1,
        "lanes": [
            {
                "name": "build",
                "fleet": "codex",
                "mode": "write",
                "prompt": "BUILD it",
                "commit": "build",
            },
            fix,
        ],
    }


def test_mission_resumes_the_same_fleet_and_records_cache_visibility(
    repo, home, monkeypatch, tmp_path
):
    _mission_fleets(monkeypatch, lambda spec: "S")
    result = run_mission(
        mission_from_dict(_resume_mission(repo), base_dir=tmp_path), home=home
    )
    build, fix = result.lanes
    assert result.ok is True
    assert fix["resume"] == {
        "from": "build",
        "session_id": "S",
        "applied": True,
        "reason": "resumed",
    }
    assert fix["session_id"] == "S"
    argv = json.loads(
        (Path(fix["attempts"][0]["run_dir"]) / "argv.json").read_text()
    )
    real = argv[argv.index("codex") :]
    assert real[real.index("resume") + 1] == "S"
    receipt = json.loads((Path(result.mission_dir) / "lanes" / "fix.json").read_text())
    assert receipt["resume"]["applied"] is True and receipt["session_id"] == "S"
    mission_receipt = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert mission_receipt["lanes"][1]["attempts"][0]["session_id"] == "S"
    summary = result.summary()["lanes"][1]
    assert summary["resume"] == fix["resume"]
    assert "resumed" not in summary
    report = Path(result.report_path).read_text()
    assert "| cached | resumed |" in report
    assert "40/100 (40%)" in report and "| yes |" in report


def test_mission_runs_fresh_when_the_fleet_differs(repo, home, monkeypatch, tmp_path):
    _mission_fleets(monkeypatch, lambda spec: "S" if spec.prompt.startswith("BUILD") else "C")
    result = run_mission(
        mission_from_dict(_resume_mission(repo, fix_fleet="claude"), base_dir=tmp_path),
        home=home,
    )
    fix = result.lanes[1]
    assert fix["ok"] is True
    assert fix["resume"]["applied"] is False
    assert fix["resume"]["reason"] == "fleet differs: claude vs codex"
    assert fix["attempts"][0]["resumed"] is None
    assert "no: fleet differs: claude vs codex" in Path(result.report_path).read_text()


def test_mission_runs_fresh_when_upstream_has_no_session(repo, home, monkeypatch, tmp_path):
    _mission_fleets(monkeypatch, lambda spec: None if spec.prompt.startswith("BUILD") else "F")
    result = run_mission(
        mission_from_dict(_resume_mission(repo), base_dir=tmp_path), home=home
    )
    fix = result.lanes[1]
    assert fix["ok"] is True
    assert fix["resume"]["applied"] is False
    assert fix["resume"]["reason"] == "upstream recorded no session"
    assert fix["attempts"][0]["resumed"] is None


def test_resume_guard_failure_falls_back_fresh(repo, home, monkeypatch, tmp_path):
    def sessions(spec: Spec) -> str:
        if spec.prompt.startswith("BUILD"):
            return "S"
        return "wrong" if spec.resume else "fresh"

    _mission_fleets(monkeypatch, sessions)
    result = run_mission(
        mission_from_dict(_resume_mission(repo, fallback=True), base_dir=tmp_path), home=home
    )
    fix = result.lanes[1]
    assert fix["ok"] is True and len(fix["attempts"]) == 2
    assert fix["attempts"][0]["resumed"]["ok"] is False
    assert fix["attempts"][1]["resumed"] is None
    assert fix["attempts"][1]["resume"]["reason"] == "previous resume failed"
    argv = json.loads(
        (Path(fix["attempts"][1]["run_dir"]) / "argv.json").read_text()
    )
    real = argv[argv.index("codex") :]
    assert "resume" not in real


@pytest.mark.parametrize(
    ("lanes", "message"),
    [
        ([{"name": "a", "fleet": "codex", "resume": "missing"}], "unknown lane"),
        ([{"name": "a", "fleet": "codex", "resume": "a"}], "itself"),
        (
            [
                {"name": "a", "fleet": "codex"},
                {"name": "b", "fleet": "codex", "resume": "a"},
            ],
            "must be in needs or be base",
        ),
        (
            [
                {"name": "a", "fleet": "codex", "resume": "b", "needs": ["b"]},
                {"name": "b", "fleet": "codex", "resume": "a", "needs": ["a"]},
            ],
            "cycle",
        ),
    ],
)
def test_invalid_resume_lane_relationships_are_refused(tmp_path, lanes, message):
    with pytest.raises(MissionInvalid, match=message):
        mission_from_dict({"prompt": "x", "lanes": lanes}, base_dir=tmp_path)


def test_lane_resume_does_not_copy_into_fallback_attempts(tmp_path):
    mission = mission_from_dict(
        {
            "prompt": "x",
            "lanes": [
                {"name": "build", "fleet": "codex"},
                {
                    "name": "fix",
                    "fleet": "codex",
                    "needs": ["build"],
                    "resume": "build",
                    "fallback": [{"fleet": "claude"}],
                },
            ],
        },
        base_dir=tmp_path,
    )
    assert mission.lanes[1].resume == "build"
    assert all(not hasattr(attempt, "resume") for attempt in mission.lanes[1].attempts)


def test_readme_documents_the_thread_reuse_contract():
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    section = readme.split("#### Thread reuse", 1)[1].split("\n## ", 1)[0]

    assert '"base": "build"' in section
    assert '"needs": ["review"]' in section
    assert '"resume": "build"' in section
    assert "same fleet" in section
    assert "fleet returned the requested id" in section
    assert "Antigravity" in section and "--conversation MISSING" in section
    assert "--resume SESSION_ID" in readme
    assert "session_id" in readme
