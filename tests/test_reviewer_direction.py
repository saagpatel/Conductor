"""Roadmap item A4, reviewer direction as a policy and reproduce-before-fix:
lane stages with a mode each stage requires, a mission-level per-stage
vendor policy, a `stage: review` lane treated as a judge for self-judging
purposes, and a `stage: fix` write dispatch that must show its own check
failing on the base before it may land.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission
from conductor.runner import dispatch


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, message: str = "reviewer-direction fixture") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _spec(repo: Path, **changes) -> Spec:
    fields = {
        "fleet": "claude",
        "prompt": "reviewer direction test",
        "cwd": str(repo),
        "mode": "write",
    }
    fields.update(changes)
    return Spec(**fields)


# --- 1. lane stages ----------------------------------------------------------


@pytest.mark.parametrize(
    "stage, mode",
    [("review", "write"), ("fix", "read"), ("build", "read")],
)
def test_stage_mode_mismatch_is_refused_naming_the_lane(tmp_path, stage, mode):
    raw = {"prompt": "x", "lanes": [{"name": "a", "fleet": "claude", "stage": stage, "mode": mode}]}
    with pytest.raises(MissionInvalid, match=rf"'a': stage '{stage}' lanes must be"):
        mission_from_dict(raw, base_dir=tmp_path)


@pytest.mark.parametrize("stage, mode", [("build", "write"), ("review", "read"), ("fix", "write")])
def test_stage_with_its_required_mode_loads(tmp_path, stage, mode):
    raw = {"prompt": "x", "lanes": [{"name": "a", "fleet": "claude", "stage": stage, "mode": mode}]}
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].stage == stage


def test_unknown_stage_is_refused_naming_the_lane(tmp_path):
    raw = {"prompt": "x", "lanes": [{"name": "a", "fleet": "claude", "stage": "deploy"}]}
    with pytest.raises(MissionInvalid, match=r"'a': stage must be one of build, review, fix"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_stage_snapshot_round_trips_through_mission_json(repo, home, tmp_path):
    raw = {
        "cwd": str(repo),
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "claude", "stage": "build", "mode": "write"},
            {
                "name": "review",
                "fleet": "codex",
                "stage": "review",
                "mode": "read",
                "base": "build",
                "prompt": "R",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    assert reloaded.lanes[0].stage == "build"
    assert reloaded.lanes[1].stage == "review"
    assert reloaded.to_dict() == mission.to_dict() == snapshot


def test_stage_appears_in_the_lane_row_of_result_json_and_report(repo, home, fake_fleet):
    fake_fleet(session_id=None)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [{"name": "build", "fleet": "claude", "stage": "build", "mode": "write"}],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)
    assert result.lanes[0]["stage"] == "build"
    receipt = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert receipt["lanes"][0]["stage"] == "build"
    report = Path(result.report_path).read_text()
    assert "- stage: build" in report


# --- 2. per-stage vendor policy -----------------------------------------------


def test_policy_allows_a_matching_vendor(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"review": {"vendors": ["anthropic"]}},
        "lanes": [{"name": "review", "fleet": "claude", "stage": "review", "mode": "read"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.policy == {"review": {"vendors": ["anthropic"]}}


def test_policy_refuses_a_disallowed_primary_vendor(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"review": {"vendors": ["google"]}},
        "lanes": [{"name": "review", "fleet": "claude", "stage": "review", "mode": "read"}],
    }
    with pytest.raises(
        MissionInvalid,
        match=r"'review' \(claude\) is on vendor 'anthropic'; "
        r"policy allows google for stage review",
    ):
        mission_from_dict(raw, base_dir=tmp_path)


def test_policy_reaches_into_a_disallowed_fallback(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"review": {"vendors": ["anthropic"]}},
        "lanes": [
            {
                "name": "review",
                "fleet": "claude",
                "stage": "review",
                "mode": "read",
                "fallback": [{"fleet": "codex", "mode": "read"}],
            }
        ],
    }
    with pytest.raises(
        MissionInvalid,
        match=r"'review' \(codex\) is on vendor 'openai'; policy allows anthropic for stage review",
    ):
        mission_from_dict(raw, base_dir=tmp_path)


def test_policy_refuses_an_unknown_vendor_id(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"review": {"vendors": ["not-a-vendor"]}},
        "lanes": [{"name": "review", "fleet": "claude", "stage": "review", "mode": "read"}],
    }
    with pytest.raises(MissionInvalid, match="unknown vendor 'not-a-vendor'"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_policy_refuses_an_unknown_stage_key(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"deploy": {"vendors": ["anthropic"]}},
        "lanes": [{"name": "review", "fleet": "claude", "stage": "review", "mode": "read"}],
    }
    with pytest.raises(MissionInvalid, match="policy: unknown stage 'deploy'"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_policy_refuses_a_stage_no_lane_declares(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"fix": {"vendors": ["anthropic"]}},
        "lanes": [{"name": "review", "fleet": "claude", "stage": "review", "mode": "read"}],
    }
    with pytest.raises(MissionInvalid, match="policy names stage 'fix' but no lane declares it"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_policy_snapshot_round_trips_through_mission_json(repo, home, tmp_path):
    raw = {
        "cwd": str(repo),
        "prompt": "x",
        "policy": {"review": {"vendors": ["anthropic"]}},
        "lanes": [{"name": "review", "fleet": "claude", "stage": "review", "mode": "read"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    assert reloaded.policy == {"review": {"vendors": ["anthropic"]}}
    assert reloaded.to_dict() == mission.to_dict() == snapshot


# --- 3. a review-stage lane is judge hygiene's business too ------------------


def test_review_stage_lane_without_a_verdict_still_refuses_to_share_a_vendor(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "claude", "model": "opus", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "model": "sonnet",
                "stage": "review",
                "mode": "read",
                "base": "build",
                "prompt": "R",
            },
        ],
    }
    with pytest.raises(MissionInvalid, match=r"'review'.*'build'.*anthropic"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_review_stage_self_judging_allow_lifts_the_refusal(tmp_path):
    raw = {
        "prompt": "x",
        "self_judging": "allow",
        "lanes": [
            {"name": "build", "fleet": "claude", "model": "opus", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "model": "sonnet",
                "stage": "review",
                "mode": "read",
                "base": "build",
                "prompt": "R",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.self_judging == "allow"


# --- 4. reproduce before fix ---------------------------------------------------


def test_reproduce_gate_refuses_a_source_only_fix_with_no_check(repo, home, fake_fleet):
    (repo / "tests").mkdir()
    (repo / "tests" / "check.py").write_text("raise SystemExit(1)\n")
    _commit(repo)
    fake_fleet(["sh", "-c", "echo fixed > feature.py"])

    result = dispatch(
        _spec(repo, stage="fix"),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: attempted",
    )

    assert result.reproduce["verdict"] == "no-check"
    assert result.reproduce["worktree"] == ""
    assert result.error == "fix without a reproducing check: no test-surface change"
    assert result.ok is False
    assert result.commit is None


def test_reproduce_gate_refuses_a_check_that_already_passes_on_the_base(
    repo, home, fake_fleet, git_out
):
    (repo / "app.txt").write_text("bad\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "check.py").write_text("raise SystemExit(1)\n")
    _commit(repo)
    fake_fleet(
        ["sh", "-c", "echo good > app.txt; printf 'raise SystemExit(0)\\n' > tests/check.py"]
    )

    result = dispatch(
        _spec(repo, stage="fix"),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: attempted",
    )

    assert result.reproduce["verdict"] == "not-reproduced"
    assert result.error == (
        "reproduce gate passed on the base: the check does not reproduce the finding"
    )
    assert result.ok is False
    assert result.commit is None
    assert not Path(result.reproduce["worktree"]).exists()
    assert "-reproduce" not in git_out(repo, "worktree", "list")


def test_reproduce_gate_commits_a_fix_whose_check_fails_on_the_base(
    repo, home, fake_fleet, git_out
):
    (repo / "app.txt").write_text("bad\n")
    base = _commit(repo)
    check_script = (
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('app.txt').read_text() == 'good\\n' else 1)\n"
    )
    fix_cmd = (
        "echo good > app.txt && mkdir -p tests && printf '%s' "
        + shlex.quote(check_script)
        + " > tests/check.py"
    )
    fake_fleet(["sh", "-c", fix_cmd])

    result = dispatch(
        _spec(repo, stage="fix", test_policy="allow"),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: address review",
    )

    assert result.reproduce["verdict"] == "reproduced"
    assert result.reproduce["exit_code"] != 0
    assert result.ok is True
    assert result.commit["committed"] is True
    assert git_out(repo, "rev-parse", "HEAD") != base
    assert not Path(result.reproduce["worktree"]).exists()
    assert "-reproduce" not in git_out(repo, "worktree", "list")


def test_reproduce_gate_records_skipped_for_read_mode_and_unstaged_dispatches(
    repo, home, fake_fleet
):
    fake_fleet(["sh", "-c", "true"])
    read_result = dispatch(_spec(repo, mode="read", stage="fix"), home=home)
    assert read_result.reproduce["verdict"] == "skipped"

    fake_fleet(["sh", "-c", "echo x > y.txt"])
    unstaged_result = dispatch(_spec(repo), home=home)
    assert unstaged_result.reproduce["verdict"] == "skipped"


def test_stage_fix_mission_lane_carries_the_reproduce_block_into_its_lane_json(
    repo, home, fake_fleet
):
    (repo / "app.txt").write_text("bad\n")
    _commit(repo)
    check_script = (
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('app.txt').read_text() == 'good\\n' else 1)\n"
    )
    fix_cmd = (
        "echo good > app.txt && mkdir -p tests && printf '%s' "
        + shlex.quote(check_script)
        + " > tests/check.py"
    )
    fake_fleet(["sh", "-c", fix_cmd])
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "test_policy": "allow",
            "lanes": [
                {
                    "name": "fix",
                    "fleet": "claude",
                    "stage": "fix",
                    "mode": "write",
                    "test": "python tests/check.py",
                    "commit": "fix: address review",
                }
            ],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)

    assert result.ok is True
    receipt = json.loads(Path(result.mission_dir, "lanes", "fix.json").read_text())
    assert receipt["attempts"][0]["reproduce"]["verdict"] == "reproduced"


# --- 5. documentation ----------------------------------------------------------


def test_readme_documents_lane_stages_policy_and_reproduce_before_fix():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split(
        "### Lane stages, reviewer policy, and reproduce before fix", 1
    )[1].split("\n### ", 1)[0]
    assert "`build`" in section and "`review`" in section and "`fix`" in section
    assert "policy allows" in section
    assert "71.6%" in section and "89.7%" in section
    assert "91.4%" in section and "82.8%" in section
    assert "docs/ROADMAP-2026-09.md" in section
    assert "a stage no lane declares" in section
    assert "fix without a reproducing check: no test-surface change" in section
    assert (
        "reproduce gate passed on the base: the check does not reproduce the finding" in section
    )
    assert "not-reproduced" in section and "no-check" in section and "reproduced" in section
    assert "self_judging: allow" in section


# --- 5. what the cross-vendor review found, pinned ---------------------------


def _fix_with_a_reproducing_check(repo: Path, fake_fleet) -> str:
    """A fleet command that fixes app.txt and adds a check failing on the base."""
    (repo / "app.txt").write_text("bad\n")
    base = _commit(repo)
    check_script = (
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('app.txt').read_text() == 'good\\n' else 1)\n"
    )
    fake_fleet(
        [
            "sh",
            "-c",
            "echo good > app.txt && mkdir -p tests && printf '%s' "
            + shlex.quote(check_script)
            + " > tests/check.py",
        ]
    )
    return base


def _gate_outcome(worktree: Path, **changes) -> dict:
    outcome = {
        "ran": True,
        "exit_code": None,
        "timed_out": False,
        "interrupted": False,
        "tail": "",
        "worktree": str(worktree),
        "patch_bytes": 0,
    }
    outcome.update(changes)
    return outcome


@pytest.mark.parametrize(
    "outcome, error",
    [
        (
            lambda wt: runner_mod._git_failure("git worktree add failed: boom", worktree=wt),
            "fix without a reproducing check: reproduce gate could not run: "
            "git worktree add failed: boom",
        ),
        (
            lambda wt: _gate_outcome(wt, timed_out=True),
            "fix without a reproducing check: reproduce gate timed out",
        ),
    ],
)
def test_a_reproduce_gate_that_could_not_run_is_not_a_reproduction(
    repo, home, fake_fleet, monkeypatch, git_out, outcome, error
):
    base = _fix_with_a_reproducing_check(repo, fake_fleet)
    monkeypatch.setattr(runner_mod, "_reproduce_gate", lambda cwd, **kw: outcome(kw["worktree"]))

    result = dispatch(
        _spec(repo, stage="fix", test_policy="allow"),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: attempted",
    )

    assert result.reproduce["verdict"] == "no-check"
    assert result.error == error
    assert result.ok is False
    assert result.commit is None
    assert git_out(repo, "rev-parse", "HEAD") == base


def test_a_stop_during_the_reproduce_gate_marks_the_run_interrupted(
    repo, home, fake_fleet, monkeypatch, git_out
):
    base = _fix_with_a_reproducing_check(repo, fake_fleet)
    monkeypatch.setattr(
        runner_mod,
        "_reproduce_gate",
        lambda cwd, **kw: _gate_outcome(kw["worktree"], interrupted=True),
    )

    result = dispatch(
        _spec(repo, stage="fix", test_policy="allow"),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: attempted",
    )

    assert result.interrupted is True
    assert result.error.startswith("interrupted: stop requested during the reproduce gate")
    assert result.ok is False
    assert git_out(repo, "rev-parse", "HEAD") == base


def test_a_fleet_self_commit_of_a_refused_fix_is_undone(repo, home, fake_fleet, git_out):
    (repo / "app.txt").write_text("bad\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "check.py").write_text("raise SystemExit(1)\n")
    base = _commit(repo)
    fake_fleet(
        ["sh", "-c", "echo good > app.txt && git add -A && git commit -q -m 'fleet: own commit'"]
    )

    result = dispatch(
        _spec(repo, stage="fix"),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: attempted",
    )

    assert result.reproduce["verdict"] == "no-check"
    assert result.ok is False
    assert git_out(repo, "rev-parse", "HEAD") == base
    assert "app.txt" in git_out(repo, "status", "--porcelain")
