"""Lane-graph policy: taint propagation, template references, self-judging
detection, and the `Mission` validation checks that need the whole lane list.

Peer review 2026-09-07 (docs/review/2026-09-07-astra-notes.md, section 5 item
4) asked for this policy to be sliced out of mission.py by invariant, not by
line count, so the two defects it repairs stay pinned by tests that name
them:

D3: taint used to depend on lane declaration order, because a single forward
pass computed each lane's inherited taint as the lane list was built, and a
`needs` edge may point forward (the source declared after the consumer).
`_propagate_taint` below runs to a fixed point over the whole lane list
instead, so the order lanes are written in cannot change the result.

D4: the resolver lane pasted every candidate sink's patch into its prompt
without being checked as a taint sink itself. `resolve_taint_sources` derives
its taint from every sink the same way `collate_taint_sources` already did
for the collate, so `Mission.validate` refuses a resolver that cannot take
tainted input before any dispatch is spent.

`MissionInvalid` lives here, not in mission.py: the functions below raise it
and mission.py imports this module for them, so mission.py re-exports the
class back (`from .graph import MissionInvalid`) rather than the two modules
importing each other. This module takes `Mission` and `Lane` objects as
arguments and reads their attributes; it must never import `mission` at
module level, or the cycle comes right back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .fleets import VENDORS, model_vendor

if TYPE_CHECKING:
    from .mission import Lane, LaneResult, Mission


class MissionInvalid(ValueError):
    """A mission file that cannot be run as written. Raised at load time."""


# A lane's place in a pipeline. "review" lanes are read mode, "build",
# "fix", and "adversarial" lanes are write mode; a lane may leave stage
# unset and be none of these. A stage is also what a mission's `policy`
# restricts by vendor. "adversarial" (E16) is appended, never inserted, so
# every existing message that joins STAGES keeps its old text as a prefix.
STAGES = ("build", "review", "fix", "adversarial")
_STAGE_MODE = {"build": "write", "review": "read", "fix": "write", "adversarial": "write"}
_LANE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

_SELF_JUDGING_VALUES = ("allow",)

# The template grammar, closed: a lane's answer or diff, or the mission's
# own prompt. Anything else between double braces is refused at load.
_TEMPLATE = re.compile(
    r"\{\{\s*(?:lanes\.([A-Za-z0-9._-]+)\.(answer|diff|test_touched|verdict|deliverable)"
    r"|(mission\.prompt))\s*\}\}"
)
_ANY_BRACES = re.compile(r"\{\{[^{}]*\}\}")


def _template_refs(text: str, where: str) -> list[tuple[str, str, bool]]:
    """Every template reference in `text`; anything else between double
    braces is refused, so a misspelt reference cannot render as `(none)`."""
    refs: list[tuple[str, str, bool]] = []
    for raw in _ANY_BRACES.findall(text):
        m = _TEMPLATE.fullmatch(raw)
        if m is None:
            raise MissionInvalid(
                f"lane '{where}': unknown template {raw}; use {{{{lanes.<name>.answer}}}}, "
                "{{lanes.<name>.diff}}, {{lanes.<name>.test_touched}}, "
                "{{lanes.<name>.verdict}}, {{lanes.<name>.deliverable}}, or {{mission.prompt}}"
            )
        refs.append((m.group(1) or "", m.group(2) or "", bool(m.group(3))))
    return refs


def _propagate_taint(lanes: list[Lane]) -> None:
    """D3: mark every lane that inherits taint, over the whole lane graph.

    A lane inherits taint from any lane it references in a template -- every
    field `_template_refs` parses (`answer`, `diff`, `deliverable`,
    `verdict`, `test_touched`), on every attempt including the cascade one --
    and from the lane whose session it `resume`s, whenever that source lane
    is itself tainted or declares `untrusted_output`.

    A `needs` edge may point forward, so the source may be declared after the
    consumer; this walk therefore runs to a fixed point over the complete
    lane list rather than trusting declaration order (which is what a single
    forward pass inside the build loop did, leaving the same two lanes
    tainted or not depending on which one was written first). Taint only ever
    spreads, so every pass either marks a lane and runs again or the graph
    has settled.

    A human lane is tainted at load with the sentinel `taint_from` value
    `["human"]`, which names no lane: it is left exactly as `_human_lane`
    built it, and stays a taint source for everything that reads it.
    """
    by_name = {lane.name: lane for lane in lanes}
    order = {lane.name: index for index, lane in enumerate(lanes)}
    sources: dict[str, list[str]] = {}
    for lane in lanes:
        if lane.human:
            continue
        refs: set[str] = set()
        for attempt in lane.attempts:
            for ref_lane, _ref_field, is_mission in _template_refs(attempt.prompt, lane.name):
                if is_mission or ref_lane == lane.name or ref_lane not in by_name:
                    continue
                refs.add(ref_lane)
        if lane.resume is not None and lane.resume in by_name and lane.resume != lane.name:
            refs.add(lane.resume)
        sources[lane.name] = sorted(refs, key=lambda name: order[name])

    changed = True
    while changed:
        changed = False
        for lane in lanes:
            if lane.human:
                continue
            taint_from = [
                name
                for name in sources[lane.name]
                if by_name[name].tainted or by_name[name].untrusted_output
            ]
            tainted = lane.tainted or bool(taint_from)
            if taint_from != lane.taint_from or tainted != lane.tainted:
                lane.taint_from = taint_from
                lane.tainted = tainted
                changed = True


def _self_judging_findings(mission: Mission) -> list[tuple[str, str, str]]:
    """Every (judge, judged, vendor) pair where a judge could score a lane on
    its own vendor: a verdict lane or a `stage: review` lane against its
    `base`, and the collate against any lane it collates over (every lane in
    the mission).

    "Could" rather than "does": which attempt of a lane ends up final is not
    known at load time, so a shared vendor on any attempt (fallbacks
    included) is enough to flag the pair.
    """
    by_name = {lane.name: lane for lane in mission.lanes}
    findings: list[tuple[str, str, str]] = []
    for lane in mission.lanes:
        judges = lane.stage == "review" or any(a.verdict is not None for a in lane.attempts)
        if lane.base is None or not judges:
            continue
        judged = by_name.get(lane.base)
        if judged is None:
            continue
        judge_vendors = {model_vendor(a.fleet, a.model) for a in lane.attempts}
        judged_vendors = {model_vendor(a.fleet, a.model) for a in judged.attempts}
        for vendor in sorted(judge_vendors & judged_vendors):
            findings.append((lane.name, lane.base, vendor))
    if mission.collate is not None:
        judge_labels = [("collate", mission.collate.fleet, mission.collate.model)] + [
            (f"collate.judges[{i}]", judge.fleet, judge.model)
            for i, judge in enumerate(mission.collate.judges)
        ]
        for label, fleet, model in judge_labels:
            judge_vendor = model_vendor(fleet, model)
            for lane in mission.lanes:
                lane_vendors = {model_vendor(a.fleet, a.model) for a in lane.attempts}
                if judge_vendor in lane_vendors:
                    findings.append((label, lane.name, judge_vendor))
    return findings


def _tainted_names(candidates: list[Lane] | list[LaneResult]) -> list[str]:
    """Names of the taint sources among `candidates`: any lane or lane
    result whose `tainted` or `untrusted_output` is set, in the candidates'
    own order. Shared by `resolve_taint_sources` (load time, over every sink)
    and `_run_resolve` (dispatch time, over the sinks that actually produced
    a patch -- always a subset of what loaded)."""
    return [c.name for c in candidates if c.tainted or c.untrusted_output]


@dataclass(frozen=True)
class CollateTaintSources:
    """What `Mission.validate` needs to gate the collate's own dispatch
    against a tainted candidate. `tainted_lanes` names the taint sources
    among the collate's candidate pool -- `mission.sinks()` when the collate
    narrows to ranked sinks (`candidates` non-zero), else every lane in the
    mission (D2: `_run_collate` hands every lane to `_collate_candidates`,
    which only narrows to sinks under that same condition) -- in mission
    order; `tainted` is whether that list is non-empty, the collate's own
    dispatch taint flag. The candidate pool itself is not carried here:
    nothing downstream of `validate` reads it, only the names."""

    tainted_lanes: list[str]

    @property
    def tainted(self) -> bool:
        return bool(self.tainted_lanes)


def collate_taint_sources(mission: Mission) -> CollateTaintSources:
    assert mission.collate is not None
    candidate_pool = mission.sinks() if mission.collate.candidates else mission.lanes
    return CollateTaintSources(tainted_lanes=_tainted_names(candidate_pool))


@dataclass(frozen=True)
class ResolveTaintSources:
    """What `Mission.validate` needs to gate the resolver's own dispatch.
    `sinks` is `mission.sinks()` (also used by `validate`'s own sink-count and
    sink-cwd checks ahead of this); `tainted_sinks` names the taint sources
    among them, in mission order."""

    sinks: list[Lane]
    tainted_sinks: list[str]


def resolve_taint_sources(mission: Mission) -> ResolveTaintSources:
    sinks = mission.sinks()
    return ResolveTaintSources(sinks=sinks, tainted_sinks=_tainted_names(sinks))


def validate_graph(mission: Mission, names: set[str]) -> None:
    """Needs and bases name real lanes, never the lane itself, and form
    no cycle; every template reference is to a declared need."""
    by_name = {lane.name: lane for lane in mission.lanes}
    for lane in mission.lanes:
        for need in lane.needs:
            if need not in names:
                raise MissionInvalid(f"lane '{lane.name}' needs unknown lane '{need}'")
            if need == lane.name:
                raise MissionInvalid(f"lane '{lane.name}' needs itself")
        if lane.base is not None and lane.base not in lane.needs:
            raise MissionInvalid(f"lane '{lane.name}': base '{lane.base}' must be a need")
        # E7: a human lane holds no commit and no fleet session -- naming
        # one as another lane's base or resume is refused here, the same
        # place an unknown or self-referential base/resume already is.
        if lane.base is not None and by_name[lane.base].human:
            raise MissionInvalid(
                f"lane '{lane.name}': base '{lane.base}' is a human lane, "
                "which holds no commit to build on"
            )
        # E10: a plan lane is read mode and never commits -- naming one as
        # another lane's base or resume is refused here, the same place a
        # human lane's is, above.
        if lane.base is not None and by_name[lane.base].plan:
            raise MissionInvalid(
                f"lane '{lane.name}': base '{lane.base}' is a plan lane, "
                "which holds no commit to build on"
            )
        if lane.resume is not None:
            if lane.resume not in names:
                raise MissionInvalid(
                    f"lane '{lane.name}' resumes unknown lane '{lane.resume}'"
                )
            if lane.resume == lane.name:
                raise MissionInvalid(f"lane '{lane.name}' resumes itself")
            if lane.resume not in lane.needs and lane.resume != lane.base:
                raise MissionInvalid(
                    f"lane '{lane.name}': resume '{lane.resume}' must be in needs or be base"
                )
            if by_name[lane.resume].human:
                raise MissionInvalid(
                    f"lane '{lane.name}': resume '{lane.resume}' is a human lane, "
                    "which holds no session to resume"
                )
            if by_name[lane.resume].script:
                raise MissionInvalid(
                    f"lane '{lane.name}': resume '{lane.resume}' is a script lane, "
                    "which holds no session to resume"
                )
            if by_name[lane.resume].plan:
                raise MissionInvalid(
                    f"lane '{lane.name}': resume '{lane.resume}' is a plan lane, "
                    "which holds no session to resume"
                )
        for attempt in lane.attempts:
            for ref_lane, ref_field, is_mission in _template_refs(attempt.prompt, lane.name):
                if is_mission:
                    if not mission.prompt:
                        raise MissionInvalid(
                            f"lane '{lane.name}' uses {{{{mission.prompt}}}} "
                            "but the mission sets no prompt"
                        )
                elif ref_lane not in lane.needs:
                    raise MissionInvalid(
                        f"lane '{lane.name}' references lanes.{ref_lane}.{ref_field} "
                        f"but does not list '{ref_lane}' in needs"
                    )
                elif (
                    ref_field in ("diff", "test_touched", "verdict")
                    and by_name[ref_lane].human
                ):
                    raise MissionInvalid(
                        f"lane '{lane.name}' references lanes.{ref_lane}.{ref_field}, but "
                        f"'{ref_lane}' is a human lane with no {ref_field}"
                    )
    # Cycle check: a lane can never wait on something that waits on it.
    needs = {lane.name: set(lane.needs) for lane in mission.lanes}
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            cycle = " -> ".join(path[path.index(name) :] + [name])
            raise MissionInvalid(f"lanes depend on each other in a cycle: {cycle}")
        state[name] = 1
        for need in needs[name]:
            visit(need, path + [name])
        state[name] = 2

    for name in needs:
        visit(name, [])


def validate_policy(mission: Mission) -> None:
    """A4: reviewer direction is a policy, not a free choice. Evidence
    (docs/ROADMAP-2026-09.md A4): Claude reviewing Codex lifted pass rate
    71.6% -> 89.7%; Codex reviewing Claude dropped it 91.4% -> 82.8%.
    `policy` restricts which vendors may run a staged lane, per stage."""
    if mission.policy is None:
        return
    declared = {lane.stage for lane in mission.lanes if lane.stage is not None}
    for stage, rule in mission.policy.items():
        if stage not in STAGES:
            raise MissionInvalid(
                f"policy: unknown stage {stage!r}; known: {', '.join(STAGES)}"
            )
        unknown_vendors = [v for v in rule["vendors"] if v not in VENDORS]
        if unknown_vendors:
            raise MissionInvalid(
                f"policy for stage '{stage}': unknown vendor {unknown_vendors[0]!r}; "
                f"known: {', '.join(VENDORS)}"
            )
        if stage not in declared:
            raise MissionInvalid(f"policy names stage '{stage}' but no lane declares it")
    for lane in mission.lanes:
        if lane.stage is None or lane.stage not in mission.policy:
            continue
        allowed = mission.policy[lane.stage]["vendors"]
        for attempt in lane.attempts:
            vendor = model_vendor(attempt.fleet, attempt.model)
            if vendor not in allowed:
                raise MissionInvalid(
                    f"lane '{lane.name}' ({attempt.label()}) is on vendor '{vendor}'; "
                    f"policy allows {', '.join(allowed)} for stage {lane.stage}"
                )


def validate_self_judging(mission: Mission) -> None:
    if mission.self_judging is not None and mission.self_judging not in _SELF_JUDGING_VALUES:
        raise MissionInvalid(
            f"self_judging must be one of {', '.join(_SELF_JUDGING_VALUES)}, "
            f"got {mission.self_judging!r}"
        )
    if mission.self_judging in _SELF_JUDGING_VALUES:
        return
    findings = _self_judging_findings(mission)
    if findings:
        judge, judged, vendor = findings[0]
        raise MissionInvalid(
            f"'{judge}' judges '{judged}', both on vendor '{vendor}'; "
            "set self_judging: allow to permit this, or route one of them to a "
            "different vendor"
        )


def validate_pause(mission: Mission, names: set[str]) -> None:
    """C2: `pause.before` names real lanes, and `pause.spend_usd` leaves
    room under `max_cost_usd` for the operator to actually see the pause
    before the ledger itself would have refused the next dispatch."""
    if mission.pause is None:
        return
    unknown = [name for name in mission.pause["before"] if name not in names]
    if unknown:
        raise MissionInvalid(f"pause.before names unknown lane '{unknown[0]}'")
    spend_usd = mission.pause["spend_usd"]
    if spend_usd is not None:
        if spend_usd <= 0:
            raise MissionInvalid("pause.spend_usd must be positive")
        if mission.max_cost_usd is not None and spend_usd >= mission.max_cost_usd:
            raise MissionInvalid("pause.spend_usd must be below max_cost_usd")


def validate_quorum(mission: Mission, names: set[str]) -> None:
    if not isinstance(mission.require, dict):
        return
    if set(mission.require) != {"pass", "of"}:
        raise MissionInvalid("quorum require must contain exactly 'pass' and 'of'")
    needed = mission.require["pass"]
    selected = mission.require["of"]
    if isinstance(needed, bool) or not isinstance(needed, int) or needed < 1:
        raise MissionInvalid("quorum pass must be an integer at least 1")
    if not isinstance(selected, list) or not all(isinstance(name, str) for name in selected):
        raise MissionInvalid("quorum of must be a list of lane names")
    if len(selected) < 2:
        raise MissionInvalid("quorum of must name at least two lanes")
    if len(selected) > 3:
        raise MissionInvalid(
            "quorum of may name at most three lanes; three judges with a dissent "
            "slot tally better than five"
        )
    if len(set(selected)) != len(selected):
        raise MissionInvalid("quorum lane names must be unique")
    unknown = [name for name in selected if name not in names]
    if unknown:
        raise MissionInvalid(f"quorum names unknown lane '{unknown[0]}'")
    if needed > len(selected):
        raise MissionInvalid("quorum pass cannot exceed the number of lanes in of")
    if len(selected) == 3 and needed == 3:
        raise MissionInvalid("quorum of three needs a dissent slot: pass must be at most 2")
    by_name = {lane.name: lane for lane in mission.lanes}
    for name in selected:
        if any(attempt.verdict is None for attempt in by_name[name].attempts):
            raise MissionInvalid(
                f"quorum lane '{name}' must have a verdict on every attempt"
            )
    vendors = {
        model_vendor(attempt.fleet, attempt.model)
        for name in selected
        for attempt in by_name[name].attempts
    }
    if len(vendors) < 2:
        raise MissionInvalid("quorum lanes must span at least two vendors")
