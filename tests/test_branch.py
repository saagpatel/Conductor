"""A lane's `branch` names its deliverable. The run-id branch is what
conductor works on; the name the mission asked for is what the operator
merges, and it is checked before any fleet spends a dollar.
"""

from __future__ import annotations

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


def test_a_lane_that_landed_nothing_creates_no_branch(repo, home, monkeypatch, tmp_path, git_out):
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
    fix = result.lanes[2]
    assert result.ok and fix["ok"] and fix["branch"] == ""
    assert git_out(repo, "branch", "--list", "refactor/*") == ""
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
