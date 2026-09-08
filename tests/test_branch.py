"""A lane's `branch` names its deliverable. The run-id branch is what
conductor works on; the name the mission asked for is what the operator
merges, and it is checked before any fleet spends a dollar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_pipeline import PIPELINE, by_prompt, envelope

from conductor.mission import MissionInvalid, mission_from_dict, run_mission


def named(pipeline: dict, repo: Path, **fix_extra) -> dict:
    lanes = [dict(lane) for lane in pipeline["lanes"]]
    lanes[2] = lanes[2] | {"branch": "refactor/timestamps"} | fix_extra
    return pipeline | {"cwd": str(repo), "lanes": lanes}


def test_the_fix_lanes_branch_is_renamed_to_the_deliverable(
    repo, home, monkeypatch, tmp_path, git_out
):
    by_prompt(
        monkeypatch,
        {
            "BUILD": ["sh", "-c", "echo v1 > built.txt"],
            "REVIEW": ["sh", "-c", f"echo '{envelope('DEFECT: x')}'"],
            "FIX": ["sh", "-c", "echo v2 >> built.txt"],
        },
    )
    mission = mission_from_dict(named(PIPELINE, repo), base_dir=tmp_path)
    result = run_mission(mission, home=home)
    build, _, fix = result.lanes
    assert result.ok and fix["ok"]
    assert fix["branch"] == "refactor/timestamps"
    assert fix["attempts"][-1]["branch"] == "refactor/timestamps"
    assert git_out(repo, "rev-parse", "refactor/timestamps") == fix["tip_sha"]
    # The build's run-id branch is untouched (gc's business); the fix's is gone.
    branches = git_out(repo, "branch", "--list", "conductor/*").split()
    assert build["branch"] in branches and len(branches) == 1
    assert "refactor/timestamps" in Path(result.report_path).read_text()


def test_a_no_op_fix_lane_names_the_tip_it_was_built_on(repo, home, monkeypatch, tmp_path, git_out):
    """NO_DEFECTS → NO_CHANGES is the happy path of a pipeline, and the
    deliverable is then the build; found live 2026-09-03 when a clean
    review left no branch to merge."""
    by_prompt(
        monkeypatch,
        {
            "BUILD": ["sh", "-c", "echo v1 > built.txt"],
            "REVIEW": ["sh", "-c", f"echo '{envelope('NO_DEFECTS')}'"],
            "FIX": ["sh", "-c", "true"],
        },
    )
    mission = mission_from_dict(named(PIPELINE, repo, no_op_ok=True), base_dir=tmp_path)
    result = run_mission(mission, home=home)
    build, _, fix = result.lanes
    assert result.ok and fix["ok"] and fix["branch"] == "refactor/timestamps"
    assert git_out(repo, "rev-parse", "refactor/timestamps") == build["tip_sha"]
    assert "nothing landed on top of the base" in Path(result.report_path).read_text()


def test_a_flat_lane_that_landed_nothing_creates_no_branch(
    repo, home, fake_fleet, tmp_path, git_out
):
    fake_fleet(["sh", "-c", "true"])
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {
                    "name": "a",
                    "fleet": "codex",
                    "mode": "write",
                    "prompt": "x",
                    "no_op_ok": True,
                    "branch": "feat/nothing",
                }
            ],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    lane = result.lanes[0]
    assert result.ok and lane["ok"] and lane["branch"] == ""
    assert git_out(repo, "branch", "--list", "feat/*") == ""
    assert "not created: no commits landed" in Path(result.report_path).read_text()


def test_an_existing_branch_is_refused_before_any_fleet_runs(repo, home, monkeypatch, tmp_path):
    spawned: list[str] = []
    by_prompt(monkeypatch, {"BUILD": ["true"], "REVIEW": ["true"], "FIX": ["true"]}, spawned)
    import subprocess

    subprocess.run(["git", "branch", "refactor/timestamps"], cwd=repo, check=True)
    mission = mission_from_dict(named(PIPELINE, repo), base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match="already exists"):
        run_mission(mission, home=home)
    assert spawned == []


def test_check_branches_does_not_treat_a_git_that_did_not_run_as_a_free_name(
    repo, tmp_path, monkeypatch
):
    """`git_run` reports spawn failure as GIT_UNRUN (-1), which is not
    'the ref does not exist'. Reading it as a free name would launch the
    lane and then fail at `_rename_branch` with commits stranded."""
    import subprocess

    from conductor import mission as mission_mod
    from conductor.verify import GIT_UNRUN

    real = mission_mod.git_run

    def git_unrun_on_verify(cwd, *args, **kwargs):
        if args[:3] == ("rev-parse", "--verify", "--quiet"):
            return subprocess.CompletedProcess(["git", *args], GIT_UNRUN, "", "EAGAIN")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(mission_mod, "git_run", git_unrun_on_verify)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [{"name": "a", "fleet": "codex", "prompt": "A", "branch": "feat/x"}],
        },
        base_dir=tmp_path,
    )
    with pytest.raises(MissionInvalid, match="git could not confirm"):
        mission_mod._check_branches(mission)


def test_an_invalid_branch_name_is_refused_before_any_fleet_runs(repo, home, tmp_path):
    lanes = [dict(lane) for lane in PIPELINE["lanes"]]
    lanes[2] = lanes[2] | {"branch": "bad..name"}
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo), "lanes": lanes}, base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match="not a valid branch name"):
        run_mission(mission, home=home, dry_run=True)


@pytest.mark.parametrize(
    ("branches", "message"),
    [
        (["conductor/x", None], "outside conductor/"),
        (["", None], "outside conductor/"),
        (["same", "same"], "two lanes claim branch"),
        ([7, None], "branch must be a string"),
    ],
)
def test_bad_branch_fields_are_refused_at_load(tmp_path, branches, message):
    lanes = [
        {"name": "a", "fleet": "codex", "prompt": "a", "branch": branches[0]},
        {"name": "b", "fleet": "codex", "prompt": "b", "branch": branches[1]},
    ]
    with pytest.raises(MissionInvalid, match=message):
        mission_from_dict({"cwd": str(tmp_path), "lanes": lanes}, base_dir=tmp_path)


def test_a_name_held_only_by_a_remote_is_refused_too(repo, home, monkeypatch, tmp_path):
    """origin/x with no local x would collide the moment the operator pushed."""
    import subprocess

    by_prompt(monkeypatch, {"BUILD": ["true"], "REVIEW": ["true"], "FIX": ["true"]})
    subprocess.run(["git", "remote", "add", "origin", str(repo)], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/refactor/timestamps", "HEAD"],
        cwd=repo,
        check=True,
    )
    mission = mission_from_dict(named(PIPELINE, repo), base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match="refs/remotes/origin/refactor/timestamps"):
        run_mission(mission, home=home)


def test_a_failed_deliverable_rename_marks_the_attempt_and_every_receipt_failed(
    repo, home, fake_fleet, tmp_path
):
    # The branch is free at mission admission, then a real Git operation wins
    # the name while the fleet runs; the final rename must fail consistently.
    fake_fleet(
        ["sh", "-c", "echo work > work.txt; git branch deliverable/build HEAD"]
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {
                    "name": "build",
                    "fleet": "codex",
                    "mode": "write",
                    "prompt": "build",
                    "commit": "build",
                    "branch": "deliverable/build",
                }
            ],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    lane = result.lanes[0]
    error = lane["attempts"][-1]["error"]
    assert result.ok is False and lane["ok"] is False
    assert "branch 'deliverable/build' not claimed" in error
    assert lane["attempts"][-1]["ok"] is False
    assert lane["attempts"][-1]["error"] == lane["attempts"][-1]["failure"] == error
    lane_receipt = json.loads((Path(result.mission_dir) / "lanes" / "build.json").read_text())
    final_receipt = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert lane_receipt["attempts"][-1]["ok"] is False
    assert final_receipt["lanes"][0]["attempts"][-1]["failure"] == error
