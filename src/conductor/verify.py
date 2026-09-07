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

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from pathlib import Path

GIT_TIMEOUT = 30

# `git_run` answers with this return code when git never ran at all: a cwd the
# fleet deleted, a hung git, or a spawn refused under load (fork exhaustion
# returns EAGAIN as an OSError). It is non-zero, so every caller that reads
# "non-zero means the command failed" keeps that reading; a caller that must
# tell "git said no" from "git said nothing" compares against it.
GIT_UNRUN = -1


def killpg(pid: int) -> None:
    """SIGKILL the process group a child started with start_new_session leads.

    The group id is the child's pid. That still holds after the child has
    exited and been reaped: the group lives on while any straggler does, and
    killpg by id reaches them. The only misfire would be a brand-new process
    that took the freed pid and made itself a group leader in the
    microseconds between the reap and this call; that window is accepted.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def git_run(
    cwd: str | Path, *args: str, timeout: int = GIT_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A cwd the fleet deleted, or a hung git, must read as a failed
        # command rather than an exception in the middle of a 3am run.
        return subprocess.CompletedProcess(["git", *args], GIT_UNRUN, "", str(exc))


_git = git_run


@dataclass
class GitState:
    """Enough of a repo's state to tell whether a run actually did anything."""

    is_repo: bool
    head: str = ""
    branch: str = ""
    dirty_files: int = 0
    manifest: str = ""
    # E1: per-path identity and bytes, not merely folded into `manifest`'s
    # single combined digest, so a caller (the read-lane deliverable
    # exemption) can ask which one path changed instead of only whether
    # anything did. Populated only when `content` is True, same as `manifest`.
    signatures: dict[str, str] = field(default_factory=dict)

    @classmethod
    def capture(cls, cwd: str, *, content: bool = True) -> GitState:
        top = _git(cwd, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return cls(is_repo=False)
        head = _git(cwd, "rev-parse", "HEAD")
        branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        root = Path(top.stdout.strip())
        status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        entries = _status_entries(status.stdout) if status.returncode == 0 else []
        signatures = _signatures(root, entries) if content else {}
        return cls(
            is_repo=True,
            # An unborn HEAD is not an error here; it just means no commits yet.
            head=head.stdout.strip() if head.returncode == 0 else "",
            branch=branch.stdout.strip() if branch.returncode == 0 else "",
            dirty_files=len(entries),
            manifest=_manifest(status.stdout, signatures) if content else "",
            signatures=signatures,
        )


def _status_entries(status: str) -> list[tuple[str, str]]:
    """Porcelain-v1 -z entries without Git's path quoting ambiguity."""
    fields = status.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        code = field[:2]
        entries.append((code, field[3:]))
        if "R" in code or "C" in code:
            index += 1  # rename/copy source path follows as its own NUL field
    return entries


def _entry_signature(path: Path) -> str:
    """One dirty path's identity and bytes, size and content hash joined."""
    try:
        if path.is_symlink():
            content = os.readlink(path).encode(errors="surrogateescape")
            size = len(content)
            content_hash = sha256(content).hexdigest()
        else:
            # A large untracked tree must not be duplicated in RSS merely
            # to prove its bytes moved; stream the same complete hash.
            content_digest = sha256()
            size = 0
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    content_digest.update(chunk)
            content_hash = content_digest.hexdigest()
    except OSError:
        size = -1
        content_hash = "missing"
    return f"{size}\0{content_hash}"


def _signatures(root: Path, entries: list[tuple[str, str]]) -> dict[str, str]:
    """Every dirty path's own signature, computed once and shared by
    `_manifest`'s combined digest and by `changed_entry_paths`' per-path
    comparison -- a large untracked file must not be hashed twice."""
    return {name: _entry_signature(root / name) for _, name in entries}


def _manifest(status: str, signatures: dict[str, str]) -> str:
    """Hash dirty path identities and bytes, not merely their count."""
    digest = sha256(status.encode(errors="surrogateescape"))
    for name in sorted(signatures):
        digest.update(f"\0{name}\0{signatures[name]}".encode(errors="surrogateescape"))
    return digest.hexdigest()


def changed_entry_paths(before: GitState, after: GitState) -> set[str]:
    """E1: every dirty path whose presence or content actually differs
    between two snapshots -- the per-path evidence `manifest`'s single
    combined hash otherwise collapses into one yes/no. Used by the read-lane
    deliverable exemption to tell "only the declared file changed" apart
    from "something else changed too"."""
    names = set(before.signatures) | set(after.signatures)
    return {name for name in names if before.signatures.get(name) != after.signatures.get(name)}


@dataclass
class Verdict:
    """What changed, and whether that counts as work."""

    checked: bool
    no_op: bool = False
    # F15: true only when `before.is_repo` and the tree is gone at `after` --
    # distinct from an ordinary `no_op` (which `no_op` stays True for too, so
    # every existing reader of that field is unchanged) so a caller can tell
    # "nothing happened" from "the tree the fleet was given no longer exists".
    vanished: bool = False
    commits_added: int = 0
    files_changed: int = 0
    dirty_delta: int = 0
    branch_before: str = ""
    branch_after: str = ""
    branch_moved: bool = False
    # E1: true only when this was a read dispatch with a declared deliverable
    # and the only dirty-path change was that file, set by runner.dispatch
    # (this module has no notion of a Spec's deliverable).
    deliverable_only: bool = False
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
            vanished=True,
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

    no_op = (
        before.head == after.head
        and before.branch == after.branch
        and before.manifest == after.manifest
    )
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


def changed_paths_since(cwd: str, base_sha: str, *, exclude: list[str] | None = None) -> list[str]:
    """Every path that differs from `base_sha` in the current tree --
    committed, staged, unstaged, and untracked -- optionally excluding Git
    pathspec globs (E16: an adversarial lane's test-surface patterns, so the
    caller can ask "did anything change outside the test?" without a second
    pass in Python). Sibling to `diff_since`: same notion of "everything the
    tree now holds that `base_sha` did not," as names rather than a patch.
    """
    exclusions = [f":(exclude,glob){pattern}" for pattern in exclude or []]
    names: set[str] = set()
    tracked = _git(cwd, "diff", "--name-only", base_sha, "--", ".", *exclusions)
    if tracked.returncode == 0:
        names.update(line for line in tracked.stdout.splitlines() if line.strip())
    status = _git(
        cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".", *exclusions
    )
    if status.returncode == 0:
        names.update(path for code, path in _status_entries(status.stdout) if code == "??")
    return sorted(names)


DIFF_LIMIT = 400_000


def diff_since(cwd: str, base_sha: str, limit: int = DIFF_LIMIT) -> str:
    """Everything the tree now holds that `base_sha` did not, as one unified
    diff: committed work, uncommitted edits, and untracked files.

    This is the evidence a judge should see. A lane's prose answer is a
    claim about what it changed; the patch is what it changed.
    """
    parts: list[str] = []
    tracked = _git(cwd, "diff", base_sha, "--")
    if tracked.returncode == 0:
        parts.append(tracked.stdout)
    status = _git(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    for code, path in _status_entries(status.stdout):
        if code == "??":
            # --no-index exits 1 whenever the files differ, which they do.
            new = _git(
                cwd,
                "-c",
                "core.quotePath=false",
                "diff",
                "--no-index",
                "--",
                "/dev/null",
                path,
            )
            if new.returncode in {0, 1}:
                parts.append(new.stdout)
            else:
                detail = new.stderr.strip() or new.stdout.strip() or f"exit {new.returncode}"
                parts.append(f"\n[conductor note: could not diff untracked {path!r}: {detail}]\n")
    text = "".join(parts)
    if len(text) > limit:
        text = text[:limit] + f"\n[... diff truncated at {limit} chars]\n"
    return text


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


def uncommit(cwd: str, outcome: CommitOutcome, base_sha: str) -> CommitOutcome:
    """Take a commit back off the branch, leaving its changes staged.

    A branch must never carry a commit that failed its gate: the receipt
    says not ok, but a clean-looking commit outlives the receipt. The work
    itself is kept in the tree (and so in a kept worktree) for whoever wants
    to look.
    """
    if not base_sha:
        return replace(outcome, reason="gate failed; commit kept: no base to reset to")
    undo = _git(cwd, "reset", "--soft", base_sha)
    if undo.returncode != 0:
        return replace(outcome, reason=f"gate failed; could not undo commit: {undo.stderr.strip()}")
    return CommitOutcome(
        attempted=True,
        committed=False,
        files=outcome.files,
        deletions=outcome.deletions,
        reason=f"gate failed; commit {outcome.sha[:8]} undone, work left staged in the tree",
    )


def discard(cwd: str, outcome: CommitOutcome, base_sha: str) -> CommitOutcome:
    """E16: like `uncommit`, but wipes the working tree back to `base_sha`
    too, instead of leaving the change staged.

    An adversarial lane that must not land has no kept-worktree story the
    way a fix's does (nothing here calls for a human to read the diff by
    hand); what does matter is that a later lane can still build cleanly on
    this one's unchanged base, which a soft reset's leftover dirty state
    would refuse (`LaneResult.buildable` requires `clean`). The reason is
    `commit_work`'s own "nothing to commit" -- once the tree is back at
    `base_sha` with nothing staged, that is exactly the state it describes,
    and `Result.failure()` already reads that reason as legitimately empty
    under `no_op_ok` instead of a distinct kind of failure.
    """
    if not base_sha:
        return replace(outcome, reason="commit kept: no base to reset to")
    undo = _git(cwd, "reset", "--hard", base_sha)
    if undo.returncode != 0:
        return replace(outcome, reason=f"could not undo commit: {undo.stderr.strip()}")
    cleaned = _git(cwd, "clean", "-fd")
    if cleaned.returncode != 0:
        return replace(outcome, reason=f"could not clean working tree: {cleaned.stderr.strip()}")
    return CommitOutcome(
        attempted=True,
        committed=False,
        files=outcome.files,
        deletions=outcome.deletions,
        reason="nothing to commit",
    )


@dataclass
class TestOutcome:
    ran: bool
    exit_code: int | None = None
    timed_out: bool = False
    interrupted: bool = False  # a stop request ended the gate
    tail: str = ""
    # F2: wall-clock seconds the gate process actually ran, from spawn to
    # exit; None when it never spawned (an OSError before Popen returned).
    duration_s: float | None = None

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.interrupted

    def to_dict(self) -> dict:
        return asdict(self)


GATE_POLL_S = 2


def run_tests(
    cwd: str,
    command: str,
    timeout: int = 900,
    *,
    stop: Callable[[], bool] | None = None,
    env: dict[str, str] | None = None,
) -> TestOutcome:
    """Run the caller's own gate. Never piped: a pipeline's exit code is the
    last stage's, so a piped gate reports the pager's success, not the suite's.

    The gate gets its own process group, exactly like a fleet: a suite that
    hangs and is killed must not leave xdist workers, a dev server, or a
    cargo test runner behind to keep editing the tree after the verdict.
    It is waited on in short polls so a stop request (`stop()` true) ends it
    the same way: a 15-minute suite must not outlive the operator's Ctrl-C.
    Output goes to a temporary file, not a pipe, so a chatty suite cannot
    block on a full pipe while conductor is not reading.

    `env` is the environment the command runs with; `None` (the default)
    inherits conductor's own, exactly as before this parameter existed.
    """
    gate_started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out:
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                shell=True,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            return TestOutcome(ran=True, tail=f"gate could not start: {exc}")
        # Import here to avoid runner -> verify -> runner at module load.
        from .runner import _kill_live_group, _register_live_group

        _register_live_group(proc.pid)
        try:
            deadline = time.monotonic() + timeout
            timed_out = interrupted = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    proc.wait(timeout=min(GATE_POLL_S, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
                if stop is not None and stop():
                    interrupted = True
                    break
        finally:
            # Every exit path kills stragglers and unregisters before pid reuse.
            _kill_live_group(proc.pid)
            proc.wait()
        gate_duration = round(time.monotonic() - gate_started, 1)
        if timed_out:
            return TestOutcome(
                ran=True,
                timed_out=True,
                tail=f"timed out after {timeout}s; process group killed",
                duration_s=gate_duration,
            )
        if interrupted:
            return TestOutcome(
                ran=True,
                interrupted=True,
                tail="interrupted: stop requested; process group killed",
                duration_s=gate_duration,
            )
        out.seek(0)
        combined = out.read().strip().splitlines()
    return TestOutcome(
        ran=True,
        exit_code=proc.returncode,
        tail="\n".join(combined[-15:]) if combined else "(no output)",
        duration_s=gate_duration,
    )
