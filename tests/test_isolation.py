"""Worktree isolation for write dispatches.

A branch is not isolation: HEAD and the index are shared, so two fleets on
one checkout race each other. These tests pin that an isolated dispatch never
touches the caller's checkout, lands its work on its own branch, and cleans up
only when nothing would be lost.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conductor import worktrees
from conductor.fleets import Spec
from conductor.runner import dispatch


def test_isolated_work_lands_on_its_own_branch_and_the_checkout_stays_clean(
    repo, home, fake_fleet, git_out
):
    fake_fleet(["sh", "-c", "echo work > new.txt"])
    result = dispatch(
        Spec(fleet="codex", prompt="isolated write", cwd=str(repo), mode="write"),
        commit_message="feat: isolated work",
        isolate=True,
        home=home,
    )
    assert result.ok is True
    iso = result.isolation
    assert iso["active"] is True
    assert iso["branch"] == f"conductor/{result.run_id}"
    # The fleet ran in the worktree, not the caller's checkout.
    assert result.cwd == iso["worktree"]
    assert result.cwd != str(repo)
    # The caller's checkout: still on main, still clean, HEAD unmoved.
    assert git_out(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git_out(repo, "status", "--porcelain") == ""
    assert git_out(repo, "rev-parse", "HEAD") == iso["base_sha"]
    # The commit is reachable on the conductor branch.
    assert "feat: isolated work" in git_out(repo, "log", "--oneline", iso["branch"])
    # Nothing uncommitted, so the desk was cleared and the branch kept.
    assert iso["kept"] is False
    assert not Path(iso["worktree"]).exists()
    assert result.summary()["branch"] == iso["branch"]
    assert result.summary()["worktree"] is None


def test_a_dirty_worktree_is_kept_and_reported(repo, home, fake_fleet, git_out):
    """Deleting an agent's uncommitted work to tidy up is the wrong trade."""
    fake_fleet(["sh", "-c", "echo draft > draft.txt"])
    result = dispatch(
        Spec(fleet="codex", prompt="isolated draft", cwd=str(repo), mode="write"),
        isolate=True,
        home=home,
    )
    iso = result.isolation
    assert result.verdict["dirty_delta"] == 1
    assert iso["kept"] is True
    assert (Path(iso["worktree"]) / "draft.txt").read_text() == "draft\n"
    assert result.summary()["worktree"] == iso["worktree"]
    assert git_out(repo, "status", "--porcelain") == ""


def test_two_isolated_dispatches_do_not_see_each_other(repo, home, fake_fleet, git_out):
    fake_fleet(["sh", "-c", "echo a > a.txt"])
    first = dispatch(
        Spec(fleet="codex", prompt="lane a", cwd=str(repo), mode="write"),
        commit_message="a",
        isolate=True,
        home=home,
    )
    fake_fleet(["sh", "-c", "test ! -e a.txt && echo b > b.txt"])
    second = dispatch(
        Spec(fleet="claude", prompt="lane b", cwd=str(repo), mode="write"),
        commit_message="b",
        isolate=True,
        home=home,
    )
    assert first.ok and second.ok
    assert first.isolation["branch"] != second.isolation["branch"]
    files_b = git_out(repo, "ls-tree", "--name-only", second.isolation["branch"])
    assert "b.txt" in files_b and "a.txt" not in files_b


def test_a_read_dispatch_that_cannot_be_isolated_proceeds_in_place(
    tmp_path, home, fake_fleet
):
    plain = tmp_path / "plain"
    plain.mkdir()
    fake_fleet(["sh", "-c", "echo hi"])
    result = dispatch(
        Spec(fleet="codex", prompt="no repo", cwd=str(plain), mode="read"),
        isolate=True,
        home=home,
    )
    assert result.isolation["requested"] is True
    assert result.isolation["active"] is False
    assert "not a git repository" in result.isolation["reason"]
    assert result.cwd == str(plain)
    assert result.exit_code == 0 and result.ok is True


def test_a_write_dispatch_that_cannot_be_isolated_is_refused_not_run_in_place(
    tmp_path, home, fake_fleet
):
    """Falling back to the shared checkout is the collision isolation exists
    to prevent. Nothing may spawn."""
    plain = tmp_path / "plain"
    plain.mkdir()
    fake_fleet(["sh", "-c", "echo leaked > leaked.txt"])
    result = dispatch(
        Spec(fleet="codex", prompt="write anyway", cwd=str(plain), mode="write"),
        isolate=True,
        home=home,
    )
    assert result.ok is False
    assert result.exit_code is None
    assert "not a git repository" in result.error and "only read mode" in result.error
    assert not (plain / "leaked.txt").exists()
    assert not Path(result.stdout_path).exists()
    assert (Path(result.run_dir) / "result.json").is_file()


def test_a_subdirectory_cwd_stays_a_subdirectory_inside_the_worktree(
    repo, home, fake_fleet, git_out
):
    sub = repo / "pkg" / "inner"
    sub.mkdir(parents=True)
    (sub / "keep.txt").write_text("keep\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "sub"], cwd=repo, check=True)
    fake_fleet(["sh", "-c", "pwd > where.txt"])
    result = dispatch(
        Spec(fleet="codex", prompt="where am i", cwd=str(sub), mode="write"),
        commit_message="chore: where",
        isolate=True,
        home=home,
    )
    assert result.ok is True
    iso = result.isolation
    assert result.cwd == str(Path(iso["worktree"]) / "pkg" / "inner")
    tree = git_out(repo, "ls-tree", "-r", "--name-only", iso["branch"])
    assert "pkg/inner/where.txt" in tree


def test_a_worktree_the_fleet_deleted_is_pruned_not_a_crash(
    repo, home, fake_fleet, git_out
):
    fake_fleet(["sh", "-c", 'cd / && rm -rf "$OLDPWD"'])
    result = dispatch(
        Spec(fleet="codex", prompt="self destruct", cwd=str(repo), mode="write"),
        isolate=True,
        home=home,
    )
    iso = result.isolation
    assert iso["kept"] is False
    assert "vanished" in iso["reason"]
    assert not Path(iso["worktree"]).exists()
    # The stale registration is gone, so the next isolation can be added.
    assert iso["worktree"] not in git_out(repo, "worktree", "list")
    assert result.ok is False  # a write that left no tree behind moved no bytes


def test_a_branch_with_commits_survives_release(repo, tmp_path, git_out):
    iso = worktrees.create(str(repo), "probe", tmp_path / "wt")
    assert iso.active
    (Path(iso.worktree) / "w.txt").write_text("w\n")
    subprocess.run(["git", "add", "-A"], cwd=iso.worktree, check=True)
    subprocess.run(["git", "commit", "-qm", "w"], cwd=iso.worktree, check=True)
    worktrees.release(iso)
    assert iso.kept is False and iso.branch == "conductor/probe"
    assert "conductor/probe" in git_out(repo, "branch", "--list", "conductor/probe")


def test_a_no_op_isolated_write_leaves_no_branch_behind(repo, home, fake_fleet, git_out):
    """A lane that landed nothing should not litter the repo with an empty
    branch; its run directory is the only trace."""
    fake_fleet(["sh", "-c", "echo 'nothing to do'"])
    result = dispatch(
        Spec(fleet="codex", prompt="no-op", cwd=str(repo), mode="write"),
        isolate=True,
        home=home,
    )
    assert result.ok is False
    assert result.isolation["kept"] is False
    assert result.isolation["branch"] == ""
    assert result.summary()["branch"] is None
    assert git_out(repo, "branch", "--list", "conductor/*") == ""
