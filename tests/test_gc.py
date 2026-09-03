"""Garbage collection preserves the only copy of work and the durable audit trail."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from conductor import worktrees
from conductor.cli import main
from conductor.gc import apply_plan, build_plan


def _git(cwd: Path | str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _receipt(home: Path, run_id: str, repo: Path) -> None:
    run = home / "runs" / run_id
    run.mkdir(parents=True)
    (run / "result.json").write_text(
        json.dumps({"isolation": {"active": True, "repo": str(repo)}})
    )


def _rows(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines() if line]


def _commit_in_worktree(path: Path, name: str) -> str:
    (path / name).write_text(f"{name}\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def test_gc_classifies_every_safe_and_unsafe_case_without_mutating(
    repo: Path, home: Path, monkeypatch, capsys
):
    old = "20200101T000000Z"
    stale = worktrees.create(str(repo), f"{old}-stale", home / "worktrees")
    shutil.rmtree(stale.worktree)

    clean = worktrees.create(str(repo), f"{old}-clean", home / "worktrees")
    dirty = worktrees.create(str(repo), f"{old}-dirty", home / "worktrees")
    (Path(dirty.worktree) / "draft.txt").write_text("draft\n")

    contained = worktrees.create(str(repo), f"{old}-contained", home / "worktrees")
    contained_tip = _commit_in_worktree(Path(contained.worktree), "contained.txt")
    _git(repo, "branch", "refactor/deliverable", contained_tip)
    _git(repo, "worktree", "remove", contained.worktree)

    unmerged = worktrees.create(str(repo), f"{old}-unmerged", home / "worktrees")
    _commit_in_worktree(Path(unmerged.worktree), "unmerged.txt")
    _git(repo, "worktree", "remove", unmerged.worktree)

    _git(repo, "branch", f"conductor/{old}-merged", "HEAD")
    foreign_path = home.parent / "foreign-worktree"
    _git(repo, "worktree", "add", "-b", "foreign/branch", str(foreign_path), "HEAD")
    _receipt(home, f"{old}-receipt", repo)
    incomplete_run = home / "runs" / f"{old}-incomplete"
    incomplete_mission = home / "missions" / f"{old}-incomplete"
    incomplete_run.mkdir(parents=True)
    incomplete_mission.mkdir(parents=True)

    before = _git(repo, "show-ref")
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["gc"]) == 0
    rows = _rows(capsys.readouterr().out)

    by_path = {row.get("path"): row for row in rows}
    by_name = {row.get("name"): row for row in rows}
    assert by_path[stale.worktree]["action"] == "prune"
    assert by_path[clean.worktree]["action"] == "remove"
    assert by_path[dirty.worktree] == {
        "action": "keep",
        "kind": "worktree",
        "path": dirty.worktree,
        "reason": "uncommitted work",
        "repo": str(repo),
    }
    assert by_name[dirty.branch]["reason"] == "uncommitted work"
    assert by_name[f"conductor/{old}-merged"]["reason"] == "merged into HEAD"
    assert by_name[contained.branch]["reason"] == "contained in refactor/deliverable"
    assert by_name[unmerged.branch]["reason"] == "unmerged commits"
    assert by_name[unmerged.branch]["commits_ahead"] == 1
    assert by_path[str(foreign_path)]["reason"] == "outside CONDUCTOR_HOME/worktrees"
    assert by_path[str(incomplete_run)]["reason"] == "no result.json"
    assert by_path[str(incomplete_mission)]["reason"] == "no result.json"

    assert Path(clean.worktree).is_dir()
    assert Path(dirty.worktree).is_dir()
    assert stale.worktree in _git(repo, "worktree", "list")
    assert _git(repo, "show-ref") == before


def test_gc_apply_performs_the_plan_but_keeps_dirty_and_foreign_work(
    repo: Path, home: Path, monkeypatch, capsys
):
    old = "20200101T000000Z"
    stale = worktrees.create(str(repo), f"{old}-stale", home / "worktrees")
    shutil.rmtree(stale.worktree)
    clean = worktrees.create(str(repo), f"{old}-clean", home / "worktrees")
    dirty = worktrees.create(str(repo), f"{old}-dirty", home / "worktrees")
    (Path(dirty.worktree) / "draft.txt").write_text("draft\n")

    contained = worktrees.create(str(repo), f"{old}-contained", home / "worktrees")
    tip = _commit_in_worktree(Path(contained.worktree), "contained.txt")
    _git(repo, "branch", "deliverable/kept", tip)
    _git(repo, "worktree", "remove", contained.worktree)

    unmerged = worktrees.create(str(repo), f"{old}-unmerged", home / "worktrees")
    _commit_in_worktree(Path(unmerged.worktree), "unmerged.txt")
    _git(repo, "worktree", "remove", unmerged.worktree)

    foreign_path = home.parent / "foreign-worktree"
    _git(repo, "worktree", "add", "-b", "foreign/branch", str(foreign_path), "HEAD")
    _receipt(home, f"{old}-receipt", repo)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    assert main(["gc", "--apply"]) == 0
    rows = _rows(capsys.readouterr().out)
    assert all(row["done"] is True and row["error"] == "" for row in rows)
    assert stale.worktree not in _git(repo, "worktree", "list")
    assert not Path(clean.worktree).exists()
    assert Path(dirty.worktree, "draft.txt").read_text() == "draft\n"
    assert Path(foreign_path).is_dir()

    branches = _git(repo, "branch", "--format=%(refname:short)").splitlines()
    assert stale.branch not in branches
    assert clean.branch not in branches
    assert contained.branch not in branches
    assert dirty.branch in branches
    assert unmerged.branch in branches
    assert "deliverable/kept" in branches
    assert "foreign/branch" in branches


def test_gc_older_than_keeps_fresh_worktree_and_branch(
    repo: Path, home: Path, monkeypatch, capsys
):
    fresh = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-fresh"
    isolation = worktrees.create(str(repo), fresh, home / "worktrees")
    _receipt(home, fresh, repo)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    assert main(["gc", "--older-than", "1", "--apply"]) == 0
    rows = _rows(capsys.readouterr().out)
    worktree = next(row for row in rows if row.get("path") == isolation.worktree)
    branch = next(row for row in rows if row.get("name") == isolation.branch)
    assert worktree["action"] == branch["action"] == "keep"
    assert worktree["reason"] == branch["reason"] == "newer than --older-than"
    assert Path(isolation.worktree).is_dir()
    assert isolation.branch in _git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_gc_reports_a_missing_explicit_repo(home: Path, monkeypatch, capsys):
    missing = home.parent / "gone"
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["gc", "--repo", str(missing)]) == 0
    assert _rows(capsys.readouterr().out) == [
        {
            "action": "keep",
            "kind": "repo",
            "path": str(missing),
            "reason": "repo path does not exist",
            "repo": str(missing),
        }
    ]


def test_gc_apply_keeps_an_in_progress_runs_worktree_and_branch(
    repo: Path, home: Path, monkeypatch, capsys
):
    run_id = "20200101T000000Z-live"
    isolation = worktrees.create(str(repo), run_id, home / "worktrees")
    (home / "runs" / run_id).mkdir(parents=True)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    assert main(["gc", "--repo", str(repo), "--apply"]) == 0
    rows = _rows(capsys.readouterr().out)
    worktree = next(row for row in rows if row.get("path") == isolation.worktree)
    branch = next(row for row in rows if row.get("name") == isolation.branch)
    assert worktree["action"] == branch["action"] == "keep"
    assert worktree["reason"] == branch["reason"] == "run in progress (no result.json)"
    assert Path(isolation.worktree).is_dir()
    assert isolation.branch in _git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_gc_rechecks_run_liveness_immediately_before_remove_and_delete(repo: Path, home: Path):
    run_id = "20200101T000000Z-became-live"
    isolation = worktrees.create(str(repo), run_id, home / "worktrees")
    plans, notices = build_plan(home, [str(repo)], 0)
    assert any(item.action == "remove" for item in plans[0].items)

    (home / "runs" / run_id).mkdir(parents=True)
    assert apply_plan(plans, notices, home) is False
    worktree = next(item for item in plans[0].items if item.path == isolation.worktree)
    branch = next(item for item in plans[0].items if item.name == isolation.branch)
    assert worktree.action == branch.action == "keep"
    assert Path(isolation.worktree).is_dir()
    assert isolation.branch in _git(repo, "branch", "--format=%(refname:short)").splitlines()


def test_gc_keeps_a_completed_lane_worktree_while_its_mission_is_running(
    repo: Path, home: Path, monkeypatch, capsys
):
    run_id = "20200101T000000Z-mission-lane"
    isolation = worktrees.create(str(repo), run_id, home / "worktrees")
    _receipt(home, run_id, repo)
    lane_dir = home / "missions" / "20200101T000000Z-live" / "lanes"
    lane_dir.mkdir(parents=True)
    (lane_dir / "build.json").write_text(json.dumps({"attempts": [{"run_id": run_id}]}))
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    assert main(["gc", "--apply"]) == 0
    rows = _rows(capsys.readouterr().out)
    worktree = next(row for row in rows if row.get("path") == isolation.worktree)
    assert worktree["reason"] == "run in progress (no result.json)"
    assert Path(isolation.worktree).is_dir()
