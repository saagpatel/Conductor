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
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .fleets import FLEETS, Spec, build_argv
from .outputs import FleetOutput
from .outputs import parse as parse_output
from .verify import (
    CommitOutcome,
    GitState,
    TestOutcome,
    Verdict,
    commit_work,
    compare,
    run_tests,
)

TAIL_LINES = 20


def conductor_home() -> Path:
    return Path(os.environ.get("CONDUCTOR_HOME", Path.home() / ".conductor"))


def _slug(text: str, limit: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit].rstrip("-")) or "run"


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
    verdict: dict = field(default_factory=dict)
    tests: dict | None = None
    commit: dict | None = None
    usage: dict | None = None
    answer_path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Success means the process succeeded AND bytes moved (when the
        target was a repo and the mode was write). Exit 0 alone is not it."""
        if self.timed_out or self.exit_code != 0:
            return False
        if self.tests and self.tests.get("exit_code") not in (0, None):
            return False
        if self.mode == "write" and self.verdict.get("checked") and self.verdict.get("no_op"):
            return False
        # A requested commit that did not happen is a failure even when the
        # dispatch itself went fine: the caller asked for landed work.
        if self.commit and not self.commit.get("committed"):
            return False
        return True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d

    def summary(self) -> dict:
        """The few lines an orchestrator actually needs to decide what next."""
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "fleet": self.fleet,
            "model": self.model,
            "effort": self.effort,
            "mode": self.mode,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 1),
            "commits": self.verdict.get("commits_added", 0),
            "files_changed": self.verdict.get("files_changed", 0),
            "dirty_delta": self.verdict.get("dirty_delta", 0),
            "no_op": self.verdict.get("no_op", False),
            "tests": (self.tests or {}).get("exit_code"),
            "committed": (self.commit or {}).get("sha", "")[:8] or None,
            "cost_usd": (self.usage or {}).get("cost_usd"),
            "tokens": (self.usage or {}).get("total_tokens"),
            "answer_path": self.answer_path,
            "run_dir": self.run_dir,
            "error": self.error,
        }


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


def dispatch(
    spec: Spec,
    *,
    dry_run: bool = False,
    test_command: str | None = None,
    commit_message: str | None = None,
    home: Path | None = None,
) -> Result:
    spec.validate()
    fleet = FLEETS[spec.fleet]
    model_id = fleet.model(spec.model).id_for(spec.effort)
    timeout = spec.resolved_timeout()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{spec.fleet}-{_slug(spec.prompt)}"
    run_dir = (home or conductor_home()) / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

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
            verdict=Verdict(checked=False, notes=["dry run"]).to_dict(),
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    before = GitState.capture(spec.cwd)
    started = time.monotonic()
    error: str | None = None
    timed_out = False
    exit_code: int | None = None

    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=spec.cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        except OSError as exc:
            error = f"cannot spawn {fleet.binary}: {exc}"
            proc = None
        if proc is not None:
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait()
                exit_code = proc.returncode
                error = f"timed out after {timeout}s; process group killed"

    duration = time.monotonic() - started

    # Commit before the verdict is taken, so the verdict describes the state
    # the caller is actually left with.
    commit: CommitOutcome | None = None
    if commit_message and not timed_out and error is None and exit_code == 0:
        commit = commit_work(spec.cwd, commit_message)

    after = GitState.capture(spec.cwd)
    verdict = compare(spec.cwd, before, after)
    if commit and commit.deletions:
        verdict.notes.append(
            f"commit removed {len(commit.deletions)} file(s): {', '.join(commit.deletions[:10])}"
        )

    tests: TestOutcome | None = None
    if test_command and not timed_out and error is None:
        tests = run_tests(spec.cwd, test_command)

    # Normalize the fleet's own envelope: the answer goes to its own file so a
    # caller can read it without wading through a transcript, and usage is
    # recorded now, while the evidence is still on disk.
    output: FleetOutput = parse_output(spec.fleet, _read(stdout_path))
    answer_path: str | None = None
    if output.answer:
        answer_file = run_dir / "answer.txt"
        answer_file.write_text(output.answer)
        answer_path = str(answer_file)

    usage_dict = None
    if output.usage:
        usage_dict = output.usage.to_dict()
        usage_dict["total_tokens"] = output.usage.total_tokens

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
        verdict=verdict.to_dict(),
        tests=tests.to_dict() if tests else None,
        commit=commit.to_dict() if commit else None,
        usage=usage_dict,
        answer_path=answer_path,
        error=error,
    )
    (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
    return result


def _replace(spec: Spec, **changes) -> Spec:
    data = asdict(spec)
    data.update(changes)
    return Spec(**data)
