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

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path

from . import attest, prices, worktrees
from . import ports as ports_mod
from .breakers import Breaker
from .budget import POLL_S, Budget, Watcher
from .errors import PARSE_FAILURE_PREFIX, error_kind
from .fleets import (
    FLEETS,
    TAINT_AGY_DENIED_TOOLS,
    TAINT_AGY_HOOKS_REL,
    DispatchRefused,
    Spec,
    build_agy_hooks_argv,
    build_argv,
    cli_version,
    taint_agy_denied_tools,
    taint_agy_matchers,
    taint_disallowed_tools,
    taint_hook_files,
)
from .outputs import INCOMPLETE, FleetOutput, agy_init_event, claude_init_event, json_line
from .outputs import parse as parse_output
from .paths import conductor_home
from .surface import Surface, missing_surface, test_surface
from .verdicts import checklist_contract, checklist_schema, parse_verdict
from .verify import (
    NO_OP_COMMIT_REASONS,
    CommitOutcome,
    GitState,
    TestOutcome,
    changed_entry_paths,
    changed_paths_since,
    commit_work,
    compare,
    diff_since,
    discard,
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
SETUP_TIMEOUT = 600


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
    # E1: {"path", "exists", "bytes", "parsed", "ok", "reason"} when
    # spec.deliverable was set, else None. `ok` is null on a dry run (declared,
    # not checked). Checked on the filesystem of the lane's actual working
    # tree, never through `git status` -- see `_check_deliverable`.
    deliverable: dict | None = None
    commit: dict | None = None
    usage: dict | None = None
    budget: dict | None = None
    breaker: dict | None = None
    answer_path: str | None = None
    diff_path: str | None = None
    # E1: a persisted copy of the deliverable's bytes (when it exists),
    # beside answer.txt and diff.patch, so it outlives a worktree that gets
    # released before the caller ever sees this Result. Set whenever the
    # file exists, regardless of `deliverable["ok"]` -- same convention as
    # `answer_path`.
    deliverable_path: str | None = None
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
    # E22: the fleet binary's own `--version` output, captured before spawn
    # (also on a refused or dry-run receipt). None when the binary is
    # missing, exits non-zero, times out, or predates this field.
    fleet_version: str | None = None
    resumed: dict | None = None
    # C4: {"ports": [<int>], "setup": <TestOutcome dict or null>,
    # "teardown": <TestOutcome dict or null>, "included": [<path>]}, set only
    # when the dispatch actually used one of ports/setup/teardown/include.
    lane_env: dict | None = None
    error: str | None = None
    dry_run: bool = False
    no_op_ok: bool = False  # a write that may legitimately change nothing
    interrupted: bool = False  # a stop request ended the run
    cancelled: bool = False  # another lane already passed; this one was cut
    # D2: {"declared": True, "tools_denied": [...]} when spec.taint was set,
    # else None. Set by dispatch(), never inferred from anything a fleet said.
    taint: dict | None = None
    # E21: {"hooks_written", "hooks_loaded", "tools_seen", "uncovered",
    # "denied_calls"} when spec.taint was set on the antigravity fleet, else
    # None. Set by dispatch() from the deny hook's own log and the stream's
    # init event, never from anything the fleet claims about itself.
    taint_enforcement: dict | None = None
    # D3: {"name", "tools": [...] | None, "applied": True | False | None} when
    # spec.agent was set, else None. `applied` is asserted from the stream's
    # own init event, never from the fleet's answer (see claude_init_event);
    # None means the run failed for an unrelated reason before the init
    # event could settle the question either way.
    agent: dict | None = None
    # E11: the pipeline stage (mission.STAGES: build, review, fix) this
    # dispatch ran as, set from spec.stage whether or not a mission is
    # involved; None outside a staged pipeline.
    stage: str | None = None
    # E11: which mission lane made this dispatch, and that mission's id.
    # Set only when a mission dispatched this run; None for a plain
    # `conductor dispatch` and for any receipt written before this field
    # existed (report.py joins those through the mission snapshot instead).
    lane: str | None = None
    mission: str | None = None
    # E17: the sha256 of this attempt's rendered prompt, scrubbed and
    # nonce-stripped the way golden.replay compares it; set by golden's
    # replay dispatcher, None on a live dispatch (nothing today reads it
    # outside golden.projection).
    prompt_sha256: str | None = None
    # E17: name -> version id (prompts.prompt_versions()) for every
    # conductor-authored prompt text this dispatch actually appended to
    # spec.prompt (the verdict checklist contract; a mission's collate,
    # resolve, or rank contract, passed in by the caller); empty when none did.
    prompt_versions: dict[str, str] = field(default_factory=dict)
    # F3: {"skipped": <reason>, "command": <test_command>} when a read lane's
    # own gate and clean gate were both skipped because the bytes comparison
    # (run before either gate) showed nothing but the lane's own no-op or its
    # declared E1 deliverable; None for every other lane, and for a read lane
    # that moved anything else (still gated, same as today).
    gate: dict | None = None
    # F12: `result.permission_denials` from a claude run under
    # `--permission-prompts none` -- `{tool_name, tool_use_id, tool_input}`
    # per denial, empty for every other fleet and for a claude run that
    # denied nothing. A write lane with a non-empty list fails (see
    # `failure()`); a read lane's list is a note on the git verdict only.
    permission_denials: list[dict] = field(default_factory=list)
    # F12: the `--permission-mode` this claude dispatch actually ran under
    # ("plan", "acceptEdits", or "bypassPermissions"), None for every other
    # fleet. `restricted` is True when `--restricted` was on the argv --
    # either because the lane declared `restricted: true` or because it is a
    # read lane with a declared `deliverable` (see `fleets._build_claude`).
    permission_mode: str | None = None
    restricted: bool = False

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
        # D15: and a fleet that never said anything terminal did not finish.
        # A stream cut short mid-step can still leave an exit code of 0, bytes
        # moved, and a green gate behind it -- every other check below then
        # passes and the lane settles as ok on a turn that never ended.
        if self.fleet_status == INCOMPLETE:
            return "fleet stream ended without a terminal event"
        # F15: a vanished working tree is never ok, whatever the mode and
        # whatever the gate did or did not do with a directory that no
        # longer exists -- checked ahead of the gate below so a read lane
        # whose F3 skip this state also disables (see the pre-gate check
        # above) does not fall through as ok merely because nothing ran.
        if self.git_verdict.get("checked") and self.git_verdict.get("vanished"):
            return "the working tree vanished during the dispatch; no work landed"
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
            # E25: a clean gate that only fails because the diff itself
            # touched the test surface is the trap AGENTS.md rule 3 exists
            # for -- the base tree's tests ran against the new source, not a
            # broken build. Name the touched files so that is not re-derived
            # by hand from the receipt every time. `infra_error` (worktree
            # add/read-tree/apply failing in `_git_failure`) means the gate
            # command never ran at all, so it is excluded even when the
            # surface was touched -- that failure has nothing to do with
            # the fixtures the diff changed.
            changed = (self.test_surface or {}).get("changed") or []
            if (
                clean.get("ran")
                and not clean.get("infra_error")
                and (self.test_surface or {}).get("touched")
                and changed
            ):
                return (
                    f"{label} exited {counted.get('exit_code')} after the diff touched "
                    f"{len(changed)} test-surface files: {_test_surface_note(changed)}"
                )
            return f"{label} exited {counted.get('exit_code')}"
        # E1: a declared deliverable that is missing, empty, unparsable, or
        # schema-invalid is checked after the process/fleet/gate checks above
        # (a run that never really finished must classify as whatever ended
        # it, not as "deliverable") and before the moved-bytes checks below
        # (the exemption those checks may apply depends on knowing this
        # first). `ok` is None on a dry run, which is not a failure.
        if self.deliverable is not None and self.deliverable.get("ok") is False:
            return self.deliverable.get("reason") or (
                f"deliverable check failed: {self.deliverable.get('path')}"
            )
        no_op = self.git_verdict.get("checked") and self.git_verdict.get("no_op")
        if self.mode == "write" and no_op and not self.no_op_ok:
            return "write dispatch moved no bytes"
        # The mirror image: a research dispatch that edited the tree ignored
        # its read-only setting (agy's read mode did exactly that live,
        # 2026-09-03), and in a shared checkout that is the collision
        # isolation exists to prevent. The bytes are the evidence. E1's
        # deliverable_only exemption lifts this by exactly the declared
        # file: a read lane may write its own product.
        if (
            self.mode == "read"
            and self.git_verdict.get("checked")
            and not self.git_verdict.get("no_op")
            and not self.git_verdict.get("deliverable_only")
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
            nothing = self.commit.get("reason") in NO_OP_COMMIT_REASONS
            if not (self.no_op_ok and nothing):
                return f"commit did not land: {self.commit.get('reason') or 'unknown'}"
        return None

    @property
    def ok(self) -> bool:
        return self.failure() is None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        # Computed, not stored: derived only from fields already on this
        # object, so a Result rehydrated from an old receipt still
        # classifies correctly without needing its own field to go stale.
        d["kind"] = error_kind(self)
        return d

    # The fields with no dataclass default: a receipt missing any of these
    # cannot be rehydrated into a Result that means anything.
    _REQUIRED_FIELDS = (
        "run_id",
        "fleet",
        "model",
        "effort",
        "mode",
        "cwd",
        "timeout",
        "exit_code",
        "timed_out",
        "duration_s",
        "run_dir",
        "stdout_path",
        "stderr_path",
        "tail",
    )
    # Computed by to_dict(), never a real field: present on every receipt but
    # accepted and discarded here so `from_dict(r.to_dict())` round-trips.
    _COMPUTED_FIELDS = frozenset({"ok", "kind"})

    @classmethod
    def from_dict(cls, raw: dict) -> Result:
        """Rehydrate a stored `result.json` (golden.py's replay reads a
        recorded receipt back this way). Unknown keys are refused by name;
        a key missing from an older receipt takes the dataclass default,
        except for the fields above, which have none and are required."""
        if not isinstance(raw, dict):
            raise ValueError("result must be an object")
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(raw) - known - cls._COMPUTED_FIELDS)
        if unknown:
            raise ValueError(f"result has unknown field(s): {', '.join(unknown)}")
        missing = sorted(f for f in cls._REQUIRED_FIELDS if f not in raw)
        if missing:
            raise ValueError(f"result is missing field(s): {', '.join(missing)}")
        data = {k: v for k, v in raw.items() if k in known}
        return cls(**data)

    def summary(self) -> dict:
        """The few lines an orchestrator actually needs to decide what next."""
        iso = self.isolation or {}
        return {
            "run_id": self.run_id,
            "ok": self.ok,
            "kind": error_kind(self),
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
            "cache_write_tokens": (self.usage or {}).get("cache_write_tokens"),
            "cap_usd": (self.budget or {}).get("cap_usd"),
            "over_cap": bool((self.budget or {}).get("exceeded")),
            "grace_used": (self.budget or {}).get("grace_used"),
            "tool_calls": (self.breaker or {}).get("tool_calls", 0),
            "breaker": (self.breaker or {}).get("tripped"),
            "ports": (self.lane_env or {}).get("ports", []),
            "answer_path": self.answer_path,
            "diff_path": self.diff_path,
            "deliverable_path": self.deliverable_path,
            "attestation_path": self.attestation_path,
            "run_dir": self.run_dir,
            "session_id": self.session_id,
            "resumed": self.resumed,
            "error": self.error or self.fleet_error,
            "failure": self.failure(),
            "cancelled": self.cancelled,
            "taint": self.taint,
            "agent": self.agent,
            "deliverable": self.deliverable,
            "prompt_versions": self.prompt_versions,
            "prompt_sha256": self.prompt_sha256,
        }


def _version_note(fleet_version: str | None) -> list[str]:
    """E22: one note when the fleet's `--version` could not be captured, so
    a receipt says why `fleet_version` is null instead of leaving a silent
    gap."""
    return [] if fleet_version is not None else ["fleet version unavailable"]


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


def _test_surface_note(changed: list[str]) -> str:
    """`a, b, c` (sorted, at most five), then `, and M more` for the rest --
    the touched-file list `Result.failure()` names on a clean-gate failure
    the diff itself caused (E25)."""
    paths = sorted(changed)
    shown = paths[:5]
    note = ", ".join(shown)
    extra = len(paths) - len(shown)
    if extra > 0:
        note += f", and {extra} more"
    return note


_SCHEMA_TYPE_CHECKS: dict[str, Callable[[object], bool]] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _schema_mismatch(data: object, schema: dict) -> str | None:
    """E1's own minimal check, the same scope as a verdict checklist's
    contract: every name in `required` is present, and every present
    property whose schema declares a `type` has a value of that type. Not a
    general JSON Schema validator."""
    if not isinstance(schema, dict):
        # D9: `Spec._validate_deliverable` refuses this before the spawn, but
        # a schema file is read again here, after the run, and a file that
        # changed underneath (or a Result rehydrated from an older receipt)
        # must read as a failed check, not as an AttributeError on `.get`.
        return "schema file is not a JSON object"
    if not isinstance(data, dict):
        return "top level is not a JSON object"
    required = schema.get("required")
    for name in required if isinstance(required, list) else []:
        if name not in data:
            return f"missing required property {name!r}"
    properties = schema.get("properties")
    for name, subschema in (properties if isinstance(properties, dict) else {}).items():
        if name not in data or not isinstance(subschema, dict):
            continue
        expected = subschema.get("type")
        # JSON Schema allows "type" to be a list of alternatives (or anything
        # else); item 3's own scope is a single declared type name, so
        # anything else is not checked rather than raised on -- a schema
        # that could not have been anticipated must not crash the dispatch.
        checker = _SCHEMA_TYPE_CHECKS.get(expected) if isinstance(expected, str) else None
        if checker is not None and not checker(data[name]):
            return f"property {name!r} must be of type {expected}"
    return None


def _repo_relative(cwd: str, path: str) -> str:
    """`path`, declared relative to `cwd`, expressed relative to the repo's
    toplevel instead -- what `changed_entry_paths` (git status, always
    root-relative) actually names. Falls back to `path` unchanged when the
    toplevel can't be found, so a non-repo cwd degrades to the old behavior
    rather than raising."""
    top = git_run(cwd, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return path
    try:
        prefix = Path(cwd).resolve().relative_to(Path(top.stdout.strip()).resolve())
    except ValueError:
        return path
    return (prefix / path).as_posix()


def _read_deliverable_only(
    spec: Spec, before: GitState, after: GitState, verdict: GitVerdict
) -> bool:
    """E1's exemption: a read lane may move exactly its declared deliverable
    and nothing else. Shared by the F3 pre-gate skip decision (evaluated
    before either gate runs) and the final git verdict the receipt carries
    (evaluated after), so the two never disagree about what "only the
    deliverable changed" means."""
    if not (
        spec.mode == "read"
        and spec.deliverable is not None
        and verdict.checked
        and not verdict.no_op
        and before.head == after.head
        and before.branch == after.branch
    ):
        return False
    changed = changed_entry_paths(before, after)
    deliverable_repo_path = _repo_relative(spec.cwd, spec.deliverable["path"])
    return bool(changed) and changed == {deliverable_repo_path}


def deliverable_path_problem(cwd: str | Path, path: str) -> str | None:
    """W5: why `<cwd>/<path>` may not be taken as a deliverable, or None
    when it may.

    The declared path is validated at load (repo-relative, no `..`,
    resolving inside cwd), but a lane runs after that and can plant a
    symlink where its product was meant to be: `plan.json -> ../secret`
    passed every later check and was copied into the run directory as the
    lane's own bytes. The rule here is the simplest one that closes it: no
    symlink anywhere from the working tree down to the file itself, and the
    resolved file still under the working tree. A lane that wants its
    product read has to write the bytes.
    """
    root = Path(cwd)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        return f"deliverable working tree is unreadable: {exc}"
    current = root_resolved
    for part in Path(path).parts:
        current = current / part
        if current.is_symlink():
            return f"deliverable is a symlink: {path}"
    try:
        resolved = current.resolve(strict=True)
    except OSError:
        return f"deliverable missing: {path}"
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return f"deliverable resolves outside the worktree: {path}"
    return None


def copy_no_follow(src: Path, dst: Path) -> None:
    """Copy `src` to `dst`, refusing (OSError) to open `src` through a
    symlink -- the byte-level half of `deliverable_path_problem`."""
    fd = os.open(str(src), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with open(fd, "rb", closefd=False) as source, dst.open("wb") as out:
            shutil.copyfileobj(source, out)
    finally:
        os.close(fd)


def _check_deliverable(spec: Spec, *, dry_run: bool) -> dict | None:
    """E1: a lane's product can be a file, not just its reply. Checked on
    the filesystem of the lane's actual working tree (its worktree when
    isolated; `spec.cwd` already points there by the time this is called),
    never through `git status`: an operator's own global excludes can hide
    an untracked file from Git entirely, which is exactly how one fixture's
    own deliverable went missing from its receipt (C7)."""
    declared = spec.deliverable
    if declared is None:
        return None
    path = declared["path"]
    if dry_run:
        return {
            "path": path,
            "exists": None,
            "bytes": None,
            "parsed": None,
            "ok": None,
            "reason": None,
        }
    full = Path(spec.cwd) / path
    # W5: before anything reads the file, and before the capture below
    # copies it. `exists: False` keeps the capture's hands off a path that
    # is refused here -- it copies whatever exists, ok or not.
    unsafe = deliverable_path_problem(spec.cwd, path)
    if unsafe is not None:
        return {
            "path": path,
            "exists": False,
            "bytes": None,
            "parsed": None,
            "ok": False,
            "reason": unsafe,
        }
    if not full.is_file():
        return {
            "path": path,
            "exists": False,
            "bytes": None,
            "parsed": None,
            "ok": False,
            "reason": f"deliverable missing: {path}",
        }
    size = full.stat().st_size
    if size == 0:
        return {
            "path": path,
            "exists": True,
            "bytes": 0,
            "parsed": None,
            "ok": False,
            "reason": f"deliverable empty: {path}",
        }
    schema_path = declared.get("schema")
    if not schema_path:
        return {
            "path": path,
            "exists": True,
            "bytes": size,
            "parsed": None,
            "ok": True,
            "reason": None,
        }
    try:
        data = json.loads(full.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "path": path,
            "exists": True,
            "bytes": size,
            "parsed": False,
            "ok": False,
            "reason": f"deliverable does not parse: {path}",
        }
    try:
        schema = json.loads(Path(schema_path).read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # D9: validated before the spawn; unreadable now means the file moved
        # or was rewritten during the run, which is a failed check on this
        # lane, not an exception on the thread that records what it cost.
        return {
            "path": path,
            "exists": True,
            "bytes": size,
            "parsed": True,
            "ok": False,
            "reason": f"deliverable schema unreadable: {exc}",
        }
    problem = _schema_mismatch(data, schema)
    if problem is not None:
        return {
            "path": path,
            "exists": True,
            "bytes": size,
            "parsed": True,
            "ok": False,
            "reason": f"deliverable does not match schema: {problem}",
        }
    return {
        "path": path,
        "exists": True,
        "bytes": size,
        "parsed": True,
        "ok": True,
        "reason": None,
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


def _agent_verdict(spec_agent: dict, init_event: dict | None) -> tuple[dict, str | None]:
    """D3: whether the stream's own init event proves the agent was applied.

    Returns the receipt's `agent` dict and, when it was not applied, the
    text `dispatch()` may use as the run's `error` (the caller decides
    whether the run otherwise looks complete enough to surface it)."""
    name = spec_agent["name"]
    wanted_tools = spec_agent.get("tools")
    receipt = {"name": name, "tools": wanted_tools, "applied": True}
    if init_event is None:
        return {**receipt, "applied": False}, f"agent '{name}' not applied: no init event"
    agents = init_event.get("agents")
    if not isinstance(agents, list) or name not in agents:
        return (
            {**receipt, "applied": False},
            f"agent '{name}' not applied: not present in the init event's agents list",
        )
    if wanted_tools is not None:
        actual = init_event.get("tools")
        actual_set = set(actual) if isinstance(actual, list) else set()
        if actual_set != set(wanted_tools):
            return (
                {**receipt, "applied": False},
                f"agent '{name}' not applied: init tools {sorted(actual_set)} do not "
                f"match {sorted(set(wanted_tools))}",
            )
    return receipt, None


# E21: reaching-out name patterns the deny hook cannot name individually
# (agy's own init event is the only inventory of what actually ran; the
# probe's tool list was not exhaustive -- see the comment above
# TAINT_AGY_DENIED_TOOLS in fleets.py).
_TAINT_AGY_UNCOVERED_SUBSTRINGS = ("subagent", "mcp", "web", "url", "message", "schedule", "inbox")
# Every PreToolUse matcher `fleets.taint_hook_files` writes, in the order it
# writes them; the preflight requires each one back by name. D5: the same
# names in both shell modes -- `run_command` is always matched, and only the
# hook's decision for it changes -- so this stays a module constant.
_TAINT_AGY_MATCHERS = taint_agy_matchers()
_TAINT_AGY_LOG_RE = re.compile(r"loaded (\d+) named hooks? from \d+ hooks\.json file\(s\)")
_TAINT_AGY_DENIED_CALL_MARKER = "denied by pre-tool hook"


def _sha256_file(path: Path) -> str | None:
    """W1: the digest of a hook file as it sits on disk, or None if it is
    gone or unreadable -- both of which are themselves a failed check."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_taint_agy_hooks(
    cwd: str, iso: worktrees.Isolation, *, taint_shell: str = "deny"
) -> tuple[list[str], dict[str, str]]:
    """E21: write the deny hook files into the worktree, before `before` is
    captured, and return their worktree-relative paths so the caller can keep
    them untracked through the worktree-scoped `core.excludesFile` that
    `_apply_include` builds. Never the shared `info/exclude`: `git rev-parse
    --git-path info/exclude` resolves to the one file every worktree of the
    repository shares (verified on git 2.55 from a linked worktree), so
    writing there would mutate the operator's checkout and leak this lane's
    pattern into every other lane's.

    W1: also returns the sha256 of each file as written, keyed by its path
    relative to `cwd`. The hook script is re-read from a writable worktree on
    every tool call, so "what conductor wrote" and "what agy ran" are two
    different claims; `_taint_agy_enforcement` re-hashes after the run and
    fails it if they differ.
    """
    hook_files = taint_hook_files(cwd, taint_shell=taint_shell)
    repo_root = Path(iso.worktree).resolve()
    cwd_root = Path(cwd).resolve()
    try:
        prefix = cwd_root.relative_to(repo_root)
    except ValueError:
        prefix = Path(".")
    written: list[str] = []
    digests: dict[str, str] = {}
    for rel_path, text in hook_files.items():
        dest = cwd_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        written.append((prefix / rel_path).as_posix())
        digest = _sha256_file(dest)
        if digest is not None:
            digests[rel_path] = digest
    return written, digests


def _uncovered_agy_tools(tools: list[str]) -> list[str]:
    denied = frozenset(TAINT_AGY_DENIED_TOOLS)
    return [
        name
        for name in tools
        if name not in denied
        and (name.startswith("browser_") or any(s in name for s in _TAINT_AGY_UNCOVERED_SUBSTRINGS))
    ]


def _denied_tool_error(event: dict) -> bool:
    """A `step_update` whose tool step ended in the hook's own denial: the
    stream's `tool_info.error.message` carries the marker. Live drill
    2026-09-07 (docs/research/2026-09-07-live-probe-taint-shell-deny.md):
    the model's final answer quoted the two denial messages verbatim, and
    a line scan for the marker counted five denials where the stream held
    two, so the count is taken from the tool-error events alone."""
    update = event.get("step_update")
    if not isinstance(update, dict) or update.get("step_type") != "tool":
        return False
    if update.get("state") != "ERROR":
        return False
    info = update.get("tool_info")
    error = info.get("error") if isinstance(info, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return isinstance(message, str) and _TAINT_AGY_DENIED_CALL_MARKER in message


def _count_denied_calls(stdout_text: str) -> int:
    count = 0
    for line in stdout_text.splitlines():
        event = json_line(line)
        if event is not None and _denied_tool_error(event):
            count += 1
    return count


_TAINT_AGY_PREFLIGHT_TIMEOUT_S = 30


def _parse_agy_hooks_result(text: str) -> list[dict] | None:
    """F13: the `/hooks` slash command's `command_result` event -- a free,
    zero-turn query naming every loaded hooks file with its `source` path
    and `enabled` flag. None when no such event parsed at all (a stream
    that never answered), an empty list when it answered with no hooks."""
    for line in text.splitlines():
        event = json_line(line)
        if event is None or event.get("event") != "command_result":
            continue
        command = event.get("command")
        if not isinstance(command, dict) or command.get("name") != "hooks":
            continue
        data = command.get("data")
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if not isinstance(hooks, list):
            return []
        return [entry for entry in hooks if isinstance(entry, dict)]
    return None


def _taint_agy_preflight(cwd: str, run_dir: Path) -> tuple[dict, str | None]:
    """F13: before a tainted antigravity dispatch spawns its paid turn, query
    `/hooks` in print mode -- free, `num_turns: 0`, no model spend -- and
    require the deny hook file `taint_hook_files` just wrote to appear
    enabled. Fails closed on a query that cannot be spawned, times out, or
    answers with no such event; this is the first source of enforcement
    evidence, the after-the-run 'loaded N named hooks' log-count check
    (`_taint_agy_enforcement`) the second.

    Returns the receipt's `taint_enforcement.preflight` dict and, when the
    hooks did not visibly hold, the text `dispatch()` folds into the run's
    `error` (prefixed `taint hooks not enforced:` so `errors.error_kind`
    still classifies it as `taint`).
    """
    argv = build_agy_hooks_argv(cwd)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_TAINT_AGY_PREFLIGHT_TIMEOUT_S,
        )
        stdout_text = proc.stdout
    except subprocess.TimeoutExpired as exc:
        stdout_text = exc.output if isinstance(exc.output, str) else ""
        if stdout_text:
            (run_dir / "hooks-preflight.json").write_text(stdout_text)
        detail = f"hooks preflight timed out after {_TAINT_AGY_PREFLIGHT_TIMEOUT_S}s"
        return {"ok": False, "loaded": [], "detail": detail}, detail
    except OSError as exc:
        detail = f"hooks preflight could not spawn: {exc}"
        return {"ok": False, "loaded": [], "detail": detail}, detail
    (run_dir / "hooks-preflight.json").write_text(stdout_text)
    hooks = _parse_agy_hooks_result(stdout_text)
    if hooks is None:
        detail = "hooks preflight returned no command_result event for '/hooks'"
        return {"ok": False, "loaded": [], "detail": detail}, detail

    def _is_our_hooks_file(entry: dict) -> bool:
        source = str(entry.get("source", ""))
        return source.endswith(TAINT_AGY_HOOKS_REL) and entry.get("enabled") is True

    match = next((entry for entry in hooks if _is_our_hooks_file(entry)), None)
    if match is None:
        detail = f"hooks preflight did not find {TAINT_AGY_HOOKS_REL!r} enabled among {hooks}"
        return {"ok": False, "loaded": hooks, "detail": detail}, detail
    # F10 anti-slop consumer (2026-09-07): agy's "loaded N named hooks" log
    # line counts hooks *files*, not matchers -- the whole deny file is one
    # named hook -- so the per-tool evidence lives here, in the answer's own
    # `actions` list: every matcher conductor wrote must be back, by name.
    actions = match.get("actions")
    matchers = {
        str(action.get("matcher"))
        for action in (actions if isinstance(actions, list) else [])
        if isinstance(action, dict) and action.get("event") == "PreToolUse"
    }
    missing = sorted(set(_TAINT_AGY_MATCHERS) - matchers)
    if missing:
        detail = (
            f"hooks preflight loaded {TAINT_AGY_HOOKS_REL!r} without matcher(s) for "
            f"{', '.join(missing)}"
        )
        return {"ok": False, "loaded": hooks, "detail": detail, "matchers_missing": missing}, detail
    return {"ok": True, "loaded": hooks, "detail": None, "matchers_missing": []}, None


def _taint_agy_enforcement(
    *,
    hooks_written: int,
    stdout_text: str,
    log_text: str,
    cwd: str | None = None,
    hook_digests: dict[str, str] | None = None,
) -> tuple[dict, str | None]:
    """E21: fail closed on the evidence, not on the fleet's own status.

    W1: `hook_digests` (what `_write_taint_agy_hooks` wrote, keyed by path
    relative to `cwd`) is re-hashed here. The hook files live in a writable
    worktree and are re-read on every tool call, so a lane that rewrote its
    own deny script mid-run would otherwise pass every other check; a
    changed, missing, or unreadable file fails the run. Both are keywords
    with defaults so an older caller (and every test that calls this
    directly) keeps working.

    Returns the receipt's `taint_enforcement` dict and, when the hooks did
    not visibly hold, the text `dispatch()` uses as the run's `error`."""
    init_event = agy_init_event(stdout_text)
    raw_tools = init_event.get("tools") if init_event is not None else None
    tools_seen = list(raw_tools) if isinstance(raw_tools, list) else []
    uncovered = _uncovered_agy_tools(tools_seen)
    denied_calls = _count_denied_calls(stdout_text)
    log_match = _TAINT_AGY_LOG_RE.search(log_text)
    hooks_loaded = int(log_match.group(1)) if log_match else None
    modified: list[str] = []
    if hook_digests and cwd is not None:
        for rel_path, digest in sorted(hook_digests.items()):
            if _sha256_file(Path(cwd) / rel_path) != digest:
                modified.append(rel_path)
    receipt = {
        "hooks_written": hooks_written,
        "hooks_loaded": hooks_loaded,
        "tools_seen": tools_seen,
        "uncovered": uncovered,
        "denied_calls": denied_calls,
        "hook_digests": dict(hook_digests or {}),
        "hooks_modified": modified,
    }
    if modified:
        # W1: checked before the log line, because a rewritten hook script
        # makes every other piece of evidence here worth nothing.
        return receipt, f"taint hooks modified during the run: {', '.join(modified)}"
    if log_match is None:
        return receipt, "no 'loaded N named hooks' line in agy.log"
    # agy counts named hooks per hooks.json file, not per matcher: the deny
    # file conductor writes is exactly one named hook however many tools it
    # covers (a tainted Gemini review lane on the F10 anti-slop consumer
    # read "loaded 1 named hooks from 1 hooks.json file(s)" for 30 matchers
    # and was wrongly failed against 30). Zero is the malformed-file signal
    # the live probe found; the per-matcher check is the preflight's.
    if hooks_loaded < 1:
        return receipt, f"agy loaded {hooks_loaded} named hook(s); the hooks file did not parse"
    if uncovered:
        return receipt, f"uncovered tool(s) reach outside the worktree: {', '.join(uncovered)}"
    return receipt, None


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
    taint: dict | None,
    agent: dict | None,
    fleet_version: str | None = None,
) -> dict:
    """The statement A5 signs into `attestation.json`: what conductor can
    check about this one dispatch without trusting the fleet's own report.

    `fleet_version` (E22) is a keyword with a default so an older caller
    (and every existing test that builds this statement by hand) keeps
    working unchanged; `conductor attest` verifies a statement missing it
    the same as any other older receipt, since verification checks the
    signature, not a fixed field set.
    """
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
        "taint": taint,
        "agent": agent,
        "cwd": cwd,
        "base_commit": base_commit,
        "tip_commit": tip_commit,
        "source_diff_sha256": attest.file_sha256(diff_path) if diff_path else None,
        "test_surface": test_surface,
        "gate": _gate_summary(tests_dict, surface_state, test_command),
        "reproduce_verdict": (reproduce_state or {}).get("verdict"),
        "fleet_version": fleet_version,
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
    env: dict[str, str] | None = None,
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
        env=env,
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
    env: dict[str, str] | None = None,
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
        env=env,
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
    env: dict[str, str] | None = None,
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
            git_env = os.environ.copy()
            git_env["GIT_INDEX_FILE"] = str(index)
            try:
                seeded = subprocess.run(
                    ["git", "read-tree", base_sha],
                    cwd=root,
                    env=git_env,
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
                    env=git_env,
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
                    env=git_env,
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
            str(worktree / relative_cwd), command, timeout=timeout, stop=stop, env=env
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
    env: dict[str, str] | None = None,
    inherited_check: str | None = None,
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

    E16: a `stage: adversarial` write dispatch runs the same transplant
    against its own base (the lane it attacks), but reads the verdict the
    other way round -- a failing gate means the lane found a defect
    (`reproduced`), a passing one means it found nothing (`not-reproduced`),
    and neither fails the lane; only a `no-check` (no gate, no base, no
    test-surface change at all) does. Before any of that, a clean run that
    changed anything outside the test surface fails the dispatch outright --
    an adversarial lane's whole deliverable is the test, never the fix.

    `inherited_check` (E16) names an adversarial lane a `stage: fix` dispatch
    is built on whose own final attempt already reproduced something: the
    failing test already sits at this dispatch's base commit, so its own
    reproduce step is satisfied without demanding a fresh test-surface change
    of its own -- the verdict reads `inherited` and the ordinary gate (run
    after this returns) is where that inherited test must now pass.
    """
    if spec.stage not in ("fix", "adversarial"):
        return _reproduce_skip("skipped", "stage is not fix"), None
    if spec.mode != "write":
        return _reproduce_skip("skipped", "mode is read"), None
    if timed_out or error is not None or exit_code != 0 or fleet_errored:
        return _reproduce_skip("skipped", "dispatch did not complete cleanly"), None
    moved = not compare(spec.cwd, before, GitState.capture(spec.cwd)).no_op
    if not moved:
        return _reproduce_skip("skipped", "the fleet made no changes"), None
    if spec.stage == "fix" and inherited_check:
        return (
            {
                "ran": False,
                "exit_code": None,
                "timed_out": False,
                "tail": f"inherited from adversarial lane '{inherited_check}'",
                "worktree": "",
                "patch_bytes": 0,
                "verdict": "inherited",
            },
            None,
        )
    subject = "fix" if spec.stage == "fix" else "adversarial lane"
    if spec.stage == "adversarial" and before.head:
        assert surface_before is not None  # every write dispatch on a repo captures it
        outside = changed_paths_since(spec.cwd, before.head, exclude=surface_before.patterns)
        if outside:
            message = f"adversarial lane changed source: {_test_surface_note(outside)}"
            return _reproduce_skip("skipped", message), message
    if not test_command:
        return (
            _reproduce_skip("no-check", "no gate set"),
            f"{subject} without a reproducing check: no gate set",
        )
    if not before.head:
        return (
            _reproduce_skip("no-check", "no base commit to re-run against"),
            f"{subject} without a reproducing check: no base commit to re-run against",
        )
    if surface_state is None or not surface_state["touched"]:
        return (
            _reproduce_skip("no-check", "no test-surface change"),
            f"{subject} without a reproducing check: no test-surface change",
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
        env=env,
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
            f"{subject} without a reproducing check: reproduce gate could not run: "
            + outcome.get("tail", ""),
        )
    if outcome.get("timed_out"):
        # A gate that never finished proves nothing either way.
        return (
            {**outcome, "verdict": "no-check"},
            f"{subject} without a reproducing check: reproduce gate timed out",
        )
    if _gate_passed(outcome, None):
        state = {**outcome, "verdict": "not-reproduced"}
        if spec.stage == "adversarial":
            # Neither verdict fails an adversarial lane; the caller still
            # withholds the commit (see dispatch()'s reproduce_blocks_commit).
            return state, None
        return state, "reproduce gate passed on the base: the check does not reproduce the finding"
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


def _operator_global_excludes(worktree: str) -> Path | None:
    """The operator's own global excludes file, read before any per-lane
    `core.excludesFile` override replaces it.

    `git config --get core.excludesFile` is queried before the worktree
    scope is ever written, so it can only resolve to a repo- or user-level
    value -- global config, or (Git's fallback when nothing is configured)
    `$XDG_CONFIG_HOME/git/ignore` or `~/.config/git/ignore`, whichever
    exists.
    """
    configured = git_run(worktree, "config", "--get", "core.excludesFile")
    if configured.returncode == 0 and configured.stdout.strip():
        path = Path(configured.stdout.strip()).expanduser()
        if path.is_file():
            return path
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    candidates = []
    if xdg_config:
        candidates.append(Path(xdg_config) / "git" / "ignore")
    candidates.append(Path.home() / ".config" / "git" / "ignore")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _apply_include(
    spec: Spec,
    iso: worktrees.Isolation,
    home: Path,
    run_id: str,
    extra_excludes: list[str] | None = None,
) -> tuple[list[str], list[str], Path | None]:
    """Copy `spec.include`'s untracked, repo-relative paths into the
    worktree and keep them untracked there too.

    A worktree-scoped `core.excludesFile` does the keeping, never the shared
    `info/exclude`: every worktree of one repo shares that file, so writing
    to it would leak this lane's include pattern into the next one. The file
    is seeded with the operator's own global excludes before the include
    patterns are appended, so setting it does not shadow whatever the
    operator already globally ignores for the run's duration. Returns the
    paths actually copied, notes for paths missing from the checkout, and
    the external exclude-list file's path (or None if nothing was copied),
    which the caller removes when the dispatch ends. `extra_excludes` (E21)
    are worktree-relative paths conductor itself wrote into the worktree
    (the taint deny hook files) that must stay untracked the same way, with
    or without an `include`.

    Raises DispatchRefused for a path Git already tracks: copying it would
    smuggle an uncommitted edit past the base commit a reviewer diffs
    against.
    """
    included: list[str] = []
    notes: list[str] = []
    to_copy: list[str] = []
    for rel in spec.include or []:
        source = Path(iso.repo) / rel
        if not source.exists():
            notes.append(f"include: {rel} does not exist in the checkout")
            continue
        tracked = git_run(iso.repo, "ls-files", "--error-unmatch", "--", rel)
        if tracked.returncode == 0:
            raise DispatchRefused(f"include: {rel} is tracked; the worktree already has it")
        to_copy.append(rel)
    extra = list(extra_excludes or [])
    if not to_copy and not extra:
        return included, notes, None

    # `extensions.worktreeConfig` is a one-time repository setting, like
    # `.git/worktrees` itself: once another lane has enabled it, leaving it
    # on is required, not just harmless, because a concurrent lane's own
    # worktree-scoped config depends on it staying enabled.
    git_run(iso.worktree, "config", "extensions.worktreeConfig", "true")
    global_excludes = _operator_global_excludes(iso.worktree)
    exclude_lines = []
    if global_excludes is not None:
        exclude_lines.append(global_excludes.read_text())
    exclude_lines.append("\n".join([*extra, *to_copy]) + "\n")

    exclude_file = home / "worktrees" / f"{run_id}-include-exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    exclude_file.write_text("\n".join(exclude_lines))
    git_run(iso.worktree, "config", "--worktree", "core.excludesFile", str(exclude_file))
    for rel in to_copy:
        source = Path(iso.repo) / rel
        dest = Path(iso.worktree) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(source, dest)
        included.append(rel)
    return included, notes, exclude_file


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
    lane: str | None = None,
    mission: str | None = None,
    prompt_versions: dict[str, str] | None = None,
    inherited_check: str | None = None,
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

    `lane` and `mission` (E11) name the mission lane and mission id that
    made this dispatch, recorded on the receipt so `report.py` can group by
    them without joining through the mission snapshot; both are None for a
    plain dispatch outside a mission.

    `prompt_versions` (E17) is the caller's own hint of which named,
    conductor-authored prompt texts (`prompts.prompt_versions()`) it already
    folded into `spec.prompt` -- a mission's collate, resolve, or rank
    contract. This dispatch adds its own `checklist_contract` id to that map
    when `spec.verdict` is set, and the union lands on the receipt
    unconditionally, empty when neither applies.

    `inherited_check` (E16) names an adversarial lane this `stage: fix`
    dispatch is built on whose own final attempt already reproduced
    something; see `_reproduce_receipt`'s own docstring for what that changes.
    """
    criteria = spec.verdict
    spec.validate()
    if spec.fleet == "script":
        # E6: there is no stream for a breaker to read, so every ceiling it
        # would otherwise watch is forced off here -- not just left at
        # whatever the caller's Spec happened to carry.
        spec = _replace(
            spec, stall_timeout=0, loop_limit=0, max_tool_calls=0, tool_idle_timeout=0
        )
    isolate = isolate or base_ref is not None
    fleet = FLEETS[spec.fleet]
    model_id = fleet.model(spec.model).id_for(spec.effort)
    timeout = spec.resolved_timeout()
    # E22: captured once, before spawn, so every receipt this dispatch can
    # produce -- refused, dry-run, or spawned -- carries the same version.
    fleet_version = cli_version(spec.fleet)
    prompt_versions = dict(prompt_versions or {})

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = home or conductor_home()
    run_id, run_dir = claim_dir(base / "runs", f"{stamp}-{spec.fleet}-{_slug(spec.prompt)}")

    if criteria is not None:
        # E17: deferred import -- prompts.py imports mission.py at module
        # level, so importing it up front here would cycle back through
        # mission.py's own `from .runner import ...`.
        from . import prompts as prompts_mod

        prompt_versions["checklist_contract"] = prompts_mod.prompt_versions()["checklist_contract"]
        # A caller cannot accidentally drift the schema away from the
        # checklist: conductor writes both from the same Criterion objects.
        schema_path = run_dir / "verdict.schema.json"
        schema_path.write_text(json.dumps(checklist_schema(criteria), indent=2))
        # F13: `--json-schema` on an antigravity read lane risks a second
        # turn that writes files (fleets.Spec._validate_schema refuses it
        # outright). The checklist contract above already embeds the same
        # schema as prompt text and parse_verdict falls back to extracting
        # embedded JSON, so a verdict lane on antigravity drops the flag
        # here instead of losing the checklist mechanism entirely.
        schema_for_spec = (
            None if (spec.fleet == "antigravity" and spec.mode == "read") else str(schema_path)
        )
        spec = _replace(
            spec,
            prompt=spec.prompt + checklist_contract(criteria),
            schema=schema_for_spec,
            verdict=None,
        )

    # Refusals need repository identity and dirty-file presence, not a hash
    # of every operator-owned untracked byte in the shared checkout.
    checkout_before = GitState.capture(spec.cwd, content=False)
    # E21: the deny hook files live in a conductor-owned worktree, nowhere
    # else -- a tainted antigravity dispatch that cannot get one is refused
    # before spawn rather than run unguarded.
    tainted_agy = spec.taint and spec.fleet == "antigravity"
    if tainted_agy and not dry_run and (not isolate or not checkout_before.is_repo):
        error = (
            "taint on antigravity refused: dispatch must isolate into a git repository; "
            "the hook files live in a conductor-owned worktree, nowhere else"
        )
        result = _refused_result(
            run_id,
            spec,
            model_id,
            timeout,
            run_dir,
            None,
            error,
            lane=lane,
            mission=mission,
            fleet_version=fleet_version,
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result
    if spec.mode == "write" and not checkout_before.is_repo and not dry_run:
        error = "write dispatch refused: cwd is not a git repository; only read mode may run there"
        result = _refused_result(
            run_id,
            spec,
            model_id,
            timeout,
            run_dir,
            None,
            error,
            lane=lane,
            mission=mission,
            fleet_version=fleet_version,
            prompt_versions=prompt_versions,
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result
    if commit_message and not isolate and checkout_before.dirty_files and not dry_run:
        error = "commit refused: the checkout has uncommitted changes; use --isolate"
        result = _refused_result(
            run_id,
            spec,
            model_id,
            timeout,
            run_dir,
            None,
            error,
            lane=lane,
            mission=mission,
            fleet_version=fleet_version,
            prompt_versions=prompt_versions,
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    # Isolation first, because everything after this line (argv, snapshots,
    # commit, tests) must see the worktree as the working directory, not the
    # shared checkout the caller named.
    iso: worktrees.Isolation | None = None
    taint_hook_paths: list[str] = []
    taint_hook_digests: dict[str, str] = {}
    if isolate and not dry_run:
        iso = worktrees.create(spec.cwd, run_id, base / "worktrees", base_ref=base_ref)
        if iso.active:
            # A cwd inside the repo stays the same subdirectory inside the
            # worktree; the fleet was pointed at that directory for a reason.
            spec = _replace(spec, cwd=worktrees.mirror_path(spec.cwd, iso))
            if tainted_agy:
                taint_hook_paths, taint_hook_digests = _write_taint_agy_hooks(
                    spec.cwd, iso, taint_shell=spec.taint_shell
                )
        elif spec.mode == "write" or base_ref is not None or tainted_agy:
            # The caller asked for a private tree and cannot have one. For a
            # write, running in the shared checkout instead is the collision
            # isolation exists to prevent, so it is refused before spawn. A
            # read dispatch changes nothing and may proceed in place -- unless
            # it is a tainted antigravity dispatch, which has nowhere else to
            # put its deny hook files.
            result = _refused_result(
                run_id,
                spec,
                model_id,
                timeout,
                run_dir,
                iso,
                f"isolation failed: {iso.reason}",
                lane=lane,
                mission=mission,
                fleet_version=fleet_version,
                prompt_versions=prompt_versions,
            )
            (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
            return result

    # The fleet writes its final answer where the caller asked, or beside the
    # run if it did not ask. Codex is the only fleet that takes this as a flag.
    spec_with_paths = spec
    if spec.fleet == "codex" and not spec.last_message:
        spec_with_paths = _replace(spec, last_message=str(run_dir / "last_message.txt"))

    argv = build_argv(spec_with_paths)
    # F12: read straight off the real argv rather than re-deriving
    # `fleets._build_claude`'s own branching here, so the receipt can never
    # disagree with what actually ran.
    permission_mode: str | None = None
    restricted_flag = False
    if spec.fleet == "claude":
        if "--permission-mode" in argv:
            permission_mode = argv[argv.index("--permission-mode") + 1]
        restricted_flag = "--restricted" in argv
    if tainted_agy:
        # E21: needs this run's own directory, which no Spec field carries;
        # appended here rather than threaded into build_argv's signature (see
        # the docstring there) so every fake fleet a test installs -- real
        # argv or not -- still gets it, and no monkeypatched build_argv with
        # the old one-argument shape breaks.
        argv = [*argv, "--log-file", str(run_dir / "agy.log")]

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
            git_verdict=GitVerdict(
                checked=False, notes=["dry run", *_version_note(fleet_version)]
            ).to_dict(),
            dry_run=True,
            fleet_version=fleet_version,
            stage=spec.stage,
            lane=lane,
            mission=mission,
            deliverable=_check_deliverable(spec, dry_run=True),
            prompt_versions=prompt_versions,
            permission_mode=permission_mode,
            restricted=restricted_flag,
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    # C4: per-lane include, ports, and setup, in that order -- include needs
    # no port, and setup's env needs whatever ports were claimed. All three
    # run before `before` is captured, so a fixture setup writes (a seeded
    # scratch DB, an installed dependency) become part of the baseline
    # instead of misread as the fleet's own work.
    claimed_ports: list[int] = []
    included_paths: list[str] = []
    lane_notes: list[str] = []
    include_exclude_file: Path | None = None
    setup_outcome: TestOutcome | None = None
    lane_env_used = bool(spec.ports or spec.setup or spec.teardown or spec.include)

    def _lane_env(teardown_outcome: TestOutcome | None = None) -> dict | None:
        if not lane_env_used:
            return None
        return {
            "ports": list(claimed_ports),
            "setup": setup_outcome.to_dict() if setup_outcome is not None else None,
            "teardown": teardown_outcome.to_dict() if teardown_outcome is not None else None,
            "included": list(included_paths),
        }

    def _bail(error: str) -> Result:
        ports_mod.release(base, claimed_ports)
        if include_exclude_file is not None:
            include_exclude_file.unlink(missing_ok=True)
        if iso is not None:
            worktrees.release(iso)
        result = _refused_result(
            run_id,
            spec,
            model_id,
            timeout,
            run_dir,
            iso,
            error,
            extra_notes=lane_notes,
            lane_env=_lane_env(),
            lane=lane,
            mission=mission,
            fleet_version=fleet_version,
            prompt_versions=prompt_versions,
            taint_enforcement=(
                {"preflight": taint_preflight} if taint_preflight is not None else None
            ),
        )
        (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
        return result

    # F13: the free `/hooks` query, before any port/setup/paid-turn spend --
    # the deny hook `_write_taint_agy_hooks` wrote above must already show up
    # enabled, or this dispatch is refused now rather than after a run that
    # would fail the same way a dollar later (`_taint_agy_enforcement` below
    # is the second, after-the-run source of the same evidence).
    taint_preflight: dict | None = None
    if tainted_agy:
        taint_preflight, preflight_problem = _taint_agy_preflight(spec.cwd, run_dir)
        if preflight_problem is not None:
            return _bail(f"taint hooks not enforced: {preflight_problem}")

    if spec.include or taint_hook_paths:
        if iso is not None and iso.active:
            try:
                included_paths, notes, include_exclude_file = _apply_include(
                    spec, iso, base, run_id, extra_excludes=taint_hook_paths
                )
                lane_notes.extend(notes)
            except DispatchRefused as exc:
                return _bail(str(exc))
        elif spec.include:
            lane_notes.append("include ignored: dispatch is not isolated")

    if spec.ports:
        claimed_ports, port_error = ports_mod.claim(spec.ports, base, run_id)
        if port_error is not None:
            return _bail(port_error)

    worktree_env = iso.worktree if (iso is not None and iso.active) else spec.cwd
    env = dict(os.environ)
    env["CONDUCTOR_RUN_ID"] = run_id
    env["CONDUCTOR_WORKTREE"] = worktree_env
    # F7: every dispatched process, fleet and gate alike, carries this so
    # `land.py` can refuse to run inside a lane's own environment -- land is
    # the lead's hands, never a fleet's, and this is what proves the caller
    # is not one.
    env["CONDUCTOR_LANE"] = "1"
    for index, port in enumerate(claimed_ports, start=1):
        env[f"CONDUCTOR_PORT_{index}"] = str(port)
    if claimed_ports:
        env["CONDUCTOR_PORTS"] = ",".join(str(port) for port in claimed_ports)

    if spec.setup:
        setup_outcome = run_tests(
            spec.cwd, spec.setup, timeout=SETUP_TIMEOUT, stop=stop_requested, env=env
        )
        if not setup_outcome.passed:
            if setup_outcome.timed_out:
                return _bail("setup timed out")
            if setup_outcome.interrupted:
                return _bail(
                    "interrupted: stop requested during setup; process group killed"
                )
            return _bail(f"setup failed: exit {setup_outcome.exit_code}")

    # D9: flipped the moment the fleet's own process is over and the paid
    # bytes are on disk. Everything after that point -- parsing the envelope,
    # checking the deliverable, settling the budget -- is conductor reading a
    # fleet's output, and a crash there must still leave a receipt.
    post_wait = False
    try:
        before = GitState.capture(spec.cwd)
        try:
            surface_before = test_surface(spec.cwd, spec.test_surface) if before.is_repo else None
        except ValueError as exc:
            return _bail(f"test surface refused: {exc}")
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
            Budget(cap_usd=spec.cap_usd, enforcement=fleet.cap, grace_usd=spec.cap_grace_usd)
            if spec.cap_usd is not None
            # E6: priced at zero and verified, never unpriced -- a script
            # dispatch never sets cap_usd (Spec.validate refuses it), so it
            # would otherwise carry no budget block at all.
            else Budget(cap_usd=None, enforcement=fleet.cap, free=True)
            if spec.fleet == "script"
            else None
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
                if cancel is not None and cancel.is_set():
                    # D12: cancelled while this dispatch was still queued.
                    # Nothing spawns, and the receipt reads `cancelled`, not
                    # `interrupted`: a stop is not what ended it.
                    cancelled = True
                    raise Interrupted(f"cancelled: {cancel_reason}; not spawned")
                if stop_requested():
                    raise Interrupted("stop requested before the fleet was spawned")
                proc = subprocess.Popen(
                    argv,
                    cwd=spec.cwd,
                    stdin=subprocess.PIPE if spec.fleet == "script" else subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                    env=env,
                )
                _register_live_group(proc.pid)
                if spec.fleet == "script" and proc.stdin is not None:
                    # E6: the prompt, when there is one, is delivered on
                    # stdin; an empty prompt just closes it at once. Fed
                    # from a thread, not written here directly: a prompt
                    # bigger than the pipe's kernel buffer blocks until the
                    # command reads it, and a command that never reads
                    # stdin at all (sleeping, or simply not looking) would
                    # otherwise block this dispatch's own timeout from ever
                    # starting to enforce itself. The thread is daemonic and
                    # unjoined -- `_wait`'s own kill closes the command's end
                    # of the pipe, which is what actually unblocks a stuck
                    # write, and nothing here needs to wait for that.
                    def _feed_stdin(pipe, data: bytes) -> None:
                        try:
                            pipe.write(data)
                        except (BrokenPipeError, OSError):
                            pass
                        finally:
                            try:
                                pipe.close()
                            except OSError:
                                pass

                    threading.Thread(
                        target=_feed_stdin,
                        args=(proc.stdin, spec.prompt.encode()),
                        daemon=True,
                    ).start()
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
                if cancelled:
                    # D12's pre-spawn cancel already said what ended this.
                    error = str(exc)
                else:
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
                    # over_cap saw a figure
                    assert watcher is not None and watcher.usage is not None
                    error = (
                        f"budget cap hit: ${watcher.usage.cost_usd:.4f} estimated against a "
                        f"${spec.cap_usd:.4f} cap; process group killed"
                    )
                elif breaker_reason is not None:
                    error = f"{breaker_reason}; process group killed"

        duration = time.monotonic() - started
        post_wait = True
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
                resume_note = (
                    f"resume failed: fleet reported session {got}, requested {spec.resume}"
                )
                # The guard must not hide the timeout, stop, cap, or spawn failure
                # that explains why the fleet could not report the requested id.
                if error is None:
                    error = resume_note

        # F12: a fleet-reported success (exit 0, `subtype: success`) that
        # silently denied a write lane's own tool call is not success -- the
        # fleet did not do what it was asked, and its own summary will say it
        # did. A read lane keeps its denials as a note on the git verdict
        # (below), never a failure: a read lane's tool set is meant to be
        # thin, and a denial there is expected, not a defect.
        denied_tools = sorted(
            {
                d["tool_name"]
                for d in output.permission_denials
                if isinstance(d, dict)
                and isinstance(d.get("tool_name"), str)
                and d["tool_name"]
            }
        )
        if denied_tools and spec.mode == "write" and error is None:
            error = f"permission denied: {', '.join(denied_tools)}"

        claude_init: dict | None = None
        if spec.fleet == "claude" and (spec.agent is not None or restricted_flag):
            claude_init = claude_init_event(_read(stdout_path))

        agent_result: dict | None = None
        if spec.agent is not None:
            agent_result, agent_problem = _agent_verdict(spec.agent, claude_init)
            if agent_problem is not None:
                if claude_init is None:
                    # A stream cut short before its own init event is not, on
                    # its own, evidence the persona failed to apply -- it is
                    # usually just evidence of whatever else ended the run
                    # early, and that reason must not be replaced. "Looks
                    # complete" means a result event was actually parsed (an
                    # entirely empty stream is `parsed=False` with no error
                    # of its own, and must not be mistaken for completeness).
                    run_looks_complete = (
                        exit_code == 0 and error is None and output.parsed and not output.error
                    )
                    if run_looks_complete:
                        if error is None:
                            error = agent_problem
                    else:
                        agent_result["applied"] = None
                elif error is None:
                    error = agent_problem

        if restricted_flag:
            # F12: `--restricted`'s own promise, verified on bytes the same
            # way E21 verifies agy's deny hooks: neither Bash nor WebFetch may
            # survive into the init event's own tool list. No init event (a
            # stream cut short for an unrelated reason) is not, on its own,
            # evidence the flag failed -- same reasoning as the agent check
            # above -- so this only ever fails closed on positive evidence.
            restricted_tools = claude_init.get("tools") if claude_init is not None else None
            restricted_tools = restricted_tools if isinstance(restricted_tools, list) else []
            present = [name for name in ("Bash", "WebFetch") if name in restricted_tools]
            if present and error is None:
                error = (
                    f"restricted mode not enforced: {', '.join(present)} present in the "
                    "init tool list"
                )

        taint_enforcement: dict | None = None
        if spec.fleet == "claude" and spec.taint and restricted_flag:
            # F12: a tainted, restricted claude read lane is guarded by both
            # mechanisms at once -- the disallowedTools deny list (D2) and
            # the confinement --restricted itself enforces -- recorded
            # together so the receipt shows both, not just the deny list.
            taint_enforcement = {
                "disallowed_tools": list(taint_disallowed_tools(spec.taint_shell)),
                "restricted": True,
            }
        if tainted_agy:
            # One entry per matcher `fleets.taint_hook_files` writes.
            hooks_written = len(taint_agy_matchers(spec.taint_shell))
            taint_enforcement, taint_problem = _taint_agy_enforcement(
                hooks_written=hooks_written,
                stdout_text=_read(stdout_path),
                log_text=_read(run_dir / "agy.log"),
                cwd=spec.cwd,
                hook_digests=taint_hook_digests,
            )
            # F13: the free preflight ran before this paid turn spawned and
            # already passed (a failing preflight bails before spawn, above)
            # -- folded in here so the receipt carries both sources beside
            # each other.
            taint_enforcement["preflight"] = taint_preflight
            if taint_problem is not None and error is None:
                error = f"taint hooks not enforced: {taint_problem}"

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
            env=env,
            inherited_check=inherited_check,
        )
        # E16: an adversarial lane's `not-reproduced` verdict does not fail
        # the lane (`reproduce_error` is None for it) but must still keep the
        # commit off the branch, same as `no-check` -- only `reproduced`,
        # `inherited` (a fix building on a reproduced adversarial base), and
        # every other stage's `skipped` (which never had a commit to block
        # anyway) may land. This is a superset of the old `reproduce_error is
        # not None` check: for a `fix` dispatch the two verdicts that ever set
        # `reproduce_error` (`no-check`, `not-reproduced`) are exactly the two
        # excluded here, so a fix lane's behavior is unchanged.
        reproduce_blocks_commit = reproduce_state.get("verdict") not in (
            "reproduced",
            "inherited",
            "skipped",
        ) or (
            # An adversarial lane that changed source outside the test surface
            # is refused with `verdict: skipped` (see `_reproduce_receipt`),
            # which the exclusion above would otherwise treat as landable --
            # right for every other skip (nothing to gate), wrong here: the
            # lane's whole deliverable is the test, never the fix, so this
            # must block a self-commit exactly as it blocks conductor's own.
            spec.stage == "adversarial"
            and reproduce_error is not None
        )
        if reproduce_error is not None:
            # A fix that reproduces nothing must not land: no commit, and its
            # ordinary gate (own or clean) is skipped below, same as any other
            # error caught before this point.
            error = reproduce_error
            if reproduce_state.get("interrupted"):
                interrupted = True

        # E1: after the fleet exits, before the gate. A dry run never reaches
        # here (dispatch() returns earlier), so this is always a real check.
        deliverable_state = _check_deliverable(spec, dry_run=False)
        deliverable_path: str | None = None
        if deliverable_state is not None and deliverable_state.get("exists"):
            # Persisted now, while spec.cwd (the worktree, when isolated) still
            # exists: a clean isolated worktree is released before this dispatch
            # returns, and a downstream lane's {{lanes.<name>.deliverable}} must
            # still be able to read it afterwards.
            dest = run_dir / "deliverable"
            try:
                copy_no_follow(Path(spec.cwd) / deliverable_state["path"], dest)
                deliverable_path = str(dest)
            except OSError:
                pass

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
            # F15 mission 2 item 3: a deliverable declared `commit: false` is
            # a receipt, not source -- excluded from the harness's own
            # commit (it is still checked on the filesystem and copied to
            # deliverables/ above, before this point).
            exclude: tuple[str, ...] = ()
            if spec.deliverable is not None and spec.deliverable.get("commit") is False:
                exclude = (spec.deliverable["path"],)
            commit = commit_work(spec.cwd, commit_message, exclude=exclude)
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

        # F3: a read lane's own gate and the clean gate re-check bytes a build
        # lane already gated. Decided on the bytes comparison taken here, before
        # either gate runs, never on the final `git_verdict` below (that capture
        # happens after the gate, and the gate's own scratch files -- pytest
        # cache, `.pyc`, ... -- must not be mistaken for the lane's own work).
        # A no-op or a change that is exactly the lane's declared E1 deliverable
        # (the same exemption the read-only check already applies) skips both;
        # a read lane that moved anything else is gated exactly as today.
        read_gate_skip: dict | None = None
        if (
            spec.mode == "read"
            and test_command
            and not timed_out
            and error is None
            and spec.stage != "adversarial"
        ):
            pre_gate_after = GitState.capture(spec.cwd)
            pre_gate_verdict = compare(spec.cwd, before, pre_gate_after)
            if (
                pre_gate_verdict.checked
                and not pre_gate_verdict.vanished
                and (
                    pre_gate_verdict.no_op
                    or _read_deliverable_only(spec, before, pre_gate_after, pre_gate_verdict)
                )
            ):
                read_gate_skip = {
                    "skipped": "read lane, source unchanged",
                    "command": test_command,
                }

        tests: TestOutcome | None = None
        # E16: an adversarial lane's own gate and the clean gate both fail by
        # construction (its deliverable is a test that fails on its own
        # tree) -- neither runs here; `_gate_passed`/`_gate_summary` already
        # read "nothing ran" as passed, so this alone never blocks the
        # commit (`reproduce_blocks_commit`, above, is what actually gates
        # it). test_policy is `allow` on every adversarial attempt (enforced
        # at load), so the clean-gate block below is already skipped too.
        if (
            test_command
            and not timed_out
            and error is None
            and spec.stage != "adversarial"
            and read_gate_skip is None
        ):
            tests = run_tests(
                spec.cwd, test_command, timeout=GATE_TIMEOUT, stop=stop_requested, env=env
            )
            if tests.interrupted:
                interrupted = True
                error = "interrupted: stop requested during the gate; process group killed"
        if surface_state is not None and spec.test_policy == "clean" and read_gate_skip is None:
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
                    env=env,
                )
                clean_gate = surface_state["clean_gate"]
                if clean_gate.get("interrupted"):
                    interrupted = True
                    error = (
                        "interrupted: stop requested during the clean gate; "
                        "process group killed"
                    )

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

        if reproduce_blocks_commit and commit and commit.committed:
            # A fix that never reproduced anything, or an adversarial lane
            # whose test passed on its own base, must not land, even when the
            # fleet committed its own work directly instead of leaving it staged
            # for conductor's own commit_work to pick up. An adversarial lane
            # gets a hard discard, not a soft one: a later lane must still be
            # able to build cleanly on its unchanged base (see `discard`).
            if spec.stage == "adversarial":
                commit = discard(spec.cwd, commit, before.head)
            else:
                commit = uncommit(spec.cwd, commit, before.head)

        after = GitState.capture(spec.cwd)
        git_verdict = compare(spec.cwd, before, after)
        if _read_deliverable_only(spec, before, after, git_verdict):
            # E1: a read lane may move exactly its declared deliverable. Any
            # other change -- a second file, a commit, a branch move -- must
            # still fail the ordinary read-only check below.
            git_verdict.deliverable_only = True
            git_verdict.notes.append(
                f"read dispatch moved only its declared deliverable: "
                f"{spec.deliverable['path']}"
            )
        git_verdict.notes.extend(output.notes)
        if denied_tools:
            # F12: a write lane already failed on this above; a read lane's
            # denials are expected (a thin tool set is the point) and land
            # here as evidence, not a verdict.
            git_verdict.notes.append(
                f"permission denied ({len(output.permission_denials)}): "
                f"{', '.join(denied_tools)}"
            )
        if resume_note:
            git_verdict.notes.append(resume_note)
        if surface_state and surface_state["touched"]:
            changed = surface_state["changed"]
            git_verdict.notes.append(
                f"test surface changed: {len(changed)} file(s): {', '.join(changed[:10])}"
            )
            if spec.test_policy == "clean" and not test_command:
                git_verdict.notes.append("test surface changed with no gate to re-run")
        if spec.stage == "adversarial":
            # E16: the dispatch's own gate and clean gate never ran for this
            # stage -- its test is expected to fail on this tree by
            # construction, so there was nothing to gate; `reproduce` above
            # carries the verdict that actually judged this lane.
            git_verdict.notes.append(
                "adversarial lane: own gate and clean gate skipped, tests recorded as "
                "skipped -- its test is expected to fail on this tree by construction"
            )
        if commit and commit.deletions:
            deleted = ", ".join(commit.deletions[:10])
            git_verdict.notes.append(f"commit removed {len(commit.deletions)} file(s): {deleted}")
        if commit and not commit.committed and (
            commit.reason.startswith("gate failed") or spec.stage == "adversarial"
        ):
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

        # Teardown runs after the gate and the commit decision, ok or not: the
        # work is already judged, so its own outcome is a note, never a reason
        # to flip the verdict.
        teardown_outcome: TestOutcome | None = None
        if spec.teardown:
            teardown_outcome = run_tests(
                spec.cwd, spec.teardown, timeout=SETUP_TIMEOUT, stop=stop_requested, env=env
            )
            if teardown_outcome.timed_out:
                git_verdict.notes.append("teardown timed out")
            elif teardown_outcome.interrupted:
                git_verdict.notes.append(
                    "teardown interrupted: stop requested during teardown; process group killed"
                )
            elif teardown_outcome.exit_code != 0:
                git_verdict.notes.append(f"teardown failed: exit {teardown_outcome.exit_code}")
    except Exception as exc:  # noqa: BLE001 - D9 boundary, re-raised below
        ports_mod.release(base, claimed_ports)
        if include_exclude_file is not None:
            include_exclude_file.unlink(missing_ok=True)
        if iso is not None:
            worktrees.release(iso)
        if not post_wait:
            # Nothing was paid for yet, or the failure is conductor's own
            # setup: unchanged, it propagates.
            raise
        # D9: the fleet ran and the money is spent. What raised is conductor
        # reading its output -- a bare NaN in a usage figure, a verdict field
        # that is a list, a deliverable schema that is not an object. Without
        # a receipt here the run directory holds only stdout.log, which
        # neither `spend` nor `report` can see, so the spend goes missing and
        # the mission records "lane crashed" with no ledger entry. Write what
        # is known instead, and let the ordinary failure path judge it.
        return _parse_failure_result(
            exc,
            run_id=run_id,
            spec=spec,
            model_id=model_id,
            timeout=timeout,
            run_dir=run_dir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            exit_code=exit_code,
            timed_out=timed_out,
            duration=duration,
            watcher=watcher,
            budget=budget,
            iso=iso,
            lane_env=_lane_env(),
            lane=lane,
            mission=mission,
            fleet_version=fleet_version,
            prompt_versions=prompt_versions,
        )
    except BaseException:
        ports_mod.release(base, claimed_ports)
        if include_exclude_file is not None:
            include_exclude_file.unlink(missing_ok=True)
        if iso is not None:
            worktrees.release(iso)
        raise


    ports_mod.release(base, claimed_ports)
    if include_exclude_file is not None:
        include_exclude_file.unlink(missing_ok=True)
    if iso is not None:
        worktrees.release(iso)
        if iso.active:
            git_verdict.notes.append(f"isolated on branch {iso.branch}; {iso.reason}")
        else:
            git_verdict.notes.append(f"isolation requested but not applied: {iso.reason}")
    if lane_notes:
        git_verdict.notes.extend(lane_notes)
    git_verdict.notes.extend(_version_note(fleet_version))

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
        deliverable=deliverable_state,
        deliverable_path=deliverable_path,
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
        lane_env=_lane_env(teardown_outcome),
        error=error,
        # E16: an adversarial lane's `not-reproduced` verdict discards its
        # commit back to the base (see `discard`), which looks exactly like
        # an ordinary no-op to the check below -- it is not one (the fleet
        # moved real bytes; the lane just found nothing) and must not fail
        # the lane the way a genuine no-op would.
        no_op_ok=no_op_ok
        or (spec.stage == "adversarial" and reproduce_state.get("verdict") == "not-reproduced"),
        interrupted=interrupted,
        cancelled=cancelled,
        taint=(
            {
                "declared": True,
                "tools_denied": (
                    list(taint_agy_denied_tools(spec.taint_shell))
                    if spec.fleet == "antigravity"
                    else list(taint_disallowed_tools(spec.taint_shell))
                ),
                # D5: which shell policy actually ran -- "denied" (the
                # boundary) or "prefix" (the opt-in discouragement).
                "taint_shell": "prefix" if spec.taint_shell == "allow" else "denied",
            }
            if spec.taint
            else None
        ),
        taint_enforcement=taint_enforcement,
        agent=agent_result,
        stage=spec.stage,
        lane=lane,
        mission=mission,
        fleet_version=fleet_version,
        prompt_versions=prompt_versions,
        gate=read_gate_skip,
        permission_denials=list(output.permission_denials),
        permission_mode=permission_mode,
        restricted=restricted_flag,
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
            taint=result.taint,
            agent=result.agent,
            fleet_version=fleet_version,
        )
        try:
            key = attest.receipt_key(base)
            envelope = attest.sign(statement, key)
            attestation_file = run_dir / "attestation.json"
            attestation_file.write_text(json.dumps(envelope, indent=2))
            result.attestation_path = str(attestation_file)
        except (OSError, RuntimeError) as exc:
            # RuntimeError: the key helper lost a first-use race and never saw
            # a complete key. Either way the dispatch is done; only its
            # attestation is missing, and the receipt says so.
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
    *,
    extra_notes: list[str] | None = None,
    lane_env: dict | None = None,
    lane: str | None = None,
    mission: str | None = None,
    fleet_version: str | None = None,
    prompt_versions: dict[str, str] | None = None,
    taint_enforcement: dict | None = None,
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
        git_verdict=GitVerdict(
            checked=False, notes=[error, *(extra_notes or []), *_version_note(fleet_version)]
        ).to_dict(),
        isolation=iso.to_dict() if iso is not None else None,
        lane_env=lane_env,
        error=error,
        stage=spec.stage,
        lane=lane,
        mission=mission,
        fleet_version=fleet_version,
        prompt_versions=dict(prompt_versions or {}),
        taint_enforcement=taint_enforcement,
    )


def _parse_failure_result(
    exc: BaseException,
    *,
    run_id: str,
    spec: Spec,
    model_id: str,
    timeout: int,
    run_dir: Path,
    stdout_path: Path,
    stderr_path: Path,
    exit_code: int | None,
    timed_out: bool,
    duration: float,
    watcher: Watcher | None,
    budget: Budget | None,
    iso: worktrees.Isolation | None,
    lane_env: dict | None,
    lane: str | None,
    mission: str | None,
    fleet_version: str | None,
    prompt_versions: dict[str, str] | None,
) -> Result:
    """D9: a receipt for a run that was paid for and then failed while its
    own output was being read.

    The price is whatever is still recoverable: the watcher's last reading,
    priced from the table when the fleet reported no figure. When there is
    none, the receipt says so in as many words rather than reading as free --
    a missing price is a gap in the ledger, never $0.00 (prices.estimate).
    The full traceback goes to `parse-error.txt` beside the transcript; the
    one-line reason goes on the receipt, where `errors.error_kind` reads it
    as `parse`.
    """
    error = f"{PARSE_FAILURE_PREFIX}{type(exc).__name__}: {exc}"
    try:
        (run_dir / "parse-error.txt").write_text(
            "".join(traceback.format_exception(exc)),
        )
    except OSError:
        pass
    usage = watcher.poll() if watcher is not None else None
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
    notes = [error, "receipt written after the run; the tree was not judged"]
    if usage is None or usage.cost_usd is None:
        notes.append("no priced usage was recovered before the failure; this run is unpriced")
    if budget is not None:
        budget.settle(
            usage.cost_usd if usage is not None else None,
            killed=False,
            interrupted=False,
            fleet_status=None,
        )
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
        tail=_tail(stdout_path),
        spawned=True,
        git_verdict=GitVerdict(
            checked=False, notes=[*notes, *_version_note(fleet_version)]
        ).to_dict(),
        isolation=iso.to_dict() if iso is not None else None,
        lane_env=lane_env,
        usage=usage.to_dict() if usage is not None else None,
        budget=budget.to_dict() if budget is not None else None,
        error=error,
        stage=spec.stage,
        lane=lane,
        mission=mission,
        fleet_version=fleet_version,
        prompt_versions=dict(prompt_versions or {}),
    )
    (run_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2))
    return result


def _replace(spec: Spec, **changes) -> Spec:
    data = asdict(spec)
    data.update(changes)
    return Spec(**data)
