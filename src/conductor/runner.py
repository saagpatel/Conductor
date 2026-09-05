"""Spawn one fleet, survive it, and report a compact result.

Three properties matter more than anything else here, and each exists because
the obvious implementation fails in an unattended run:

  * stdout and stderr stream to files, never through the orchestrator.
    A verbose agent's transcript is tens of thousands of tokens; the caller
    gets a path and a tail, and reads more only if it decides to.
  * the child runs in its own process group, so a timeout kills the whole
    tree. An orphaned grandchild still editing the repo will race whatever
    runs next.
  * stdin is closed. Several of these CLIs block forever on a non-TTY stdin
    with no writer, which presents as a hang with no output at all.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import attest, prices, worktrees
from .breakers import Breaker
from .budget import POLL_S, Budget, Watcher
from .fleets import FLEETS, Spec, build_argv
from .outputs import FleetOutput
from .outputs import parse as parse_output
from .paths import conductor_home
from .surface import Surface, missing_surface, test_surface
from .verdicts import checklist_contract, checklist_schema, parse_verdict
from .verify import (
    CommitOutcome,
    GitState,
    TestOutcome,
    commit_work,
    compare,
    diff_since,
    git_run,
    killpg,
    run_tests,
    uncommit,
)
from .verify import (
    Verdict as GitVerdict,
)

TAIL_LINES = 20
GATE_TIMEOUT = 900


def _slug(text: str, limit: int = 32, default: str = "run") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit].rstrip("-")) or default


@dataclass
class Result:
    """One dispatch, as the orchestrator should see it: small, and honest
    about whether anything happened."""

    run_id: str
    fleet: str
    model: str
    effort: str
    mode: str
    cwd: str
    timeout: int
    exit_code: int | None
    timed_out: bool
    duration_s: float
    run_dir: str
    stdout_path: str
    stderr_path: str
    tail: str
    spawned: bool = False  # True only after Popen returned a live process group.
    git_verdict: dict = field(default_factory=dict)
    verdict: dict | None = None
    tests: dict | None = None
    test_surface: dict | None = None
    reproduce: dict | None = None
    commit: dict | None = None
    usage: dict | None = None
    budget: dict | None = None
    breaker: dict | None = None
    answer_path: str | None = None
    diff_path: str | None = None
    attestation_path: str | None = None
    # The same bounds the signed attestation carries, recorded here too so a
    # verifier can compare against this receipt directly instead of
    # re-deriving them from `isolation`/`commit`, which is only correct for
    # an isolated or landed dispatch (A5 review: a non-isolated read lane's
    # real HEAD was read back as a mismatch otherwise).
    base_commit: str | None = None
    tip_commit: str | None = None
    isolation: dict | None = None
    fleet_status: str | None = None
    fleet_error: str | None = None
    session_id: str | None = None
    resumed: dict | None = None
    error: str | None = None
    dry_run: bool = False
    no_op_ok: bool = False  # a write that may legitimately change nothing
    interrupted: bool = False  # a stop request ended the run
    cancelled: bool = False  # another lane already passed; this one was cut

    @property
    def gate_passed(self) -> bool:
        """Whether the lane gate or its clean replacement supplied the verdict."""
        return _gate_passed(self.tests, self.test_surface)

    def failure(self) -> str | None:
        """Why the run is not ok, in one line, or None when it is.

        Success means the process succeeded AND bytes moved (when the target
        was a repo and the mode was write) AND the gate passed AND any
        requested commit landed. Exit 0 alone is not it.
        """
        if self.dry_run:
            return None
        if self.error:
            return self.error
        if self.budget and self.budget.get("exceeded"):
            return _over_budget(self.budget)
        if self.budget and self.budget.get("unpriced"):
            return "cap unenforced: the run came back unpriced"
        if self.timed_out:
            return f"timed out after {self.timeout}s"
        if self.exit_code != 0:
            return f"exit code {self.exit_code}"
        # A fleet that says it failed is believed, whatever its exit code.
        if self.fleet_error:
            return f"fleet reported: {self.fleet_error}"
        # A gate that ran and did not exit 0 sinks the run; that includes a
        # gate that hung, which has no exit code at all.
        clean = (self.test_surface or {}).get("clean_gate") or {}
        counted = clean if clean.get("ran") else self.tests
        label = "clean gate" if clean.get("ran") else "gate"
        if counted and counted.get("ran") and not self.gate_passed:
            if counted.get("interrupted"):
                return f"{label} interrupted"
            if counted.get("timed_out"):
                return f"{label} timed out"
            return f"{label} exited {counted.get('exit_code')}"
        no_op = self.git_verdict.get("checked") and self.git_verdict.get("no_op")
        if self.mode == "write" and no_op and not self.no_op_ok:
            return "write dispatch moved no bytes"
        # The mirror image: a research dispatch that edited the tree ignored
        # its read-only setting (agy's read mode did exactly that live,
        # 2026-09-03), and in a shared checkout that is the collision
        # isolation exists to prevent. The bytes are the evidence.
        if (
            self.mode == "read"
            and self.git_verdict.get("checked")
            and not self.git_verdict.get("no_op")
        ):
            return "read dispatch moved bytes"
        # A read dispatch's answer IS its work. Exit 0 with nothing said is
        # the read-mode twin of the exit-0 no-op: a cursor lane once spent
        # 15K output tokens and handed back nothing usable, and read as ok.
        if self.mode == "read" and self.exit_code is not None and not self.answer_path:
            return "read dispatch returned no answer"
        # A requested commit that did not happen is a failure even when the
        # dispatch itself went fine: the caller asked for landed work.
        if self.commit and not self.commit.get("committed"):
            nothing = self.commit.get("reason") == "nothing to commit"
            if not (self.no_op_ok and nothing):
                return f"commit did not land: {self.commit.get('reason') or 'unknown'}"
        return None

    @property
    def ok(self) -> bool:
        return self.failure() is None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d

    def summary(self) -> dict:
        """The few lines an orchestrator actually needs to decide what next."""
        iso = self.isolation or {}
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "fleet": self.fleet,
            "model": self.model,
            "effort": self.effort,
            "mode": self.mode,
            "spawned": self.spawned,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 1),
            "commits": self.git_verdict.get("commits_added", 0),
            "files_changed": self.git_verdict.get("files_changed", 0),
            "dirty_delta": self.git_verdict.get("dirty_delta", 0),
            "no_op": self.git_verdict.get("no_op", False),
            "verdict": (
                "invalid"
                if self.verdict and self.verdict.get("invalid")
                else ("pass" if self.verdict and self.verdict.get("passed") else "fail")
                if self.verdict
                else None
            ),
            "tests": (self.tests or {}).get("exit_code"),
            "test_touched": bool((self.test_surface or {}).get("touched")),
            "committed": (self.commit or {}).get("sha", "")[:8] or None,
            "branch": iso.get("branch") or None,
            "worktree": iso.get("worktree") if iso.get("kept") else None,
            "tip": iso.get("tip_sha") or None,
            "clean": iso.get("clean"),
            "cost_usd": (self.usage or {}).get("cost_usd"),
            "cost_basis": (self.usage or {}).get("cost_basis"),
            "tokens": (self.usage or {}).get("total_tokens"),
            "input_tokens": (self.usage or {}).get("input_tokens"),
            "cache_read_tokens": (self.usage or {}).get("cache_read_tokens"),
            "cap_usd": (self.budget or {}).get("cap_usd"),
            "over_cap": bool((self.budget or {}).get("exceeded")),
            "tool_calls": (self.breaker or {}).get("tool_calls", 0),
            "breaker": (self.breaker or {}).get("tripped"),
            "answer_path": self.answer_path,
            "diff_path": self.diff_path,
            "attestation_path": self.attestation_path,
            "run_dir": self.run_dir,
            "session_id": self.session_id,
            "resumed": self.resumed,
            "error": self.error or self.fleet_error,
            "failure": self.failure(),
            "cancelled": self.cancelled,
        }


def _over_budget(budget: dict) -> str:
    seen = budget.get("observed_usd")
    spent = f"${seen:.4f}" if seen is not None else "an unpriced spend"
    return f"over budget: {spent} against a ${budget['cap_usd']:.4f} cap"


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _tail(path: Path, lines: int = TAIL_LINES) -> str:
    try:
        content = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _surface_result(policy: str, before: Surface, after: Surface) -> dict:
    changed = before.diff(after)
    return {
        "policy": policy,
        "patterns": before.patterns,
        "digest_before": before.digest,
        "digest_after": after.digest,
        "touched": bool(changed),
        "changed": changed,
    }


def _gate_passed(tests: dict | None, surface: dict | None) -> bool:
    clean = (surface or {}).get("clean_gate") or {}
    counted = clean if clean.get("ran") else tests
    if not counted or not counted.get("ran"):
        return True
    return (
        counted.get("exit_code") == 0
        and not counted.get("timed_out")
        and not counted.get("interrupted")
    )


def _gate_summary(
    tests_dict: dict | None, surface_state: dict | None, test_command: str | None
) -> dict:
    """Which gate run counted for this dispatch, and its verdict, in the
    shape the signed receipt carries (A5's `gate` block)."""
    clean = (surface_state or {}).get("clean_gate") or {}
    if clean.get("ran"):
        counted, label = clean, "clean"
    elif tests_dict and tests_dict.get("ran"):
        counted, label = tests_dict, "own"
    else:
        counted, label = None, "none"
    return {
        "command": test_command,
        "counted": label,
        "exit_code": counted.get("exit_code") if counted else None,
        "passed": _gate_passed(tests_dict, surface_state),
    }


def _commit_bounds(
    before: GitState,
    after: GitState,
    commit: CommitOutcome | None,
    iso: worktrees.Isolation | None,
) -> tuple[str | None, str | None]:
    """The base and tip commit a dispatch actually ran between, on whichever
    evidence exists: a landed commit's sha, else the isolated worktree's
    tip, else the cwd's own HEAD after the run, so a fleet that deletes its
    working tree still yields a receipt (both fields null). This is the one
    place that derives them, so the signed statement and the plain receipt
    (`Result.base_commit`/`tip_commit`) can never disagree with each other."""
    tip_commit = None
    if commit is not None and commit.committed:
        tip_commit = commit.sha or None
    elif iso is not None and iso.tip_sha:
        tip_commit = iso.tip_sha
    elif after.head:
        tip_commit = after.head
    return before.head or None, tip_commit


def _lane_receipt_statement(
    *,
    run_id: str,
    fleet: str,
    model: str,
    mode: str,
    stage: str | None,
    cwd: str,
    base_commit: str | None,
    tip_commit: str | None,
    diff_path: str | None,
    surface_state: dict | None,
    tests_dict: dict | None,
    test_command: str | None,
    reproduce_state: dict | None,
    ok: bool,
    error: str | None,
) -> dict:
    """The statement A5 signs into `attestation.json`: what conductor can
    check about this one dispatch without trusting the fleet's own report."""
    test_surface = (
        {
            "digest_before": surface_state["digest_before"],
            "digest_after": surface_state["digest_after"],
            "touched": surface_state["touched"],
        }
        if surface_state is not None
        else None
    )
    return {
        "_type": "conductor/lane-receipt/v1",
        "run_id": run_id,
        "fleet": fleet,
        "model": model,
        "mode": mode,
        "stage": stage,
        "cwd": cwd,
        "base_commit": base_commit,
        "tip_commit": tip_commit,
        "source_diff_sha256": attest.file_sha256(diff_path) if diff_path else None,
        "test_surface": test_surface,
        "gate": _gate_summary(tests_dict, surface_state, test_command),
        "reproduce_verdict": (reproduce_state or {}).get("verdict"),
        "ok": ok,
        "error": error,
        "ended_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def _git_failure(detail: str, *, worktree: Path, patch_bytes: int = 0) -> dict:
    # `infra_error` marks a failure in the transplant machinery itself (worktree
    # add, read-tree, apply, ...) rather than in the gate command it was meant
    # to run. A caller that treats "the gate command exited nonzero" as meaningful
    # evidence (the reproduce gate, in particular: exit nonzero there normally
    # means the check reproduces the bug) must not read this the same way.
    outcome = TestOutcome(ran=True, exit_code=1, tail=detail).to_dict()
    outcome.update(worktree=str(worktree), patch_bytes=patch_bytes, infra_error=True)
    return outcome


def _clean_gate(
    cwd: str,
    *,
    base_sha: str,
    patterns: list[str],
    command: str,
    timeout: int,
    stop: Callable[[], bool],
    worktree: Path,
) -> dict:
    """Run the gate at the base commit with only non-test changes transplanted."""
    exclusions = [f":(exclude,glob){pattern}" for pattern in patterns]
    return _transplant_gate(
        cwd,
        base_sha=base_sha,
        pathspecs=[".", *exclusions],
        command=command,
        timeout=timeout,
        stop=stop,
        worktree=worktree,
    )


def _reproduce_gate(
    cwd: str,
    *,
    base_sha: str,
    patterns: list[str],
    command: str,
    timeout: int,
    stop: Callable[[], bool],
    worktree: Path,
) -> dict:
    """The mirror image of `_clean_gate`: run the gate at the base commit
    with only the test-surface change transplanted in, everything else left
    at the base. A fix lane's reproduce step needs the new or changed check
    alone, isolated from whatever source the fix also touched."""
    pathspecs = [f":(glob){pattern}" for pattern in patterns]
    return _transplant_gate(
        cwd,
        base_sha=base_sha,
        pathspecs=pathspecs,
        command=command,
        timeout=timeout,
        stop=stop,
        worktree=worktree,
    )


def _transplant_gate(
    cwd: str,
    *,
    base_sha: str,
    pathspecs: list[str],
    command: str,
    timeout: int,
    stop: Callable[[], bool],
    worktree: Path,
) -> dict:
    """Run the gate at the base commit with only the selected changes
    transplanted, through a temporary index so the fleet's own index is
    never touched. `pathspecs` picks the selection: `.` plus excludes keeps
    everything but the test surface (`_clean_gate`); the surface's own globs
    alone keep only the test surface (`_reproduce_gate`)."""
    top = git_run(cwd, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return _git_failure("git worktree add failed: repository root vanished", worktree=worktree)
    root = Path(top.stdout.strip())
    try:
        relative_cwd = Path(cwd).resolve().relative_to(root.resolve())
    except ValueError:
        relative_cwd = Path()

    worktree.parent.mkdir(parents=True, exist_ok=True)
    added = git_run(root, "worktree", "add", "--detach", str(worktree), base_sha, timeout=60)
    if added.returncode != 0:
        detail = added.stderr.strip() or added.stdout.strip() or f"exit {added.returncode}"
        return _git_failure(f"git worktree add failed: {detail}", worktree=worktree)

    patch = b""
    try:
        with tempfile.TemporaryDirectory(prefix="conductor-clean-index-") as temp_dir:
            index = Path(temp_dir) / "index"
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(index)
            try:
                seeded = subprocess.run(
                    ["git", "read-tree", base_sha],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _git_failure(
                    f"git read-tree for clean gate failed: {exc}", worktree=worktree
                )
            if seeded.returncode != 0:
                detail = (seeded.stderr or seeded.stdout).decode(errors="replace").strip()
                return _git_failure(
                    f"git read-tree for clean gate failed: "
                    f"{detail or f'exit {seeded.returncode}'}",
                    worktree=worktree,
                )
            try:
                staged = subprocess.run(
                    ["git", "add", "-A"],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _git_failure(f"git add for clean gate failed: {exc}", worktree=worktree)
            if staged.returncode != 0:
                detail = (staged.stderr or staged.stdout).decode(errors="replace").strip()
                return _git_failure(
                    f"git add for clean gate failed: {detail or f'exit {staged.returncode}'}",
                    worktree=worktree,
                )

            try:
                diff = subprocess.run(
                    [
                        "git",
                        "diff",
                        "--cached",
                        "--binary",
                        "-M",
                        base_sha,
                        "--",
                        *pathspecs,
                    ],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _git_failure(f"git diff for clean gate failed: {exc}", worktree=worktree)
            if diff.returncode != 0:
                detail = (diff.stderr or diff.stdout).decode(errors="replace").strip()
                return _git_failure(
                    f"git diff for clean gate failed: {detail or f'exit {diff.returncode}'}",
                    worktree=worktree,
                )
            patch = diff.stdout

        if patch:
            try:
                applied = subprocess.run(
                    ["git", "apply", "--binary", "--whitespace=nowarn"],
                    cwd=worktree,
                    input=patch,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _git_failure(
                    f"git apply for clean gate failed: {exc}",
                    worktree=worktree,
                    patch_bytes=len(patch),
                )
            if applied.returncode != 0:
                detail = (applied.stderr or applied.stdout).decode(errors="replace").strip()
                return _git_failure(
                    f"git apply for clean gate failed: {detail or f'exit {applied.returncode}'}",
                    worktree=worktree,
                    patch_bytes=len(patch),
                )

        outcome = run_tests(
            str(worktree / relative_cwd), command, timeout=timeout, stop=stop
        ).to_dict()
        outcome.update(worktree=str(worktree), patch_bytes=len(patch))
        return outcome
    finally:
        # The clean tree is conductor's scratch evidence, never the fleet's
        # only copy of work, so even a failed gate cannot leave it for gc.
        git_run(root, "worktree", "remove", "--force", str(worktree), timeout=60)


def _reproduce_skip(verdict: str, reason: str) -> dict:
    """A reproduce receipt for a dispatch that never ran the reproduce gate."""
    return {
        "ran": False,
        "exit_code": None,
        "timed_out": False,
        "tail": reason,
        "worktree": "",
        "patch_bytes": 0,
        "verdict": verdict,
    }


def _reproduce_receipt(
    spec: Spec,
    *,
    before: GitState,
    surface_before: Surface | None,
    surface_state: dict | None,
    test_command: str | None,
    timed_out: bool,
    error: str | None,
    exit_code: int | None,
    fleet_errored: bool,
    home: Path,
    run_id: str,
) -> tuple[dict, str | None]:
    """Reproduce before fix: a `stage: fix` write dispatch must show its own
    check failing on the base before it may land. Runs the caller's gate
    against the base commit with only the test-surface change transplanted
    in and requires it to fail there; a base run that passes, or a fix with
    no test-surface change at all, means there is nothing reproduced and the
    fix must not land. Returns the receipt block and, when the fix must be
    refused, the error string (`None` when it may proceed to its ordinary
    gate, and for every case the ordinary no-op and mode/stage handling
    already covers).
    """
    if spec.stage != "fix":
        return _reproduce_skip("skipped", "stage is not fix"), None
    if spec.mode != "write":
        return _reproduce_skip("skipped", "mode is read"), None
    if timed_out or error is not None or exit_code != 0 or fleet_errored:
        return _reproduce_skip("skipped", "dispatch did not complete cleanly"), None
    moved = not compare(spec.cwd, before, GitState.capture(spec.cwd)).no_op
    if not moved:
        return _reproduce_skip("skipped", "the fleet made no changes"), None
    if not test_command:
        return (
            _reproduce_skip("no-check", "no gate set"),
            "fix without a reproducing check: no gate set",
        )
    if not before.head:
        return (
            _reproduce_skip("no-check", "no base commit to re-run against"),
            "fix without a reproducing check: no base commit to re-run against",
        )
    if surface_state is None or not surface_state["touched"]:
        return (
            _reproduce_skip("no-check", "no test-surface change"),
            "fix without a reproducing check: no test-surface change",
        )
    assert surface_before is not None  # surface_state implies it was captured
    outcome = _reproduce_gate(
        spec.cwd,
        base_sha=before.head,
        patterns=surface_before.patterns,
        command=test_command,
        timeout=GATE_TIMEOUT,
        stop=stop_requested,
        worktree=home / "worktrees" / f"{run_id}-reproduce",
    )
    if outcome.get("interrupted"):
        return (
            {**outcome, "verdict": "skipped"},
            "interrupted: stop requested during the reproduce gate; process group killed",
        )
    if outcome.get("infra_error"):
        # The transplant itself failed (worktree add, read-tree, apply, ...);
        # the gate command never ran, so a nonzero exit here is not evidence
        # that anything was reproduced.
        return (
            {**outcome, "verdict": "no-check"},
            "fix without a reproducing check: reproduce gate could not run: "
            + outcome.get("tail", ""),
        )
    if outcome.get("timed_out"):
        # A gate that never finished proves nothing either way.
        return (
            {**outcome, "verdict": "no-check"},
            "fix without a reproducing check: reproduce gate timed out",
        )
    if _gate_passed(outcome, None):
        return (
            {**outcome, "verdict": "not-reproduced"},
            "reproduce gate passed on the base: the check does not reproduce the finding",
        )
    return {**outcome, "verdict": "reproduced"}, None


# Set by the CLI's signal handlers. Every wait loop polls it, so a stop
# request ends each running dispatch the same way a timeout does: the
# fleet's process group is killed, the run is priced from the watcher and
# receipted, and its worktree is released. Nothing is orphaned.
_STOP = threading.Event()
_LIVE_GROUPS: set[int] = set()
_LIVE_GROUPS_LOCK = threading.RLock()


def _register_live_group(pgid: int) -> None:
    with _LIVE_GROUPS_LOCK:
        _LIVE_GROUPS.add(pgid)


def _kill_live_group(pgid: int) -> None:
    """Kill and forget one group before its pid can be reused."""
    killpg(pgid)
    with _LIVE_GROUPS_LOCK:
        _LIVE_GROUPS.discard(pgid)


def kill_live_groups() -> None:
    """Synchronously kill every fleet or gate process group still registered."""
    with _LIVE_GROUPS_LOCK:
        groups = tuple(_LIVE_GROUPS)
    for pgid in groups:
        _kill_live_group(pgid)


def request_stop() -> None:
    """Ask every running dispatch to end at its next poll."""
    _STOP.set()


def stop_requested() -> bool:
    return _STOP.is_set()


def clear_stop() -> None:
    _STOP.clear()


def dispatch(
    spec: Spec,
    *,
    dry_run: bool = False,
    test_command: str | None = None,
    commit_message: str | None = None,
    isolate: bool = False,
    home: Path | None = None,
    no_op_ok: bool = False,
    base_ref: str | None = None,
    cancel: threading.Event | None = None,
    cancel_reason: str = "another lane already passed",
) -> Result:
    """Run one fleet and report honestly.

    `base_ref` starts the isolated worktree from that commit instead of the
    repo's HEAD (a pipeline stage building on an earlier stage's tip); it
    forces isolation, and a dispatch that cannot get its worktree is then
    refused in either mode, because running against HEAD would be running
    against the wrong code.

    `cancel` ends a still-running fleet the way a cap kill does (process
    group killed, priced from the watcher's last reading, no gate, no
    commit) the moment it is set; a caller sets it once its own reason for
    cancelling is known (a mission: another lane already passed) and
    `cancel_reason` names that reason for the receipt. A cancel that arrives
    after the fleet already exited cleanly changes nothing.
    """
    criteria = spec.verdict
    spec.validate()
    isolate = isolate or base_ref is not None
    fleet = FLEETS[spec.fleet]
    model_id = fleet.model(spec.model).id_for(spec.effort)
    timeout = spec.resolved_timeout()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = home or conductor_home()
    run_id, run_dir = claim_dir(base / "runs", f"{stamp}-{spec.fleet}-{_slug(spec.prompt)}")

    if criteria is not None:
        # A caller cannot accidentally drift the schema away from the
        # checklist: conductor writes both from the same Criterion objects.
        schema_path = run_dir / "verdict.schema.json"
        schema_path.write_text(json.dumps(checklist_schema(criteria), indent=2))
        spec = _replace(
            spec,
            prompt=spec.prompt + checklist_contract(criteria),
            schema=str(schema_path),
            verdict=None,
        )

    # Refusals need repository identity and dirty-file presence, not a hash
    # of every operator-owned untracked byte in the shared checkout.
    checkout_before = GitState.capture(spec.cwd, content=False)
    if spec.mode == "write" and not checkout_before.is_repo and not dry_run:
        error = "write dispatch refused: cwd is not a git repository; only read mode may run there"
        result = _refused_result(run_id, spec, model_id, timeout, run_dir, None, error)
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result
    if commit_message and not isolate and checkout_before.dirty_files and not dry_run:
        error = "commit refused: the checkout has uncommitted changes; use --isolate"
        result = _refused_result(run_id, spec, model_id, timeout, run_dir, None, error)
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    # Isolation first, because everything after this line (argv, snapshots,
    # commit, tests) must see the worktree as the working directory, not the
    # shared checkout the caller named.
    iso: worktrees.Isolation | None = None
    if isolate and not dry_run:
        iso = worktrees.create(spec.cwd, run_id, base / "worktrees", base_ref=base_ref)
        if iso.active:
            # A cwd inside the repo stays the same subdirectory inside the
            # worktree; the fleet was pointed at that directory for a reason.
            spec = _replace(spec, cwd=worktrees.mirror_path(spec.cwd, iso))
        elif spec.mode == "write" or base_ref is not None:
            # The caller asked for a private tree and cannot have one. For a
            # write, running in the shared checkout instead is the collision
            # isolation exists to prevent, so it is refused before spawn. A
            # read dispatch changes nothing and may proceed in place.
            result = _refused_result(
                run_id, spec, model_id, timeout, run_dir, iso, f"isolation failed: {iso.reason}"
            )
            (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
            return result

    # The fleet writes its final answer where the caller asked, or beside the
    # run if it did not ask. Codex is the only fleet that takes this as a flag.
    spec_with_paths = spec
    if spec.fleet == "codex" and not spec.last_message:
        spec_with_paths = _replace(spec, last_message=str(run_dir / "last_message.txt"))

    argv = build_argv(spec_with_paths)

    (run_dir / "prompt.txt").write_text(spec.prompt)
    (run_dir / "argv.json").write_text(json.dumps(argv, indent=2))

    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"

    if dry_run:
        result = Result(
            run_id=run_id,
            fleet=spec.fleet,
            model=model_id,
            effort=spec.effort,
            mode=spec.mode,
            cwd=spec.cwd,
            timeout=timeout,
            exit_code=None,
            timed_out=False,
            duration_s=0.0,
            run_dir=str(run_dir),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            tail="(dry run: nothing spawned)",
            spawned=False,
            git_verdict=GitVerdict(checked=False, notes=["dry run"]).to_dict(),
            dry_run=True,
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    before = GitState.capture(spec.cwd)
    try:
        surface_before = test_surface(spec.cwd, spec.test_surface) if before.is_repo else None
    except ValueError as exc:
        if iso is not None:
            worktrees.release(iso)
        error = f"test surface refused: {exc}"
        result = _refused_result(run_id, spec, model_id, timeout, run_dir, iso, error)
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result
    started = time.monotonic()
    error: str | None = None
    timed_out = False
    capped = False
    interrupted = False
    cancelled = False
    breaker_reason: str | None = None
    breaker: Breaker | None = None
    exit_code: int | None = None
    budget = (
        Budget(cap_usd=spec.cap_usd, enforcement=fleet.cap) if spec.cap_usd is not None else None
    )
    # The watcher follows the fleet's running usage whether or not there is a
    # cap: it is also the only price a run that conductor kills can get.
    watcher = (
        Watcher(
            spec.fleet,
            model_id,
            stdout_path,
            spec.cap_usd if fleet.cap == "watcher" else None,
        )
        if spec.fleet in {"claude", "codex", "antigravity"}
        else None
    )

    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc: subprocess.Popen | None = None
        try:
            if stop_requested():
                raise Interrupted("stop requested before the fleet was spawned")
            proc = subprocess.Popen(
                argv,
                cwd=spec.cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            _register_live_group(proc.pid)
            if (
                spec.stall_timeout
                or spec.loop_limit
                or spec.max_tool_calls
                or spec.tool_idle_timeout
            ):
                breaker = Breaker(
                    spec.fleet,
                    stdout_path,
                    stall_s=spec.stall_timeout,
                    loop_limit=spec.loop_limit,
                    max_tool_calls=spec.max_tool_calls,
                    idle_s=spec.tool_idle_timeout,
                )
        except OSError as exc:
            error = f"cannot spawn {fleet.binary}: {exc}"
        except Interrupted as exc:
            interrupted = True
            error = f"interrupted: {exc}"
        if proc is not None:
            exit_code, timed_out, capped, interrupted, breaker_reason = _wait(
                proc,
                timeout,
                watcher,
                breaker,
                run_dir=run_dir,
                stdout_path=stdout_path,
                started=started,
                cancel=cancel,
            )
            # `_wait` folds a per-dispatch cancel into `interrupted` (same poll,
            # same kill); this is the only place that tells the two apart, so
            # the receipt says which one actually ended the run.
            cancelled = interrupted and cancel is not None and cancel.is_set()
            if cancelled:
                interrupted = False
            if timed_out:
                error = f"timed out after {timeout}s; process group killed"
            elif cancelled:
                error = f"cancelled: {cancel_reason}"
            elif interrupted:
                error = "interrupted: stop requested; process group killed"
            elif capped:
                assert watcher is not None and watcher.usage is not None  # over_cap saw a figure
                error = (
                    f"budget cap hit: ${watcher.usage.cost_usd:.4f} estimated against a "
                    f"${spec.cap_usd:.4f} cap; process group killed"
                )
            elif breaker_reason is not None:
                error = f"{breaker_reason}; process group killed"

    duration = time.monotonic() - started
    breaker_state = breaker.to_dict() if breaker is not None else None

    # The fleet's own envelope first: a fleet that says it failed (on any
    # exit code) must not have its work committed as if it had succeeded.
    output: FleetOutput = parse_output(spec.fleet, _read(stdout_path))
    resumed: dict | None = None
    resume_note: str | None = None
    if spec.resume is not None:
        resume_ok = output.session_id == spec.resume
        resumed = {
            "requested": spec.resume,
            "ok": resume_ok,
            "session_id": output.session_id,
        }
        if resume_ok:
            resume_note = f"resumed session {spec.resume}"
        else:
            got = output.session_id or "none"
            resume_note = f"resume failed: fleet reported session {got}, requested {spec.resume}"
            # The guard must not hide the timeout, stop, cap, or spawn failure
            # that explains why the fleet could not report the requested id.
            if error is None:
                error = resume_note
    answer = output.answer
    if not answer and spec_with_paths.last_message:
        # Codex writes its final message to the -o file; if the event stream
        # gave nothing (an older binary, a crash mid-stream) that file is the
        # next best evidence.
        answer = _read(Path(spec_with_paths.last_message)).strip()
    checklist_verdict = parse_verdict(answer, criteria) if criteria is not None else None
    if checklist_verdict is not None:
        (run_dir / "verdict.json").write_text(json.dumps(checklist_verdict.to_dict(), indent=2))
        if (
            checklist_verdict.invalid
            and error is None
            and not (spec.mode == "read" and not answer)
        ):
            error = f"verdict invalid: {checklist_verdict.invalid}"

    surface_state: dict | None = None
    if surface_before is not None:
        try:
            surface_after = test_surface(spec.cwd, spec.test_surface)
        except ValueError:
            # A fleet that deleted its desk must still get a receipt and a
            # release attempt; the vanished tracked test files are observable.
            surface_after = missing_surface(surface_before)
        surface_state = _surface_result(spec.test_policy, surface_before, surface_after)
    forbid_touched = bool(
        surface_state and surface_state["touched"] and spec.test_policy == "forbid"
    )
    forbid_error = None
    if forbid_touched:
        forbid_error = (
            "test surface changed under policy forbid: "
            + ", ".join(surface_state["changed"])
        )

    reproduce_state, reproduce_error = _reproduce_receipt(
        spec,
        before=before,
        surface_before=surface_before,
        surface_state=surface_state,
        test_command=test_command,
        timed_out=timed_out,
        error=error,
        exit_code=exit_code,
        fleet_errored=bool(output.error),
        home=base,
        run_id=run_id,
    )
    if reproduce_error is not None:
        # A fix that reproduces nothing must not land: no commit, and its
        # ordinary gate (own or clean) is skipped below, same as any other
        # error caught before this point.
        error = reproduce_error
        if reproduce_state.get("interrupted"):
            interrupted = True

    # Commit before the Git verdict is taken, so it describes the state
    # the caller is actually left with.
    commit: CommitOutcome | None = None
    if (
        commit_message
        and not forbid_touched
        and not timed_out
        and error is None
        and exit_code == 0
        and not output.error
    ):
        commit = commit_work(spec.cwd, commit_message)
    # A fleet's self-commit is landed work even when conductor was not asked
    # to commit it. Only a descendant on the same branch belongs to this run:
    # treating a checkout of an existing branch as a commit would reset that
    # branch's unrelated history when the gate fails.
    self_commit = None
    if commit is None or (not commit.committed and commit.reason == "nothing to commit"):
        self_commit = _self_commit_sha(spec.cwd, before)
    if self_commit:
        commit = CommitOutcome(
            attempted=commit is not None,
            committed=True,
            sha=self_commit,
            reason="the fleet committed its own work",
        )

    tests: TestOutcome | None = None
    if test_command and not timed_out and error is None:
        tests = run_tests(
            spec.cwd, test_command, timeout=GATE_TIMEOUT, stop=stop_requested
        )
        if tests.interrupted:
            interrupted = True
            error = "interrupted: stop requested during the gate; process group killed"
    if surface_state is not None and spec.test_policy == "clean":
        if not surface_state["touched"]:
            surface_state["clean_gate"] = {
                "ran": False,
                "reason": "test surface unchanged",
            }
        elif not test_command:
            surface_state["clean_gate"] = {"ran": False, "reason": "no gate set"}
        elif not before.head:
            surface_state["clean_gate"] = {
                "ran": False,
                "reason": "no base commit to re-run against",
            }
        elif tests is None or not _gate_passed(tests.to_dict(), None):
            surface_state["clean_gate"] = {
                "ran": False,
                "reason": "lane gate failed",
            }
        else:
            surface_state["clean_gate"] = _clean_gate(
                spec.cwd,
                base_sha=before.head,
                patterns=surface_before.patterns,
                command=test_command,
                timeout=GATE_TIMEOUT,
                stop=stop_requested,
                worktree=base / "worktrees" / f"{run_id}-clean",
            )
            clean_gate = surface_state["clean_gate"]
            if clean_gate.get("interrupted"):
                interrupted = True
                error = "interrupted: stop requested during the clean gate; process group killed"

    if commit and commit.committed and not _gate_passed(
        tests.to_dict() if tests else None, surface_state
    ):
        # A branch must never carry a commit that failed whichever gate
        # counts; the work stays staged in the tree for the kept worktree.
        commit = uncommit(spec.cwd, commit, before.head)

    if forbid_touched:
        if commit and commit.committed:
            commit = uncommit(spec.cwd, commit, before.head)
        error = forbid_error

    if reproduce_error is not None and commit and commit.committed:
        # A fix that never reproduced anything must not land, even when the
        # fleet committed its own work directly instead of leaving it staged
        # for conductor's own commit_work to pick up.
        commit = uncommit(spec.cwd, commit, before.head)

    after = GitState.capture(spec.cwd)
    git_verdict = compare(spec.cwd, before, after)
    git_verdict.notes.extend(output.notes)
    if resume_note:
        git_verdict.notes.append(resume_note)
    if surface_state and surface_state["touched"]:
        changed = surface_state["changed"]
        git_verdict.notes.append(
            f"test surface changed: {len(changed)} file(s): {', '.join(changed[:10])}"
        )
        if spec.test_policy == "clean" and not test_command:
            git_verdict.notes.append("test surface changed with no gate to re-run")
    if commit and commit.deletions:
        git_verdict.notes.append(
            f"commit removed {len(commit.deletions)} file(s): {', '.join(commit.deletions[:10])}"
        )
    if commit and not commit.committed and commit.reason.startswith("gate failed"):
        git_verdict.notes.append(commit.reason)
    diff_path: str | None = None
    if git_verdict.checked and not git_verdict.no_op and before.head:
        # The patch is the evidence a judge should see; the answer is a claim.
        patch = diff_since(spec.cwd, before.head)
        if patch:
            (run_dir / "diff.patch").write_text(patch)
            diff_path = str(run_dir / "diff.patch")

    # The answer goes to its own file so a caller can read it without wading
    # through a transcript, and usage is recorded now, while the evidence is
    # still on disk.
    answer_path: str | None = None
    if answer:
        answer_file = run_dir / "answer.txt"
        answer_file.write_text(answer)
        answer_path = str(answer_file)

    usage = output.usage
    if usage is None and watcher is not None:
        # The stream ended without a final figure (conductor killed the run,
        # or the fleet crashed); the watcher's last reading is the only
        # price this run will get.
        usage = watcher.poll()
    if usage is not None and usage.cost_usd is None:
        estimated = prices.estimate(
            model_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )
        if estimated is not None:
            usage.cost_usd = estimated
            usage.cost_basis = "estimated"
    usage_dict = usage.to_dict() if usage is not None else None
    if budget is not None:
        budget.settle(
            usage.cost_usd if usage else None,
            killed=capped or breaker_reason is not None,
            # A cancel is not the cap firing either: another lane winning says
            # nothing about this one's spend.
            interrupted=interrupted or cancelled,
            fleet_status=output.status,
        )
        if watcher is not None and watcher.usage is None:
            git_verdict.notes.append(
                "budget watcher saw no running usage; cap checked after the run"
            )

    if iso is not None:
        worktrees.release(iso)
        if iso.active:
            git_verdict.notes.append(f"isolated on branch {iso.branch}; {iso.reason}")
        else:
            git_verdict.notes.append(f"isolation requested but not applied: {iso.reason}")

    base_commit, tip_commit = _commit_bounds(before, after, commit, iso)
    result = Result(
        run_id=run_id,
        fleet=spec.fleet,
        model=model_id,
        effort=spec.effort,
        mode=spec.mode,
        cwd=spec.cwd,
        timeout=timeout,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration,
        run_dir=str(run_dir),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        tail=_tail(stderr_path) if (exit_code not in (0, None)) else _tail(stdout_path),
        spawned=proc is not None,
        git_verdict=git_verdict.to_dict(),
        verdict=checklist_verdict.to_dict() if checklist_verdict is not None else None,
        tests=tests.to_dict() if tests else None,
        test_surface=surface_state,
        reproduce=reproduce_state,
        commit=commit.to_dict() if commit else None,
        usage=usage_dict,
        budget=budget.to_dict() if budget is not None else None,
        breaker=breaker_state,
        answer_path=answer_path,
        diff_path=diff_path,
        base_commit=base_commit,
        tip_commit=tip_commit,
        isolation=iso.to_dict() if iso is not None else None,
        fleet_status=output.status,
        fleet_error=output.error,
        session_id=output.session_id,
        resumed=resumed,
        error=error,
        no_op_ok=no_op_ok,
        interrupted=interrupted,
        cancelled=cancelled,
    )
    if result.spawned:
        statement = _lane_receipt_statement(
            run_id=run_id,
            fleet=spec.fleet,
            model=model_id,
            mode=spec.mode,
            stage=spec.stage,
            cwd=spec.cwd,
            base_commit=base_commit,
            tip_commit=tip_commit,
            diff_path=diff_path,
            surface_state=surface_state,
            tests_dict=tests.to_dict() if tests else None,
            test_command=test_command,
            reproduce_state=reproduce_state,
            ok=result.ok,
            error=result.failure(),
        )
        try:
            key = attest.receipt_key(base)
            envelope = attest.sign(statement, key)
            attestation_file = run_dir / "attestation.json"
            attestation_file.write_text(json.dumps(envelope, indent=2))
            result.attestation_path = str(attestation_file)
        except OSError as exc:
            reason = f"attestation not written: {exc}"
            print(reason, file=sys.stderr)
            if result.error is None:
                result.error = reason
    (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
    return result


class Interrupted(Exception):
    """A stop was requested before this dispatch could spawn its fleet."""


def _self_commit_sha(cwd: str, before: GitState) -> str | None:
    """A new descendant on the same branch, never an unrelated checkout."""
    head = git_run(cwd, "rev-parse", "HEAD")
    branch = git_run(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or branch.returncode != 0:
        return None
    sha = head.stdout.strip()
    if not sha or sha == before.head or branch.stdout.strip() != before.branch:
        return None
    if not before.head:  # the fleet made the repository's first commit
        return sha
    ancestor = git_run(cwd, "merge-base", "--is-ancestor", before.head, sha)
    return sha if ancestor.returncode == 0 else None


def _write_liveness(
    run_dir: Path,
    pid: int,
    stdout_path: Path,
    started: float,
    watcher: Watcher | None,
    breaker: Breaker | None,
) -> None:
    now_utc = datetime.now(UTC)
    at_str = now_utc.isoformat().replace("+00:00", "Z")
    elapsed_s = round(max(0.0, time.monotonic() - started), 1)
    try:
        stdout_bytes = stdout_path.stat().st_size
    except OSError:
        stdout_bytes = 0

    spend_usd: float | None = None
    if watcher is not None:
        usage = watcher.poll()
        if usage is not None and usage.cost_usd is not None:
            spend_usd = usage.cost_usd

    heartbeat: dict[str, object] = {
        "at": at_str,
        "elapsed_s": elapsed_s,
        "pid": pid,
        "stdout_bytes": stdout_bytes,
        "spend_usd": spend_usd,
    }
    if breaker is not None:
        heartbeat.update(breaker.to_dict())

    tmp_file = run_dir / f"liveness.tmp.{os.getpid()}"
    target_file = run_dir / "liveness.json"
    try:
        tmp_file.write_text(json.dumps(heartbeat, indent=2))
        os.replace(tmp_file, target_file)
    except OSError as exc:
        # A heartbeat that cannot be written must not end the run; the run
        # then reads as `silent` in `conductor runs`, which is the truth.
        print(f"liveness heartbeat not written: {exc}", file=sys.stderr)


def _wait(
    proc: subprocess.Popen,
    timeout: int,
    watcher: Watcher | None,
    breaker: Breaker | None,
    *,
    run_dir: Path | None = None,
    stdout_path: Path | None = None,
    started: float | None = None,
    cancel: threading.Event | None = None,
) -> tuple[int | None, bool, bool, bool, str | None]:
    """Wait for the fleet, in short polls so the budget watcher and a stop
    request get a look in. Returns (exit_code, timed_out, over_cap,
    interrupted, breaker_reason).

    `cancel` is polled in the same loop as the global stop flag and folds
    into `interrupted` on the same terms: set only while the fleet is still
    running, and never once it has already exited cleanly. The caller (only
    `dispatch` passes one) tells a stop from a cancel apart by checking
    `cancel.is_set()` itself, so this return shape stays exactly what every
    existing caller already unpacks.

    Whatever ended the wait, the process group is killed afterwards. On a
    timeout or a cap that is the point; after a clean exit it clears any
    tool the fleet left running: agy's print timeout, for one, returns
    while its shell child keeps running and keeps editing the tree. The
    group is conductor's own (start_new_session), so nothing else is hit.
    """
    start_time = started if started is not None else time.monotonic()
    out_path = stdout_path or (run_dir / "stdout.log" if run_dir else None)

    def beat() -> None:
        if run_dir is not None and out_path is not None:
            _write_liveness(run_dir, proc.pid, out_path, start_time, watcher, breaker)

    beat()
    deadline = time.monotonic() + timeout
    timed_out = over_cap = interrupted = False
    breaker_reason: str | None = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            proc.wait(timeout=min(POLL_S, remaining))
            break
        except subprocess.TimeoutExpired:
            pass
        beat()
        if _STOP.is_set() or (cancel is not None and cancel.is_set()):
            interrupted = True
            break
        if watcher is not None and watcher.over_cap():
            over_cap = True
            break
        if breaker is not None and (breaker_reason := breaker.check()) is not None:
            break
    if (
        not (timed_out or over_cap or interrupted)
        and breaker is not None
        and breaker_reason is None
    ):
        # Fast runs may finish between polls. Parse their final complete lines
        # so receipts still count tools and a just-completed runaway is not
        # allowed to evade the ceiling by exiting in the same two-second tick.
        breaker_reason = breaker.check(final=True)
    beat()
    _kill_live_group(proc.pid)
    proc.wait()
    return proc.returncode, timed_out, over_cap, interrupted, breaker_reason


def claim_dir(parent: Path, name: str) -> tuple[str, Path]:
    """Create `parent/name` atomically, suffixing the name on collision.

    Two lanes of one mission can start on the same fleet with the same
    prompt inside the same second, and two missions can share a name and a
    second; their ids, and therefore their worktree branches and receipts,
    must still differ. mkdir is the lock.
    """
    parent.mkdir(parents=True, exist_ok=True)
    candidate = name
    n = 1
    while True:
        try:
            (parent / candidate).mkdir(exist_ok=False)
            return candidate, parent / candidate
        except FileExistsError:
            n += 1
            candidate = f"{name}-{n}"


def _refused_result(
    run_id: str,
    spec: Spec,
    model_id: str,
    timeout: int,
    run_dir: Path,
    iso: worktrees.Isolation | None,
    error: str,
) -> Result:
    """A result for a dispatch conductor declined to spawn."""
    return Result(
        run_id=run_id,
        fleet=spec.fleet,
        model=model_id,
        effort=spec.effort,
        mode=spec.mode,
        cwd=spec.cwd,
        timeout=timeout,
        exit_code=None,
        timed_out=False,
        duration_s=0.0,
        run_dir=str(run_dir),
        stdout_path=str(run_dir / "stdout.log"),
        stderr_path=str(run_dir / "stderr.log"),
        tail="(not spawned)",
        spawned=False,
        git_verdict=GitVerdict(checked=False, notes=[error]).to_dict(),
        isolation=iso.to_dict() if iso is not None else None,
        error=error,
    )


def _replace(spec: Spec, **changes) -> Spec:
    data = asdict(spec)
    data.update(changes)
    return Spec(**data)
