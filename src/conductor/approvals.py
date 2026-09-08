"""Plan-lane approval consumption: reading a pause, answering it, and
launching (or refusing to launch) the child mission a plan lane produced.

Peer review 2026-09-07 (docs/review/2026-09-07-astra-notes.md, section 5 item
4) asked for this to be sliced out of mission.py by invariant, the same way
graph.py already was, so the two defects it repairs stay pinned by tests that
name them:

D1: one planner's answered pause used to launch every parked planner's
child, not just the one the answer named. `_execute_mission` resolves
exactly the planner named by the answered pause's `lane` (`pause_answer`'s
`kind: "child"` record) and re-asks any other still-parked planner on the
same or a later resume; `_launch_plan_child` and `_resume_plan_child` are the
two ends of that repair.

D2: an approval used to be an approval of a path, not of the bytes the
operator was actually shown when the pause fired. `_plan_check_child` hashes
the child mission file's own bytes into the pause (`child_sha256`,
`child_policy`); `_launch_plan_child` re-reads and re-hashes the child before
launch and refuses if the digest moved, then reruns every one of
`_plan_check_child`'s gates in full against the parent's ledger as it stands
now, rather than trusting what was true at park time.

`_plan_check_child` and `_launch_plan_child` call `load_mission`,
`run_mission`, and `claim_dir`, which live in `mission.py`; both import
`mission` lazily inside their own bodies to reach them, the way
`mission._run_resolve` already imports `prompts`. Every other mission.py-only
helper this module's functions call at runtime (`_json_object`,
`_effective_ceiling`, `_first_child_error`, `_human_deliverable_path`,
`_lock_holder`, `_lock_status`, `PLAN_MAX_DEPTH`) is reached the same lazy
way, for the same reason: mission.py re-exports every name in this module
(so `conductor.mission._name` keeps resolving for existing callers and
tests), which makes a module-level import back from here a cycle. This
module must never import `conductor.mission` at module level -- a test pins
that.

The monkeypatch hazard: `_answer_human_pause` and `_launch_plan_child` used
to call `datetime.now(UTC)` directly, and two tests patch
`conductor.mission.datetime` to freeze the clock. Moving the call into this
module would leave that patch silently not applying. Resolved by keeping
every such call site in `mission.py` (which still owns the patchable
`datetime` name) and passing the stamp down as a keyword default of `None`
-- `None` means "now", computed here only when a caller (typically a test
exercising one of these functions directly) does not supply one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .graph import MissionInvalid
from .runner import _slug, deliverable_path_problem

if TYPE_CHECKING:
    from .mission import Lane, LaneResult, Ledger, Mission


def _check_pause(
    pause: dict | None,
    lane_name: str,
    ledger: Ledger,
    *,
    answered_lanes: set[str],
    answered_spend: bool,
) -> dict | None:
    """Whether this about-to-start lane should park the mission instead:
    `pause.before` first, then `pause.spend_usd`, mirroring the order the
    operator declared them in. None once a pause point has been answered
    `continue` (`_execute_mission` reads that history from `pause.json`)."""
    if not pause:
        return None
    if lane_name in pause["before"] and lane_name not in answered_lanes:
        return {
            "kind": "lane",
            "lane": lane_name,
            "spent_usd": None,
            "threshold": None,
            "reason": f"lane {lane_name} is a pause point",
            "question": f"Lane '{lane_name}' is a pause point; continue the mission?",
        }
    threshold = pause["spend_usd"]
    if threshold is not None and not answered_spend:
        spent = ledger.to_dict()["spent_usd"]
        if spent >= threshold:
            return {
                "kind": "spend",
                "lane": None,
                "spent_usd": spent,
                "threshold": threshold,
                "reason": f"spend ${spent:.4f} reached pause.spend_usd ${threshold:.4f}",
                "question": (
                    f"Spend ${spent:.4f} reached pause.spend_usd ${threshold:.4f}; "
                    "continue the mission?"
                ),
            }
    return None


def _answered_pause_points(mission_dir: Path) -> tuple[set[str], bool]:
    """Which pause points already have a `continue` answer on record: a named
    lane's pause fires at most once, and the spend pause fires at most once
    per mission, across as many resumes as it takes."""
    from . import mission as mission_mod

    doc = mission_mod._json_object(mission_dir / "pause.json")
    answered_lanes: set[str] = set()
    answered_spend = False
    for record in (doc or {}).get("answers") or []:
        if not isinstance(record, dict) or record.get("answer") != "continue":
            continue
        if record.get("kind") == "lane" and isinstance(record.get("lane"), str):
            answered_lanes.add(record["lane"])
        elif record.get("kind") == "spend":
            answered_spend = True
    return answered_lanes, answered_spend


def _child_digest(path: str | None) -> str | None:
    """D2: sha256 of the child mission file's own bytes -- what the operator
    was actually shown when the pause was raised. `None` when the file
    cannot be read, which is never treated as a match."""
    if not path:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _child_policy(child: Mission, depth: int) -> dict:
    """D2: the part of a checked child a receipt should carry in words as
    well as in a digest -- what the approval was an approval of."""
    from . import mission as mission_mod

    per_hour, per_day = mission_mod._effective_ceiling(child)
    return {
        "max_cost_usd": child.max_cost_usd,
        "ceiling": {"per_hour_usd": per_hour, "per_day_usd": per_day},
        "depth": depth,
        "lanes": len(child.lanes),
    }


def _plan_check_child(
    mission: Mission,
    deliverable_path: str | None,
    *,
    ledger: Ledger,
    base: Path,
    lane_cwd: str | None = None,
    clamp_budget: bool = False,
) -> tuple[dict, str | None]:
    """E10: everything the scheduler must confirm about a plan lane's
    deliverable before the mission may ask the operator to launch it: the
    file loads as a mission, its depth stays within `PLAN_MAX_DEPTH`, its
    budget is bounded and within the parent's remaining ledger, its ceiling
    is no looser than the parent's, and it dry-runs clean. Returns the
    `plan` block (`LaneResult.plan`, minus the eventual `child` key) and the
    first refusal message, or `None` once every check has passed."""
    from . import mission as mission_mod

    child_depth = mission.depth + 1
    plan: dict = {
        "child_path": deliverable_path,
        "child_name": None,
        "child_max_cost_usd": None,
        "depth": child_depth,
        "dry_run_ok": False,
        "refused": None,
        # D2: what the operator is approving, in bytes and in words. Both are
        # re-derived at launch and the launch is refused if either moved.
        "child_sha256": None,
        "child_policy": None,
    }
    if not deliverable_path:
        message = "plan lane produced no deliverable to load"
        plan["refused"] = message
        return plan, message
    try:
        child = mission_mod.load_mission(deliverable_path, base_dir=lane_cwd or mission.cwd)
    except MissionInvalid as exc:
        message = str(exc)
        plan["refused"] = message
        return plan, message
    plan["child_name"] = child.name
    plan["child_max_cost_usd"] = child.max_cost_usd
    plan["child_sha256"] = _child_digest(child.source)
    plan["child_policy"] = _child_policy(child, child_depth)
    if child_depth > mission_mod.PLAN_MAX_DEPTH or (
        child_depth == mission_mod.PLAN_MAX_DEPTH
        and any(child_lane.plan for child_lane in child.lanes)
    ):
        message = "child would exceed the plan depth limit"
        plan["refused"] = message
        return plan, message
    remaining = ledger.remaining()
    if child.max_cost_usd is None:
        message = "child has no max_cost_usd; a planned mission's budget must be bounded"
        plan["refused"] = message
        return plan, message
    if remaining is not None and child.max_cost_usd > remaining:
        # `clamp_budget` is the launch-time call. E10 second spec item 1 says
        # the child's cap is clamped to the parent's remaining ledger there,
        # and the parent can have spent since the park (a mission parks while
        # other lanes are still dispatching), so refusing an over-budget
        # child at launch made that clamp dead code and failed a plan the
        # operator had just approved. Nothing left at all is still a refusal
        # -- there is no budget to clamp to (2026-09-08 review).
        if not clamp_budget or remaining <= 0:
            message = (
                f"child budget ${child.max_cost_usd:.2f} is over the parent's "
                f"remaining ${remaining:.2f}"
            )
            plan["refused"] = message
            return plan, message
    parent_hour, parent_day = mission_mod._effective_ceiling(mission)
    child_hour, child_day = mission_mod._effective_ceiling(child)
    for label, parent_bound, child_bound in (
        ("per_hour_usd", parent_hour, child_hour),
        ("per_day_usd", parent_day, child_day),
    ):
        if parent_bound is not None and (child_bound is None or child_bound > parent_bound):
            message = f"child ceiling {label} is looser than the parent's"
            plan["refused"] = message
            return plan, message
    dry_result = mission_mod.run_mission(child, home=base, dry_run=True)
    if not dry_result.ok:
        message = f"child dry run failed: {mission_mod._first_child_error(dry_result)}"
        plan["refused"] = message
        return plan, message
    plan["dry_run_ok"] = True
    return plan, None


def _plan_lane_failure(resolved: LaneResult, message: str, *, plan: dict) -> LaneResult:
    """Fail an already-dispatched plan lane's `LaneResult` in place, the same
    shape every other conductor-side check (agent, taint, adversarial) uses:
    the failure reads back as an ordinary failed attempt, kind 'plan'."""
    resolved.ok = False
    if resolved.attempts:
        resolved.attempts[-1] = {
            **resolved.attempts[-1],
            "ok": False,
            "error": f"plan: {message}",
            "failure": f"plan: {message}",
            "kind": "plan",
        }
    resolved.kinds = [*resolved.kinds[:-1], "plan"] if resolved.kinds else ["plan"]
    resolved.plan = plan
    return resolved


def _resume_plan_child(
    old: LaneResult, base: Path, *, prior_result: dict | None = None
) -> tuple[LaneResult, tuple[float, int] | None]:
    """E10 second spec: a plan lane whose prior receipt already names a
    launched child (item 2's pre-launch receipt, still `state: "launched"`
    if a crash cut the launch short, or item 4's finished one that never got
    rolled up, including one whose rollup never made it into `result.json`
    before a crash -- see `_trusted_lane`). Refuses the resume outright while
    the child is still live or paused; otherwise adopts the plan lane's
    outcome without dispatching anything, rolling the child's spend into the
    parent's ledger exactly once. Never called once a receipt is already
    `state: "finished"` with `rolled_up: true` AND the mission-level
    `result.json` already names the child -- `_trusted_lane` accepts only
    that fully-committed shape directly."""
    from . import mission as mission_mod

    plan = dict(old.plan or {})
    child_block = dict(plan.get("child") or {})
    child_id = str(child_block.get("mission_id"))
    child_dir = base / "missions" / child_id
    resolved = replace(old)
    resolved.attempts = [dict(attempt) for attempt in old.attempts]
    resolved.kinds = list(old.kinds)

    def missing() -> tuple[LaneResult, None]:
        failed_plan = {**plan, "child": {**child_block, "state": "missing"}}
        return _plan_lane_failure(resolved, f"child '{child_id}' missing", plan=failed_plan), None

    if not child_dir.is_dir():
        return missing()
    child_lock = child_dir / "running.json"
    if child_lock.exists():
        # D10: the same rule as a claim -- a lock whose body cannot be read
        # is not proven stale, and a child that may still be running is not
        # a child whose receipt may be adopted.
        running_raw = mission_mod._lock_holder(child_lock, f"child '{child_id}'")
        if running_raw:
            live, reason = mission_mod._lock_status(running_raw)
            if live:
                raise MissionInvalid(f"child '{child_id}' is still running ({reason})")
    result_path = child_dir / "result.json"
    if not result_path.is_file():
        return missing()
    child_result = mission_mod._json_object(result_path) or {}
    paused_block = child_result.get("paused")
    still_parked = isinstance(paused_block, dict) and "answer" not in paused_block
    pause_doc = mission_mod._json_object(child_dir / "pause.json")
    pause_unanswered = isinstance(pause_doc, dict) and pause_doc.get("answer") is None
    if still_parked or pause_unanswered:
        raise MissionInvalid(f"child '{child_id}' is paused; resume it first")

    already_rolled_up = child_block.get("rolled_up") is True and child_id in (
        (prior_result or {}).get("children") or []
    )
    ok = bool(child_result.get("ok"))
    cost_usd = float(child_result.get("cost_usd") or 0.0)
    unpriced = int((child_result.get("budget") or {}).get("unpriced_dispatches") or 0)
    plan["child"] = {
        "mission_id": child_id,
        "ok": ok,
        "cost_usd": cost_usd,
        "paused": False,
        "report_path": child_result.get("report_path"),
        "state": "finished",
        "rolled_up": True,
    }
    if ok:
        resolved.ok = True
        resolved.plan = plan
    else:
        resolved = _plan_lane_failure(
            resolved, f"child mission '{child_id}' was not ok", plan=plan
        )
    rollup = None if already_rolled_up else (cost_usd, unpriced)
    return resolved, rollup


_PAUSE_RECORD_FIELDS = ("kind", "lane", "spent_usd", "threshold", "asked_at", "question")


def _answer_human_pause(
    mission: Mission,
    mission_dir: Path,
    pause_path: Path,
    pause_doc: dict,
    *,
    answer: str | None,
    answer_file: str | None,
    answered_at: str | None = None,
) -> dict | None:
    """E7: resolve a `kind: human` pause. `--answer stop` stops the mission
    exactly like any other pause (the caller treats the returned record the
    same way); `--answer continue` makes no sense with nothing dispatched to
    resume, so it is refused; anything else is the operator's answer text,
    written to `answers/<lane>.txt` the way any lane's answer is kept. The
    history records the answer's length and when it landed, never the text
    itself a second time -- it is already on disk, once.

    `answered_at`: see the module docstring's monkeypatch note. `None`
    (the default) computes "now" here; `mission.run_mission` always passes
    its own `datetime.now(UTC)` stamp explicitly instead."""
    from . import mission as mission_mod

    if answer is not None and answer_file is not None:
        raise MissionInvalid("--answer and --answer-file are mutually exclusive")
    stamp = answered_at if answered_at is not None else datetime.now(UTC).isoformat()
    if answer == "stop":
        resolved = {key: pause_doc.get(key) for key in _PAUSE_RECORD_FIELDS}
        resolved["answer"] = "stop"
        resolved["answered_at"] = stamp
        pause_doc["answer"] = "stop"
        pause_doc["answers"] = [*(pause_doc.get("answers") or []), resolved]
        pause_path.write_text(json.dumps(pause_doc, indent=2))
        return resolved
    if answer == "continue":
        raise MissionInvalid("a human lane needs an answer")
    if answer_file is not None:
        try:
            text = Path(answer_file).expanduser().read_text()
        except OSError as exc:
            raise MissionInvalid(f"cannot read --answer-file: {exc}") from exc
    elif answer is not None:
        text = answer
    else:
        raise MissionInvalid(
            f"mission is paused: {pause_doc.get('question')}; "
            "resume with --answer TEXT, --answer-file PATH, or --answer stop"
        )
    lane_name = pause_doc.get("lane")
    lane = next((candidate for candidate in mission.lanes if candidate.name == lane_name), None)
    if lane is None:
        raise MissionInvalid(f"paused lane '{lane_name}' is no longer in the mission")
    deliverable = lane.attempts[0].deliverable
    if deliverable is not None:
        resolved_deliverable = mission_mod._human_deliverable_path(
            mission.cwd, deliverable["path"]
        )
        if not resolved_deliverable.is_file():
            raise MissionInvalid(
                f"lane '{lane_name}' declares a deliverable at '{resolved_deliverable}', "
                "which does not exist"
            )
        unsafe = deliverable_path_problem(mission.cwd, deliverable["path"])
        if unsafe is not None:
            # W5: a symlink planted where the operator's product belongs is
            # not the operator's product.
            raise MissionInvalid(f"lane '{lane_name}': {unsafe}")
    answers_dir = mission_dir / "answers"
    answers_dir.mkdir(exist_ok=True)
    (answers_dir / f"{lane_name}.txt").write_text(text)
    resolved = {key: pause_doc.get(key) for key in _PAUSE_RECORD_FIELDS}
    resolved["answer"] = "text"
    resolved["answer_length"] = len(text)
    resolved["answered_at"] = stamp
    pause_doc["answer"] = "answered"
    pause_doc["answers"] = [*(pause_doc.get("answers") or []), resolved]
    pause_path.write_text(json.dumps(pause_doc, indent=2))
    return None


_PLAN_PAUSE_CHILD_FIELDS = (
    "child_path",
    "child_name",
    "child_max_cost_usd",
    # D2: the approval is of these bytes under this policy, so the pause
    # document carries both and the launch re-derives both.
    "child_sha256",
    "child_policy",
)


def _plan_pause_info(lane_name: str, plan: dict) -> dict:
    """E10: the `kind: "child"` pause a parked plan lane raises. Built in two
    places -- when the lane's own dispatch settles ok, and (D1) when a later
    run finds it still parked because the answer it saw named another lane --
    so the operator sees the same question either way."""
    info = {
        "kind": "child",
        "lane": lane_name,
        "spent_usd": None,
        "threshold": None,
        "reason": (f"lane {lane_name} planned a mission and is waiting for the operator"),
        "question": (
            f"Lane '{lane_name}' planned mission '{plan['child_name']}' "
            f"(${plan['child_max_cost_usd']:.2f}); launch it?"
        ),
    }
    for key in _PLAN_PAUSE_CHILD_FIELDS:
        info[key] = plan.get(key)
    return info


def _launch_plan_child(
    lane: Lane,
    parked: LaneResult,
    *,
    base: Path,
    mission_id: str,
    mission_dir: Path,
    ledger: Ledger,
    stop_answer: dict | None,
    child_base_dir: str | None = None,
    parent: Mission | None = None,
    stamp: str | None = None,
) -> tuple[LaneResult, str | None, tuple[float, int] | None]:
    """E10: resolve a parked plan lane's unconditional pause. `stop` fails
    the lane naming the refusal, without ever loading the deliverable again.
    `continue` loads it fresh, stamps the child's `depth` and `parent`,
    clamps its `max_cost_usd` to the parent's remaining ledger (E10 second
    spec, item 1), mints its mission id and receipts the launch before
    dispatching anything (item 2), then launches it synchronously in this
    process -- its own directory, running lock, and receipt chain, never the
    parent's. Returns the finalized `LaneResult`, the launched child's
    mission id (None when nothing launched, i.e. the operator said stop),
    and a (cost_usd, unpriced_dispatches) rollup for the caller's ledger,
    None until the child reaches its own finality.

    D2: the approval is of the bytes that were checked. Before anything is
    launched the child file is read and hashed again and the launch is
    refused if the digest moved since the park, and `_plan_check_child`'s
    own gates (depth, a bounded budget within the parent's remaining, a
    ceiling no looser, a clean dry run) are rerun in full rather than
    assumed -- an edit between the park and the answer changes none of them
    otherwise, and only `max_cost_usd` was ever re-read here. Every one of
    those is a recorded lane failure, never an exception out of the
    scheduler: `parent` is the parent mission the gates are checked
    against, and a call without it refuses rather than launching unchecked.

    `stamp`: see the module docstring's monkeypatch note. `None` (the
    default) computes "now" here; `mission.run_mission`'s caller always
    passes its own `datetime.now(UTC)` stamp explicitly instead.
    """
    from . import mission as mission_mod

    plan = dict(parked.plan or {})
    resolved = replace(parked)
    resolved.attempts = [dict(attempt) for attempt in parked.attempts]
    resolved.kinds = list(parked.kinds)

    def _fail(message: str) -> LaneResult:
        return _plan_lane_failure(resolved, message, plan=plan)

    if (
        stop_answer is not None
        and stop_answer.get("kind") == "child"
        and stop_answer.get("lane") == lane.name
    ):
        return _fail("child launch refused by the operator"), None, None

    if parent is None:
        return _fail("cannot re-check the approved child plan: no parent mission"), None, None
    try:
        # The same base as `_plan_check_child`'s own load, which falls back
        # to the parent's cwd: a relative child path must resolve to the same
        # file at launch as it did at the park, or the digest below reads as
        # a changed plan when nothing changed.
        child = mission_mod.load_mission(
            plan["child_path"], base_dir=child_base_dir or parent.cwd
        )
    except MissionInvalid as exc:
        return _fail(f"child plan no longer loads: {exc}"), None, None

    # D2: the operator approved bytes, not a path. Re-read and re-hash them
    # before anything else; an approval that cannot be tied to what is on
    # disk now is not an approval of it.
    approved = plan.get("child_sha256")
    current = _child_digest(child.source)
    if not isinstance(approved, str) or current is None or current != approved:
        old8 = approved[:8] if isinstance(approved, str) else "unrecorded"
        new8 = current[:8] if current is not None else "unreadable"
        return (
            _fail(
                f"child plan changed since it was approved: {old8} -> {new8}; "
                "re-run to approve the revised plan"
            ),
            None,
            None,
        )

    # And the gates the park ran are rerun in full against this ledger --
    # the digest proves the file did not move, these prove the parent's
    # budget and ceiling still admit it.
    rechecked, refusal = _plan_check_child(
        parent,
        plan["child_path"],
        ledger=ledger,
        base=base,
        lane_cwd=child_base_dir,
        # The cap the parent's ledger can no longer cover is clamped below,
        # not refused: that is what E10 second spec item 1 asks for, and the
        # ledger is what actually stops the child either way.
        clamp_budget=True,
    )
    if refusal is not None:
        plan["refused"] = refusal
        return _fail(f"child plan no longer passes its checks: {refusal}"), None, None
    plan["child_policy"] = rechecked["child_policy"]

    child.depth = plan["depth"]
    child.parent = {"mission_id": mission_id, "lane": lane.name}

    # Item 1: the child's budget is the parent's -- clamp its own cap to
    # what the parent's ledger actually has left, right here at launch
    # (`_plan_check_child`'s own bound, checked at park time, can have
    # drifted by now).
    parent_remaining = ledger.remaining()
    if parent_remaining is not None:
        if child.max_cost_usd is None:
            # D2: an edit that dropped the cap used to hit an `assert` on the
            # scheduler's own thread and take the resume down with it.
            return (
                _fail("child has no max_cost_usd; a planned mission's budget must be bounded"),
                None,
                None,
            )
        child.max_cost_usd = min(child.max_cost_usd, parent_remaining)
        child.budget_from_parent = True

    # Item 2: mint the child's id now and receipt the launch before it
    # dispatches anything, so a crash mid-child still leaves a receipt
    # naming it.
    launch_stamp = (
        stamp if stamp is not None else datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    child_id, _child_dir = mission_mod.claim_dir(
        base / "missions", f"{launch_stamp}-{_slug(child.name, default='mission')}"
    )
    plan["child"] = {"mission_id": child_id, "state": "launched"}
    resolved.plan = plan
    (mission_dir / "lanes" / f"{lane.name}.json").write_text(
        json.dumps(resolved.to_dict(), indent=2)
    )

    child_result = mission_mod.run_mission(child, home=base, mission_id=child_id)
    rolled_up = child_result.paused is None
    plan["child"] = {
        "mission_id": child_result.mission_id,
        "ok": child_result.ok,
        "cost_usd": child_result.cost_usd,
        "paused": child_result.paused is not None,
        "report_path": child_result.report_path,
        "state": "finished",
        "rolled_up": rolled_up,
    }
    rollup = (
        (child_result.cost_usd, child_result.budget["unpriced_dispatches"])
        if rolled_up
        else None
    )
    if child_result.paused is not None:
        result = _fail(f"child paused: {child_result.mission_id}")
    elif not child_result.ok:
        result = _fail(f"child mission '{child_result.mission_id}' was not ok")
    else:
        resolved.ok = True
        resolved.plan = plan
        result = resolved
    return result, child_result.mission_id, rollup


def read_pause(mission_dir: Path) -> dict | None:
    """The pause document for this mission, or None when there is none (or
    it cannot be read as a JSON object) -- what `run_mission` reads before
    deciding whether an operator's answer applies to anything."""
    from . import mission as mission_mod

    return mission_mod._json_object(mission_dir / "pause.json")


def record_pause_answer(
    pause_path: Path,
    pause_doc: dict,
    answer: str,
    *,
    answered_at: str | None = None,
) -> dict:
    """Record a non-human pause's `continue` or `stop` answer to
    `pause.json`, returning the resolved record `run_mission` carries
    forward as `pause_answer` (and, on `stop`, as `stop_answer` too).

    `answered_at`: see the module docstring's monkeypatch note."""
    resolved = {key: pause_doc.get(key) for key in _PAUSE_RECORD_FIELDS}
    resolved["answer"] = answer
    resolved["answered_at"] = (
        answered_at if answered_at is not None else datetime.now(UTC).isoformat()
    )
    pause_doc["answer"] = answer
    pause_doc["answers"] = [*(pause_doc.get("answers") or []), resolved]
    pause_path.write_text(json.dumps(pause_doc, indent=2))
    return resolved


def write_pause_park(
    mission_dir: Path,
    pause_info: dict,
    ledger_budget: dict,
    *,
    asked_at: str | None = None,
) -> dict:
    """Write `pause.json` when a lane parks the mission (C2): the pause's own
    kind, lane, threshold and question, the ledger's figures at the moment
    of the park (a mission can park while other lanes are still dispatching,
    so this is the one artifact a paused mission has for them), and any
    plan-child fields (E10). Returns the document written.

    `asked_at`: see the module docstring's monkeypatch note."""
    from . import mission as mission_mod

    prior_pause = mission_mod._json_object(mission_dir / "pause.json") or {}
    new_pause_doc = {
        "kind": pause_info["kind"],
        "lane": pause_info["lane"],
        "spent_usd": pause_info["spent_usd"],
        "threshold": pause_info["threshold"],
        "asked_at": asked_at if asked_at is not None else datetime.now(UTC).isoformat(),
        "question": pause_info["question"],
        "answer": None,
        "answers": prior_pause.get("answers") or [],
        "budget": ledger_budget,
    }
    if "ask_path" in pause_info:
        new_pause_doc["ask_path"] = pause_info["ask_path"]
    for key in _PLAN_PAUSE_CHILD_FIELDS:
        if key in pause_info:
            new_pause_doc[key] = pause_info[key]
    (mission_dir / "pause.json").write_text(json.dumps(new_pause_doc, indent=2))
    return new_pause_doc
