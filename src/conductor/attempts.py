"""The attempt lifecycle: parsing an attempt's fields into an `Attempt`, the
cascade and retry mission-level templates that build one, the human- and
script-lane field validators, the cascade's escalation summary, and the
resume-time checks that decide whether a lane's durable receipt may be
trusted without redispatching it.

Peer review 2026-09-07 (docs/review/2026-09-07-astra-notes.md, section 5 item
4) asked for this to be sliced out of mission.py by invariant, the same way
graph.py and approvals.py already were. Two invariants stay pinned by tests
that name them:

- every paid dispatch is counted once across a retry (`_run_receipt_spend`,
  which stays in mission.py, prices each retried attempt from its own run
  receipt, never twice across a lane's retries or a later resume);
- a rehearsal is never trusted on resume (`_trusted_lane` refuses a receipt
  whose last attempt was not `spawned`, and refuses one whose recorded
  artifact digest no longer matches the bytes on disk).

The attempt lifecycle proper -- actually dispatching a lane, its retry loop,
and folding a rerun's history into `previous_attempts` -- stays in
`_execute_mission` (`_run_attempts` and its closures `dispatch_one`,
`fresh_lane_result`, `settle`): they close over the scheduler's own local
state (the ledger, the running lock, the cancel events), and moving them
would change their call signature, which this slice forbids. `_run_receipt_spend`
stays too -- F20 owns it, and it reads `spend.effects`, never a `Lane` or an
`Attempt` directly.

Watch for names these functions use that stay in mission.py: `LaneResult`,
`Mission`, `Lane` (typing only, imported under `TYPE_CHECKING`), `_json_object`,
`_human_deliverable_path`, `_reject_unknown`, and `git_run` (still reached as
`mission.git_run`, not imported here directly -- see below). Every one of
these is reached with a lazy `from . import mission as mission_mod` inside
the function that needs it, never at module level: mission.py re-exports
every name in this module (so `conductor.mission._name` keeps resolving for
existing callers and tests), which makes a module-level import back from here
a cycle. A test pins that this module never imports `conductor.mission` at
module level.

The monkeypatch hazard: `tests/test_cascade.py` patches `conductor.mission.git_run`
in two tests that exercise `_trusted_lane`'s tip-commit check under a refused
or missing git spawn. Resolved the same way slice 2 (approvals.py) resolved
`datetime`: `_git_answer` reaches `git_run` through `mission_mod.git_run`,
looked up lazily, so the patch on `conductor.mission.git_run` still applies
and neither test's patch target needed to change.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from . import attest
from .errors import KINDS
from .fleets import TAINT_SHELL_MODES, Spec
from .graph import MissionInvalid
from .verdicts import Criterion, parse_checklist
from .verify import GIT_UNRUN

if TYPE_CHECKING:
    from .mission import Lane, LaneResult, Mission

# Fields an attempt may set, in the order they cascade mission -> lane -> attempt.
_INHERITED = (
    "fleet",
    "model",
    "effort",
    "mode",
    "cwd",
    "prompt",
    "timeout",
    "stall_timeout",
    "loop_limit",
    "max_tool_calls",
    "tool_idle_timeout",
    "test",
    "test_policy",
    "test_surface",
    "commit",
    "isolate",
    "cap_usd",
    "no_op_ok",
    "schema",
    "verdict",
    "ports",
    "setup",
    "teardown",
    "include",
    "agent",
    "deliverable",
    "restricted",
    "taint_shell",
    "command",
)
_BREAKER_KEYS = frozenset({"stall_timeout", "loop_limit", "max_tool_calls", "tool_idle_timeout"})

# Every key a mission file may use, per object. A typo (`need` for `needs`)
# would otherwise silently turn a dependent lane into a root.
# E24: cap_grace_usd is deliberately not in _INHERITED -- it is stated per
# lane or per attempt and never cascades (mission_from_dict and
# _parse_cascade both refuse it above the lane; _attempt_fields never lets
# it survive an uncredited copy of a parent's fields either).
_ATTEMPT_KEYS = frozenset(_INHERITED) | {"prompt_file", "agent_file", "cap_grace_usd"}
# D3: a review-stage lane's persona may not carry these -- a read lane's
# job is to look, not to edit or run a shell.
_AGENT_WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "Bash"})
_FALLBACK_KEYS = _ATTEMPT_KEYS
# C5: `on` selects a fallback by the previous attempt's error kind. It is not
# in `_INHERITED` (each fallback's own, never cascaded to the next) and not
# in `_FALLBACK_KEYS`, which the cascade attempt is also checked against --
# a cascade attempt is a lane's first attempt, never a response to a kind.
_FALLBACK_ENTRY_KEYS = _FALLBACK_KEYS | {"on"}


@dataclass
class Attempt:
    """One dispatch within a lane: the primary or one of its fallbacks."""

    fleet: str
    model: str | None = None
    effort: str = "standard"
    mode: str = "read"
    # E26: this attempt's own repository; cascades like `schema`, resolved at
    # parse time the same way the mission-level `cwd` is. None (the common
    # case) means the mission's cwd -- see `effective_cwd`.
    cwd: str | None = None
    prompt: str = ""
    timeout: int | None = None
    stall_timeout: int | None = 600
    loop_limit: int | None = 6
    max_tool_calls: int | None = None
    tool_idle_timeout: int | None = None
    test: str | None = None
    commit: str | None = None
    schema: str | None = None
    verdict: list[Criterion] | None = None
    isolate: bool | None = None
    cap_usd: float | None = None
    # E24: stated on this lane or this attempt only; never cascades (see
    # _ATTEMPT_KEYS above and _attempt_fields below).
    cap_grace_usd: float | None = None
    no_op_ok: bool = False
    test_policy: str = "clean"
    test_surface: list[str] | None = None
    ports: int = 0
    setup: str | None = None
    teardown: str | None = None
    include: list[str] | None = None
    # D3: an inline persona for this attempt; cascades like `schema`. See
    # fleets.Spec.agent for the shape and its validation.
    agent: dict | None = None
    # E1: this attempt's product, a file rather than (or beside) its reply;
    # cascades like `schema`, whose `schema` key resolves relative to the
    # mission file the same way. See fleets.Spec.deliverable for the shape.
    deliverable: dict | None = None
    # F12: forces `--restricted` on a claude read lane even without a
    # declared `deliverable`; cascades like `agent`. See fleets.Spec.restricted.
    restricted: bool = False
    # D5: what a tainted lane may do with a shell -- "deny" (the default and
    # the only boundary) or the opt-in "allow", which restores the old
    # command-prefix list. Stated per lane beside `taint`; refused at
    # dispatch on a lane that is not tainted. See fleets.Spec.taint_shell.
    taint_shell: str = "deny"
    # C5: which of the previous attempt's error `KINDS` this fallback answers;
    # None (every fallback but a hand-set one) means every kind, as before.
    on: list[str] | None = None
    # E6: the shell command a `fleet: "script"` attempt runs; None on every
    # other fleet (refused at load if it names one anyway).
    command: str | None = None

    def spec(
        self,
        cwd: str,
        *,
        cap_usd: float | None = None,
        prompt: str | None = None,
        resume: str | None = None,
        stage: str | None = None,
        taint: bool = False,
    ) -> Spec:
        """The dispatch; `cap_usd` overrides the attempt's own (the mission
        ledger passes what it has left), `prompt` the rendered template,
        `stage` the lane's pipeline stage (item 4's reproduce gate reads it
        off the Spec, not the mission), and `taint` the lane's computed
        (not this attempt's own) taint state (D2).

        E6: a script attempt has no stream for a breaker to read and no
        tool surface for taint to deny, and never carries a dollar cap (a
        mission-wide `cap_usd`/`ledger.remaining()` must not leak onto it
        either, since fleets.Spec refuses one) -- every field below that a
        script fleet cannot use is forced off here, once, rather than left
        to whatever the caller happened to pass in.
        """
        script = self.fleet == "script"
        return Spec(
            fleet=self.fleet,
            prompt=self.prompt if prompt is None else prompt,
            cwd=cwd,
            model=self.model,
            effort=self.effort,
            mode=self.mode,
            timeout=self.timeout,
            stall_timeout=0 if script else self.stall_timeout,
            loop_limit=0 if script else self.loop_limit,
            max_tool_calls=0 if script else self.max_tool_calls,
            tool_idle_timeout=0 if script else self.tool_idle_timeout,
            schema=self.schema,
            verdict=self.verdict,
            resume=resume,
            cap_usd=None if script else (self.cap_usd if cap_usd is None else cap_usd),
            cap_grace_usd=self.cap_grace_usd,
            test_policy=self.test_policy,
            test_surface=self.test_surface,
            stage=stage,
            ports=self.ports,
            setup=self.setup,
            teardown=self.teardown,
            include=self.include,
            taint=False if script else taint,
            taint_shell="deny" if script else self.taint_shell,
            agent=self.agent,
            deliverable=self.deliverable,
            restricted=self.restricted,
            command=self.command,
        )

    def effective_cwd(self, mission_cwd: str) -> str:
        """E26: this attempt's own repository, or the mission's default."""
        return self.cwd if self.cwd is not None else mission_cwd

    def isolated(self) -> bool:
        """Every lane isolates unless told otherwise. Write lanes must never
        share a tree; read lanes sharing the checkout only confound each
        other's byte check, and one fleet ignoring its read-only flag (agy
        did, live) would be editing the orchestrator's own tree."""
        return self.isolate if self.isolate is not None else True

    def label(self) -> str:
        return f"{self.fleet}/{self.model}" if self.model else self.fleet


def _attempt_fields(raw: dict, base_dir: Path, parent: dict) -> dict:
    """Merge one level of the cascade: explicit keys override the parent's."""
    out = dict(parent)
    if parent.get("fleet") and raw.get("fleet") and raw["fleet"] != parent["fleet"]:
        # Model and command names are fleet-local. A fallback that switches
        # fleet must not carry its parent's model with it: caught live
        # 2026-09-03 when an antigravity fallback inherited "luna" from its
        # codex primary. E6: a script attempt's own command is exactly as
        # local -- a fallback that leaves script for a model fleet (or the
        # reverse) must not carry a stray command across the switch.
        # (A mission-level model has no fleet to differ from and does cascade;
        # load-time validation refuses it on any lane it does not fit.)
        out.pop("model", None)
        out.pop("command", None)
    for key in _INHERITED:
        if key in raw and (raw[key] is not None or key in _BREAKER_KEYS):
            out[key] = raw[key]
    # E24: cap_grace_usd is not in _INHERITED and must never survive an
    # uncredited copy of `parent`'s fields (out started as dict(parent)
    # above) -- a lane's grace must not leak onto its fallback attempts, or
    # a cascade attempt built from this lane's primary. Only this level's
    # own, explicit value (present in `raw`, even if null) counts.
    out.pop("cap_grace_usd", None)
    if "cap_grace_usd" in raw:
        out["cap_grace_usd"] = raw["cap_grace_usd"]
    # E6: a mission- or lane-level cap_usd default must not silently become
    # a script attempt's own cap -- fleets.Spec refuses any cap_usd on that
    # fleet, so a mission-wide cap would otherwise refuse every script lane
    # it reaches. Only this level's own explicit value (present in `raw`)
    # survives; Attempt.spec() also forces cap_usd off for a script attempt
    # as a second line of defense.
    if out.get("fleet") == "script" and "cap_usd" not in raw:
        out.pop("cap_usd", None)
    if raw.get("cwd"):
        # E26: resolved exactly as the mission-level `cwd` is (relative to
        # the mission file's directory, `expanduser`, `resolve`), so a lane
        # or attempt that names its own repository is just as portable.
        out["cwd"] = str((base_dir / Path(str(raw["cwd"])).expanduser()).resolve())
    if raw.get("prompt_file"):
        prompt_path = (base_dir / str(raw["prompt_file"])).expanduser().resolve()
        try:
            out["prompt"] = prompt_path.read_text()
        except OSError as exc:
            raise MissionInvalid(f"cannot read prompt_file: {exc}") from exc
    if raw.get("agent_file") is not None and raw.get("agent") is not None:
        raise MissionInvalid("agent and agent_file are mutually exclusive")
    if raw.get("agent_file"):
        agent_path = (base_dir / str(raw["agent_file"])).expanduser().resolve()
        try:
            out["agent"] = json.loads(agent_path.read_text())
        except OSError as exc:
            raise MissionInvalid(f"cannot read agent_file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise MissionInvalid(f"agent_file is not valid JSON: {exc}") from exc
    if "schema" in raw and raw["schema"]:
        out["schema"] = str((base_dir / str(raw["schema"])).expanduser().resolve())
    raw_deliverable = raw.get("deliverable")
    if isinstance(raw_deliverable, dict) and raw_deliverable.get("schema"):
        resolved_deliverable = dict(raw_deliverable)
        resolved_deliverable["schema"] = str(
            (base_dir / str(resolved_deliverable["schema"])).expanduser().resolve()
        )
        out["deliverable"] = resolved_deliverable
    return out


def _validate_on(raw_on: object, where: str) -> list[str] | None:
    if raw_on is None:
        return None
    if not isinstance(raw_on, list) or not all(isinstance(k, str) for k in raw_on):
        raise MissionInvalid(f"{where}: on must be a list of kind strings")
    unknown = [k for k in raw_on if k not in KINDS]
    if unknown:
        raise MissionInvalid(
            f"{where}: on names unknown kind {unknown[0]!r}; known: {', '.join(KINDS)}"
        )
    return list(raw_on)


def _attempt(fields: dict, *, where: str, on: object = None) -> Attempt:
    if "fleet" not in fields:
        raise MissionInvalid(f"{where}: fleet is required")
    # E6: a script attempt's ask is optional -- its command is the work;
    # every other fleet still needs one (nothing else tells it what to do).
    if fields.get("fleet") != "script" and not str(fields.get("prompt", "")).strip():
        raise MissionInvalid(f"{where}: no prompt (set prompt or prompt_file on the mission)")
    for key in ("model", "test", "commit", "schema", "test_policy", "cwd", "command"):
        if fields.get(key) is not None and not isinstance(fields[key], str):
            raise MissionInvalid(f"{where}: {key} must be a string")
    surface = fields.get("test_surface")
    if surface is not None and (
        not isinstance(surface, list) or not all(isinstance(item, str) for item in surface)
    ):
        raise MissionInvalid(f"{where}: test_surface must be a list of strings")
    for key in ("setup", "teardown"):
        if fields.get(key) is not None and not isinstance(fields[key], str):
            raise MissionInvalid(f"{where}: {key} must be a string")
    include = fields.get("include")
    if include is not None and (
        not isinstance(include, list) or not all(isinstance(item, str) for item in include)
    ):
        raise MissionInvalid(f"{where}: include must be a list of strings")
    ports = fields.get("ports")
    if ports is not None and (
        isinstance(ports, bool) or not isinstance(ports, int) or ports < 0
    ):
        raise MissionInvalid(f"{where}: ports must be a non-negative integer")
    for key in ("isolate", "no_op_ok"):
        if fields.get(key) is not None and not isinstance(fields[key], bool):
            raise MissionInvalid(f"{where}: {key} must be true or false")
    # D5: named here so a typo is a load-time refusal on the lane rather than
    # a DispatchRefused after the mission has already started paying.
    if fields.get("taint_shell") is not None and fields["taint_shell"] not in TAINT_SHELL_MODES:
        raise MissionInvalid(
            f"{where}: taint_shell must be one of {', '.join(TAINT_SHELL_MODES)}"
        )
    for key in _BREAKER_KEYS:
        value = fields.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise MissionInvalid(f"{where}: {key} must be positive; 0 or null disables")
    if fields.get("agent") is not None and not isinstance(fields["agent"], dict):
        raise MissionInvalid(f"{where}: agent must be an object")
    if fields.get("deliverable") is not None and not isinstance(fields["deliverable"], dict):
        raise MissionInvalid(f"{where}: deliverable must be an object")
    verdict = None
    if "verdict" in fields:
        try:
            verdict = parse_checklist(fields["verdict"])
        except ValueError as exc:
            raise MissionInvalid(f"{where}: verdict: {exc}") from exc
    validated_on = _validate_on(on, where)
    # E6: a script attempt's ordinary job is to change the tree; a model
    # attempt's is to look, unless it says otherwise. Still overridable
    # (`mode: read` for a script review lane).
    default_mode = "write" if fields.get("fleet") == "script" else "read"
    try:
        return Attempt(
            fleet=str(fields["fleet"]),
            model=fields.get("model"),
            effort=str(fields.get("effort", "standard")),
            mode=str(fields.get("mode", default_mode)),
            cwd=fields.get("cwd"),
            # E6: a script attempt's prompt is optional and may never have
            # been set anywhere in the cascade -- "fleet is required" above
            # guarantees `fields["fleet"]`, but nothing guarantees "prompt".
            prompt=str(fields.get("prompt", "")),
            timeout=int(fields["timeout"]) if fields.get("timeout") is not None else None,
            stall_timeout=_breaker_value(fields, "stall_timeout", 600),
            loop_limit=_breaker_value(fields, "loop_limit", 6),
            max_tool_calls=_breaker_value(fields, "max_tool_calls", None),
            tool_idle_timeout=_breaker_value(fields, "tool_idle_timeout", None),
            test=fields.get("test"),
            commit=fields.get("commit"),
            schema=fields.get("schema"),
            verdict=verdict,
            isolate=fields.get("isolate"),
            cap_usd=float(fields["cap_usd"]) if fields.get("cap_usd") is not None else None,
            cap_grace_usd=(
                float(fields["cap_grace_usd"]) if fields.get("cap_grace_usd") is not None else None
            ),
            no_op_ok=bool(fields.get("no_op_ok", False)),
            test_policy=str(fields.get("test_policy", "clean")),
            test_surface=list(surface) if surface is not None else None,
            ports=int(ports) if ports is not None else 0,
            setup=fields.get("setup"),
            teardown=fields.get("teardown"),
            include=list(include) if include is not None else None,
            agent=fields.get("agent"),
            deliverable=fields.get("deliverable"),
            restricted=bool(fields.get("restricted", False)),
            taint_shell=str(fields.get("taint_shell", "deny")),
            on=validated_on,
            command=fields.get("command"),
        )
    except (TypeError, ValueError) as exc:
        raise MissionInvalid(f"{where}: {exc}") from exc


def _breaker_value(fields: dict, key: str, default: int | None) -> int | None:
    """Preserve an explicit mission null: it disables instead of defaulting.

    `int()` alone accepted three shapes a breaker cannot mean (2026-09-08
    review): `true`, which JSON and TOML both allow and `int` turns into 1 --
    `stall_timeout: true` then killed a healthy run after one second; a
    negative, which trips on the first check because the elapsed time is
    already past it; and a float, silently truncated. Each is refused here,
    at load, rather than becoming a breaker that fires on a healthy run.
    """
    if key not in fields or fields[key] is None:
        return None if key in fields else default
    value = fields[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a whole number of seconds or calls, or null")
    if value < 0:
        raise ValueError(f"{key} must not be negative; use 0 or null to disable it")
    return value


def _default_lane_name(primary: Attempt, existing: list[Lane]) -> str:
    base = primary.label().replace("/", "-")
    taken = {lane.name for lane in existing}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


# E7: human lanes -- a lane whose fleet is the operator rather than a fleet
# CLI. Never dispatched (Attempt.spec is never called for one, and fleets.py
# never hears about it), so its attempt carries only the ask and, optionally,
# a deliverable path; every other field it could set would describe a
# dispatch that never happens.
_HUMAN_ATTEMPT_ALLOWED = frozenset({"fleet", "prompt", "deliverable"})


def _validate_human_attempt(attempt: Attempt, where: str) -> None:
    """Every Attempt field but the ask and its optional deliverable must sit
    at Attempt's own default -- whether the mission file set it directly on
    this lane or it arrived through a mission-level default cascading onto
    it the way it would onto any other lane (a mission-level `test`, most
    concretely: D1 cascades it onto every lane already)."""
    reference = Attempt(fleet="human", prompt=attempt.prompt)
    for f in fields(Attempt):
        if f.name in _HUMAN_ATTEMPT_ALLOWED:
            continue
        if getattr(attempt, f.name) != getattr(reference, f.name):
            raise MissionInvalid(f"{where}: a human lane may not set {f.name}")


# E6: an attempt whose fleet is "script" has no effort dial, no model but
# "sh", no dollar cap to enforce, and no structured-output or persona
# surface -- refused with the field named, the same shape
# _validate_human_attempt checks for a human lane's Attempt fields.
_SCRIPT_ATTEMPT_DENIED = (
    "effort",
    "model",
    "cap_usd",
    "cap_grace_usd",
    "schema",
    "verdict",
    "agent",
    "restricted",
    "taint_shell",
)


def _validate_script_attempt(attempt: Attempt, *, where: str) -> None:
    reference = Attempt(fleet="script", prompt=attempt.prompt, command=attempt.command)
    for f in fields(Attempt):
        if f.name not in _SCRIPT_ATTEMPT_DENIED:
            continue
        if getattr(attempt, f.name) != getattr(reference, f.name):
            raise MissionInvalid(f"{where}: a script attempt may not set {f.name}")


def _parse_cascade(raw_cascade: object, base_dir: Path) -> dict | None:
    """B3: the mission's cheap-first attempt, an attempt-shaped object with a
    required `fleet`. Parsed once, at the mission level; merged onto each
    qualifying lane's own primary fields (the way a fallback merges) when
    that lane's cascade attempt is built."""
    from . import mission as mission_mod

    if raw_cascade is None:
        return None
    if not isinstance(raw_cascade, dict):
        raise MissionInvalid("cascade must be an object")
    mission_mod._reject_unknown(raw_cascade, _FALLBACK_KEYS, "cascade")
    if "fleet" not in raw_cascade:
        raise MissionInvalid("cascade: fleet is required")
    if "cap_grace_usd" in raw_cascade:
        # E24: same reasoning as the mission-level refusal above -- grace is
        # a per-lane decision, and the cascade template applies to every
        # qualifying lane at once.
        raise MissionInvalid("cap_grace_usd is stated per lane, not in cascade")
    return _attempt_fields(raw_cascade, base_dir, {})


_DEFAULT_RETRY_KINDS = ("rate_limit", "transport")


def _parse_retry(raw_retry: object) -> dict | None:
    """C5: retry the same attempt on its own vendor before the fallback walk
    moves on, for the kinds a moment's wait is likely to fix. `None` (the
    default) retries nothing; `kinds` and `backoff_s` have defaults, but a
    mission that sets `retry` must say how many extra tries it is paying for."""
    if raw_retry is None:
        return None
    if not isinstance(raw_retry, dict):
        raise MissionInvalid("retry must be an object")
    unknown = sorted(set(raw_retry) - {"kinds", "attempts", "backoff_s"})
    if unknown:
        raise MissionInvalid(f"retry: unknown field(s) {', '.join(unknown)}")
    kinds_raw = raw_retry.get("kinds", list(_DEFAULT_RETRY_KINDS))
    if not isinstance(kinds_raw, list) or not all(isinstance(k, str) for k in kinds_raw):
        raise MissionInvalid("retry.kinds must be a list of kind strings")
    unknown_kinds = [k for k in kinds_raw if k not in KINDS]
    if unknown_kinds:
        raise MissionInvalid(
            f"retry.kinds names unknown kind {unknown_kinds[0]!r}; known: {', '.join(KINDS)}"
        )
    if "attempts" not in raw_retry:
        raise MissionInvalid("retry.attempts is required")
    attempts_raw = raw_retry["attempts"]
    if isinstance(attempts_raw, bool) or not isinstance(attempts_raw, int) or not (
        1 <= attempts_raw <= 5
    ):
        raise MissionInvalid("retry.attempts must be an integer from 1 to 5")
    try:
        backoff_s = float(raw_retry.get("backoff_s", 0))
    except (TypeError, ValueError) as exc:
        raise MissionInvalid(f"retry.backoff_s must be a number: {exc}") from exc
    if backoff_s < 0:
        raise MissionInvalid("retry.backoff_s must be at least 0")
    return {"kinds": list(kinds_raw), "attempts": attempts_raw, "backoff_s": backoff_s}


def _cascade_target_lanes(mission: Mission) -> list[Lane]:
    """Lanes the cascade attempt was actually prepended to at load. A lane
    that opted out with `cascade: false` has no cheap attempt, so its own
    primary must not be counted as one (cross-vendor review of B3)."""
    if mission.cascade is None:
        return []
    return [lane for lane in mission.lanes if lane.cascaded]


def _cascade_label(cascade: dict) -> str:
    fleet = cascade.get("fleet")
    model = cascade.get("model")
    return f"{fleet}/{model}" if model else str(fleet)


def escalation_attempts(attempts: list[dict]) -> list[dict]:
    """The attempt summaries that represent an escalation past the lane's
    first declared attempt.

    `dispatch_one` appends one summary per dispatch, and a transient retry is
    a dispatch: a lane whose cheap attempt failed once on a rate limit and
    then succeeded on its own retry has two summaries and never escalated.
    Counting `attempts[1:]` read that as an escalation, which is the rate
    AGENTS.md rule B3 quotes (2026-09-08 review).

    `retry_of` is the only sound mark. The `attempt` label is fleet/model,
    and the c5-build-cascade-capped fixture is a real mission whose cascade
    and primary are the same fleet and model at different efforts: two
    genuinely different declared attempts sharing one label. A summary
    written before `retry_of` existed carries neither, and reads as an
    escalation exactly as it does today.
    """
    return [entry for entry in attempts[1:] if entry.get("retry_of") is None]


def _escalation_summary(mission: Mission, lane_results: list[LaneResult]) -> dict | None:
    """B3: the mission-wide escalation rate and dollars, logged beside the
    cap (docs/ROADMAP-2026-09.md item B3)."""
    if mission.cascade is None:
        return None
    names = {lane.name for lane in _cascade_target_lanes(mission)}
    targeted = [lr for lr in lane_results if lr.name in names]
    lanes_n = len(targeted)
    cheap_ok = sum(1 for lr in targeted if lr.attempts and lr.attempts[0].get("ok") is True)
    escalated = sum(1 for lr in targeted if lr.escalated)
    cascade_usd = sum(lr.attempts[0].get("cost_usd") or 0.0 for lr in targeted if lr.attempts)
    escalated_usd = sum(
        sum(a.get("cost_usd") or 0.0 for a in escalation_attempts(lr.attempts))
        for lr in targeted
        if lr.escalated
    )
    return {
        "lanes": lanes_n,
        "cheap_ok": cheap_ok,
        "escalated": escalated,
        "rate": round(escalated / lanes_n, 3) if lanes_n else None,
        "cascade_usd": round(cascade_usd, 6),
        "escalated_usd": round(escalated_usd, 6),
    }


def _artifact_matches(recorded: str | None, expected: Path) -> bool:
    """Whether a receipt's recorded artifact path is the file conductor
    expects to find there.

    W5: a symlink on either side is refused outright rather than compared
    through. Every artifact conductor writes itself (an answer, a diff, a
    captured deliverable) is a regular file, so the only way one of these
    is a symlink is that something else put it there -- and resolving it
    would make bytes from outside the tree read as the lane's own."""
    if recorded is None:
        return True
    try:
        recorded_path = Path(recorded)
        if recorded_path.is_symlink() or expected.is_symlink():
            return False
        return recorded_path.resolve() == expected.resolve() and expected.is_file()
    except OSError:
        return False


def _artifact_digest(recorded: str | None) -> str | None:
    """The sha256 of one artifact file, or None when there is nothing to hash.

    W3: a symlink is never hashed. W5 already refuses one as a lane artifact,
    and hashing through it would bind bytes from outside the mission
    directory into the receipt as if the lane had produced them."""
    if not recorded:
        return None
    try:
        path = Path(recorded)
        if path.is_symlink() or not path.is_file():
            return None
    except OSError:
        return None
    return attest.file_sha256(path)


def _artifact_paths(result: LaneResult) -> tuple[tuple[str, str | None], ...]:
    return (
        ("answer", result.answer_path),
        ("diff", result.diff_path),
        ("deliverable", result.deliverable_path),
    )


def _record_artifact_digests(result: LaneResult) -> LaneResult:
    """W3: bind the bytes of this lane's artifacts into its receipt."""
    digests: dict[str, str] = {}
    for kind, recorded in _artifact_paths(result):
        digest = _artifact_digest(recorded)
        if digest is not None:
            digests[kind] = digest
    result.artifact_sha256 = digests
    return result


def _artifact_bytes_match(
    lane_name: str,
    result: LaneResult,
    digests: dict[str, str],
    notes: list[str] | None,
) -> bool:
    """W3: whether every artifact this receipt records still hashes to what
    the receipt recorded when the lane settled.

    `_artifact_matches` only says the file is where the receipt claims and is
    a regular file; the bytes inside it are what a downstream lane actually
    consumes through {{lanes.<name>.answer}}, so they are what resume has to
    authenticate. A receipt written before this field existed records no
    digest, and is trusted on its paths as before with a note saying so."""
    for kind, recorded in _artifact_paths(result):
        actual = _artifact_digest(recorded)
        if actual is None:
            continue
        expected = digests.get(kind)
        if expected is None:
            if notes is not None:
                notes.append(
                    f"lane '{lane_name}': receipt records no digest for its {kind}; "
                    "trusted on path only"
                )
            continue
        if expected != actual:
            if notes is not None:
                notes.append(
                    f"lane '{lane_name}': {kind} bytes differ from the receipt's digest; "
                    "not trusted"
                )
            return False
    return True


def _trusted_lane(
    mission: Mission,
    mission_dir: Path,
    lane: Lane,
    result: LaneResult,
    *,
    prior_result: dict | None = None,
    notes: list[str] | None = None,
    digests: dict[str, str] | None = None,
) -> bool:
    """Whether a completed receipt is enough to skip every effect of a lane.

    W3: `digests` are the artifact hashes to authenticate the files on disk
    against, defaulting to the ones this receipt itself carries. A human lane
    passes the durable receipt's digests explicitly, because the `result` it
    is checked against was just rebuilt from the same files."""
    from . import mission as mission_mod

    if digests is None:
        digests = result.artifact_sha256
    if result.name != lane.name:
        return False
    if lane.plan:
        child = (result.plan or {}).get("child")
        if child is not None:
            # E10 second spec: a plan lane that already launched a child is
            # never judged by the ordinary checks below -- `result.ok` and
            # `result.attempts` describe the plan lane's own dispatch, not
            # whether the child it launched is still live, paused, or truly
            # done. Trusted as-is only once the child reached finality and
            # its spend was rolled into the parent's ledger; every other
            # shape (a launch a crash interrupted, a freshly parked child)
            # must be re-derived against the child's own disk state by
            # `_resume_plan_child`.
            if child.get("state") != "finished" or child.get("rolled_up") is not True:
                return False
            # Cross-vendor review (Grok): `settle()` writes this lane's
            # receipt with `rolled_up: true` before the mission's own
            # `result.json` is rewritten with that rollup folded in. If a
            # crash lands in that window, the receipt alone would claim a
            # rollup the mission-level record never actually saw -- trust it
            # only once `result.json` itself already names the child, never
            # on the lane receipt's say-so alone.
            child_id = child.get("mission_id")
            return child_id is not None and child_id in (
                (prior_result or {}).get("children") or []
            )
    if lane.human:
        # E7: a human lane has no run receipt; its answer file (and declared
        # deliverable) on disk is the whole record. The receipt's own
        # `answer_path` must point at that file, the same artifact check
        # every other lane's answer gets.
        if result.ok is not True or result.attempts:
            return False
        if not _artifact_matches(
            result.answer_path, mission_dir / "answers" / f"{lane.name}.txt"
        ):
            return False
        if not (mission_dir / "answers" / f"{lane.name}.txt").is_file():
            return False
        deliverable = lane.attempts[0].deliverable
        if deliverable is not None:
            resolved = mission_mod._human_deliverable_path(mission.cwd, deliverable["path"])
            if not resolved.is_file():
                return False
            if not _artifact_matches(result.deliverable_path, resolved):
                return False
        return _artifact_bytes_match(lane.name, result, digests, notes)
    if result.skipped is not None:
        # A cancelled lane is never settled by its own receipt: what settles
        # it is whether the sink that beat it is still being kept on this
        # resume. That is not knowable here -- `kept` is still being built --
        # so the decision moves to `mission._keep_cancelled_lanes`, a second
        # pass that runs once the kept set is complete, and this returns
        # False so the lane reaches it.
        #
        # The previous rule was `and prior_ok`, which looked safe and was
        # not: an interrupt forces `ok = False`, and SIGINT-then-resume is
        # the documented flow (AGENTS.md rule 8), which promises finished
        # lanes are not paid twice. Every cancelled lane was re-dispatched
        # and re-paid on exactly the resume the rule exists to serve
        # (2026-09-08 review).
        return False
    if result.ok is not True:
        return False
    if not result.attempts:
        return False
    if result.attempts[-1].get("spawned") is not True:
        # A rehearsal writes ok receipts without doing the work. Trusting one
        # turns the next real resume into another rehearsal with no dispatch.
        return False
    if not _artifact_matches(result.answer_path, mission_dir / "answers" / f"{lane.name}.txt"):
        return False
    if not _artifact_matches(result.diff_path, mission_dir / "diffs" / f"{lane.name}.patch"):
        return False
    if not _artifact_matches(
        result.deliverable_path, mission_dir / "deliverables" / f"{lane.name}-deliverable"
    ):
        return False
    if result.verdict is not None and not (
        mission_dir / "verdicts" / f"{lane.name}.json"
    ).is_file():
        return False
    if not _artifact_bytes_match(lane.name, result, digests, notes):
        return False
    # E26: the repository this receipt's commits actually landed in, not
    # necessarily the mission's default.
    repo = result.cwd or mission.cwd
    if result.tip_sha and result.tip_sha != result.base_sha:
        commit = _git_answer(repo, "cat-file", "-e", f"{result.tip_sha}^{{commit}}")
        if commit is None:
            _note_git_unrun(notes, lane.name, "tip commit")
        elif commit.returncode != 0:
            return False
    if lane.branch:
        if not result.tip_sha or result.branch != lane.branch:
            return False
        branch = _git_answer(
            repo,
            "rev-parse",
            "--verify",
            f"refs/heads/{lane.branch}^{{commit}}",
        )
        if branch is None:
            _note_git_unrun(notes, lane.name, "branch tip")
        elif branch.returncode != 0 or branch.stdout.strip() != result.tip_sha:
            return False
    return True


def _git_answer(repo: str | Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """A git result the trust check may read, or None when git never ran.

    `git_run` reports a spawn failure or a hung git as `GIT_UNRUN`, which is
    not a verdict on the repository. Five recorded resumes under machine load
    (two or three suites gating at once) dropped a kept lane into `rerun` and
    paid for its work again because the first shape of this check read that
    code as "the commit is missing". One retry covers a momentary refusal; a
    second `GIT_UNRUN` is reported to the caller as no answer, never as no.
    """
    from . import mission as mission_mod

    for _ in range(2):
        proc = mission_mod.git_run(repo, *args)
        if proc.returncode != GIT_UNRUN:
            return proc
    return None


def _note_git_unrun(notes: list[str] | None, lane_name: str, what: str) -> None:
    if notes is not None:
        notes.append(
            f"lane '{lane_name}': git could not run to confirm its {what}; the receipt "
            "was trusted as recorded"
        )


def _salvage_previous_lane(lane: Lane, raw: dict) -> LaneResult:
    """Keep safe accounting fields from a receipt that cannot be trusted."""
    from . import mission as mission_mod

    known = {
        key: value
        for key, value in raw.items()
        if key in mission_mod.LaneResult.__dataclass_fields__
    }
    known["name"] = lane.name
    known["ok"] = False
    known["kept"] = False
    try:
        return mission_mod.LaneResult.from_dict(known)
    except (TypeError, ValueError):
        attempts: list[dict] = []
        for key in ("previous_attempts", "attempts"):
            value = raw.get(key)
            if isinstance(value, list):
                attempts.extend(
                    item
                    for item in value
                    if isinstance(item, dict) and isinstance(item.get("run_id"), str)
                )
        return mission_mod.LaneResult(name=lane.name, ok=False, previous_attempts=attempts)


def _read_previous_lanes(
    mission_dir: Path, mission: Mission
) -> tuple[dict[str, LaneResult], list[str], int]:
    from . import mission as mission_mod

    previous: dict[str, LaneResult] = {}
    notes: list[str] = []
    accounting_unknown = 0
    for lane in mission.lanes:
        raw = mission_mod._json_object(mission_dir / "lanes" / f"{lane.name}.json")
        if raw is None:
            continue
        try:
            previous[lane.name] = mission_mod.LaneResult.from_dict(raw)
        except (TypeError, ValueError) as exc:
            salvaged = _salvage_previous_lane(lane, raw)
            previous[lane.name] = salvaged
            run_ids = [
                attempt.get("run_id")
                for attempt in [*salvaged.previous_attempts, *salvaged.attempts]
                if isinstance(attempt.get("run_id"), str)
            ]
            if not run_ids:
                accounting_unknown += 1
                accounting = "no run ids were salvageable; budget marked unverifiable"
            else:
                accounting = f"salvaged {len(set(run_ids))} run id(s) for spend and history"
            notes.append(
                f"lane '{lane.name}': previous receipt unreadable ({exc}); "
                f"lane will rerun and {accounting}"
            )
    return previous, notes, accounting_unknown
