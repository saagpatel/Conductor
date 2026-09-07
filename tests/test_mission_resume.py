"""Mission resume trusts durable work without paying for it a second time."""

from __future__ import annotations

import hashlib
import json
import shlex
import socket
import subprocess
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
                    "rank": False,
                },
                # fix-review (codex) judges build (codex), and the claude
                # collate shares a vendor with the claude review lane; this
                # mission exercises every field, self-judging included.
                "self_judging": "allow",
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
    # F2: an ordinary resume never parked on a pause point, so it adds
    # nothing to paused_s -- and launched_at is the first run's, not this
    # resume's own start.
    assert first.wall is not None and first.wall["paused_s"] == 0.0
    assert resumed.wall["paused_s"] == 0.0
    assert resumed.wall["launched_at"] == first.wall["launched_at"]


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
            # The stop lands the moment the second lane is about to spawn:
            # `first` has already settled (concurrency 1), so the interrupt
            # can only reach `second`. A wall-clock timer here lost the race
            # under machine load twice on 2026-09-07 (three suites at once),
            # interrupting `first` too and failing a green build's gate.
            request_stop()
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
            # Resume/kept-collate accounting is under test, not vendor
            # diversity; this claude-only mission would otherwise be
            # refused at load (A3 self-judging).
            "self_judging": "allow",
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
            # Cumulative-budget accounting across reruns is under test, not
            # vendor diversity.
            "self_judging": "allow",
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


def _codex_verdict_stream(answer: str) -> str:
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}})
    done = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return f"{item}\n{done}\n"


def test_quorum_resume_tallies_a_kept_verdict_lane(
    repo, home, monkeypatch, tmp_path
):
    calls = {"R1": 0, "R2": 0, "R3": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        failed_first = key == "R2" and calls[key] == 1
        exit_code = 1 if failed_first else 0
        answer = _verdict_answer(True)
        if spec.fleet == "codex":
            body = shlex.quote(_codex_verdict_stream(answer))
            return ["sh", "-c", f"printf '%s' {body}; exit {exit_code}"]
        return _command(answer, exit_code=exit_code)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    # r3 is on a different vendor than r1/r2: a quorum confined to one
    # vendor is refused at load (A3).
    lanes = [
        {
            "name": name.lower(),
            "fleet": "codex" if name == "R3" else "claude",
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


# --- D10: publishing a lock is not the same as leaving a stale one -----------


def _parked_mission(repo, home, tmp_path):
    mission = mission_from_dict(
        {"cwd": str(repo), "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}]},
        base_dir=tmp_path,
    )
    return run_mission(mission, home=home, dry_run=True)


@pytest.mark.parametrize(
    ("body", "reason"),
    [("", "is empty"), ('{"pid": 12', "not readable JSON"), ("[]", "not a JSON object")],
)
def test_a_lock_mid_publication_is_not_reclaimed(tmp_path, body, reason):
    """D10: `open("x")` published a zero-byte file and wrote the JSON into it
    afterwards. A contender that read that window got `None` from
    `_json_object`, `_lock_status({})` answered "pid None is not alive", and
    a live mission's lock was unlinked out from under it. Unreadable is not
    proven stale, so the claim refuses and says why."""
    (tmp_path / "running.json").write_text(body)

    with pytest.raises(MissionInvalid) as error:
        mission_mod._acquire_running_lock(tmp_path)

    assert "not reclaiming it" in str(error.value)
    assert reason in str(error.value)
    assert (tmp_path / "running.json").read_text() == body


def test_a_resume_refuses_a_half_written_lock(repo, home, tmp_path):
    """The same window, through the resume an operator actually runs."""
    first = _parked_mission(repo, home, tmp_path)
    mission_dir = Path(first.mission_dir)
    (mission_dir / "running.json").write_text("")

    with pytest.raises(MissionInvalid, match="not reclaiming it"):
        run_mission(_snapshot(first), home=home, resume_dir=mission_dir, dry_run=True)

    assert (mission_dir / "running.json").read_text() == ""


def test_a_dead_pids_lock_is_still_reclaimed(repo, home, tmp_path):
    """The staleness rule itself is unchanged: a readable lock whose pid is
    gone is proven stale and is replaced, with a note."""
    first = _parked_mission(repo, home, tmp_path)
    mission_dir = Path(first.mission_dir)
    (mission_dir / "running.json").write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "started": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
                "owner": "someone-elses-token",
            }
        )
    )

    resumed = run_mission(_snapshot(first), home=home, resume_dir=mission_dir, dry_run=True)

    assert "removed stale running.json lock before resume" in "\n".join(resumed.notes)
    assert not (mission_dir / "running.json").exists()


def test_a_published_lock_is_whole_and_owner_bound(tmp_path):
    """D10: the lock is linked into place already complete, a second claim
    never replaces it, and only its own owner token releases it -- unlink and
    release were not owner-bound at all."""
    lock = tmp_path / "running.json"

    assert mission_mod._publish_lock(lock, mission_mod._lock_body("mine")) is True
    body = json.loads(lock.read_text())
    assert body["owner"] == "mine" and body["pid"] > 0
    assert not list(tmp_path.glob(".running.json.*.tmp"))

    # A second publication does not steal a lock that exists.
    assert mission_mod._publish_lock(lock, mission_mod._lock_body("theirs")) is False
    assert json.loads(lock.read_text())["owner"] == "mine"

    assert mission_mod._release_lock(lock, "theirs") is False
    assert lock.is_file()
    assert mission_mod._release_lock(lock, "mine") is True
    assert not lock.exists()


def test_a_source_lock_mid_publication_is_not_reclaimed(home, tmp_path):
    """The file lock (E9) publishes and releases the same way."""
    source = tmp_path / "mission.json"
    source.write_text("{}")
    lock, owner, notes = mission_mod._acquire_source_lock(home, str(source), "m1")
    assert notes == [] and json.loads(lock.read_text())["mission_id"] == "m1"

    lock.write_text("")
    with pytest.raises(MissionInvalid, match="not reclaiming it"):
        mission_mod._acquire_source_lock(home, str(source), "m2")

    lock.write_text(json.dumps(mission_mod._lock_body(owner, mission_id="m1")))
    assert mission_mod._release_lock(lock, owner) is True


# --- W3: resume authenticates artifact bytes, not just artifact paths --------


def _digest_mission(repo, tmp_path, *, deliverable: bool = False) -> Mission:
    """A build lane (answer, diff, optionally a captured deliverable) and a
    read lane downstream of it, for the artifact-digest checks below."""
    lane: dict = {
        "name": "build",
        "fleet": "claude",
        "mode": "write",
        "prompt": "BUILD",
        "commit": "build",
    }
    if deliverable:
        lane["deliverable"] = {"path": "out.json"}
    return mission_from_dict(
        {
            "cwd": str(repo),
            "concurrency": 1,
            "lanes": [
                lane,
                {
                    "name": "read",
                    "fleet": "claude",
                    "needs": ["build"],
                    "prompt": "READ {{lanes.build.answer}}",
                },
            ],
        },
        base_dir=tmp_path,
    )


def test_untouched_lane_is_kept_and_its_receipt_carries_artifact_digests(
    repo, home, monkeypatch, tmp_path
):
    calls = {"BUILD": 0, "READ": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        action = ":" if key == "READ" else "printf 'v1\\n' > built.txt; printf '1\\n' > out.json"
        return _command("done", action=action)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = _digest_mission(repo, tmp_path, deliverable=True)
    first = run_mission(mission, home=home)
    assert first.ok is True

    receipt = json.loads((Path(first.mission_dir) / "lanes" / "build.json").read_text())
    assert set(receipt["artifact_sha256"]) == {"answer", "diff", "deliverable"}
    for kind, key in (
        ("answer", "answer_path"),
        ("diff", "diff_path"),
        ("deliverable", "deliverable_path"),
    ):
        raw = Path(receipt[key]).read_bytes()
        assert receipt["artifact_sha256"][kind] == hashlib.sha256(raw).hexdigest()

    resumed = run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))

    assert resumed.ok is True
    assert calls == {"BUILD": 1, "READ": 1}
    assert resumed.resumed_from["kept"] == ["build", "read"]
    assert resumed.resumed_from["rerun"] == []
    assert "bytes differ" not in "\n".join(resumed.notes)


def test_rewritten_answer_bytes_force_a_rerun_with_a_note(repo, home, monkeypatch, tmp_path):
    calls = {"BUILD": 0, "READ": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        action = ":" if key == "READ" else "printf 'v1\\n' > built.txt"
        return _command("done", action=action)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = _digest_mission(repo, tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True

    answer_path = Path(first.mission_dir) / "answers" / "build.txt"
    answer_path.write_text("an answer the build lane never wrote\n")

    resumed = run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))

    assert calls == {"BUILD": 2, "READ": 2}
    assert resumed.resumed_from["rerun"] == ["build", "read"]
    assert (
        "lane 'build': answer bytes differ from the receipt's digest; not trusted"
        in resumed.notes
    )
    # The rerun still owns the first attempt's spend and history.
    assert resumed.lanes[0]["previous_attempts"][0]["run_id"] == first.lanes[0][
        "attempts"
    ][0]["run_id"]


def test_swapped_deliverable_bytes_force_a_rerun_with_a_note(repo, home, monkeypatch, tmp_path):
    calls = {"BUILD": 0, "READ": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        action = ":" if key == "READ" else "printf 'v1\\n' > built.txt; printf '1\\n' > out.json"
        return _command("done", action=action)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = _digest_mission(repo, tmp_path, deliverable=True)
    first = run_mission(mission, home=home)
    assert first.ok is True

    kept = Path(first.mission_dir) / "deliverables" / "build-deliverable"
    assert kept.is_file()
    kept.write_text("2\n")

    resumed = run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))

    assert calls == {"BUILD": 2, "READ": 2}
    assert resumed.resumed_from["rerun"] == ["build", "read"]
    assert (
        "lane 'build': deliverable bytes differ from the receipt's digest; not trusted"
        in resumed.notes
    )


def test_receipt_without_digests_is_trusted_on_path_only(repo, home, monkeypatch, tmp_path):
    calls = {"BUILD": 0, "READ": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        action = ":" if key == "READ" else "printf 'v1\\n' > built.txt"
        return _command("done", action=action)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = _digest_mission(repo, tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True

    # A receipt from a conductor that predates the digests: the field is
    # simply absent, and the lane is still kept on its paths alone.
    receipt_path = Path(first.mission_dir) / "lanes" / "build.json"
    receipt = json.loads(receipt_path.read_text())
    del receipt["artifact_sha256"]
    receipt_path.write_text(json.dumps(receipt))

    resumed = run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))

    assert resumed.ok is True
    assert calls == {"BUILD": 1, "READ": 1}
    assert "build" in resumed.resumed_from["kept"]
    assert (
        "lane 'build': receipt records no digest for its answer; trusted on path only"
        in resumed.notes
    )


def test_human_lane_with_rewritten_answer_bytes_is_not_trusted(
    repo, home, monkeypatch, tmp_path
):
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "concurrency": 1,
            "lanes": [
                {"name": "ask", "fleet": "human", "prompt": "Approve?"},
                {
                    "name": "use",
                    "fleet": "claude",
                    "needs": ["ask"],
                    "prompt": "USE {{lanes.ask.answer}}",
                },
            ],
        },
        base_dir=tmp_path,
    )
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: _command("used"))
    parked = run_mission(mission, home=home)
    assert parked.paused["kind"] == "human"

    answered = run_mission(
        _snapshot(parked), home=home, resume_dir=Path(parked.mission_dir), answer="ship it"
    )
    assert answered.ok is True
    receipt = json.loads((Path(answered.mission_dir) / "lanes" / "ask.json").read_text())
    assert set(receipt["artifact_sha256"]) == {"answer"}

    answer_path = Path(answered.mission_dir) / "answers" / "ask.txt"
    answer_path.write_text("do not ship it\n")

    def refuse(spec: Spec) -> list[str]:
        raise AssertionError("a rewritten human answer must not be trusted downstream")

    monkeypatch.setattr(runner_mod, "build_argv", refuse)
    again = run_mission(_snapshot(answered), home=home, resume_dir=Path(answered.mission_dir))

    assert again.ok is False
    assert again.paused["kind"] == "human" and again.paused["lane"] == "ask"
    assert (
        "lane 'ask': answer bytes differ from the receipt's digest; not trusted" in again.notes
    )
