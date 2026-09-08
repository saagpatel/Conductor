"""Plan and apply cleanup without discarding a fleet's only copy of work.

Git's worktree and branch stores outlive conductor's processes.  This module
reconstructs their relationship from current Git state and the durable run
receipts, then deletes only objects whose contents are already recoverable.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .paths import conductor_home
from .verify import git_run

_RUN_STAMP = re.compile(r"^(\d{8}T\d{6}Z)")


@dataclass
class Item:
    """One independently reviewable decision in a cleanup plan."""

    repo: str
    kind: str
    action: str
    reason: str
    name: str = ""
    path: str = ""
    commits_ahead: int | None = None
    expected_tip: str = ""
    done: bool | None = None
    error: str = ""

    def to_dict(self, *, applied: bool) -> dict[str, str | int | bool]:
        row: dict[str, str | int | bool] = {
            "repo": self.repo,
            "kind": self.kind,
            "action": self.action,
            "reason": self.reason,
        }
        if self.name:
            row["name"] = self.name
        if self.path:
            row["path"] = self.path
        if self.commits_ahead is not None:
            row["commits_ahead"] = self.commits_ahead
        if applied:
            row["done"] = bool(self.done)
            row["error"] = self.error
        return row


@dataclass
class Worktree:
    """The porcelain fields gc needs; omitted fields cannot justify deletion."""

    path: Path
    branch: str = ""


@dataclass
class RepoPlan:
    """Items sharing one Git repository and therefore one prune operation."""

    repo: Path
    items: list[Item]


def _json_object(path: Path) -> dict[str, object] | None:
    try:
        raw: object = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        return None
    return raw


def _candidate_repos(home: Path, explicit: list[str]) -> list[Path]:
    candidates = [Path(value).expanduser().resolve() for value in explicit]
    runs = home / "runs"
    if runs.is_dir():
        for result_file in sorted(runs.glob("*/result.json")):
            data = _json_object(result_file)
            isolation = data.get("isolation") if data is not None else None
            if not isinstance(isolation, dict) or isolation.get("active") is not True:
                continue
            repo = isolation.get("repo")
            if isinstance(repo, str) and repo:
                candidates.append(Path(repo).expanduser().resolve())
    return list(dict.fromkeys(candidates))


def _parse_worktrees(repo: Path) -> tuple[list[Worktree], str]:
    listed = git_run(repo, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        return [], listed.stderr.strip() or listed.stdout.strip() or "git worktree list failed"
    worktrees: list[Worktree] = []
    for block in listed.stdout.strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if fields.get("worktree"):
            branch = fields.get("branch", "").removeprefix("refs/heads/")
            worktrees.append(Worktree(Path(fields["worktree"]), branch))
    return worktrees, ""


def _inside(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved != root and resolved.is_relative_to(root)


def _age_reason(run_id: str, hours: float, now: datetime) -> str | None:
    if hours == 0:
        return None
    match = _RUN_STAMP.match(run_id)
    if match is None:
        return "run_id has no timestamp"
    # `_RUN_STAMP` matches an impossible calendar (20260230, 20261399T256199).
    # `spend._run_time` already treats that as no stamp; raising here aborts
    # the whole plan, so one corrupt worktree or branch name skips cleanup.
    try:
        created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return "run_id timestamp is unparseable"
    if created >= now - timedelta(hours=hours):
        return "newer than --older-than"
    return None


def _branch_disposition(repo: Path, name: str, tip: str) -> tuple[str, str, int | None]:
    merged = git_run(repo, "merge-base", "--is-ancestor", tip, "HEAD")
    if merged.returncode == 0:
        return "delete-branch", "merged into HEAD", None
    if merged.returncode not in {0, 1}:
        return "keep", "could not determine reachability", None

    contains = git_run(repo, "branch", "--contains", tip, "--format=%(refname:short)")
    if contains.returncode != 0:
        return "keep", "could not determine reachability", None
    other = sorted(
        branch.strip()
        for branch in contains.stdout.splitlines()
        if branch.strip() and not branch.strip().startswith("conductor/")
    )
    if other:
        return "delete-branch", f"contained in {other[0]}", None

    ahead = git_run(repo, "rev-list", "--count", f"HEAD..{name}")
    if ahead.returncode != 0:
        return "keep", "could not determine reachability", None
    try:
        count = int(ahead.stdout.strip())
    except ValueError:
        return "keep", "could not determine reachability", None
    return "keep", "unmerged commits", count


def _unmerged_branch_reason(repo: Path, branch: str) -> str:
    """Why this worktree's branch is still the only copy of its work, or "".

    A worktree with no branch (detached HEAD) has nothing to protect here;
    neither does one whose branch `_branch_disposition` already judges
    reachable from HEAD or from another non-conductor branch.
    """
    if not branch:
        return ""
    tip = _current_tip(repo, branch)
    if not tip:
        return ""
    action, reason, _ahead = _branch_disposition(repo, branch, tip)
    return f"unmerged work on {branch} ({reason})" if action == "keep" else ""


def _mission_awaiting_resume(data: dict[str, object] | None) -> bool:
    """A parked or interrupted mission is still the salvage source for its runs.

    `run_mission` writes `result.json` unconditionally, including when it
    parks (`paused` without an `answer`) or writes an interrupted receipt.
    Presence of that file is not finishedness. An unreadable receipt is
    treated as still live: gc cannot prove the mission is done.
    """
    if data is None:
        return True
    paused = data.get("paused")
    if isinstance(paused, dict) and "answer" not in paused:
        return True
    return data.get("interrupted") is True


def _protect_lane_runs(mission_dir: Path, protected: set[str]) -> None:
    lanes_dir = mission_dir / "lanes"
    for receipt in lanes_dir.glob("*.json") if lanes_dir.is_dir() else ():
        data = _json_object(receipt)
        if data is None:
            continue
        # `previous_attempts` names the runs a retry superseded. Those
        # runs have their own result.json, so the `runs/` scan above does
        # not protect them, and until the mission itself finishes their
        # worktree can still be the only copy of what the superseded
        # attempt built (export.py reads both lists for the same reason).
        for key in ("previous_attempts", "attempts"):
            attempts = data.get(key)
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if isinstance(attempt, dict) and isinstance(attempt.get("run_id"), str):
                    protected.add(attempt["run_id"])


def _in_progress_run_ids(home: Path) -> set[str]:
    """Runs whose only worktree/branch copy may still be in use."""
    protected: set[str] = set()
    runs = home / "runs"
    if runs.is_dir():
        protected.update(
            directory.name
            for directory in runs.iterdir()
            if directory.is_dir() and not (directory / "result.json").is_file()
        )
    missions = home / "missions"
    if not missions.is_dir():
        return protected
    for mission_dir in missions.iterdir():
        if not mission_dir.is_dir():
            continue
        result_file = mission_dir / "result.json"
        if result_file.is_file() and not _mission_awaiting_resume(_json_object(result_file)):
            continue
        _protect_lane_runs(mission_dir, protected)
    return protected


def _run_in_progress(home: Path, run_id: str, protected_runs: set[str]) -> bool:
    """The cached mission set plus an adjacent stat of this one run."""
    candidates = (run_id, _worktree_run_id(run_id))
    return any(
        candidate in protected_runs
        or (
            (home / "runs" / candidate).is_dir()
            and not (home / "runs" / candidate / "result.json").is_file()
        )
        for candidate in dict.fromkeys(candidates)
    )


def _worktree_run_id(name: str) -> str:
    """A clean-gate scratch tree belongs to the dispatch before its suffix."""
    return name.removesuffix("-clean")


def _plan_repo(
    repo: Path,
    worktree_root: Path,
    older_than: float,
    now: datetime,
    protected_runs: set[str],
) -> RepoPlan:
    items: list[Item] = []
    worktrees, error = _parse_worktrees(repo)
    if error:
        items.append(
            Item(str(repo), "repo", "keep", f"cannot list worktrees: {error}", path=str(repo))
        )
        return RepoPlan(repo, items)

    kept_branches: dict[str, str] = {}
    for worktree in worktrees:
        path = worktree.path
        if not _inside(path, worktree_root):
            reason = "outside CONDUCTOR_HOME/worktrees"
            items.append(Item(str(repo), "worktree", "keep", reason, path=str(path)))
            if worktree.branch:
                kept_branches[worktree.branch] = reason
            continue

        if path.name in protected_runs or _worktree_run_id(path.name) in protected_runs:
            reason = "run in progress (no result.json)"
            items.append(Item(str(repo), "worktree", "keep", reason, path=str(path)))
            if worktree.branch:
                kept_branches[worktree.branch] = reason
            continue

        age_reason = _age_reason(path.name, older_than, now)
        if not path.exists():
            action = "keep" if age_reason else "prune"
            reason = age_reason or "worktree directory is gone"
            items.append(Item(str(repo), "worktree", action, reason, path=str(path)))
            if age_reason and worktree.branch:
                kept_branches[worktree.branch] = age_reason
            continue

        status = git_run(path, "status", "--porcelain")
        dirty = status.returncode != 0 or bool(status.stdout.strip())
        if dirty:
            item = Item(str(repo), "worktree", "keep", "uncommitted work", path=str(path))
            if worktree.branch:
                kept_branches[worktree.branch] = "uncommitted work"
        elif age_reason:
            item = Item(str(repo), "worktree", "keep", age_reason, path=str(path))
            if worktree.branch:
                kept_branches[worktree.branch] = age_reason
        else:
            # A clean worktree is not automatically a finished one. AGENTS.md
            # rule 6's salvage path reads a kept worktree from disk, and
            # `salvage.emit` refuses outright once it is gone ("kept worktree
            # is missing on disk"). Removing the directory leaves the branch,
            # but the branch is not what salvage reads. So a worktree whose
            # own branch still carries commits reachable from nowhere else is
            # the only copy of that work in the shape salvage needs, and is
            # kept until the work lands or the branch is merged away.
            unmerged = _unmerged_branch_reason(repo, worktree.branch)
            if unmerged:
                item = Item(str(repo), "worktree", "keep", unmerged, path=str(path))
                kept_branches[worktree.branch] = unmerged
            else:
                item = Item(str(repo), "worktree", "remove", "clean worktree", path=str(path))
        items.append(item)

    branches = git_run(
        repo,
        "for-each-ref",
        "--format=%(refname:short)\t%(objectname)",
        "refs/heads/conductor/",
    )
    if branches.returncode != 0:
        items.append(
            Item(
                str(repo),
                "repo",
                "keep",
                f"cannot list branches: {branches.stderr.strip() or branches.stdout.strip()}",
                path=str(repo),
            )
        )
        return RepoPlan(repo, items)

    for line in branches.stdout.splitlines():
        name, separator, tip = line.partition("\t")
        if not separator or not name.startswith("conductor/"):
            continue
        if name in kept_branches:
            action, reason, ahead = "keep", kept_branches[name], None
        elif name.removeprefix("conductor/") in protected_runs:
            action, reason, ahead = "keep", "run in progress (no result.json)", None
        else:
            age_reason = _age_reason(name.removeprefix("conductor/"), older_than, now)
            if age_reason:
                action, reason, ahead = "keep", age_reason, None
            else:
                action, reason, ahead = _branch_disposition(repo, name, tip)
        items.append(
            Item(
                str(repo),
                "branch",
                action,
                reason,
                name=name,
                commits_ahead=ahead,
                expected_tip=tip,
            )
        )
    return RepoPlan(repo, items)


def _read_port_claim(path: Path) -> tuple[str, str | None]:
    """Return the claimed run id, or ("", why) when gc cannot name a run.

    `UnicodeDecodeError` is a ValueError, not an OSError: garbage bytes in
    a claim file used to abort the whole plan. Empty is the create-then-write
    window in `ports.claim` (`O_CREAT | O_EXCL`, then the run id).
    """
    try:
        run_id = path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return "", "claim file unreadable"
    if not run_id:
        return "", "claim file empty"
    return run_id, None


def _unattributable_claim_reason(
    path: Path, hours: float, now: datetime, *, why: str
) -> str | None:
    """Keep a claim gc cannot attribute unless `--older-than` proves it dead.

    Deleting an empty or unreadable claim races a live `ports.claim`: the
    file exists before the run id is written. Keeping every such file
    forever leaks ports. The operator's `--older-than` is the lever that
    the claim's own mtime is old enough to be certainly dead. hours==0
    cannot prove that, so the claim is kept.
    """
    if hours == 0:
        return why
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return why
    if mtime >= now - timedelta(hours=hours):
        return "newer than --older-than"
    return None


def _port_items(
    home: Path, protected_runs: set[str], *, older_than: float = 0, now: datetime | None = None
) -> list[Item]:
    """One item per port claim file, live or stale.

    A claim file is created before the fleet spawns and removed when the
    dispatch ends (runner.dispatch's own cleanup); one still on disk means
    either the run is still going or it crashed hard enough to skip its own
    cleanup. Liveness is the same "no result.json yet" check that protects a
    run's worktree and branch, so a claim is never reclaimed while its run
    could still be using the port. A claim gc cannot attribute to a run is
    not a claim gc can prove is dead: it is kept unless `--older-than`
    says its mtime is old enough.
    """
    items: list[Item] = []
    ports_dir = home / "ports"
    if not ports_dir.is_dir():
        return items
    when = datetime.now(UTC) if now is None else now
    for claim_file in sorted(p for p in ports_dir.iterdir() if p.is_file()):
        run_id, unreadable = _read_port_claim(claim_file)
        if run_id and _run_in_progress(home, run_id, protected_runs):
            items.append(
                Item(
                    "",
                    "port",
                    "keep",
                    "run in progress (no result.json)",
                    name=run_id,
                    path=str(claim_file),
                )
            )
            continue
        if unreadable:
            keep = _unattributable_claim_reason(
                claim_file, older_than, when, why=unreadable
            )
            items.append(
                Item(
                    "",
                    "port",
                    "keep" if keep else "remove",
                    keep or unreadable,
                    name=run_id,
                    path=str(claim_file),
                )
            )
            continue
        items.append(
            Item(
                "",
                "port",
                "remove",
                "run completed",
                name=run_id,
                path=str(claim_file),
            )
        )
    return items


def _apply_port_remove(
    item: Item,
    home: Path,
    protected_runs: set[str],
    *,
    older_than: float = 0,
    now: datetime | None = None,
) -> bool:
    """Recheck liveness immediately before deleting, same as a worktree remove.

    Re-read the claim file rather than trusting `item.name`: an empty name
    used to skip this guard entirely, and a dispatch can write its run id
    into a file that was empty at plan time.
    """
    path = Path(item.path)
    when = datetime.now(UTC) if now is None else now
    if not path.exists():
        item.done = True
        return False
    run_id, unreadable = _read_port_claim(path)
    if run_id:
        item.name = run_id
        if _run_in_progress(home, run_id, protected_runs):
            item.action = "keep"
            item.reason = "run in progress (no result.json)"
            item.done = True
            return False
    elif unreadable:
        keep = _unattributable_claim_reason(path, older_than, when, why=unreadable)
        if keep:
            item.action = "keep"
            item.reason = keep
            item.done = True
            return False
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        item.done = False
        item.error = str(exc)
        return True
    item.done = not path.exists()
    if not item.done:
        item.error = "claim file remains after remove"
        return True
    return False


def _audit_items(home: Path) -> list[Item]:
    items: list[Item] = []
    for plural, kind in (("runs", "run"), ("missions", "mission")):
        parent = home / plural
        if not parent.is_dir():
            continue
        for directory in sorted(path for path in parent.iterdir() if path.is_dir()):
            if not (directory / "result.json").is_file():
                items.append(Item("", kind, "keep", "no result.json", path=str(directory)))
    return items


def _main_worktree(candidate: Path) -> Path | None:
    """The repository a candidate path belongs to, as its main worktree.

    A lane worktree is recorded as the `isolation.repo` of the runs that
    ran inside it, and `git rev-parse --show-toplevel` answers with the
    linked worktree itself, so one repository was planned once per lane
    worktree named in a receipt: every action duplicated, and the second
    pass of `--apply` failing on a worktree the first had already removed
    (2026-09-07, exit 1 on a clean run). The common dir is the same for
    every worktree of a repository; its parent is the main worktree.

    Two shapes reach here. A working repository's common dir is its `.git`,
    whose parent is the main worktree. A bare repository (and a repository
    made with `--separate-git-dir`) has a common dir under some other name,
    and it has no main worktree at all -- but the common dir is still the
    same absolute path for every worktree of that repository, so it is the
    dedup key, and `git worktree list` and `for-each-ref` both answer from
    it. Falling back to `--show-toplevel` instead answered with the linked
    worktree, which reintroduced the very duplication this function exists
    to prevent (2026-09-08).
    """
    common = git_run(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode != 0:
        return None
    git_dir = Path(common.stdout.strip()).resolve()
    if git_dir.name == ".git":
        return git_dir.parent
    return git_dir


def build_plan(
    home: Path, explicit_repos: list[str], older_than: float
) -> tuple[list[RepoPlan], list[Item]]:
    """Build a current plan without letting observation mutate Git state."""
    now = datetime.now(UTC)
    plans: list[RepoPlan] = []
    notices: list[Item] = []
    seen: set[Path] = set()
    protected_runs = _in_progress_run_ids(home)
    for candidate in _candidate_repos(home, explicit_repos):
        if not candidate.exists():
            notices.append(
                Item(
                    str(candidate),
                    "repo",
                    "keep",
                    "repo path does not exist",
                    path=str(candidate),
                )
            )
            continue
        repo = _main_worktree(candidate)
        if repo is None:
            notices.append(
                Item(str(candidate), "repo", "keep", "not a git repository", path=str(candidate))
            )
            continue
        if repo in seen:
            continue
        seen.add(repo)
        plans.append(
            _plan_repo(repo, (home / "worktrees").resolve(), older_than, now, protected_runs)
        )
    notices.extend(_audit_items(home))
    notices.extend(_port_items(home, protected_runs, older_than=older_than, now=now))
    return plans, notices


def _current_tip(repo: Path, name: str) -> str:
    result = git_run(repo, "rev-parse", "--verify", f"refs/heads/{name}^{{commit}}")
    return result.stdout.strip() if result.returncode == 0 else ""


def _apply_prunes(plan: RepoPlan, worktree_root: Path) -> bool:
    targets = [item for item in plan.items if item.action == "prune"]
    if not targets:
        return False
    worktrees, error = _parse_worktrees(plan.repo)
    if error:
        for item in targets:
            item.done = False
            item.error = error
        return True

    target_paths = {Path(item.path).resolve() for item in targets}
    missing = {worktree.path.resolve() for worktree in worktrees if not worktree.path.exists()}
    unsafe = [
        path for path in missing if path not in target_paths or not _inside(path, worktree_root)
    ]
    if unsafe:
        message = "git worktree prune would affect an out-of-scope registration"
        for item in targets:
            item.done = False
            item.error = message
        return True

    result = git_run(plan.repo, "worktree", "prune", "--expire", "now")
    remaining, read_error = _parse_worktrees(plan.repo)
    remaining_paths = {worktree.path.resolve() for worktree in remaining}
    for item in targets:
        gone = Path(item.path).resolve() not in remaining_paths
        item.done = result.returncode == 0 and not read_error and gone
        if not item.done:
            item.error = (
                result.stderr.strip()
                or result.stdout.strip()
                or read_error
                or "worktree registration remains after prune"
            )
    return any(item.done is not True for item in targets)


def _apply_removes(
    plan: RepoPlan, worktree_root: Path, home: Path, protected_runs: set[str]
) -> bool:
    failed = False
    for item in (entry for entry in plan.items if entry.action == "remove"):
        path = Path(item.path)
        if _run_in_progress(home, path.name, protected_runs):
            item.action = "keep"
            item.reason = "run in progress (no result.json)"
            item.done = True
            continue
        if not _inside(path, worktree_root):
            item.done = False
            item.error = "worktree is outside CONDUCTOR_HOME/worktrees"
            failed = True
            continue
        status = git_run(path, "status", "--porcelain")
        if status.returncode != 0 or status.stdout.strip():
            item.done = False
            item.error = "worktree is no longer clean"
            failed = True
            continue
        # Planning and the earlier checks can both go stale; this read is
        # deliberately adjacent to the destructive command it guards.
        if _run_in_progress(home, path.name, protected_runs):
            item.action = "keep"
            item.reason = "run in progress (no result.json)"
            item.done = True
            continue
        worktrees, error = _parse_worktrees(plan.repo)
        if error:
            item.done = False
            item.error = error
            failed = True
            continue
        branch = next(
            (
                worktree.branch
                for worktree in worktrees
                if worktree.path.resolve() == path.resolve()
            ),
            "",
        )
        unmerged = _unmerged_branch_reason(plan.repo, branch)
        if unmerged:
            item.action = "keep"
            item.reason = unmerged
            item.done = True
            continue
        removed = git_run(plan.repo, "worktree", "remove", str(path))
        remaining, error = _parse_worktrees(plan.repo)
        registered = any(worktree.path.resolve() == path.resolve() for worktree in remaining)
        item.done = removed.returncode == 0 and not error and not path.exists() and not registered
        if not item.done:
            item.error = (
                removed.stderr.strip()
                or removed.stdout.strip()
                or error
                or "worktree remains after remove"
            )
            failed = True
    return failed


def _apply_branches(plan: RepoPlan, home: Path, protected_runs: set[str]) -> bool:
    failed = False
    for item in (entry for entry in plan.items if entry.action == "delete-branch"):
        run_id = item.name.removeprefix("conductor/")
        if _run_in_progress(home, run_id, protected_runs):
            item.action = "keep"
            item.reason = "run in progress (no result.json)"
            item.done = True
            continue
        if not item.name.startswith("conductor/"):
            item.done = False
            item.error = "refusing a branch outside conductor/"
            failed = True
            continue
        tip = _current_tip(plan.repo, item.name)
        if not tip:
            item.done = True
            continue
        if tip != item.expected_tip:
            item.done = False
            item.error = "branch tip changed after planning"
            failed = True
            continue
        worktrees, error = _parse_worktrees(plan.repo)
        if error or any(worktree.branch == item.name for worktree in worktrees):
            item.done = False
            item.error = error or "branch is still checked out by a worktree"
            failed = True
            continue
        action, _, _ = _branch_disposition(plan.repo, item.name, tip)
        if action != "delete-branch":
            item.done = False
            item.error = "branch is no longer safely contained"
            failed = True
            continue
        # Reachability is not liveness. Re-read the run immediately before
        # deleting the ref so a newly started run wins the race with gc.
        if _run_in_progress(home, run_id, protected_runs):
            item.action = "keep"
            item.reason = "run in progress (no result.json)"
            item.done = True
            continue
        deleted = git_run(plan.repo, "branch", "-D", "--", item.name)
        item.done = deleted.returncode == 0 and not _current_tip(plan.repo, item.name)
        if not item.done:
            item.error = (
                deleted.stderr.strip() or deleted.stdout.strip() or "branch remains after delete"
            )
            failed = True
    return failed


def apply_plan(
    plans: list[RepoPlan], notices: list[Item], home: Path, *, older_than: float = 0
) -> bool:
    """Apply the planned order and read back every destructive action."""
    failed = False
    worktree_root = (home / "worktrees").resolve()
    # Mission receipts cannot begin referring to an existing run id after
    # this scan; each attempt claims a fresh run. Cache that expensive walk,
    # then stat the one run beside every remove/delete so new runs still win.
    protected_runs = _in_progress_run_ids(home)
    now = datetime.now(UTC)
    for plan in plans:
        failed = _apply_prunes(plan, worktree_root) or failed
        failed = _apply_removes(plan, worktree_root, home, protected_runs) or failed
        failed = _apply_branches(plan, home, protected_runs) or failed
        for item in plan.items:
            if item.action == "keep":
                item.done = True
    for item in notices:
        if item.kind == "port" and item.action == "remove":
            failed = (
                _apply_port_remove(item, home, protected_runs, older_than=older_than, now=now)
                or failed
            )
        else:
            item.done = True
    return failed


def cmd_gc(args: argparse.Namespace) -> int:
    """Print a JSON-lines plan, applying it only after an explicit flag."""
    if args.older_than < 0:
        print("error: --older-than must be non-negative")
        return 2
    home = conductor_home()
    plans, notices = build_plan(home, args.repo, args.older_than)
    failed = (
        apply_plan(plans, notices, home, older_than=args.older_than) if args.apply else False
    )
    for item in [entry for plan in plans for entry in plan.items] + notices:
        print(json.dumps(item.to_dict(applied=args.apply), sort_keys=True))
    return 1 if failed else 0
