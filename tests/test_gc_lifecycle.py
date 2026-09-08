"""gc keeps what a later step still needs, and plans each repository once.

Three failure modes, each of which lost or duplicated work before it was
pinned here: a clean worktree whose branch is the only copy of committed work
(AGENTS.md rule 6's salvage path reads it from disk), a run named only by a
lane receipt's `previous_attempts`, and a bare repository whose linked
worktrees each produced their own copy of the same plan.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conductor import worktrees
from conductor.gc import apply_plan, build_plan

_OLD = "20200101T000000Z"


def _git(cwd: Path | str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(f"{name}\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", name)
    return _git(path, "rev-parse", "HEAD")


def _receipt(home: Path, run_id: str, repo: Path) -> None:
    run = home / "runs" / run_id
    run.mkdir(parents=True)
    (run / "result.json").write_text(json.dumps({"isolation": {"active": True, "repo": str(repo)}}))


def _worktree_rows(plans, path: Path) -> list:
    return [
        item
        for plan in plans
        for item in plan.items
        if item.kind == "worktree" and Path(item.path) == path
    ]


def test_a_clean_worktree_holding_unmerged_commits_is_kept_for_salvage(repo: Path, home: Path):
    kept = worktrees.create(str(repo), f"{_OLD}-kept", home / "worktrees")
    path = Path(kept.worktree)
    _commit(path, "salvageable.txt")
    _receipt(home, f"{_OLD}-kept", repo)

    plans, _notices = build_plan(home, [], 0.0)
    rows = _worktree_rows(plans, path)

    assert len(rows) == 1
    assert rows[0].action == "keep", "a clean worktree whose branch is unmerged is a salvage source"
    assert "unmerged work" in rows[0].reason


def test_a_clean_worktree_whose_work_already_landed_is_still_removed(repo: Path, home: Path):
    done = worktrees.create(str(repo), f"{_OLD}-done", home / "worktrees")
    path = Path(done.worktree)
    tip = _commit(path, "landed.txt")
    _git(repo, "merge", "--ff-only", tip)
    _receipt(home, f"{_OLD}-done", repo)

    plans, _notices = build_plan(home, [], 0.0)
    rows = _worktree_rows(plans, path)

    assert len(rows) == 1
    assert rows[0].action == "remove"
    assert rows[0].reason == "clean worktree"


def test_gc_apply_rechecks_unmerged_reachability_before_removing_a_worktree(
    repo: Path, home: Path
):
    done = worktrees.create(str(repo), f"{_OLD}-became-unmerged", home / "worktrees")
    path = Path(done.worktree)
    _receipt(home, f"{_OLD}-became-unmerged", repo)

    plans, notices = build_plan(home, [], 0.0)
    rows = _worktree_rows(plans, path)
    assert len(rows) == 1
    assert rows[0].action == "remove"
    assert rows[0].reason == "clean worktree"

    _commit(path, "salvage-after-plan.txt")
    apply_plan(plans, notices, home)
    worktree = next(item for item in plans[0].items if Path(item.path) == path)

    assert path.is_dir()
    assert worktree.action == "keep"
    assert "unmerged work" in worktree.reason


def test_a_superseded_attempt_of_a_live_mission_is_protected(repo: Path, home: Path):
    run_id = f"{_OLD}-superseded"
    superseded = worktrees.create(str(repo), run_id, home / "worktrees")
    path = Path(superseded.worktree)
    tip = _commit(path, "retried.txt")
    # The work landed, so nothing but `previous_attempts` can protect it.
    _git(repo, "merge", "--ff-only", tip)
    _receipt(home, run_id, repo)

    mission = home / "missions" / f"{_OLD}-live"
    (mission / "lanes").mkdir(parents=True)
    (mission / "lanes" / "build.json").write_text(
        json.dumps(
            {
                "previous_attempts": [{"run_id": run_id}],
                "attempts": [{"run_id": f"{_OLD}-current"}],
            }
        )
    )

    plans, _notices = build_plan(home, [], 0.0)
    rows = _worktree_rows(plans, path)

    assert len(rows) == 1
    assert rows[0].action == "keep"
    assert rows[0].reason == "run in progress (no result.json)"


def test_a_bare_repository_is_planned_once_not_once_per_linked_worktree(
    repo: Path, home: Path, tmp_path: Path
):
    """A run receipt records its lane worktree as `isolation.repo`, so every
    lane of one repository reaches `_main_worktree` by a different path. For a
    bare repository the `--show-toplevel` fallback answered with the linked
    worktree itself, so the same repository was planned once per lane: every
    action duplicated, and `--apply`'s second pass failing on what its first
    had already removed."""
    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "--bare", "-q", str(repo), str(bare))

    root = home / "worktrees"
    root.mkdir(parents=True)
    paths = []
    for suffix in ("one", "two"):
        run_id = f"{_OLD}-{suffix}"
        path = root / run_id
        _git(bare, "worktree", "add", "-q", "-b", f"conductor/{run_id}", str(path), "HEAD")
        paths.append(path)
        _receipt(home, run_id, path)

    plans, _notices = build_plan(home, [], 0.0)

    assert len(plans) == 1, "one bare repository is one plan, whatever its worktrees report"
    assert plans[0].repo == bare.resolve()
    for path in paths:
        assert len(_worktree_rows(plans, path)) == 1
