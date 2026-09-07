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
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import attest, golden
from .mission import LaneResult
from .runner import GATE_TIMEOUT
from .verify import git_run, run_tests


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


def _mission_test(mission_raw: dict) -> str | None:
    test = mission_raw.get("test")
    return test if isinstance(test, str) and test else None


def _golden_check(worktree: Path) -> list[str]:
    """The same replay `conductor golden check` runs against `tests/golden`
    at the repo root, but through golden's own Python API and against the
    merged worktree, never a subprocess of conductor calling itself."""
    golden_root = worktree / "tests" / "golden"
    if not golden_root.is_dir():
        return []
    diffs: list[str] = []
    for fixture_dir in sorted({p.parent for p in golden_root.glob("*/golden.json")}):
        try:
            lines = golden.check(fixture_dir)
        except (golden.GoldenError, OSError, ValueError) as exc:
            diffs.append(f"{fixture_dir.name}: {exc}")
            continue
        diffs.extend(f"{fixture_dir.name}: {line}" for line in lines)
    return diffs


def _gate_env(mission_id: str, lane: str, worktree: str) -> dict[str, str]:
    """The environment a lane's own gate gets (`runner.dispatch`): the
    process environment plus the two variables every dispatched process
    carries. Never `CONDUCTOR_LANE` -- this gate is land's own act, not a
    lane's, so nothing it runs may itself refuse to run `land`."""
    env = dict(os.environ)
    env["CONDUCTOR_RUN_ID"] = f"land-{mission_id}-{lane}"
    env["CONDUCTOR_WORKTREE"] = worktree
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
        detail = "verified" if attestation["verified"] else json.dumps(attestation)
        steps.append({"name": "attest", "ok": attestation["verified"], "detail": _tail(detail)})
        return attestation["verified"], steps, str(worktree)
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
) -> LandResult:
    message = f"Merge branch '{branch}' into {current_branch}"
    merged = git_run(root, "merge", "--no-ff", "-m", message, "--", branch)
    if merged.returncode != 0:
        detail = merged.stderr.strip() or merged.stdout.strip() or f"exit {merged.returncode}"
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            failed_step="merge",
            pre_merge_sha=pre_merge_head,
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
    branch_ref = git_run(lane_repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if branch_ref.returncode != 0:
        raise LandInvalid(f"branch '{branch}' does not exist in the lane's repository")

    if os.environ.get("CONDUCTOR_LANE"):
        raise LandInvalid(
            "land refuses to run inside a lane's environment: it is the lead's own act"
        )

    top = git_run(checkout, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise LandInvalid(f"checkout '{checkout}' is not a git repository")
    root = top.stdout.strip()

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
    branch_tip = git_run(root, "rev-parse", branch).stdout.strip()

    if git_run(root, "merge-base", "--is-ancestor", branch_tip, head).returncode == 0:
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            already_merged=True,
            ok=True,
        )
    if git_run(root, "merge-base", "--is-ancestor", head, branch_tip).returncode != 0:
        raise LandInvalid(f"branch '{branch}' is not a descendant of the checkout's HEAD")

    test_command = gate_command or _mission_test(mission_raw)
    if not test_command:
        raise LandInvalid("no gate command: pass --test, or the mission snapshot must set one")

    if dry_run:
        log = git_run(root, "log", "--oneline", f"HEAD..{branch}")
        would_merge = [ln for ln in log.stdout.splitlines() if ln.strip()]
        return LandResult(
            mission=mission_id,
            lane=lane,
            checkout=root,
            branch=branch,
            dry_run=True,
            ok=True,
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
