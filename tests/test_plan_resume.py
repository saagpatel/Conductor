"""E10 second spec: budget rollup and a defined resume over a plan lane's
launched child.

Builds on `test_plan_lanes.py`'s fixtures and helpers -- see that file's
module docstring for the shape of a plan lane, its park, and its answered
launch. This file covers what happens once a child has actually launched:
its budget clamp, the pre-launch receipt, the ledger rollup, and the resume
outcomes (`_resume_plan_child`) a parent whose plan lane already named a
child can land on.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_plan_lanes import _child_raw, _plan_lane, _snapshot, _write_deliverable_argv, envelope

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def _continue(first, home):
    return run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )


def _resume(result, home):
    return run_mission(
        _snapshot(result), home=home, resume_dir=Path(result.mission_dir), answer=None
    )


def _lanes_plan_path(mission_dir: str) -> Path:
    return Path(mission_dir) / "lanes" / "plan.json"


def _plan_child(result) -> dict:
    return next(lane for lane in result.lanes if lane["name"] == "plan")["plan"]["child"]


def _corrupt_to_launched(mission_dir: str, *, mission_id: str) -> None:
    """Simulate the parent process dying between item 2's pre-launch receipt
    write and the child's own finish: overwrite the on-disk plan lane
    receipt's `plan.child` back to the shape `_launch_plan_child` writes
    before it ever calls `run_mission(child)` -- `ok` stays true, the same
    as the pre-launch write. The parent's own `result.json` is rolled back
    the same way (a crash there never reaches the code that finalizes it),
    so a later resume must re-derive the real outcome from the child's own
    directory rather than trust a stale 'finished' receipt or double-count
    a rollup `result.json` had already recorded."""
    lane_path = _lanes_plan_path(mission_dir)
    lane_raw = json.loads(lane_path.read_text())
    lane_raw["plan"]["child"] = {"mission_id": mission_id, "state": "launched"}
    lane_raw["ok"] = True
    lane_path.write_text(json.dumps(lane_raw, indent=2))

    result_path = Path(mission_dir) / "result.json"
    result_raw = json.loads(result_path.read_text())
    result_raw["children_cost_usd"] = 0.0
    result_raw["children"] = [c for c in result_raw.get("children") or [] if c != mission_id]
    result_path.write_text(json.dumps(result_raw, indent=2))


def _corrupt_result_after_finalized_rollup(mission_dir: str, *, child_cost_usd: float) -> None:
    """Simulate the parent process dying after `settle()` writes the plan
    lane's rolled-up receipt (`state: "finished"`, `rolled_up: true`,
    written before `result.json` is finalized -- see the E10 second spec
    comment above the `plan_children` loop in `_execute_mission`) but before
    `result.json` itself is rewritten with that rollup baked in: the lane
    receipt already claims the rollup happened while the mission-level
    record does not yet show it."""
    result_path = Path(mission_dir) / "result.json"
    result_raw = json.loads(result_path.read_text())
    result_raw["children"] = []
    result_raw["cost_usd"] = round(result_raw["cost_usd"] - child_cost_usd, 6)
    result_raw["children_cost_usd"] = 0.0
    budget = result_raw.get("budget")
    if isinstance(budget, dict):
        budget["cost_usd"] = result_raw["cost_usd"]
    result_path.write_text(json.dumps(result_raw, indent=2))


# --- item 1: the child's budget is the parent's -----------------------------


def test_child_budget_is_unclamped_under_a_budgetless_parent(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict({"cwd": str(repo), "lanes": [_plan_lane()]}, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)

    child_block = _plan_child(resumed)
    child_snapshot = json.loads(
        (Path(child_block["report_path"]).parent / "mission.json").read_text()
    )
    assert child_snapshot["budget_from_parent"] is False
    assert child_snapshot["max_cost_usd"] == 5.0


def test_unpriced_child_makes_the_parent_unverifiable(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)

    # CHILDBUILD's own dispatch reports no usage at all -- unpriced (no
    # tokens for `prices.estimate` to price), so the child's own budget
    # (and, once rolled up, the parent's) cannot be verified.
    unpriced = json.dumps({"result": "done"})
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{unpriced}'"]
    )
    resumed = _continue(first, home)

    child_block = _plan_child(resumed)
    assert child_block["rolled_up"] is True
    assert resumed.budget["unverifiable"] is True
    assert resumed.ok is False


# --- item 2: the receipt before the launch ----------------------------------


def test_receipt_names_the_child_before_it_dispatches(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    seen: dict = {}

    def build(spec):
        if spec.prompt.split()[0] == "CHILDBUILD":
            # The parent's own receipt must already name the child and say
            # "launched" before this, the child's *own* first dispatch, ever
            # runs.
            raw = json.loads(_lanes_plan_path(first.mission_dir).read_text())
            seen["child"] = raw["plan"]["child"]
        return ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    resumed = _continue(first, home)

    assert seen["child"]["state"] == "launched"
    assert "ok" not in seen["child"]
    assert seen["child"]["mission_id"] == _plan_child(resumed)["mission_id"]


# --- item 3: resume over a child --------------------------------------------


def test_resume_refuses_while_the_child_is_still_running(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)
    child_id = _plan_child(resumed)["mission_id"]
    child_dir = Path(home) / "missions" / child_id

    _corrupt_to_launched(resumed.mission_dir, mission_id=child_id)
    (child_dir / "running.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
            }
        )
    )

    with pytest.raises(MissionInvalid, match=f"child '{child_id}' is still running"):
        _resume(resumed, home)


def test_resume_refuses_while_the_child_is_paused(repo, home, fake_fleet, tmp_path):
    child_raw = _child_raw(
        repo, max_cost_usd=5.0, name="paused-child", pause={"before": ["childbuild"]}
    )
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    resumed = _continue(first, home)
    assert resumed.ok is False
    child_id = _plan_child(resumed)["mission_id"]

    _corrupt_to_launched(resumed.mission_dir, mission_id=child_id)

    with pytest.raises(MissionInvalid, match=f"child '{child_id}' is paused; resume it first"):
        _resume(resumed, home)


def test_resume_adopts_a_child_that_finished_ok_rolling_up_exactly_once(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)
    assert resumed.ok is True
    child_id = _plan_child(resumed)["mission_id"]

    _corrupt_to_launched(resumed.mission_dir, mission_id=child_id)
    third = _resume(resumed, home)
    assert third.ok is True
    plan_lane = next(lane for lane in third.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is True
    assert plan_lane["plan"]["child"]["rolled_up"] is True
    assert third.children_cost_usd == pytest.approx(0.2)
    assert third.cost_usd == pytest.approx(0.3)
    assert any(
        note.startswith(f"child '{child_id}' spent") and "rolled into this budget" in note
        for note in third.notes
    )

    # A further resume must not roll the same child's spend in a second time.
    fourth = _resume(third, home)
    assert fourth.children_cost_usd == pytest.approx(0.2)
    assert fourth.cost_usd == pytest.approx(0.3)


def test_resume_recovers_a_rollup_whose_finalize_never_landed(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    """Cross-vendor review (Grok, finding 1): `settle()` writes the plan
    lane's receipt (`state: "finished"`, `rolled_up: true`) before
    `result.json` is rewritten with the rollup folded in. If the process
    dies in that window, `result.json` on disk still shows the rollup as
    never having happened even though the lane receipt says it did. A
    further resume must not trust the stale receipt at face value -- it
    must notice the mission-level record disagrees and redo the rollup,
    also restoring the child's id to `children` (finding 2)."""
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)
    assert resumed.ok is True
    child_id = _plan_child(resumed)["mission_id"]
    plan_lane = next(lane for lane in resumed.lanes if lane["name"] == "plan")
    assert plan_lane["plan"]["child"]["rolled_up"] is True
    assert resumed.children_cost_usd == pytest.approx(0.2)

    _corrupt_result_after_finalized_rollup(resumed.mission_dir, child_cost_usd=0.2)

    third = _resume(resumed, home)
    assert third.ok is True
    assert child_id in third.children
    assert third.children_cost_usd == pytest.approx(0.2)
    assert third.cost_usd == pytest.approx(0.3)

    # A further resume must not roll the same child's spend in a second time.
    fourth = _resume(third, home)
    assert fourth.children_cost_usd == pytest.approx(0.2)
    assert fourth.cost_usd == pytest.approx(0.3)
    assert fourth.children.count(child_id) == 1


def test_resume_adopts_a_child_that_finished_not_ok(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)

    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "exit 1"])
    resumed = _continue(first, home)
    assert resumed.ok is False
    child_id = _plan_child(resumed)["mission_id"]

    _corrupt_to_launched(resumed.mission_dir, mission_id=child_id)
    third = _resume(resumed, home)
    plan_lane = next(lane for lane in third.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is False
    assert plan_lane["attempts"][-1]["error"] == f"plan: child mission '{child_id}' was not ok"
    assert plan_lane["plan"]["child"]["rolled_up"] is True
    assert third.paused is None


def test_resume_fails_the_lane_when_the_child_is_missing(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)
    bogus_id = "20200101T000000Z-never-existed"

    _corrupt_to_launched(resumed.mission_dir, mission_id=bogus_id)
    third = _resume(resumed, home)
    plan_lane = next(lane for lane in third.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is False
    assert plan_lane["attempts"][-1]["error"] == f"plan: child '{bogus_id}' missing"
    assert third.paused is None


# --- item 4: report, and `conductor missions` -------------------------------


def test_report_has_a_children_section(repo, home, fake_fleet, monkeypatch, tmp_path):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)

    report = Path(resumed.report_path).read_text()
    assert "## Children" in report
    child_id = resumed.children[0]
    assert child_id in report
    assert "finished" in report

    child_report = Path(_plan_child(resumed)["report_path"]).read_text()
    assert f"planned by `{resumed.mission_id}`, lane `plan`" in child_report.splitlines()[0]


def test_missions_listing_marks_parent_and_children(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = _continue(first, home)
    child_id = resumed.children[0]

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["missions"]) == 0
    rows = json.loads(out.getvalue())
    by_id = {row["mission_id"]: row for row in rows}
    assert by_id[resumed.mission_id]["children"] == 1
    assert by_id[child_id]["parent"] == resumed.mission_id


# --- item 5: snapshot round-trip and backfill -------------------------------


def test_snapshot_round_trips_budget_from_parent(tmp_path):
    mission = mission_from_dict(
        {"cwd": str(tmp_path), "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    mission.budget_from_parent = True
    raw = mission.to_dict()
    reloaded = Mission.from_snapshot(raw)
    assert reloaded.to_dict() == raw
    assert reloaded.budget_from_parent is True


def test_backfill_snapshot_fills_missing_budget_from_parent(tmp_path):
    from conductor import golden as golden_mod

    mission = mission_from_dict(
        {"cwd": str(tmp_path), "lanes": [{"name": "a", "fleet": "claude", "prompt": "x"}]},
        base_dir=tmp_path,
    )
    raw = mission.to_dict()
    del raw["budget_from_parent"]
    backfilled = golden_mod._backfill_snapshot(raw)
    reloaded = Mission.from_snapshot(backfilled)
    assert reloaded.budget_from_parent is False
