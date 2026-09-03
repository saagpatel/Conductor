"""Verify on bytes.

An agent's exit code is a claim, and so is its final message. Both routinely
read "done, tests pass" over a run that changed nothing. The only evidence
that work happened is the repository before and after, so conductor snapshots
git state around every dispatch and reports the delta whether or not anyone
asked.

The failure this exists to catch has a name in the operator's notes: exit 0
with an empty diff, which reads as success in every log and every summary.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

GIT_TIMEOUT = 30


def git_run(
    cwd: str | Path, *args: str, timeout: int = GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A cwd the fleet deleted, or a hung git, must read as a failed
        # command rather than an exception in the middle of a 3am run.
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


_git = git_run


@dataclass
class GitState:
    """Enough of a repo's state to tell whether a run actually did anything."""

    is_repo: bool
    head: str = ""
    branch: str = ""
    dirty_files: int = 0

    @classmethod
    def capture(cls, cwd: str) -> GitState:
        top = _git(cwd, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return cls(is_repo=False)
        head = _git(cwd, "rev-parse", "HEAD")
        branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        status = _git(cwd, "status", "--porcelain")
        return cls(
            is_repo=True,
            # An unborn HEAD is not an error here; it just means no commits yet.
            head=head.stdout.strip() if head.returncode == 0 else "",
            branch=branch.stdout.strip() if branch.returncode == 0 else "",
            dirty_files=len([ln for ln in status.stdout.splitlines() if ln.strip()]),
        )


@dataclass
class Verdict:
    """What changed, and whether that counts as work."""

    checked: bool
    no_op: bool = False
    commits_added: int = 0
    files_changed: int = 0
    dirty_delta: int = 0
    branch_before: str = ""
    branch_after: str = ""
    branch_moved: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def compare(cwd: str, before: GitState, after: GitState) -> Verdict:
    """Diff two snapshots into a verdict about whether bytes moved."""
    if before.is_repo and not after.is_repo:
        # The tree the fleet was given no longer exists. Whatever it did, no
        # work landed anywhere the caller can use.
        return Verdict(
            checked=True,
            no_op=True,
            branch_before=before.branch,
            notes=["the working tree vanished during the dispatch; no work landed"],
        )
    if not (before.is_repo and after.is_repo):
        return Verdict(
            checked=False,
            notes=["not a git repository; conductor cannot verify on bytes here"],
        )

    notes: list[str] = []
    commits = 0
    files = 0

    if before.head and after.head and before.head != after.head:
        rev = _git(cwd, "rev-list", "--count", f"{before.head}..{after.head}")
        if rev.returncode == 0:
            commits = int(rev.stdout.strip() or "0")
        stat = _git(cwd, "diff", "--name-only", before.head, after.head)
        if stat.returncode == 0:
            files = len([ln for ln in stat.stdout.splitlines() if ln.strip()])
    elif not before.head and after.head:
        # Unborn to born: the run made the repo's first commit.
        rev = _git(cwd, "rev-list", "--count", "HEAD")
        commits = int(rev.stdout.strip() or "0") if rev.returncode == 0 else 1
        notes.append("first commit(s) in a previously empty repository")

    dirty_delta = after.dirty_files - before.dirty_files
    branch_moved = before.branch != after.branch
    if branch_moved:
        notes.append(f"branch changed: {before.branch or '(none)'} -> {after.branch or '(none)'}")

    no_op = commits == 0 and dirty_delta == 0 and not branch_moved
    if no_op:
        notes.append(
            "no commits, no working-tree change: the run reported an outcome but moved no bytes"
        )

    return Verdict(
        checked=True,
        no_op=no_op,
        commits_added=commits,
        files_changed=files,
        dirty_delta=dirty_delta,
        branch_before=before.branch,
        branch_after=after.branch,
        branch_moved=branch_moved,
        notes=notes,
    )


@dataclass
class CommitOutcome:
    """Result of conductor committing a dispatch's work itself."""

    attempted: bool
    committed: bool = False
    sha: str = ""
    files: int = 0
    deletions: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def commit_work(cwd: str, message: str) -> CommitOutcome:
    """Commit whatever the dispatch left in the working tree.

    Committing belongs to conductor rather than to the fleets, because the
    fleets do not agree on whether they can commit at all: Codex's
    workspace-write sandbox blocks writes to .git, so it edits files and stops,
    while Claude Code, Cursor, and Antigravity all commit happily. Leaving it to
    each vendor makes "did it commit?" a property of the vendor instead of the
    work, and mixes four author identities and four message conventions into
    one history.

    Deletions are staged like anything else, but they are named in the receipt.
    A bulk stage that quietly swallows removed source files is the failure this
    reporting exists to prevent.
    """
    status = _git(cwd, "status", "--porcelain")
    if status.returncode != 0:
        return CommitOutcome(attempted=True, reason="not a git repository")
    lines = [ln for ln in status.stdout.splitlines() if ln.strip()]
    if not lines:
        return CommitOutcome(attempted=True, reason="nothing to commit")

    deletions = [ln[3:].strip() for ln in lines if ln[:2].strip() in {"D", "AD", "RD"}]

    add = _git(cwd, "add", "-A")
    if add.returncode != 0:
        return CommitOutcome(attempted=True, reason=f"git add failed: {add.stderr.strip()}")
    done = _git(cwd, "commit", "-m", message)
    if done.returncode != 0:
        return CommitOutcome(
            attempted=True,
            reason=f"git commit failed: {done.stderr.strip() or done.stdout.strip()}",
        )
    sha = _git(cwd, "rev-parse", "HEAD").stdout.strip()
    return CommitOutcome(
        attempted=True,
        committed=True,
        sha=sha,
        files=len(lines),
        deletions=deletions,
    )


@dataclass
class TestOutcome:
    ran: bool
    exit_code: int | None = None
    timed_out: bool = False
    tail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def run_tests(cwd: str, command: str, timeout: int = 900) -> TestOutcome:
    """Run the caller's own gate. Never piped: a pipeline's exit code is the
    last stage's, so a piped gate reports the pager's success, not the suite's.
    """
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return TestOutcome(ran=True, timed_out=True, tail=f"timed out after {timeout}s")
    except OSError as exc:
        return TestOutcome(ran=True, tail=f"gate could not start: {exc}")
    combined = (proc.stdout + proc.stderr).strip().splitlines()
    return TestOutcome(
        ran=True,
        exit_code=proc.returncode,
        tail="\n".join(combined[-15:]) if combined else "(no output)",
    )
