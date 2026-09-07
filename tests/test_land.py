"""F7: `conductor land` -- merge a lane's branch, gate the merged head in a
fresh worktree, run `golden check`, and attest the mission. All against a
temporary git repository built in the test, never this checkout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conductor.cli import main
from conductor.land import LandInvalid, land
from conductor.mission import mission_from_dict, run_mission


def _claude_ok_argv(*shell: str) -> list[str]:
    payload = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"ok","usage":{"inputTokens":10,"outputTokens":1}}'
    )
    script = "; ".join(("printf '%s\\n' " + f"'{payload}'", *shell))
    return ["sh", "-c", script]


def _run_landable_lane(
    repo: Path,
    home: Path,
    fake_fleet,
    *,
    edit: str = "echo x >> app.py",
    mission_test: str | None = "true",
    branch: str = "feat/land",
    lane_name: str = "fix",
) -> tuple[str, str]:
    """A write lane with `commit` and `branch` set: a clean, committed run
    whose worktree is removed and whose run-id branch is renamed to
    `branch` -- `test_branch.py`'s own fixture convention. The mission-level
    `test` (never a lane-level one) is what a lane inherits by default and
    what `land()` falls back to reading off the mission snapshot."""
    fake_fleet(_claude_ok_argv(edit))
    lane: dict = {
        "name": lane_name,
        "fleet": "claude",
        "mode": "write",
        "commit": "feat: change",
        "branch": branch,
    }
    mission: dict = {"cwd": str(repo), "prompt": "x", "lanes": [lane]}
    if mission_test is not None:
        mission["test"] = mission_test
    m = mission_from_dict(mission, base_dir=repo)
    result = run_mission(m, home=home)
    assert result.ok, result.summary()
    mission_dir = Path(result.mission_dir)
    return mission_dir.name, lane_name


def test_land_merges_gates_golden_checks_attests_and_writes_a_receipt(
    repo, home, fake_fleet, git_out
):
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    pre_head = git_out(repo, "rev-parse", "HEAD")

    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.ok is True
    assert result.already_merged is False
    assert result.dry_run is False
    assert result.merge_sha and result.merge_sha != pre_head
    assert result.pre_merge_sha == pre_head
    assert git_out(repo, "rev-parse", "HEAD") == result.merge_sha
    assert [step["name"] for step in result.steps] == ["merge", "gate", "golden", "attest"]
    assert all(step["ok"] for step in result.steps)
    # The gate ran in a scratch worktree that no longer exists afterwards.
    assert result.worktree and not Path(result.worktree).is_dir()
    # The branch survives; the merge commit's message names it.
    assert git_out(repo, "rev-parse", "feat/land")
    log = git_out(repo, "log", "-1", "--format=%s")
    assert "feat/land" in log

    assert result.receipt_path and Path(result.receipt_path).is_file()
    receipt = json.loads(Path(result.receipt_path).read_text())
    assert receipt["mission"] == mission_id
    assert receipt["lane"] == lane
    assert [step["name"] for step in receipt["steps"]] == ["merge", "gate", "golden", "attest"]
    assert "recorded_at" in receipt
    assert "refused" not in receipt


def test_a_red_gate_resets_the_checkout_and_exits_1_with_the_branch_intact(
    repo, home, fake_fleet, git_out
):
    # The mission's own gate ("true") is what let the lane land in the first
    # place; land()'s own `--test` override is what fails on the merged head.
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    pre_head = git_out(repo, "rev-parse", "HEAD")
    pre_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout

    result = land(mission_id, lane, home=home, checkout=str(repo), gate_command="exit 1")

    assert result.ok is False
    assert result.failed_step == "gate"
    assert result.reset is True
    assert git_out(repo, "rev-parse", "HEAD") == pre_head
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status == pre_status
    # The branch itself is never deleted.
    assert git_out(repo, "rev-parse", "feat/land")


def test_a_failed_merge_step_leaves_the_checkout_mergeable_again(
    repo, home, fake_fleet, git_out
):
    """Grok item 1: a merge that fails after the auto-commit is attempted
    (a rejecting commit-msg hook, here) must not leave `MERGE_HEAD` set --
    `git merge --abort` is exactly enough before the merge commit exists,
    unlike the post-commit red-step case the reset --hard path covers."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    pre_head = git_out(repo, "rev-parse", "HEAD")

    try:
        result = land(mission_id, lane, home=home, checkout=str(repo))
    finally:
        hook.unlink()

    assert result.ok is False
    assert result.failed_step == "merge"
    assert not (repo / ".git" / "MERGE_HEAD").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status == ""
    assert git_out(repo, "rev-parse", "HEAD") == pre_head
    # The checkout is mergeable again: land can be retried without a
    # separate "checkout is mid-merge" refusal in the way.
    result2 = land(mission_id, lane, home=home, checkout=str(repo))
    assert result2.ok is True


def test_the_gate_runs_with_the_same_environment_a_lanes_gate_gets(
    repo, home, fake_fleet, tmp_path
):
    """Grok item 2: `runner.dispatch` stamps `CONDUCTOR_LANE=1` on the exact
    env dict a lane's own gate runs under (runner.py:1615-1622), so land's
    own gate step -- re-running the same command against the merged head --
    must carry it too, per spec item 2's "same environment a lane's gate
    gets"."""
    marker = tmp_path / "gate-env.out"
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)

    result = land(mission_id, lane, home=home, checkout=str(repo), gate_command=f"env > {marker}")

    assert result.ok is True
    env_text = marker.read_text()
    assert "CONDUCTOR_LANE=1" in env_text


def test_cli_land_exits_1_on_a_red_gate(repo, home, fake_fleet, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    assert (
        main(
            ["land", mission_id, "--lane", lane, "--checkout", str(repo), "--test", "exit 1"]
        )
        == 1
    )


def test_already_merged_exits_0_and_touches_nothing(
    repo, home, fake_fleet, monkeypatch, git_out
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    land(mission_id, lane, home=home, checkout=str(repo))
    pre_head = git_out(repo, "rev-parse", "HEAD")

    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.already_merged is True
    assert result.ok is True
    assert git_out(repo, "rev-parse", "HEAD") == pre_head
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 0


def test_dry_run_merges_nothing(repo, home, fake_fleet, monkeypatch, git_out):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    pre_head = git_out(repo, "rev-parse", "HEAD")

    result = land(mission_id, lane, home=home, checkout=str(repo), dry_run=True)

    assert result.dry_run is True
    assert result.ok is True
    assert result.gate_command == "true"
    assert len(result.would_merge) == 1
    assert git_out(repo, "rev-parse", "HEAD") == pre_head
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert status == ""
    assert (
        main(["land", mission_id, "--lane", lane, "--checkout", str(repo), "--dry-run"]) == 0
    )
    assert git_out(repo, "rev-parse", "HEAD") == pre_head


def test_cli_land_defaults_checkout_to_the_lanes_repository(
    repo, home, fake_fleet, monkeypatch, git_out
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)

    assert main(["land", mission_id, "--lane", lane]) == 0
    assert git_out(repo, "rev-parse", "feat/land") == git_out(repo, "rev-parse", "HEAD^2")


def test_cli_json_prints_the_receipt_on_disk(repo, home, fake_fleet, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)

    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)

    receipts = list((home / "missions" / mission_id / "land").glob(f"{lane}-*.json"))
    assert len(receipts) == 1
    on_disk = json.loads(receipts[0].read_text())
    assert printed == on_disk


# --- refusals: exit 3, checkout untouched -------------------------------


def _unchanged(repo: Path, git_out) -> tuple[str, str]:
    head = git_out(repo, "rev-parse", "HEAD")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
    ).stdout
    return head, status


def test_refuses_a_dirty_checkout(repo, home, fake_fleet, git_out, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    before = _unchanged(repo, git_out)
    (repo / "dirty.txt").write_text("uncommitted\n")

    with pytest.raises(LandInvalid, match="uncommitted changes"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3

    (repo / "dirty.txt").unlink()
    assert _unchanged(repo, git_out) == before


def test_refuses_a_mid_merge_checkout(repo, home, fake_fleet, git_out, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    before = _unchanged(repo, git_out)
    (repo / ".git" / "MERGE_HEAD").write_text(git_out(repo, "rev-parse", "HEAD") + "\n")

    with pytest.raises(LandInvalid, match="mid-merge"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3

    (repo / ".git" / "MERGE_HEAD").unlink()
    assert _unchanged(repo, git_out) == before


def test_refuses_when_the_checkouts_current_branch_is_the_lane_branch_itself(
    repo, home, fake_fleet, git_out, monkeypatch
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    subprocess.run(["git", "checkout", "-q", "feat/land"], cwd=repo, check=True)
    before = _unchanged(repo, git_out)

    with pytest.raises(LandInvalid, match="feat/land' itself"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3
    assert _unchanged(repo, git_out) == before

    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)


def test_refuses_a_lane_with_no_branch_on_its_receipt(repo, home, fake_fleet, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    fake_fleet(_claude_ok_argv())
    lane = {"name": "read-lane", "fleet": "claude", "mode": "read"}
    mission = mission_from_dict(
        {"cwd": str(repo), "prompt": "x", "lanes": [lane]}, base_dir=repo
    )
    result = run_mission(mission, home=home)
    mission_id = Path(result.mission_dir).name

    with pytest.raises(LandInvalid, match="no branch"):
        land(mission_id, "read-lane", home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", "read-lane", "--checkout", str(repo)]) == 3


def test_refuses_a_missing_branch(repo, home, fake_fleet, git_out, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    subprocess.run(["git", "branch", "-D", "feat/land"], cwd=repo, check=True)
    before = _unchanged(repo, git_out)

    with pytest.raises(LandInvalid, match="does not exist"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3
    assert _unchanged(repo, git_out) == before


def test_refuses_inside_a_lanes_own_environment(repo, home, fake_fleet, monkeypatch, git_out):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    before = _unchanged(repo, git_out)
    monkeypatch.setenv("CONDUCTOR_LANE", "1")

    with pytest.raises(LandInvalid, match="lane's environment"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3
    assert _unchanged(repo, git_out) == before


def test_refuses_with_no_gate_command(repo, home, fake_fleet, git_out, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet, mission_test=None)
    before = _unchanged(repo, git_out)

    with pytest.raises(LandInvalid, match="no gate command"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3
    assert _unchanged(repo, git_out) == before


def test_refuses_an_unknown_mission(home):
    with pytest.raises(LandInvalid, match="mission 'no-such' does not exist"):
        land("no-such", "fix", home=home, checkout=str(home))
    # Nowhere to receipt: a typo must not conjure `missions/no-such/`.
    assert not (home / "missions" / "no-such").exists()


def test_refuses_an_unknown_lane_and_still_writes_a_receipt(repo, home, fake_fleet):
    mission_id, _ = _run_landable_lane(repo, home, fake_fleet)

    with pytest.raises(LandInvalid, match="lane 'nope' does not exist"):
        land(mission_id, "nope", home=home, checkout=str(repo))
    receipts = list((home / "missions" / mission_id / "land").glob("nope-*.json"))
    assert len(receipts) == 1
    refused = json.loads(receipts[0].read_text())
    assert "refused" in refused
