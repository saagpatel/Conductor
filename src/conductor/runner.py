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
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import prices, worktrees
from .budget import POLL_S, Budget, Watcher
from .fleets import FLEETS, Spec, build_argv
from .outputs import FleetOutput
from .outputs import parse as parse_output
from .paths import conductor_home
from .verify import (
    CommitOutcome,
    GitState,
    TestOutcome,
    Verdict,
    commit_work,
    compare,
    diff_since,
    git_run,
    killpg,
    run_tests,
    uncommit,
)

TAIL_LINES = 20


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
    verdict: dict = field(default_factory=dict)
    tests: dict | None = None
    commit: dict | None = None
    usage: dict | None = None
    budget: dict | None = None
    answer_path: str | None = None
    diff_path: str | None = None
    isolation: dict | None = None
    fleet_status: str | None = None
    fleet_error: str | None = None
    error: str | None = None
    dry_run: bool = False
    no_op_ok: bool = False  # a write that may legitimately change nothing
    interrupted: bool = False  # a stop request ended the run

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
        if self.tests and self.tests.get("ran"):
            if self.tests.get("timed_out"):
                return "gate timed out"
            if self.tests.get("exit_code") != 0:
                return f"gate exited {self.tests.get('exit_code')}"
        no_op = self.verdict.get("checked") and self.verdict.get("no_op")
        if self.mode == "write" and no_op and not self.no_op_ok:
            return "write dispatch moved no bytes"
        # The mirror image: a research dispatch that edited the tree ignored
        # its read-only setting (agy's read mode did exactly that live,
        # 2026-09-03), and in a shared checkout that is the collision
        # isolation exists to prevent. The bytes are the evidence.
        if self.mode == "read" and self.verdict.get("checked") and not self.verdict.get("no_op"):
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
            "commits": self.verdict.get("commits_added", 0),
            "files_changed": self.verdict.get("files_changed", 0),
            "dirty_delta": self.verdict.get("dirty_delta", 0),
            "no_op": self.verdict.get("no_op", False),
            "tests": (self.tests or {}).get("exit_code"),
            "committed": (self.commit or {}).get("sha", "")[:8] or None,
            "branch": iso.get("branch") or None,
            "worktree": iso.get("worktree") if iso.get("kept") else None,
            "tip": iso.get("tip_sha") or None,
            "clean": iso.get("clean"),
            "cost_usd": (self.usage or {}).get("cost_usd"),
            "cost_basis": (self.usage or {}).get("cost_basis"),
            "tokens": (self.usage or {}).get("total_tokens"),
            "cap_usd": (self.budget or {}).get("cap_usd"),
            "over_cap": bool((self.budget or {}).get("exceeded")),
            "answer_path": self.answer_path,
            "diff_path": self.diff_path,
            "run_dir": self.run_dir,
            "error": self.error or self.fleet_error,
            "failure": self.failure(),
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
) -> Result:
    """Run one fleet and report honestly.

    `base_ref` starts the isolated worktree from that commit instead of the
    repo's HEAD (a pipeline stage building on an earlier stage's tip); it
    forces isolation, and a dispatch that cannot get its worktree is then
    refused in either mode, because running against HEAD would be running
    against the wrong code.
    """
    spec.validate()
    isolate = isolate or base_ref is not None
    fleet = FLEETS[spec.fleet]
    model_id = fleet.model(spec.model).id_for(spec.effort)
    timeout = spec.resolved_timeout()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = home or conductor_home()
    run_id, run_dir = claim_dir(base / "runs", f"{stamp}-{spec.fleet}-{_slug(spec.prompt)}")

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
            verdict=Verdict(checked=False, notes=["dry run"]).to_dict(),
            dry_run=True,
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    before = GitState.capture(spec.cwd)
    started = time.monotonic()
    error: str | None = None
    timed_out = False
    capped = False
    interrupted = False
    exit_code: int | None = None
    budget = (
        Budget(cap_usd=spec.cap_usd, enforcement=fleet.cap) if spec.cap_usd is not None else None
    )
    # The watcher follows the fleet's running usage whether or not there is a
    # cap: it is also the only price a run that conductor kills can get.
    watcher = (
        Watcher(spec.fleet, model_id, stdout_path, spec.cap_usd) if fleet.cap == "watcher" else None
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
        except OSError as exc:
            error = f"cannot spawn {fleet.binary}: {exc}"
        except Interrupted as exc:
            interrupted = True
            error = f"interrupted: {exc}"
        if proc is not None:
            exit_code, timed_out, capped, interrupted = _wait(proc, timeout, watcher)
            if timed_out:
                error = f"timed out after {timeout}s; process group killed"
            elif interrupted:
                error = "interrupted: stop requested; process group killed"
            elif capped:
                assert watcher is not None and watcher.usage is not None  # over_cap saw a figure
                error = (
                    f"budget cap hit: ${watcher.usage.cost_usd:.4f} estimated against a "
                    f"${spec.cap_usd:.4f} cap; process group killed"
                )

    duration = time.monotonic() - started

    # The fleet's own envelope first: a fleet that says it failed (on any
    # exit code) must not have its work committed as if it had succeeded.
    output: FleetOutput = parse_output(spec.fleet, _read(stdout_path))

    # Commit before the verdict is taken, so the verdict describes the state
    # the caller is actually left with.
    commit: CommitOutcome | None = None
    if commit_message and not timed_out and error is None and exit_code == 0 and not output.error:
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
        tests = run_tests(spec.cwd, test_command, stop=stop_requested)
        if tests.interrupted:
            interrupted = True
            error = "interrupted: stop requested during the gate; process group killed"
        if commit and commit.committed and not tests.passed:
            # A branch must never carry a commit that failed its gate; the
            # work stays staged in the tree for the kept worktree.
            commit = uncommit(spec.cwd, commit, before.head)

    after = GitState.capture(spec.cwd)
    verdict = compare(spec.cwd, before, after)
    verdict.notes.extend(output.notes)
    if commit and commit.deletions:
        verdict.notes.append(
            f"commit removed {len(commit.deletions)} file(s): {', '.join(commit.deletions[:10])}"
        )
    if commit and not commit.committed and commit.reason.startswith("gate failed"):
        verdict.notes.append(commit.reason)
    diff_path: str | None = None
    if verdict.checked and not verdict.no_op and before.head:
        # The patch is the evidence a judge should see; the answer is a claim.
        patch = diff_since(spec.cwd, before.head)
        if patch:
            (run_dir / "diff.patch").write_text(patch)
            diff_path = str(run_dir / "diff.patch")

    # The answer goes to its own file so a caller can read it without wading
    # through a transcript, and usage is recorded now, while the evidence is
    # still on disk.
    answer = output.answer
    if not answer and spec_with_paths.last_message:
        # Codex writes its final message to the -o file; if the event stream
        # gave nothing (an older binary, a crash mid-stream) that file is the
        # next best evidence.
        answer = _read(Path(spec_with_paths.last_message)).strip()
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
            killed=capped,
            interrupted=interrupted,
            fleet_status=output.status,
        )
        if watcher is not None and watcher.usage is None:
            verdict.notes.append("budget watcher saw no running usage; cap checked after the run")

    if iso is not None:
        worktrees.release(iso)
        if iso.active:
            verdict.notes.append(f"isolated on branch {iso.branch}; {iso.reason}")
        else:
            verdict.notes.append(f"isolation requested but not applied: {iso.reason}")

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
        verdict=verdict.to_dict(),
        tests=tests.to_dict() if tests else None,
        commit=commit.to_dict() if commit else None,
        usage=usage_dict,
        budget=budget.to_dict() if budget is not None else None,
        answer_path=answer_path,
        diff_path=diff_path,
        isolation=iso.to_dict() if iso is not None else None,
        fleet_status=output.status,
        fleet_error=output.error,
        error=error,
        no_op_ok=no_op_ok,
        interrupted=interrupted,
    )
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


def _wait(
    proc: subprocess.Popen, timeout: int, watcher: Watcher | None
) -> tuple[int | None, bool, bool, bool]:
    """Wait for the fleet, in short polls so the budget watcher and a stop
    request get a look in. Returns (exit_code, timed_out, over_cap,
    interrupted).

    Whatever ended the wait, the process group is killed afterwards. On a
    timeout or a cap that is the point; after a clean exit it clears any
    tool the fleet left running: agy's print timeout, for one, returns
    while its shell child keeps running and keeps editing the tree. The
    group is conductor's own (start_new_session), so nothing else is hit.
    """
    deadline = time.monotonic() + timeout
    timed_out = over_cap = interrupted = False
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
        if _STOP.is_set():
            interrupted = True
            break
        if watcher is not None and watcher.over_cap():
            over_cap = True
            break
    _kill_live_group(proc.pid)
    proc.wait()
    return proc.returncode, timed_out, over_cap, interrupted


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
        verdict=Verdict(checked=False, notes=[error]).to_dict(),
        isolation=iso.to_dict() if iso is not None else None,
        error=error,
    )


def _replace(spec: Spec, **changes) -> Spec:
    data = asdict(spec)
    data.update(changes)
    return Spec(**data)
