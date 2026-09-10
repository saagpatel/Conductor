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

from dataclasses import asdict, dataclass
from pathlib import Path

from .verify import git_run

GIT_TIMEOUT = 60


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
    # Where the worktree ended up: the commit HEAD pointed at when the
    # dispatch was over, and whether everything in the tree was in it. A
    # later lane may build on `tip_sha` only when `clean` is True; a branch
    # name proves nothing about uncommitted work left in a kept worktree.
    tip_sha: str = ""
    clean: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def create(repo: str, name: str, parent: Path, base_ref: str | None = None) -> Isolation:
    """Add a worktree for `repo` at `parent/name` on a fresh branch from
    `base_ref` (a commit; HEAD when not given).

    Returns an inactive Isolation with a reason rather than raising when the
    target cannot be isolated (not a repo, unborn HEAD, git failure), so the
    caller can decide whether to proceed in place. It does not decide that
    here: a read dispatch on a non-repo is fine, a write dispatch is the
    caller's call.
    """
    top = git_run(repo, "rev-parse", "--show-toplevel", timeout=GIT_TIMEOUT)
    if top.returncode != 0:
        return Isolation(requested=True, active=False, reason="not a git repository")
    root = top.stdout.strip()
    head = git_run(
        root, "rev-parse", "--verify", f"{base_ref or 'HEAD'}^{{commit}}", timeout=GIT_TIMEOUT
    )
    if head.returncode != 0:
        reason = (
            "repository has no commits yet"
            if base_ref is None
            else f"base {base_ref} is not a commit in this repository"
        )
        return Isolation(requested=True, active=False, repo=root, reason=reason)
    base = head.stdout.strip()
    branch = f"conductor/{name}"
    path = parent / name
    parent.mkdir(parents=True, exist_ok=True)
    added = git_run(root, "worktree", "add", "-b", branch, str(path), base, timeout=GIT_TIMEOUT)
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
        git_run(iso.repo, "worktree", "prune", timeout=GIT_TIMEOUT)
        iso.kept = False
        iso.reason = "worktree directory vanished during the dispatch; registration pruned"
        return iso
    status = git_run(iso.worktree, "status", "--porcelain", timeout=GIT_TIMEOUT)
    dirty = status.returncode != 0 or bool(status.stdout.strip())
    # The exit code, not just the output: a `rev-parse` that did not run
    # returns an empty stdout, and an empty `head` is never equal to a
    # `base_sha` that is always set here, so a clean no-op lane was reported
    # as "commits remain on the branch" and its `tip_sha` went onto the
    # receipt as "" for a caller to merge (2026-09-08 review).
    rev = git_run(iso.worktree, "rev-parse", "HEAD", timeout=GIT_TIMEOUT)
    if rev.returncode != 0 or not rev.stdout.strip():
        iso.kept = True
        detail = rev.stderr.strip() or f"exit {rev.returncode}"
        iso.reason = f"worktree kept: could not read its HEAD: {detail}"
        return iso
    head = rev.stdout.strip()
    iso.tip_sha = head
    iso.clean = not dirty
    if dirty:
        iso.kept = True
        iso.reason = "worktree kept: it holds uncommitted changes"
        return iso
    removed = git_run(iso.repo, "worktree", "remove", iso.worktree, timeout=GIT_TIMEOUT)
    if removed.returncode != 0:
        iso.kept = True
        iso.reason = f"worktree kept: remove failed: {removed.stderr.strip()}"
        return iso
    iso.kept = False
    if head == iso.base_sha:
        # Clean and still at the base: nothing landed, so a branch would only
        # be litter. A no-op lane leaves no trace but its run directory.
        deleted = git_run(iso.repo, "branch", "-D", iso.branch, timeout=GIT_TIMEOUT)
        if deleted.returncode != 0:
            # Lock contention, or a branch checked out somewhere else. Saying
            # "branch deleted" about a branch still in the repository is the
            # receipt disagreeing with the bytes (2026-09-08 review).
            detail = deleted.stderr.strip() or f"exit {deleted.returncode}"
            iso.reason = f"worktree removed; no commits landed, branch kept: {detail}"
            return iso
        iso.branch = ""
        iso.reason = "worktree removed; no commits landed, branch deleted"
        return iso
    iso.reason = "worktree removed; commits remain on the branch"
    return iso
