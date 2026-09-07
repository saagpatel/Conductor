"""F7: `conductor land` -- merge a lane's branch, gate the merged head in a
fresh worktree, run `golden check`, and attest the mission. All against a
temporary git repository built in the test, never this checkout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conductor.cli import main
from conductor.land import _GOLDEN_CHILD, LandInvalid, _golden_check, land
from conductor.mission import mission_from_dict, run_mission


@pytest.fixture(autouse=True)
def _not_inside_a_lane(monkeypatch):
    """These tests stand in for the lead's shell. When the suite itself runs
    under a lane's gate, or under `conductor land`'s own gate (which
    carries the lane marker on purpose, so a gate can never land), every
    call to `land()` here would inherit `CONDUCTOR_LANE` and refuse -- the
    first live landing (F12, 2026-09-07) failed its gate on exactly that.
    The one test that asserts the refusal sets the marker itself."""
    monkeypatch.delenv("CONDUCTOR_LANE", raising=False)


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


def test_a_branch_that_diverged_from_an_older_tip_lands_with_a_merge_commit(
    repo, home, fake_fleet, monkeypatch, git_out
):
    """The first live `land` (F12, 2026-09-07) was refused because the lane's
    branch was not a descendant of HEAD: it had been launched on 0.52.0 and
    the checkout had moved to 0.53.0 since. That is every merge this project
    makes. Shared history is the requirement, not descent."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    (repo / "moved-on.txt").write_text("the checkout moved on\n")
    subprocess.run(["git", "add", "moved-on.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "chore: a later release"], cwd=repo, check=True
    )
    pre_head = git_out(repo, "rev-parse", "HEAD")

    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.ok is True and result.already_merged is False
    head = git_out(repo, "rev-parse", "HEAD")
    assert head != pre_head
    parents = git_out(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3, parents
    assert pre_head in parents


def test_a_branch_with_no_shared_history_is_refused(
    repo, home, fake_fleet, monkeypatch, git_out, tmp_path
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    receipt = json.loads((home / "missions" / mission_id / "lanes" / f"{lane}.json").read_text())
    branch = receipt["branch"]
    subprocess.run(["git", "checkout", "-q", "--orphan", "elsewhere"], cwd=repo, check=True)
    subprocess.run(["git", "rm", "-rfq", "."], cwd=repo, check=True)
    (repo / "root.txt").write_text("unrelated\n")
    subprocess.run(["git", "add", "root.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "unrelated root"], cwd=repo, check=True)
    pre_head = git_out(repo, "rev-parse", "HEAD")

    with pytest.raises(LandInvalid, match="shares no history"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert git_out(repo, "rev-parse", "HEAD") == pre_head
    assert git_out(repo, "rev-parse", branch)


# --- D6: the merge is pinned to the receipted sha, in the same repository ---


def test_refuses_a_branch_that_moved_since_the_lane_receipted_it(
    repo, home, fake_fleet, git_out, monkeypatch
):
    """D6: `land` merged whatever `refs/heads/<branch>` pointed at when it
    ran, discarding the sha the lane actually receipted. A branch moved after
    the run (a rebase, a stray commit, a hand-made reset) would have landed
    unread work."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    receipted = json.loads(
        (home / "missions" / mission_id / "lanes" / f"{lane}.json").read_text()
    )["tip_sha"]
    subprocess.run(
        ["git", "branch", "-f", "feat/land", "feat/land^"], cwd=repo, check=True
    )
    before = _unchanged(repo, git_out)

    with pytest.raises(LandInvalid, match="the branch moved since the run"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(repo)]) == 3
    assert _unchanged(repo, git_out) == before
    # The refusal names both shas, so the lead can see which is which.
    receipts = sorted((home / "missions" / mission_id / "land").glob(f"{lane}-*.json"))
    refused = json.loads(receipts[-1].read_text())["refused"]
    assert receipted in refused and git_out(repo, "rev-parse", "feat/land") in refused


def test_a_tag_shadowing_the_branch_name_does_not_change_what_is_merged(
    repo, home, fake_fleet, git_out, monkeypatch
):
    """D6: the destination resolved the bare name (`git rev-parse feat/land`),
    which a tag of the same name shadows. The merge now names the sha the
    lane's `refs/heads/` tip resolved to, so the tag is inert."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    branch_tip = git_out(repo, "rev-parse", "feat/land")
    # A tag pointing somewhere else entirely, named exactly like the branch.
    (repo / "decoy.txt").write_text("not the lane's work\n")
    subprocess.run(["git", "add", "decoy.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "chore: decoy"], cwd=repo, check=True)
    decoy = git_out(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "tag", "feat/land", decoy], cwd=repo, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD^"], cwd=repo, check=True)

    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.ok is True
    assert result.tip_sha == branch_tip
    parents = git_out(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert branch_tip in parents and decoy not in parents
    assert not (repo / "decoy.txt").is_file()
    receipt = json.loads(Path(result.receipt_path).read_text())
    assert receipt["tip_sha"] == branch_tip
    assert receipt["merge_sha"] == git_out(repo, "rev-parse", "HEAD")


def test_refuses_a_checkout_that_is_a_different_repository(
    repo, home, fake_fleet, git_out, monkeypatch, tmp_path
):
    """D6: nothing tied the destination to the lane's repository, so a
    different repository holding a branch of the same name was a valid
    destination -- and its own same-named branch is what would have merged."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=other, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=other, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=other, check=True)
    (other / "seed.txt").write_text("someone else's repository\n")
    subprocess.run(["git", "add", "-A"], cwd=other, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=other, check=True)
    subprocess.run(["git", "branch", "feat/land"], cwd=other, check=True)
    before = _unchanged(other, git_out)

    with pytest.raises(LandInvalid, match="not the same repository"):
        land(mission_id, lane, home=home, checkout=str(other))
    assert main(["land", mission_id, "--lane", lane, "--checkout", str(other)]) == 3
    assert _unchanged(other, git_out) == before


def test_already_merged_still_refuses_a_moved_branch(
    repo, home, fake_fleet, git_out, monkeypatch
):
    """D6: the already-merged shortcut returned ok before any identity check
    ran, so a moved branch read as `already_merged` instead of a refusal."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    land(mission_id, lane, home=home, checkout=str(repo))
    # The branch moves onto the merge commit: it is still an ancestor of HEAD,
    # so the shortcut would have answered ok=True.
    subprocess.run(["git", "branch", "-f", "feat/land", "HEAD"], cwd=repo, check=True)
    before = _unchanged(repo, git_out)

    with pytest.raises(LandInvalid, match="the branch moved since the run"):
        land(mission_id, lane, home=home, checkout=str(repo))
    assert _unchanged(repo, git_out) == before


def test_already_merged_records_the_pinned_tip(repo, home, fake_fleet, git_out, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    first = land(mission_id, lane, home=home, checkout=str(repo))

    again = land(mission_id, lane, home=home, checkout=str(repo))

    assert again.already_merged is True and again.ok is True
    assert again.tip_sha == first.tip_sha == git_out(repo, "rev-parse", "feat/land")


def test_already_merged_dry_run_records_the_dry_run_flag(repo, home, fake_fleet, monkeypatch):
    """A `--dry-run` on a landed lane answered `dry_run: false` on its
    receipt, because the already-merged shortcut returned before the flag
    was recorded (third drill pass, 2026-09-07)."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    land(mission_id, lane, home=home, checkout=str(repo))

    again = land(mission_id, lane, home=home, checkout=str(repo), dry_run=True)

    assert again.already_merged is True and again.dry_run is True


# --- D19: golden check runs the merged tree's own implementation ---------


def _fake_conductor_tree(worktree: Path, sentinel: str) -> None:
    """A merged worktree that looks enough like conductor's own source for
    `_golden_check` to take the subprocess path: one fixture directory and a
    `src/conductor` whose `golden` and `cli` are stand-ins that say who they
    are. Nothing here imports the real package."""
    fixture = worktree / "tests" / "golden" / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "golden.json").write_text("{}\n")
    pkg = worktree / "src" / "conductor"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "golden.py").write_text(f"SENTINEL = {sentinel!r}\n")
    (pkg / "cli.py").write_text(
        "import sys\n"
        "\n"
        "from conductor import golden\n"
        "\n"
        "\n"
        "def main(argv):\n"
        "    print('argv: ' + ' '.join(argv))\n"
        "    print('golden: ' + golden.SENTINEL)\n"
        "    print('root: ' + sys.modules['conductor'].__file__)\n"
        "    return 1\n"
    )


def test_golden_check_runs_the_merged_worktrees_own_golden(tmp_path):
    """D19: `_golden_check` called `golden.check` in this process, so landing
    a change to golden, mission, or the parser replayed the merged tree's new
    fixtures with the old code. The check is a subprocess now, importing from
    the merged worktree's own `src`."""
    worktree = tmp_path / "merged"
    worktree.mkdir()
    _fake_conductor_tree(worktree, "the-merged-trees-own-golden")

    lines = _golden_check(worktree)

    assert any("golden: the-merged-trees-own-golden" in line for line in lines), lines
    # The subcommand the child ran, and the import root it resolved to.
    assert any(line == "argv: golden check" for line in lines), lines
    assert any(line.startswith(f"root: {worktree}/src/conductor/") for line in lines), lines


def test_golden_check_is_clean_when_the_merged_trees_check_exits_0(tmp_path):
    worktree = tmp_path / "merged"
    worktree.mkdir()
    _fake_conductor_tree(worktree, "clean")
    cli = worktree / "src" / "conductor" / "cli.py"
    cli.write_text(cli.read_text().replace("    return 1\n", "    return 0\n"))

    assert _golden_check(worktree) == []


def test_golden_check_reports_a_child_that_will_not_start(tmp_path):
    """A merged tree whose own `cli` cannot even be imported is a red golden
    step naming why, never a swallowed exception or a silent pass."""
    worktree = tmp_path / "merged"
    worktree.mkdir()
    _fake_conductor_tree(worktree, "broken")
    (worktree / "src" / "conductor" / "cli.py").write_text("raise RuntimeError('boom')\n")

    lines = _golden_check(worktree)

    assert any("boom" in line for line in lines), lines


def test_the_golden_child_refuses_an_import_from_outside_the_worktree(tmp_path):
    """The subprocess is only worth anything if `conductor` really came from
    the merged worktree; the child says so itself rather than trusting the
    PYTHONPATH it was handed."""
    elsewhere = tmp_path / "elsewhere"
    pkg = elsewhere / "conductor"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text("def main(argv):\n    return 0\n")
    worktree = tmp_path / "merged"
    worktree.mkdir()

    done = subprocess.run(
        [sys.executable, "-c", _GOLDEN_CHILD, str(worktree)],
        cwd=worktree,
        env={**os.environ, "PYTHONPATH": str(elsewhere)},
        capture_output=True,
        text=True,
    )

    assert done.returncode == 3, done
    assert "not from" in done.stderr and str(worktree) in done.stderr


def test_golden_check_still_replays_in_process_for_a_tree_that_is_not_conductor(tmp_path):
    """A merged tree with fixtures but no `src/conductor` has no
    implementation of its own to be stale about; the in-process replay stands,
    and a bad fixture is still a diff line rather than an exception."""
    worktree = tmp_path / "merged"
    fixture = worktree / "tests" / "golden" / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "golden.json").write_text("{}\n")

    lines = _golden_check(worktree)

    assert lines and all(line.startswith("fixture: ") for line in lines), lines
