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
    except (OSError, json.JSONDecodeError):
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
    created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
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
        if not mission_dir.is_dir() or (mission_dir / "result.json").is_file():
            continue
        lanes_dir = mission_dir / "lanes"
        for receipt in lanes_dir.glob("*.json") if lanes_dir.is_dir() else ():
            data = _json_object(receipt)
            attempts = data.get("attempts") if data is not None else None
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if isinstance(attempt, dict) and isinstance(attempt.get("run_id"), str):
                    protected.add(attempt["run_id"])
    return protected


def _run_in_progress(home: Path, run_id: str, protected_runs: set[str]) -> bool:
    """The cached mission set plus an adjacent stat of this one run."""
    run_id = _worktree_run_id(run_id)
    run_dir = home / "runs" / run_id
    return run_id in protected_runs or (
        run_dir.is_dir() and not (run_dir / "result.json").is_file()
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

        if _worktree_run_id(path.name) in protected_runs:
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
        top = git_run(candidate, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            notices.append(
                Item(str(candidate), "repo", "keep", "not a git repository", path=str(candidate))
            )
            continue
        repo = Path(top.stdout.strip()).resolve()
        if repo in seen:
            continue
        seen.add(repo)
        plans.append(
            _plan_repo(repo, (home / "worktrees").resolve(), older_than, now, protected_runs)
        )
    notices.extend(_audit_items(home))
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


def apply_plan(plans: list[RepoPlan], notices: list[Item], home: Path) -> bool:
    """Apply the planned order and read back every destructive action."""
    failed = False
    worktree_root = (home / "worktrees").resolve()
    # Mission receipts cannot begin referring to an existing run id after
    # this scan; each attempt claims a fresh run. Cache that expensive walk,
    # then stat the one run beside every remove/delete so new runs still win.
    protected_runs = _in_progress_run_ids(home)
    for plan in plans:
        failed = _apply_prunes(plan, worktree_root) or failed
        failed = _apply_removes(plan, worktree_root, home, protected_runs) or failed
        failed = _apply_branches(plan, home, protected_runs) or failed
        for item in plan.items:
            if item.action == "keep":
                item.done = True
    for item in notices:
        item.done = True
    return failed


def cmd_gc(args: argparse.Namespace) -> int:
    """Print a JSON-lines plan, applying it only after an explicit flag."""
    if args.older_than < 0:
        print("error: --older-than must be non-negative")
        return 2
    home = conductor_home()
    plans, notices = build_plan(home, args.repo, args.older_than)
    failed = apply_plan(plans, notices, home) if args.apply else False
    for item in [entry for plan in plans for entry in plan.items] + notices:
        print(json.dumps(item.to_dict(applied=args.apply), sort_keys=True))
    return 1 if failed else 0
