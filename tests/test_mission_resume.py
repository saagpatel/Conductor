"""Mission resume trusts durable work without paying for it a second time."""

from __future__ import annotations

import json
import shlex
import socket
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conductor import mission as mission_mod
from conductor import runner as runner_mod
from conductor.cli import build_parser, main
from conductor.fleets import Spec
from conductor.mission import Mission, MissionInvalid, load_mission, mission_from_dict, run_mission
from conductor.runner import clear_stop, request_stop


@pytest.fixture(autouse=True)
def _fresh_stop():
    clear_stop()
    yield
    clear_stop()


def _envelope(answer: str, cost: float = 0.1) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "usage": {"input_tokens": 10, "output_tokens": 1},
            "total_cost_usd": cost,
        }
    )


def _command(
    answer: str,
    *,
    cost: float = 0.1,
    action: str = ":",
    exit_code: int = 0,
) -> list[str]:
    output = shlex.quote(_envelope(answer, cost))
    return ["sh", "-c", f"{action}; printf '%s\\n' {output}; exit {exit_code}"]


def _snapshot(result) -> Mission:
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    return Mission.from_snapshot(raw)


def test_snapshot_round_trips_every_mission_feature(tmp_path, repo, home):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Implement the whole specification.\n")
    schema = tmp_path / "shape.json"
    schema.write_text('{"type":"object"}')
    source = tmp_path / "mission.json"
    source.write_text(
        json.dumps(
            {
                "name": "snapshot-all-fields",
                "cwd": str(repo),
                "prompt_file": prompt.name,
                "effort": "hard",
                "timeout": 17,
                "stall_timeout": None,
                "loop_limit": 4,
                "max_tool_calls": 9,
                "test": "true",
                "test_policy": "allow",
                "test_surface": ["tests/**", "pyproject.toml"],
                "isolate": True,
                "cap_usd": 1.5,
                "concurrency": 3,
                "max_cost_usd": 8,
                "template_max_chars": 1234,
                "require": {"pass": 1, "of": ["review", "fix-review"]},
                "lanes": [
                    {
                        "name": "build",
                        "fleet": "codex",
                        "model": "luna",
                        "mode": "write",
                        "schema": schema.name,
                        "commit": "feat: build",
                        "no_op_ok": True,
                    },
                    {
                        "name": "review",
                        "fleet": "claude",
                        "model": "sonnet",
                        "base": "build",
                        "prompt": "Review {{lanes.build.diff}} and {{mission.prompt}}",
                        "verdict": [{"id": "correct", "question": "Is it correct?"}],
                    },
                    {
                        "name": "fix-review",
                        "fleet": "codex",
                        "model": "sol",
                        "base": "build",
                        "needs": ["review"],
                        "resume": "build",
                        "branch": "deliverable/snapshot",
                        "prompt": "Judge {{lanes.review.verdict}}",
                        "verdict": ["correct"],
                        "fallback": [{"fleet": "claude", "model": "opus"}],
                    },
                ],
                "collate": {
                    "fleet": "claude",
                    "model": "haiku",
                    "effort": "cheap",
                    "timeout": 11,
                    "instructions": "Pick the strongest result.",
                    "max_chars": 321,
                    "cap_usd": 0.5,
                    "include_diffs": False,
                },
            }
        )
    )

    mission = load_mission(source)
    result = run_mission(mission, home=home, dry_run=True)
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    reloaded = Mission.from_snapshot(raw)

    assert raw["snapshot_version"] == 1
    assert raw["source"] == str(source.resolve())
    assert reloaded.to_dict() == mission.to_dict() == raw


def test_failed_pipeline_resumes_only_the_failed_lane_and_keeps_total_spend(
    repo, home, monkeypatch, tmp_path
):
    calls = {"BUILD": 0, "REVIEW": 0, "FIX": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        if key == "BUILD":
            return _command("built", cost=0.1, action="printf 'v1\\n' > built.txt")
        if key == "REVIEW":
            return _command("needs a fix", cost=0.2, action="test -f built.txt")
        if calls[key] == 1:
            return _command("failed", cost=0.3, exit_code=7)
        return _command("fixed", cost=0.3, action="printf 'v2\\n' >> built.txt")

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "name": "resume-pipeline",
            "cwd": str(repo),
            "concurrency": 1,
            "max_cost_usd": 2,
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "prompt": "BUILD it",
                    "commit": "build",
                },
                {
                    "name": "review",
                    "fleet": "claude",
                    "base": "build",
                    "prompt": "REVIEW {{lanes.build.diff}}",
                },
                {
                    "name": "fix",
                    "fleet": "claude",
                    "mode": "write",
                    "base": "build",
                    "needs": ["review"],
                    "prompt": "FIX {{lanes.review.answer}}",
                    "commit": "fix",
                },
            ],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    first_runs = {
        lane["name"]: [attempt["run_id"] for attempt in lane["attempts"]]
        for lane in first.lanes
    }
    run_dirs_before = {path.name for path in (home / "runs").iterdir()}

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert first.ok is False and resumed.ok is True
    assert calls == {"BUILD": 1, "REVIEW": 1, "FIX": 2}
    assert [lane["kept"] for lane in resumed.lanes] == [True, True, False]
    assert resumed.lanes[0]["attempts"][0]["run_id"] == first_runs["build"][0]
    assert resumed.lanes[1]["attempts"][0]["run_id"] == first_runs["review"][0]
    assert resumed.lanes[2]["attempts"][0]["run_id"] != first_runs["fix"][0]
    assert resumed.lanes[2]["previous_attempts"][0]["run_id"] == first_runs["fix"][0]
    assert run_dirs_before <= {path.name for path in (home / "runs").iterdir()}
    assert resumed.budget["spent_usd"] == pytest.approx(0.9)
    assert resumed.resumes[0]["kept"] == ["build", "review"]
    assert resumed.resumes[0]["rerun"] == ["fix"]
    report = Path(resumed.report_path).read_text()
    assert "- resumed: attempt 2; kept build, review; rerun fix" in report
    assert "| build | (kept) | True |" in report


def test_dry_run_receipt_is_rerun_before_a_real_resume(repo, home, monkeypatch, tmp_path):
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
        },
        base_dir=tmp_path,
    )
    rehearsal = run_mission(mission, home=home, dry_run=True)
    rehearsal_run = rehearsal.lanes[0]["attempts"][0]["run_id"]
    calls = 0

    def fake_build(spec: Spec) -> list[str]:
        nonlocal calls
        calls += 1
        return _command("actually ran")

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    resumed = run_mission(
        _snapshot(rehearsal), home=home, resume_dir=Path(rehearsal.mission_dir)
    )

    assert calls == 1 and resumed.ok is True
    assert resumed.resumed_from["kept"] == []
    assert resumed.resumed_from["rerun"] == ["a"]
    assert resumed.lanes[0]["previous_attempts"][0]["spawned"] is False
    assert resumed.lanes[0]["attempts"][0]["run_id"] != rehearsal_run


def test_bogus_tip_commit_forces_a_lane_rerun(repo, home, monkeypatch, tmp_path):
    calls = 0

    def fake_build(spec: Spec) -> list[str]:
        nonlocal calls
        calls += 1
        return _command("done", action=f"printf '{calls}\\n' > work.txt")

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "prompt": "BUILD",
                    "commit": "build",
                }
            ],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    receipt_path = Path(first.mission_dir) / "lanes" / "build.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["tip_sha"] = "f" * 40
    receipt_path.write_text(json.dumps(receipt))

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert calls == 2
    assert resumed.resumed_from["kept"] == []
    assert resumed.resumed_from["rerun"] == ["build"]


def test_kept_unpriced_lane_stays_unpriced_in_the_resumed_budget(
    repo, home, monkeypatch, tmp_path
):
    calls = 0
    unpriced = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
        }
    )

    def fake_build(spec: Spec) -> list[str]:
        nonlocal calls
        calls += 1
        return ["sh", "-c", f"printf '%s\\n' {shlex.quote(unpriced)}"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert calls == 1 and resumed.lanes[0]["kept"] is True
    assert resumed.budget["unpriced_dispatches"] == 1
    assert resumed.budget["unverifiable"] is False


def test_moved_deliverable_branch_refuses_then_previous_tip_is_reclaimed(
    repo, home, monkeypatch, tmp_path, git_out
):
    calls = 0

    def fake_build(spec: Spec) -> list[str]:
        nonlocal calls
        calls += 1
        return _command("done", action=f"printf '{calls}\\n' > work.txt")

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "prompt": "BUILD",
                    "commit": "build",
                    "branch": "deliverable/build",
                }
            ],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    tip = first.lanes[0]["tip_sha"]
    subprocess.run(
        ["git", "branch", "-f", "deliverable/build", "main"], cwd=repo, check=True
    )
    with pytest.raises(MissionInvalid, match="already exists"):
        run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))
    assert calls == 1

    subprocess.run(
        ["git", "branch", "-f", "deliverable/build", tip], cwd=repo, check=True
    )
    Path(first.lanes[0]["answer_path"]).unlink()
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert calls == 2 and resumed.ok is True
    assert git_out(repo, "rev-parse", "deliverable/build") == resumed.lanes[0]["tip_sha"]
    assert "deleted branch 'deliverable/build' at its previous tip before rerun" in resumed.notes


def test_dry_run_resume_does_not_delete_a_reclaimable_branch(
    repo, home, monkeypatch, tmp_path, git_out
):
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: _command("done", action="printf 'work\\n' > work.txt"),
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "prompt": "BUILD",
                    "commit": "build",
                    "branch": "deliverable/build",
                }
            ],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    first_tip = first.lanes[0]["tip_sha"]
    Path(first.lanes[0]["answer_path"]).unlink()

    rehearsal = run_mission(
        _snapshot(first),
        home=home,
        dry_run=True,
        resume_dir=Path(first.mission_dir),
    )

    assert git_out(repo, "rev-parse", "deliverable/build") == first_tip
    assert "would delete branch 'deliverable/build'" in "\n".join(rehearsal.notes)
    assert rehearsal.lanes[0]["attempts"][0]["spawned"] is False

    resumed = run_mission(
        _snapshot(rehearsal), home=home, resume_dir=Path(rehearsal.mission_dir)
    )
    assert resumed.ok is True
    assert git_out(repo, "rev-parse", "deliverable/build") == resumed.lanes[0]["tip_sha"]
    assert "deleted branch 'deliverable/build'" in "\n".join(resumed.notes)


def test_interrupted_mission_keeps_ok_lane_and_reruns_interrupted_lane(
    repo, home, monkeypatch, tmp_path
):
    calls = {"FIRST": 0, "SECOND": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        if key == "FIRST":
            return _command("first")
        if calls[key] == 1:
            return ["sh", "-c", "sleep 60"]
        return _command("second")

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "concurrency": 1,
            "lanes": [
                {"name": "first", "fleet": "claude", "prompt": "FIRST"},
                {"name": "second", "fleet": "claude", "prompt": "SECOND"},
            ],
        },
        base_dir=tmp_path,
    )
    threading.Timer(0.8, request_stop).start()
    first = run_mission(mission, home=home)
    clear_stop()

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert first.interrupted is True and resumed.ok is True
    assert calls == {"FIRST": 1, "SECOND": 2}
    assert resumed.resumed_from["kept"] == ["first"]
    assert resumed.resumed_from["rerun"] == ["second"]
    assert resumed.lanes[1]["previous_attempts"][0]["error"].startswith("interrupted")


def test_scheduler_skip_preserves_paid_attempts_across_another_resume(
    repo, home, monkeypatch, tmp_path
):
    calls = {"BUILD": 0, "FIX": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        if key == "BUILD" and calls[key] == 1:
            return _command("built", cost=0.1, action="printf 'work\\n' > work.txt")
        if key == "BUILD":
            return _command("build failed", cost=0.1, exit_code=7)
        return _command("fix failed", cost=0.3, exit_code=8)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "concurrency": 1,
            "max_cost_usd": 0.5,
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "prompt": "BUILD",
                    "commit": "build",
                },
                {
                    "name": "fix",
                    "fleet": "claude",
                    "base": "build",
                    "prompt": "FIX",
                },
            ],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    paid_fix_run = first.lanes[1]["attempts"][0]["run_id"]
    Path(first.lanes[0]["answer_path"]).unlink()

    skipped = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )
    assert skipped.lanes[1]["attempts"] == []
    assert skipped.lanes[1]["previous_attempts"][0]["run_id"] == paid_fix_run
    assert skipped.lanes[1]["cost_usd"] == pytest.approx(0.3)
    assert skipped.budget["spent_usd"] == pytest.approx(0.5)

    calls_before = dict(calls)
    exhausted = run_mission(
        _snapshot(skipped), home=home, resume_dir=Path(skipped.mission_dir)
    )
    assert calls == calls_before
    assert exhausted.budget["spent_usd"] == pytest.approx(0.5)
    assert exhausted.lanes[1]["previous_attempts"][0]["run_id"] == paid_fix_run


def test_collate_is_kept_until_a_summarized_lane_reruns(
    repo, home, monkeypatch, tmp_path
):
    calls = {"A": 0, "B": 0, "You": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        return _command(f"{key} answer")

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A"},
                {"name": "b", "fleet": "claude", "prompt": "B"},
            ],
            "collate": {"fleet": "claude"},
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    kept = run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))
    assert calls == {"A": 1, "B": 1, "You": 1}
    assert kept.resumed_from["collate"] == "kept"

    Path(kept.lanes[0]["answer_path"]).unlink()
    rerun = run_mission(_snapshot(kept), home=home, resume_dir=Path(kept.mission_dir))
    assert calls == {"A": 2, "B": 1, "You": 2}
    assert rerun.resumed_from["collate"] == "rerun"
    assert len(rerun.resumes) == 2


def test_every_rerun_collate_remains_in_the_cumulative_budget(
    repo, home, monkeypatch, tmp_path
):
    def fake_build(spec: Spec) -> list[str]:
        cost = 0.2 if spec.prompt.startswith("You are collating") else 0.1
        return _command("done", cost=cost)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "max_cost_usd": 2,
            "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
            "collate": {"fleet": "claude"},
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    for expected in (0.6, 0.9):
        Path(result.lanes[0]["answer_path"]).unlink()
        result = run_mission(
            _snapshot(result), home=home, resume_dir=Path(result.mission_dir)
        )
        assert result.budget["spent_usd"] == pytest.approx(expected)

    assert len(result.previous_collates) == 2
    assert len({item["run_id"] for item in result.previous_collates}) == 2


def test_unreadable_lane_receipt_reruns_with_visible_preserved_spend(
    repo, home, monkeypatch, tmp_path
):
    calls = 0

    def fake_build(spec: Spec) -> list[str]:
        nonlocal calls
        calls += 1
        return _command("done", cost=0.25)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "max_cost_usd": 1,
            "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    receipt_path = Path(first.mission_dir) / "lanes" / "a.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["future_field"] = "newer conductor"
    receipt_path.write_text(json.dumps(receipt))

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert calls == 2 and resumed.resumed_from["rerun"] == ["a"]
    assert resumed.budget["spent_usd"] == pytest.approx(0.5)
    assert resumed.lanes[0]["previous_attempts"][0]["run_id"] == first.lanes[0][
        "attempts"
    ][0]["run_id"]
    assert "previous receipt unreadable" in "\n".join(resumed.notes)


def test_resume_refusals_stale_lock_cli_and_mission_listing(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["mission", "--resume", "missing", "--dry-run"]) == 3
    assert "does not exist" in capsys.readouterr().err

    incomplete = home / "missions" / "no-snapshot"
    incomplete.mkdir(parents=True)
    assert main(["mission", "--resume", incomplete.name, "--dry-run"]) == 3
    assert "no mission.json" in capsys.readouterr().err

    mission = mission_from_dict(
        {"cwd": str(repo), "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}]},
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home, dry_run=True)
    mission_dir = Path(first.mission_dir)
    snapshot_path = mission_dir / "mission.json"
    valid_snapshot = snapshot_path.read_text()
    invalid_snapshot = json.loads(valid_snapshot)
    invalid_snapshot["snapshot_version"] = 99
    snapshot_path.write_text(json.dumps(invalid_snapshot))
    assert main(["mission", "--resume", first.mission_id, "--dry-run"]) == 3
    assert "snapshot_version" in capsys.readouterr().err
    snapshot_path.write_text(valid_snapshot)

    sleeper = subprocess.Popen(["sleep", "60"])
    try:
        (mission_dir / "running.json").write_text(
            json.dumps(
                {
                    "pid": sleeper.pid,
                    "started": datetime.now(UTC).isoformat(),
                    "host": socket.gethostname(),
                }
            )
        )
        assert main(["mission", "--resume", first.mission_id, "--dry-run"]) == 3
        assert "still running" in capsys.readouterr().err

        (mission_dir / "running.json").write_text(
            json.dumps(
                {
                    "pid": sleeper.pid,
                    "started": "2000-01-01T00:00:00+00:00",
                    "host": socket.gethostname(),
                }
            )
        )
        monkeypatch.setattr(
            mission_mod,
            "_process_started",
            lambda pid: datetime.now(UTC),
        )
        assert main(["mission", "--resume", first.mission_id, "--dry-run"]) == 0
        reused = json.loads(capsys.readouterr().out)
        assert "started after the lock and was reused" in "\n".join(reused["notes"])
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)
        (mission_dir / "running.json").unlink(missing_ok=True)

    (mission_dir / "running.json").write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "started": datetime.now(UTC).isoformat(),
                "host": "another-host.example",
            }
        )
    )
    assert main(["mission", "--resume", first.mission_id, "--dry-run"]) == 3
    assert "differs from this host" in capsys.readouterr().err
    (mission_dir / "running.json").unlink()

    (mission_dir / "running.json").write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "started": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
            }
        )
    )
    assert main(["mission", "--resume", first.mission_id, "--dry-run"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert "removed stale running.json lock before resume" in "\n".join(resumed["notes"])
    assert not (mission_dir / "running.json").exists()

    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(
            ["mission", str(tmp_path / "x.json"), "--resume", first.mission_id]
        )
    assert error.value.code == 2
    capsys.readouterr()

    (mission_dir / "running.json").write_text("{}")
    assert main(["missions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(item for item in rows if item["mission_id"] == first.mission_id)
    assert row["resumes"] == 2 and row["running"] is True


def _verdict_answer(passed: bool) -> str:
    return json.dumps(
        {
            "verdict": "pass" if passed else "fail",
            "criteria": [
                {"id": "correct", "ok": passed, "evidence": "src/example.py:1"}
            ],
            "summary": "checked",
        }
    )


def test_quorum_resume_tallies_a_kept_verdict_lane(
    repo, home, monkeypatch, tmp_path
):
    calls = {"R1": 0, "R2": 0, "R3": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        failed_first = key == "R2" and calls[key] == 1
        return _command(
            _verdict_answer(True),
            exit_code=1 if failed_first else 0,
        )

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    lanes = [
        {
            "name": name.lower(),
            "fleet": "claude",
            "prompt": name,
            "verdict": ["correct"],
        }
        for name in ("R1", "R2", "R3")
    ]
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "concurrency": 1,
            "lanes": lanes,
            "require": {"pass": 2, "of": ["r1", "r2", "r3"]},
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    Path(first.lanes[2]["answer_path"]).unlink()

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert resumed.ok is True and resumed.quorum["met"] is True
    assert resumed.resumed_from["kept"] == ["r1"]
    assert resumed.resumed_from["rerun"] == ["r2", "r3"]
    assert "r1" in resumed.quorum["passed"]
    assert calls == {"R1": 1, "R2": 2, "R3": 2}


def test_readme_documents_the_mission_resume_contract():
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    section = readme.split("### Resuming a mission", 1)[1].split("\n## ", 1)[0]
    assert "conductor mission --resume MISSION_ID" in section
    assert "commit" in section and "branch" in section
    assert "max_cost_usd" in section and "across resumes" in section
    assert "running.json" in section
    assert "half-committed" in section
    assert "not re-verified" in section
