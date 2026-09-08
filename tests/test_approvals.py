"""F21 slice 2: approval consumption, moved from mission.py into
approvals.py. These tests pin the two defects the peer review
(docs/review/2026-09-07-astra-notes.md, section 5 item 4) found already
repaired on this tree -- D1 (one planner's answered pause used to launch
every parked planner's child) and D2 (an approval was of a path, not of the
bytes the operator was actually shown) -- plus the module boundary the
extraction itself must hold. Every scenario below is expected to pass on the
current code too: the point is to pin a repair already made, not to make
one.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_plan_lanes import _child_raw, _plan_lane, _snapshot, _write_deliverable_argv, envelope

from conductor import runner as runner_mod
from conductor.mission import mission_from_dict, run_mission


def _dual_plan_lanes() -> list[dict]:
    return [
        {
            "name": "planA",
            "fleet": "claude",
            "prompt": "PLANA write the child mission",
            "plan": True,
            "deliverable": {"path": "child_a.json"},
        },
        {
            "name": "planB",
            "fleet": "claude",
            "prompt": "PLANB write the child mission",
            "plan": True,
            "deliverable": {"path": "child_b.json"},
        },
    ]


def _dual_plan_build(child_a: dict, child_b: dict):
    def build(spec):
        first_word = spec.prompt.split()[0]
        if first_word == "PLANA":
            return _write_deliverable_argv(child_a, path="child_a.json")
        if first_word == "PLANB":
            return _write_deliverable_argv(child_b, path="child_b.json")
        return ["sh", "-c", f"echo '{envelope('built')}'"]

    return build


# --- D1: one answer resolves one planner, never every parked planner -------


def test_answered_pause_launches_only_the_named_planner(repo, home, monkeypatch, tmp_path):
    """D1, continue path: a mission with two plan lanes that both park in
    the same pass. Before the repair, an answer to one of them launched
    every parked planner's child; `_execute_mission` now resolves exactly
    the planner the answered pause's `lane` names (`_launch_plan_child`'s
    own `stop_answer.get('lane') == lane.name`-style targeting applies the
    same way to a `continue`), and leaves the other planner parked, asking
    for itself by name."""
    child_a = _child_raw(repo, max_cost_usd=5.0, name="child-a")
    child_b = _child_raw(repo, max_cost_usd=5.0, name="child-b")
    monkeypatch.setattr(runner_mod, "build_argv", _dual_plan_build(child_a, child_b))

    mission = mission_from_dict(
        {"cwd": str(repo), "lanes": _dual_plan_lanes()}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"
    answered_lane = first.paused["lane"]
    other_lane = "planB" if answered_lane == "planA" else "planA"

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )

    assert resumed.children is not None and len(resumed.children) == 1
    answered_receipt = next(lane for lane in resumed.lanes if lane["name"] == answered_lane)
    assert answered_receipt["plan"]["child"]["mission_id"] == resumed.children[0]
    other_receipt = next(lane for lane in resumed.lanes if lane["name"] == other_lane)
    assert other_receipt["plan"].get("child") is None
    assert other_receipt["plan"]["refused"] is None

    pause_doc = json.loads((Path(resumed.mission_dir) / "pause.json").read_text())
    assert pause_doc["answer"] is None
    assert pause_doc["lane"] == other_lane


def test_stop_refuses_only_the_named_planner_and_the_other_can_still_launch(
    repo, home, monkeypatch, tmp_path
):
    """D1, stop path: answering `stop` on whichever of the two parked
    planners' pause fired first must claim no child directory at all, and
    must not touch the other, still-parked planner -- it stays exactly as
    it parked, still asking to launch its own child by name, never
    silently resolved by an answer that named someone else.

    The stop refuses that one launch, not the planner forever: a later
    bare resume reconsiders the refused lane like any other unresolved
    one (it reparks), from where a `continue` launches it, and the
    never-touched planner is asked next in that same resume -- proving
    the stop did not corrupt or permanently block it either."""
    child_a = _child_raw(repo, max_cost_usd=5.0, name="child-a")
    child_b = _child_raw(repo, max_cost_usd=5.0, name="child-b")
    monkeypatch.setattr(runner_mod, "build_argv", _dual_plan_build(child_a, child_b))

    mission = mission_from_dict(
        {"cwd": str(repo), "lanes": _dual_plan_lanes()}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"
    answered_lane = first.paused["lane"]
    other_lane = "planB" if answered_lane == "planA" else "planA"

    stopped = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="stop"
    )
    assert stopped.children == []
    refused = next(lane for lane in stopped.lanes if lane["name"] == answered_lane)
    assert refused["ok"] is False
    assert refused["attempts"][-1]["error"] == "plan: child launch refused by the operator"
    assert refused["plan"].get("child") is None

    untouched = next(lane for lane in stopped.lanes if lane["name"] == other_lane)
    assert untouched["ok"] is True
    assert untouched["plan"].get("child") is None
    assert untouched["plan"]["refused"] is None

    reparked = run_mission(
        _snapshot(stopped), home=home, resume_dir=Path(stopped.mission_dir), answer=None
    )
    assert reparked.paused["lane"] == answered_lane

    launched_first = run_mission(
        _snapshot(reparked), home=home, resume_dir=Path(reparked.mission_dir), answer="continue"
    )
    assert len(launched_first.children) == 1
    assert launched_first.paused["lane"] == other_lane

    launched_both = run_mission(
        _snapshot(launched_first),
        home=home,
        resume_dir=Path(launched_first.mission_dir),
        answer="continue",
    )
    assert launched_both.ok is True
    assert len(launched_both.children) == 2


# --- D2: the approval is of the bytes, not the path -------------------------


def test_launch_refuses_a_child_edited_after_the_park(repo, home, monkeypatch, tmp_path):
    """D2: park a plan lane, then edit the parked child mission file by one
    byte (a trailing space keeps it valid JSON, so the launch fails on the
    digest check specifically rather than a load error). Resuming with
    `--answer continue` must fail the lane with `child plan changed since
    it was approved`, claim no new directory under `missions/`, and leave
    `pause.json`'s own `child_sha256` at the pre-edit value it recorded
    when the operator was actually shown the file."""
    child = _child_raw(repo, max_cost_usd=5.0, name="child-a")
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: _write_deliverable_argv(child, cost=0.1)
    )
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"

    pause_before = json.loads((Path(first.mission_dir) / "pause.json").read_text())
    original_sha256 = pause_before["child_sha256"]
    child_path = Path(first.paused["child_path"])
    with child_path.open("a") as handle:
        handle.write(" ")

    before_dirs = {path.name for path in (home / "missions").iterdir()}
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )
    after_dirs = {path.name for path in (home / "missions").iterdir()}

    assert resumed.ok is False
    assert resumed.children == []
    assert after_dirs == before_dirs
    plan_lane = next(lane for lane in resumed.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is False
    assert "child plan changed since it was approved" in plan_lane["attempts"][-1]["error"]

    pause_after = json.loads((Path(resumed.mission_dir) / "pause.json").read_text())
    assert pause_after["child_sha256"] == original_sha256


# --- item 3: the monkeypatch hazard, the other half -------------------------


def test_launch_plan_child_stamps_the_launched_child_with_the_patched_clock(
    repo, home, monkeypatch, tmp_path
):
    """`_launch_plan_child` also calls `datetime.now(UTC)`, for the
    launched child's own mission-id stamp -- the other half of item 3's
    monkeypatch hazard, alongside `_answer_human_pause`'s `answered_at`
    (pinned in tests/test_human.py's
    `test_answer_text_resumes_and_the_answer_is_fenced_downstream`). The
    call site stays in mission.py, passed down as `stamp=`, for the same
    reason: so a test patching `conductor.mission.datetime` still governs
    it. If that stamp were ever computed inside approvals.py's own
    `datetime` instead, this patch would silently stop applying and the
    launched child's mission id would carry the real clock instead of the
    frozen one."""
    child = _child_raw(repo, max_cost_usd=5.0, name="child-a")
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: _write_deliverable_argv(child, cost=0.1)
    )
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"

    from conductor import mission as mission_mod

    frozen = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    real_datetime = mission_mod.datetime
    monkeypatch.setattr(mission_mod, "datetime", FrozenDatetime)
    try:
        resumed = run_mission(
            _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
        )
    finally:
        monkeypatch.setattr(mission_mod, "datetime", real_datetime)

    assert resumed.ok is True
    assert len(resumed.children) == 1
    assert resumed.children[0].startswith(frozen.strftime("%Y%m%dT%H%M%SZ"))


# --- structural: approvals.py must never import mission at module level ----


class _MissionImportVisitor(ast.NodeVisitor):
    """Finds every `from .mission import ...`, `from . import mission[ as
    x]`, `from conductor.mission import ...`, or `import conductor.mission`
    that is not inside a function body or a `TYPE_CHECKING` block -- every
    spelling that names `conductor.mission` and would reintroduce the
    module cycle if hoisted to module scope."""

    def __init__(self) -> None:
        self.violations: list[ast.AST] = []
        self._function_depth = 0
        self._type_checking_depth = 0

    def _guarded(self) -> bool:
        return self._function_depth > 0 or self._type_checking_depth > 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        if not is_type_checking:
            self.generic_visit(node)
            return
        self._type_checking_depth += 1
        for child in node.body:
            self.visit(child)
        self._type_checking_depth -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._guarded():
            return
        # `from .mission import ...` (level 1, module "mission"),
        # `from . import mission[ as x]` (level 1, module None, an alias
        # named "mission" -- the exact spelling this file's own lazy
        # imports use inside function bodies), and the absolute spelling
        # `from conductor.mission import ...` (level 0) all name the same
        # module and would reintroduce the same cycle at module scope.
        if node.level == 1 and node.module == "mission":
            self.violations.append(node)
        elif node.level == 1 and node.module is None and any(
            alias.name == "mission" for alias in node.names
        ):
            self.violations.append(node)
        elif node.level == 0 and node.module == "conductor.mission":
            self.violations.append(node)
        # And the absolute form of the relative spelling above: `from
        # conductor import mission[ as x]`, which the guard used to miss
        # entirely (2026-09-08 audit of this file).
        elif (
            node.level == 0
            and node.module == "conductor"
            and any(alias.name == "mission" for alias in node.names)
        ):
            self.violations.append(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._guarded():
            return
        for alias in node.names:
            if alias.name in ("conductor.mission", "mission"):
                self.violations.append(node)


def test_approvals_has_no_module_level_import_of_mission():
    """Item 1: mission.py re-exports every name this module defines, so a
    module-level `from .mission import ...` (or `import conductor.mission`)
    here would be a real import cycle at process start -- `from . import
    mission as mission_mod` inside a function body, or a `TYPE_CHECKING`
    guard for type hints only, is what every mission.py-only name this
    module's functions call at runtime actually uses instead."""
    approvals_path = Path(__file__).resolve().parent.parent / "src" / "conductor" / "approvals.py"
    tree = ast.parse(approvals_path.read_text(), filename="approvals.py")
    visitor = _MissionImportVisitor()
    visitor.visit(tree)
    assert visitor.violations == []


def test_the_import_cycle_guard_also_catches_the_relative_module_spelling():
    """Cross-vendor review of f21-approvals: the guard above must reject
    `from . import mission` (and `... as mission_mod`) hoisted to module
    scope exactly as it rejects `from .mission import ...` -- both name
    the same module and reintroduce the same cycle. This is the single
    most likely regression, since it is the exact spelling `approvals.py`
    already uses eight times inside function bodies for the lazy import;
    a check that only looked for `from .mission import ...` would miss
    `from . import mission as mission_mod` moved to module level (`ast`
    represents it as `ImportFrom` with `module is None`, not `"mission"`)."""
    poisoned = (
        "from __future__ import annotations\n"
        "from . import mission as mission_mod\n"
        "def f():\n"
        "    pass\n"
    )
    visitor = _MissionImportVisitor()
    visitor.visit(ast.parse(poisoned, filename="poisoned.py"))
    assert len(visitor.violations) == 1


def test_the_import_cycle_guard_also_catches_the_absolute_spelling():
    """Same regression, absolute form: `from conductor.mission import ...`
    names the same module as `from .mission import ...` but is a level-0
    import (`ast.ImportFrom.level == 0`, `module == "conductor.mission"`),
    which the level-1-only check would miss."""
    poisoned = "from conductor.mission import load_mission\n"
    visitor = _MissionImportVisitor()
    visitor.visit(ast.parse(poisoned, filename="poisoned.py"))
    assert len(visitor.violations) == 1


@pytest.mark.parametrize(
    "poisoned",
    [
        "from conductor import mission\n",
        "from conductor import mission as mission_mod\n",
        "from conductor import ceiling, mission\n",
    ],
)
def test_the_import_cycle_guard_catches_the_absolute_package_spelling(poisoned):
    """The fourth spelling, and the one the guard missed until a 2026-09-08
    audit of this file: `from conductor import mission` is level 0 with
    `module == "conductor"`, so neither the level-1 branch nor the
    `"conductor.mission"` branch saw it. It binds the same module and
    reintroduces the same cycle."""
    visitor = _MissionImportVisitor()
    visitor.visit(ast.parse(poisoned, filename="poisoned.py"))
    assert len(visitor.violations) == 1
