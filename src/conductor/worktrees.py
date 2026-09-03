"""Give each write dispatch its own checkout.

A branch is not isolation: HEAD and the index are shared mutable state, and
two fleets editing one working tree race each other whether or not they are
on different branches. A git worktree is the only thing that gives a dispatch
a private HEAD, so a mission fanning one prompt out to three fleets gets three
worktrees, three branches, and three separately verifiable results instead of
one tree with three agents' edits interleaved.

The worktree lives under conductor's own home, not inside the target repo, so
the target's status stays clean and nothing needs to be gitignored. The
branch is named after the run id, which is unique and sorts by time. After the
dispatch the worktree is removed when it holds nothing uncommitted (the branch
keeps the commits); a dirty worktree is left in place and its path reported,
because deleting an agent's uncommitted work to tidy up is the wrong trade.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

GIT_TIMEOUT = 60


def _git(cwd: str | Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A vanished cwd or a hung git must read as a failed command, not as
        # an exception in the middle of an unattended run.
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


@dataclass
class Isolation:
    """One dispatch's private checkout, and what became of it."""

    requested: bool
    active: bool
    repo: str = ""
    worktree: str = ""
    branch: str = ""
    base_sha: str = ""
    kept: bool | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def create(repo: str, name: str, parent: Path) -> Isolation:
    """Add a worktree for `repo` at `parent/name` on a fresh branch from HEAD.

    Returns an inactive Isolation with a reason rather than raising when the
    target cannot be isolated (not a repo, unborn HEAD, git failure), so the
    caller can decide whether to proceed in place. It does not decide that
    here: a read dispatch on a non-repo is fine, a write dispatch is the
    caller's call.
    """
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return Isolation(requested=True, active=False, reason="not a git repository")
    root = top.stdout.strip()
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        return Isolation(
            requested=True, active=False, repo=root, reason="repository has no commits yet"
        )
    base = head.stdout.strip()
    branch = f"conductor/{name}"
    path = parent / name
    parent.mkdir(parents=True, exist_ok=True)
    added = _git(root, "worktree", "add", "-b", branch, str(path), base)
    if added.returncode != 0:
        return Isolation(
            requested=True,
            active=False,
            repo=root,
            reason=f"git worktree add failed: {added.stderr.strip() or added.stdout.strip()}",
        )
    return Isolation(
        requested=True,
        active=True,
        repo=root,
        worktree=str(path),
        branch=branch,
        base_sha=base,
    )


def mirror_path(cwd: str, iso: Isolation) -> str:
    """The path inside the worktree that corresponds to `cwd` in the repo.

    `cwd` may be a subdirectory of the repo; the fleet should run in the same
    subdirectory of its private copy, not at the worktree root.
    """
    try:
        rel = Path(cwd).resolve().relative_to(Path(iso.repo).resolve())
    except ValueError:
        return iso.worktree
    return str(Path(iso.worktree) / rel)


def release(iso: Isolation) -> Isolation:
    """Remove the worktree if it is clean; keep it (and say so) if it is not.

    The branch survives either way. Commits are what the caller merges; the
    worktree was only ever the fleet's desk.
    """
    if not iso.active:
        return iso
    if not Path(iso.worktree).is_dir():
        # The fleet (or something else) removed its own desk. Nothing to keep;
        # tell git so the stale registration does not block the next add.
        _git(iso.repo, "worktree", "prune")
        iso.kept = False
        iso.reason = "worktree directory vanished during the dispatch; registration pruned"
        return iso
    status = _git(iso.worktree, "status", "--porcelain")
    dirty = status.returncode != 0 or bool(status.stdout.strip())
    if dirty:
        iso.kept = True
        iso.reason = "worktree kept: it holds uncommitted changes"
        return iso
    removed = _git(iso.repo, "worktree", "remove", iso.worktree)
    if removed.returncode != 0:
        iso.kept = True
        iso.reason = f"worktree kept: remove failed: {removed.stderr.strip()}"
        return iso
    iso.kept = False
    iso.reason = "worktree removed; commits remain on the branch"
    return iso
