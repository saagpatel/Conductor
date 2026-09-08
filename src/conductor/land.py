"""F7: `conductor land` -- the lead's hands, once the diff is read and the
reviewers have covered it.

AGENTS.md's release sequence has been the same by hand every time: merge the
fix lane's branch into the checkout's branch, gate the merged head in a
fresh worktree, run `golden check`, attest the mission, and only then bump
the version. This module is that sequence made repeatable and receipted. It
never dispatches a fleet and never runs inside a mission -- `runner.dispatch`
stamps `CONDUCTOR_LANE=1` on every process it spawns, fleet and gate alike,
and `land` refuses outright when that variable is present in its own
environment, so no fleet can ever reach it.

Every call, refused or not, is receipted under
`<home>/missions/<mission_id>/land/<lane>-<timestamp>.json`, on every path
that reaches the mission directory -- the same rule `salvage.py` follows: a
mission that does not exist has nowhere to hold a receipt, so that one
refusal alone writes nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import attest, golden
from .mission import LaneResult
from .runner import GATE_TIMEOUT
from .verify import git_run, run_tests, same_repo


class LandInvalid(ValueError):
    """A land request that cannot proceed as asked."""


@dataclass
class LandResult:
    mission: str
    lane: str
    checkout: str
    branch: str
    dry_run: bool = False
    already_merged: bool = False
    ok: bool = False
    failed_step: str = ""
    merge_sha: str = ""
    pre_merge_sha: str = ""
    # D6: the commit that was merged -- the lane receipt's own `tip_sha`,
    # re-resolved from `refs/heads/<branch>^{commit}` in the lane's
    # repository and refused when the two disagree. Every merge names this
    # sha, never the branch name (a tag of the same name shadows it).
    tip_sha: str = ""
    reset: bool = False
    # The scratch worktree the gate and `golden check` ran in, for the
    # merged head -- removed on every path by the time `land()` returns.
    worktree: str = ""
    steps: list[dict] = field(default_factory=list)
    would_merge: list[str] = field(default_factory=list)
    gate_command: str = ""
    # Set after the receipt is written; not itself part of the receipt (the
    # timestamp in its name is not known before the write happens).
    receipt_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _write_receipt(home: Path, mission_id: str, lane: str, payload: dict) -> Path:
    land_dir = home / "missions" / mission_id / "land"
    land_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision, not `claim_dir`'s second-granularity stamp: a
    # dry run followed by a real land within one second must not silently
    # overwrite each other's receipt.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = land_dir / f"{lane}-{stamp}.json"
    doc = {
        **payload,
        "mission": mission_id,
        "lane": lane,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(doc, indent=2))
    return path


def _load_json(path: Path, *, what: str) -> dict:
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise LandInvalid(f"{what} is unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LandInvalid(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise LandInvalid(f"{what} must be a JSON object")
    return raw


def _ungated_merge(home: Path, mission_id: str, lane: str, root: str) -> str | None:
    """The merge sha of an earlier `land` of this lane that failed a check
    and could not put the checkout back, when that merge is still reachable
    from HEAD. None otherwise.

    The already-merged shortcut below answers `ok=True` for any tip that is
    an ancestor of HEAD, which is right for a landing that passed and wrong
    for one that did not: `_perform` resets to the pre-merge head only when
    HEAD is still exactly the merge commit and the tree is clean, so a
    failed gate over a dirty submodule checkout, or a `reset --hard` that
    itself failed, leaves the ungated merge in the branch. A second `land`
    then reported success over it without running gate, golden, or attest
    (2026-09-08 review). Read from this lane's own land receipts, which
    already record `merge_sha` and whether the reset happened.
    """
    land_dir = home / "missions" / mission_id / "land"
    if not land_dir.is_dir():
        return None
    for path in sorted(land_dir.glob(f"{lane}-*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or raw.get("ok") is not False:
            continue
        merge_sha = raw.get("merge_sha")
        if not isinstance(merge_sha, str) or not merge_sha or raw.get("reset") is True:
            continue
        if git_run(root, "merge-base", "--is-ancestor", merge_sha, "HEAD").returncode == 0:
            return merge_sha
    return None


def _mission_test(mission_raw: dict) -> str | None:
    test = mission_raw.get("test")
    return test if isinstance(test, str) and test else None


# D19: the child that runs `golden check` for the merged worktree. It asserts
# where `conductor` came from before it runs anything -- the whole point of
# the subprocess is that the merged tree's own implementation replays the
# merged tree's own fixtures, and an import that resolved to the running
# `land`'s copy (a stale PYTHONPATH, an installed package ahead of it on
# sys.path) would be exactly the silent staleness this replaces. Exit 3 is
# that refusal; 0 and 1 are `cmd_golden_check`'s own clean and diff answers.
_GOLDEN_CHILD = """\
import sys
from pathlib import Path

import conductor

root = Path(conductor.__file__).resolve().parent
worktree = Path(sys.argv[1]).resolve()
if not root.is_relative_to(worktree):
    sys.stderr.write("golden check imported conductor from %s, not from %s\\n" % (root, worktree))
    raise SystemExit(3)

from conductor.cli import main

raise SystemExit(main(["golden", "check", *sys.argv[2:]]))
"""

GOLDEN_TIMEOUT = GATE_TIMEOUT


def _golden_check(worktree: Path, *, timeout: int = GOLDEN_TIMEOUT) -> list[str]:
    """`conductor golden check` against the merged worktree's `tests/golden`.

    D19: this used to call `golden.check` in this process, which meant the
    merged tree's new fixtures were replayed by the *old* golden, mission,
    and parser code -- precisely backwards when what is landing is a change
    to those modules. The check now runs as a subprocess with the merged
    worktree's `src` as its import root, and the child refuses (exit 3) if
    `conductor` did not in fact resolve there.

    A merged tree that is not conductor's own source (no `src/conductor`) has
    no implementation to be stale about, so it keeps the in-process replay.
    """
    golden_root = worktree / "tests" / "golden"
    if not golden_root.is_dir():
        return []
    src = worktree / "src"
    if not (src / "conductor" / "golden.py").is_file():
        diffs: list[str] = []
        for fixture_dir in sorted({p.parent for p in golden_root.glob("*/golden.json")}):
            try:
                lines = golden.check(fixture_dir)
            except (golden.GoldenError, OSError, ValueError) as exc:
                diffs.append(f"{fixture_dir.name}: {exc}")
                continue
            diffs.extend(f"{fixture_dir.name}: {line}" for line in lines)
        return diffs

    env = dict(os.environ)
    # The merged worktree's source ahead of anything else the caller carried:
    # the child's own import-root assertion is what makes that a fact rather
    # than a hope.
    env["PYTHONPATH"] = str(src)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # `golden check` with no directories reads `tests/golden` relative to its
    # own cwd, and replay itself never dispatches a fleet or runs a notify
    # command -- `golden.replay` passes its own dispatcher and notifier -- so
    # the child has no live effect to guard against beyond where it imports
    # from.
    try:
        done = subprocess.run(
            [sys.executable, "-c", _GOLDEN_CHILD, str(worktree)],
            cwd=str(worktree),
            env=env,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"golden check did not run: {exc}"]
    if done.returncode == 0:
        # Clean. `cmd_golden_check` still prints E22's version-drift notes on
        # this path, and those were never check failures.
        return []
    output = "\n".join(
        line for line in (done.stdout + done.stderr).splitlines() if line.strip()
    )
    return output.splitlines() or [f"golden check exited {done.returncode} with no output"]


def _gate_env(mission_id: str, lane: str, worktree: str) -> dict[str, str]:
    """The exact environment a lane's own gate gets (`runner.dispatch`,
    runner.py:1615-1622): the process environment plus the three variables
    every dispatched process carries, `CONDUCTOR_LANE` included -- this gate
    re-runs the lane's own command against the merged head, and spec item 2
    asks for the same environment, not a land-specific one."""
    env = dict(os.environ)
    env["CONDUCTOR_RUN_ID"] = f"land-{mission_id}-{lane}"
    env["CONDUCTOR_WORKTREE"] = worktree
    env["CONDUCTOR_LANE"] = "1"
    return env


def _run_checks(
    home: Path, mission_id: str, lane: str, root: str, merge_sha: str, test_command: str
) -> tuple[bool, list[dict], str]:
    """The gate, then `golden check`, then the mission's attestation, all
    against the merged head -- in a worktree that is removed on every path,
    never the caller's own checkout."""
    steps: list[dict] = []
    parent = Path(tempfile.mkdtemp(prefix="conductor-land-"))
    worktree = parent / "worktree"
    added = git_run(root, "worktree", "add", "--detach", str(worktree), merge_sha, timeout=60)
    if added.returncode != 0:
        detail = added.stderr.strip() or added.stdout.strip() or f"exit {added.returncode}"
        steps.append({"name": "worktree", "ok": False, "detail": _tail(detail)})
        shutil.rmtree(parent, ignore_errors=True)
        return False, steps, str(worktree)
    try:
        gate = run_tests(
            str(worktree),
            test_command,
            timeout=GATE_TIMEOUT,
            stop=lambda: False,
            env=_gate_env(mission_id, lane, str(worktree)),
        )
        steps.append({"name": "gate", "ok": gate.passed, "detail": _tail(gate.tail)})
        if not gate.passed:
            return False, steps, str(worktree)

        golden_diffs = _golden_check(worktree)
        steps.append(
            {
                "name": "golden",
                "ok": not golden_diffs,
                "detail": _tail("\n".join(golden_diffs)),
            }
        )
        if golden_diffs:
            return False, steps, str(worktree)

        try:
            attestation = attest.attest_mission(home, mission_id)
        except attest.AttestInvalid as exc:
            steps.append({"name": "attest", "ok": False, "detail": str(exc)})
            return False, steps, str(worktree)
        # D7: only a chain that is complete as well as unbroken may land.
        # `state` is the finer verdict -- `partial` (a valid prefix of the
        # mission's own recorded chain) and `empty` are refusals here, not
        # green steps.
        chain_ok = attestation.get("state") == "verified"
        detail = "verified" if chain_ok else json.dumps(attestation)
        steps.append({"name": "attest", "ok": chain_ok, "detail": _tail(detail)})
        return chain_ok, steps, str(worktree)
    finally:
        git_run(root, "worktree", "remove", "--force", str(worktree), timeout=60)
        shutil.rmtree(parent, ignore_errors=True)


def _perform(
    home: Path,
    mission_id: str,
    lane: str,
    *,
    root: str,
    branch: str,
    current_branch: str,
    pre_merge_head: str,
    test_command: str,
    tip_sha: str = "",
) -> LandResult:
    # D6: the merge names the pinned commit, never the branch name. The
    # message still names the branch (that is what the log is read for), but
    # what git resolves is the sha the lane's receipt was verified against,
    # so a tag of the same name, or a branch that moved between the check and
    # the merge, cannot change what lands.
    message = f"Merge branch '{branch}' into {current_branch}"
    merged = git_run(root, "merge", "--no-ff", "-m", message, "--", tip_sha or branch)
    if merged.returncode != 0:
        detail = merged.stderr.strip() or merged.stdout.strip() or f"exit {merged.returncode}"
        # No merge commit exists yet -- `git merge --abort` is exactly
        # enough here (unlike the post-commit case below, where only a
        # reset can undo it): a rejecting commit-msg hook, for one, leaves
        # MERGE_HEAD set and the tree staged even though the command itself
        # failed, and the next `land` must not find the checkout mid-merge.
        #
        # The abort's own exit code is read: an abort that itself fails (an
        # index.lock, a timeout, `git_run`'s own GIT_UNRUN) leaves MERGE_HEAD
        # set, and every later `land` then refuses with "checkout is
        # mid-merge" naming nothing about why. Saying it in this receipt is
        # what turns that into something the operator can act on.
        aborted = git_run(root, "merge", "--abort")
        if (
            aborted.returncode != 0
            and git_run(root, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0
        ):
            abort_detail = (
                aborted.stderr.strip() or aborted.stdout.strip() or f"exit {aborted.returncode}"
            )
            detail = (
                f"{detail}\ngit merge --abort also failed ({_tail(abort_detail, 3)}); "
                "the checkout is still mid-merge and must be aborted by hand"
            )
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            failed_step="merge",
            pre_merge_sha=pre_merge_head,
            tip_sha=tip_sha,
            gate_command=test_command,
            steps=[{"name": "merge", "ok": False, "detail": _tail(detail)}],
        )
    merge_sha = git_run(root, "rev-parse", "HEAD").stdout.strip()
    steps = [{"name": "merge", "ok": True, "detail": merge_sha}]

    ok, check_steps, worktree = _run_checks(home, mission_id, lane, root, merge_sha, test_command)
    steps.extend(check_steps)
    if ok:
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            ok=True,
            merge_sha=merge_sha,
            pre_merge_sha=pre_merge_head,
            tip_sha=tip_sha,
            worktree=worktree,
            gate_command=test_command,
            steps=steps,
        )

    failed_step = next((step["name"] for step in steps if not step["ok"]), "unknown")
    current_head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    status = git_run(root, "status", "--porcelain")
    reset_done = False
    if current_head == merge_sha and status.returncode == 0 and not status.stdout.strip():
        reset = git_run(root, "reset", "--hard", pre_merge_head)
        reset_done = reset.returncode == 0
    return LandResult(
        mission=mission_id,
        lane=lane,
        checkout=root,
        branch=branch,
        failed_step=failed_step,
        merge_sha=merge_sha,
        pre_merge_sha=pre_merge_head,
        tip_sha=tip_sha,
        reset=reset_done,
        worktree=worktree,
        gate_command=test_command,
        steps=steps,
    )


def _land(
    home: Path,
    mission_id: str,
    lane: str,
    *,
    checkout: str,
    gate_command: str | None,
    dry_run: bool,
) -> LandResult:
    mission_dir = home / "missions" / mission_id
    if not mission_dir.is_dir():
        raise LandInvalid(f"mission '{mission_id}' does not exist")
    lane_path = mission_dir / "lanes" / f"{lane}.json"
    if not lane_path.is_file():
        raise LandInvalid(f"lane '{lane}' does not exist in mission '{mission_id}'")
    lane_raw = _load_json(lane_path, what=f"lane '{lane}' receipt")
    try:
        lane_result = LaneResult.from_dict(lane_raw)
    except ValueError as exc:
        raise LandInvalid(f"lane '{lane}' receipt is invalid: {exc}") from exc
    branch = lane_result.branch
    if not branch:
        raise LandInvalid(f"lane '{lane}' has no branch on its receipt")

    mission_raw = _load_json(mission_dir / "mission.json", what=f"mission '{mission_id}' snapshot")
    lane_repo = lane_result.cwd or mission_raw.get("cwd")
    if not isinstance(lane_repo, str) or not lane_repo:
        raise LandInvalid(f"lane '{lane}' has no repository to land from")
    # D6: resolve the branch to a commit once, in the lane's own repository,
    # and pin it to the sha the lane receipted. `mission._trusted_lane` makes
    # exactly this comparison before it trusts a receipt; land merges what
    # the receipt says was built, or it merges nothing. `^{commit}` is what
    # keeps a tag of the same name from answering in the branch's place.
    branch_ref = git_run(
        lane_repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"
    )
    if branch_ref.returncode != 0:
        raise LandInvalid(f"branch '{branch}' does not exist in the lane's repository")
    tip_sha = branch_ref.stdout.strip()
    if not lane_result.tip_sha:
        raise LandInvalid(f"lane '{lane}' has no tip commit on its receipt")
    if tip_sha != lane_result.tip_sha:
        raise LandInvalid(
            f"branch '{branch}' is at {tip_sha}, but lane '{lane}' receipted "
            f"{lane_result.tip_sha}: the branch moved since the run"
        )

    if os.environ.get("CONDUCTOR_LANE"):
        raise LandInvalid(
            "land refuses to run inside a lane's environment: it is the lead's own act"
        )

    top = git_run(checkout, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise LandInvalid(f"checkout '{checkout}' is not a git repository")
    root = top.stdout.strip()
    # D6: the destination must be the lane's own repository (or a worktree of
    # it). Otherwise the pinned sha is a stranger's commit at best, and a
    # same-named branch in an unrelated repository is what would have landed.
    if not same_repo(root, lane_repo):
        raise LandInvalid(
            f"checkout '{root}' is not the same repository as the lane's, '{lane_repo}'"
        )

    on_branch = git_run(root, "symbolic-ref", "--quiet", "HEAD")
    if on_branch.returncode != 0:
        raise LandInvalid("checkout is not on a branch")
    current_branch = git_run(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if current_branch == branch:
        raise LandInvalid(f"checkout's current branch is '{branch}' itself")

    status = git_run(root, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        raise LandInvalid("checkout has uncommitted changes")

    if git_run(root, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode == 0:
        raise LandInvalid("checkout is mid-merge")

    head = git_run(root, "rev-parse", "HEAD").stdout.strip()
    # D6: the pinned sha from the lane's repository, not a second, unqualified
    # `rev-parse <branch>` in the destination -- the identity checks above are
    # what this ref stands on, and the already-merged shortcut below answers
    # about that same commit.
    branch_tip = tip_sha

    if git_run(root, "merge-base", "--is-ancestor", branch_tip, head).returncode == 0:
        ungated = _ungated_merge(home, mission_id, lane, root)
        if ungated is not None:
            raise LandInvalid(
                f"an earlier land of lane '{lane}' failed its checks and left merge "
                f"{ungated[:12]} in the checkout: this branch holds an ungated merge, "
                "so undo it by hand before landing again"
            )
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            already_merged=True,
            # The flag the caller passed, recorded as on every other return:
            # a `--dry-run --json` on a landed lane used to read `dry_run:
            # false` (third drill pass, 2026-09-07).
            dry_run=dry_run,
            ok=True,
            tip_sha=tip_sha,
        )
    # A lane's branch almost never descends from the checkout's HEAD: every
    # Phase E and F item was launched on one tip and merged onto a later one
    # (`--no-ff` on a diverged branch is the whole point). The first live
    # `land` refused F12 for exactly that, so the check is shared history,
    # not descent: a branch with no merge base was born elsewhere.
    if git_run(root, "merge-base", head, branch_tip).returncode != 0:
        raise LandInvalid(f"branch '{branch}' shares no history with the checkout's HEAD")

    test_command = gate_command or _mission_test(mission_raw)
    if not test_command:
        raise LandInvalid("no gate command: pass --test, or the mission snapshot must set one")

    if dry_run:
        log = git_run(root, "log", "--oneline", f"{head}..{branch_tip}")
        would_merge = [ln for ln in log.stdout.splitlines() if ln.strip()]
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            dry_run=True,
            ok=True,
            tip_sha=tip_sha,
            would_merge=would_merge,
            gate_command=test_command,
        )

    return _perform(
        home,
        mission_id,
        lane,
        root=root,
        branch=branch,
        current_branch=current_branch,
        pre_merge_head=head,
        test_command=test_command,
        tip_sha=tip_sha,
    )


def land(
    mission_id: str,
    lane: str,
    *,
    home: Path,
    checkout: str,
    gate_command: str | None = None,
    dry_run: bool = False,
) -> LandResult:
    """Merge a lane's branch into `checkout`, gate the merged head in a
    fresh worktree, run `golden check`, and attest the mission -- the lead's
    hands after the diff is read and the reviewers have covered it. Never
    dispatches a fleet, never bumps a version, never pushes, and never runs
    inside a mission (see the module docstring on `CONDUCTOR_LANE`).

    Refuses with `LandInvalid` (nothing changed) for every check the module
    docstring and F7's spec describe; a red step past the merge instead
    resets the checkout to its pre-merge HEAD when that is safe and returns
    an `ok=False` result naming the failing step.
    """
    home = Path(home)
    try:
        result = _land(
            home, mission_id, lane, checkout=checkout, gate_command=gate_command, dry_run=dry_run
        )
    except LandInvalid as exc:
        if (home / "missions" / mission_id).is_dir():
            _write_receipt(home, mission_id, lane, {"refused": str(exc)})
        raise
    path = _write_receipt(home, mission_id, lane, result.to_dict())
    result.receipt_path = str(path)
    return result
