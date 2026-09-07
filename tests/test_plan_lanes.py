"""E10: planner lanes.

A lane whose deliverable is a mission file: conductor loads it, dry-runs it,
and asks the operator to launch it -- see the README's "Planner lanes"
section, `mission.py`'s `_plan_check_child` (every check against the
deliverable's mission file), the unconditional `kind: "child"` park in
`_execute_mission`'s scheduler loop, and `_launch_plan_child` (the operator's
`continue`/`stop` answer, resolved the same way `_answer_human_pause`
resolves a human lane's).
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import golden as golden_mod
from conductor import runner as runner_mod
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def envelope(answer: str = "planned", cost: float | None = None) -> str:
    payload: dict = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": answer,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def _write_deliverable_argv(
    payload: dict, *, path: str = "child.json", cost: float | None = None
) -> list[str]:
    """A fake fleet dispatch that writes `payload` as the plan lane's own
    declared deliverable, then answers ok -- the same shape
    test_deliverables.py's `fake_fleet(["sh", "-c", "echo out > report.txt; ..."])`
    uses, just writing a mission file instead of a plain report."""
    text = json.dumps(payload)
    return [
        "sh",
        "-c",
        f"printf '%s' {shlex.quote(text)} > {path}; printf '%s' {shlex.quote(envelope(cost=cost))}",
    ]


def _plan_lane(*, deliverable_path: str = "child.json", extra: dict | None = None) -> dict:
    lane = {
        "name": "plan",
        "fleet": "claude",
        "prompt": "PLAN write the child mission",
        "plan": True,
        "deliverable": {"path": deliverable_path},
    }
    if extra:
        lane |= extra
    return lane


def _child_raw(
    repo: Path,
    *,
    max_cost_usd: float | None = 5.0,
    ceiling: dict | None = None,
    name: str = "child-mission",
    extra_lanes: list[dict] | None = None,
    pause: dict | None = None,
) -> dict:
    raw: dict = {
        "name": name,
        "cwd": str(repo),
        "lanes": [{"name": "childbuild", "fleet": "claude", "prompt": "CHILDBUILD go"}],
    }
    if max_cost_usd is not None:
        raw["max_cost_usd"] = max_cost_usd
    if ceiling is not None:
        raw["ceiling"] = ceiling
    if extra_lanes:
        raw["lanes"].extend(extra_lanes)
    if pause is not None:
        raw["pause"] = pause
    return raw


def _snapshot(result) -> Mission:
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    return Mission.from_snapshot(raw)


def _assert_plan_refused(result, message: str) -> dict:
    plan_lane = next(lane for lane in result.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is False
    assert plan_lane["plan"]["refused"] == message
    assert plan_lane["attempts"][-1]["error"] == f"plan: {message}"
    assert plan_lane["kinds"][-1] == "plan"
    assert result.ok is False
    assert result.paused is None
    return plan_lane


# --- item 1: load-time validation ------------------------------------------


def test_plan_lane_refuses_write_mode(tmp_path):
    with pytest.raises(MissionInvalid, match="a plan lane must be read mode"):
        mission_from_dict(
            {"cwd": str(tmp_path), "lanes": [_plan_lane(extra={"mode": "write"})]},
            base_dir=tmp_path,
        )


def test_plan_lane_refuses_missing_deliverable(tmp_path):
    raw_lane = {"name": "plan", "fleet": "claude", "prompt": "PLAN", "plan": True}
    with pytest.raises(MissionInvalid, match="a plan lane must declare a deliverable"):
        mission_from_dict({"cwd": str(tmp_path), "lanes": [raw_lane]}, base_dir=tmp_path)


def test_plan_lane_refuses_a_wrongly_named_deliverable(tmp_path):
    with pytest.raises(
        MissionInvalid, match=r"a plan lane's deliverable path must end in \.json or \.toml"
    ):
        mission_from_dict(
            {"cwd": str(tmp_path), "lanes": [_plan_lane(deliverable_path="child.txt")]},
            base_dir=tmp_path,
        )


def test_human_lane_refuses_plan(tmp_path):
    with pytest.raises(MissionInvalid, match="a human lane may not set plan"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [{"name": "ask", "fleet": "human", "prompt": "approve?", "plan": True}],
            },
            base_dir=tmp_path,
        )


def test_script_lane_refuses_plan(tmp_path):
    with pytest.raises(MissionInvalid, match="a script lane may not set plan"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {
                        "name": "plan",
                        "fleet": "script",
                        "command": "true",
                        "plan": True,
                        "deliverable": {"path": "child.json"},
                    }
                ],
            },
            base_dir=tmp_path,
        )


def test_plan_lane_refuses_taint(tmp_path):
    with pytest.raises(MissionInvalid, match="a plan lane may not be tainted"):
        mission_from_dict(
            {"cwd": str(tmp_path), "lanes": [_plan_lane(extra={"taint": True})]},
            base_dir=tmp_path,
        )


def test_plan_lane_refuses_untrusted_output(tmp_path):
    with pytest.raises(MissionInvalid, match="a plan lane may not be untrusted-output"):
        mission_from_dict(
            {"cwd": str(tmp_path), "lanes": [_plan_lane(extra={"untrusted_output": True})]},
            base_dir=tmp_path,
        )


def test_lane_may_not_build_on_a_plan_lane(tmp_path):
    with pytest.raises(MissionInvalid, match="is a plan lane, which holds no commit to build on"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "prompt": "x",
                "lanes": [
                    _plan_lane(),
                    {"name": "build", "fleet": "codex", "needs": ["plan"], "base": "plan"},
                ],
            },
            base_dir=tmp_path,
        )


def test_lane_may_not_resume_a_plan_lane(tmp_path):
    with pytest.raises(MissionInvalid, match="is a plan lane, which holds no session to resume"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "prompt": "x",
                "lanes": [
                    _plan_lane(),
                    {"name": "use", "fleet": "claude", "needs": ["plan"], "resume": "plan"},
                ],
            },
            base_dir=tmp_path,
        )


# --- item 2: the successful park --------------------------------------------


def test_plan_lane_parks_the_mission_with_kind_child(repo, home, fake_fleet, tmp_path):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    result = run_mission(mission, home=home)

    assert result.ok is False
    assert result.paused["kind"] == "child"
    assert result.paused["lane"] == "plan"
    assert result.paused["child_name"] == "fix-the-test"
    assert result.paused["child_max_cost_usd"] == 5.0
    assert (
        result.paused["question"]
        == "Lane 'plan' planned mission 'fix-the-test' ($5.00); launch it?"
    )

    plan_lane = next(lane for lane in result.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is True
    assert plan_lane["plan"]["refused"] is None
    assert plan_lane["plan"]["dry_run_ok"] is True
    assert plan_lane["plan"]["depth"] == 1
    assert plan_lane["plan"]["child_name"] == "fix-the-test"
    assert plan_lane["plan"]["child_max_cost_usd"] == 5.0
    assert plan_lane["plan"]["child_path"] is not None

    pause_doc = json.loads((Path(result.mission_dir) / "pause.json").read_text())
    assert pause_doc["kind"] == "child"
    assert pause_doc["lane"] == "plan"
    assert pause_doc["child_name"] == "fix-the-test"
    assert pause_doc["child_max_cost_usd"] == 5.0
    assert pause_doc["answer"] is None

    lane_receipt = json.loads((Path(result.mission_dir) / "lanes" / "plan.json").read_text())
    assert lane_receipt["ok"] is True
    assert lane_receipt["plan"]["dry_run_ok"] is True


# --- item 3: the child mission's own checks ---------------------------------


def test_child_that_fails_to_load_refuses_the_plan_lane(repo, home, fake_fleet, tmp_path):
    fake_fleet(_write_deliverable_argv({"cwd": str(repo)}))  # no 'lanes' key
    mission = mission_from_dict({"cwd": str(repo), "lanes": [_plan_lane()]}, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    _assert_plan_refused(result, "mission needs a 'lanes' list")


def test_child_over_the_parents_remaining_budget_refuses_the_plan_lane(
    repo, home, fake_fleet, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0)
    fake_fleet(_write_deliverable_argv(child_raw, cost=1.5))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 2.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    result = run_mission(mission, home=home)
    _assert_plan_refused(
        result, "child budget $5.00 is over the parent's remaining $0.50"
    )


def test_child_with_no_max_cost_usd_refuses_the_plan_lane(repo, home, fake_fleet, tmp_path):
    child_raw = _child_raw(repo, max_cost_usd=None)
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict({"cwd": str(repo), "lanes": [_plan_lane()]}, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    _assert_plan_refused(
        result, "child has no max_cost_usd; a planned mission's budget must be bounded"
    )


def test_child_with_a_looser_ceiling_refuses_the_plan_lane(repo, home, fake_fleet, tmp_path):
    child_raw = _child_raw(repo, max_cost_usd=5.0)  # inherits ceiling.py's defaults: 10.0/25.0
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "ceiling": {"per_hour_usd": 5.0, "per_day_usd": 25.0},
            "lanes": [_plan_lane()],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    _assert_plan_refused(result, "child ceiling per_hour_usd is looser than the parent's")


def test_a_child_that_itself_plans_a_grandchild_refuses_the_plan_lane(
    repo, home, fake_fleet, tmp_path
):
    nested_plan_lane = {
        "name": "grandplan",
        "fleet": "claude",
        "prompt": "PLAN nested",
        "plan": True,
        "deliverable": {"path": "grandchild.json"},
    }
    child_raw = _child_raw(repo, max_cost_usd=5.0, extra_lanes=[nested_plan_lane])
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict({"cwd": str(repo), "lanes": [_plan_lane()]}, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    _assert_plan_refused(result, "child would exceed the plan depth limit")


def test_a_child_whose_own_dry_run_is_not_ok_refuses_the_plan_lane(repo, tmp_path, monkeypatch):
    """`_plan_check_child`'s own dry-run-failure branch, pinned directly.

    Every dry run the ordinary scheduler can produce sets a lane's `ok` to
    `True` unconditionally the instant `dry_run` is true (`mission.py`: `if
    dry_run or (result.ok and result.gate_passed): out.ok = True`), and every
    refusal a dry run would otherwise hit -- a non-repo cwd, isolation, a
    pause point -- is itself gated `and not dry_run`. A Spec whose own shape
    is invalid (an absolute deliverable path, an unknown model, ...) never
    even reaches that scheduler: `Mission.validate()` -- called by
    `mission_from_dict`, so by `load_mission` too -- already runs
    `attempt.spec(...).validate()` over every attempt and turns a
    `DispatchRefused` into a load-time `MissionInvalid`, which is the
    'invalid child' refusal above, not this one. No mission this repo's
    fleets can build reaches a not-ok dry run through the ordinary path, so
    this pins the branch directly: `_plan_check_child` only ever reads
    whatever `run_mission(..., dry_run=True)` hands back, and a not-ok one
    (whatever produces it) is exactly what must fail the plan lane."""
    import types

    import conductor.mission as mission_mod

    child_path = tmp_path / "child.json"
    child_path.write_text(json.dumps(_child_raw(repo, max_cost_usd=5.0)))
    parent = mission_from_dict(
        {"cwd": str(repo), "lanes": [{"name": "noop", "fleet": "claude", "prompt": "NOOP"}]},
        base_dir=tmp_path,
    )
    # A stand-in for the `MissionResult` a real dry run would return:
    # `_plan_check_child` and `_first_child_error` only ever read `.ok` and
    # `.lanes` off it, so a plain namespace pins the contract without having
    # to fabricate every other required field a real MissionResult carries.
    fake_dry_result = types.SimpleNamespace(
        ok=False,
        lanes=[
            {
                "name": "childbuild",
                "ok": False,
                "skipped": None,
                "attempts": [{"failure": "boom", "error": "boom"}],
            }
        ],
    )
    monkeypatch.setattr(mission_mod, "run_mission", lambda *a, **k: fake_dry_result)
    plan, message = mission_mod._plan_check_child(
        parent, str(child_path), ledger=mission_mod.Ledger(None), base=tmp_path
    )
    assert message == "child dry run failed: lane 'childbuild': boom"
    assert plan["refused"] == message
    assert plan["dry_run_ok"] is False


# --- item 4: the answered launch --------------------------------------------


def test_answer_continue_launches_the_child_through_the_fake_fleet(
    repo, home, fake_fleet, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0, name="fix-the-test")
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"

    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('built', 0.2)}'"]
    )
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )

    assert resumed.ok is True
    plan_lane = next(lane for lane in resumed.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is True
    child_block = plan_lane["plan"]["child"]
    assert child_block["ok"] is True
    assert child_block["paused"] is False
    assert child_block["mission_id"] in resumed.children
    assert any(
        note.startswith(f"child '{child_block['mission_id']}' spent")
        and "not rolled into this budget" in note
        for note in resumed.notes
    )

    child_snapshot = json.loads(
        (Path(child_block["report_path"]).parent / "mission.json").read_text()
    )
    assert child_snapshot["depth"] == 1
    assert child_snapshot["parent"] == {"mission_id": resumed.mission_id, "lane": "plan"}

    child_result = json.loads(
        (Path(child_block["report_path"]).parent / "result.json").read_text()
    )
    assert child_result["ok"] is True
    assert child_result["lanes"][0]["name"] == "childbuild"


def test_answer_stop_refuses_the_plan_lane(repo, home, fake_fleet, tmp_path):
    child_raw = _child_raw(repo, max_cost_usd=5.0)
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict({"cwd": str(repo), "lanes": [_plan_lane()]}, base_dir=tmp_path)
    first = run_mission(mission, home=home)

    stopped = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="stop"
    )
    assert stopped.ok is False
    plan_lane = next(lane for lane in stopped.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is False
    assert plan_lane["attempts"][-1]["error"] == "plan: child launch refused by the operator"
    assert plan_lane["kinds"][-1] == "plan"
    assert plan_lane["plan"].get("child") is None

    pause_doc = json.loads((Path(stopped.mission_dir) / "pause.json").read_text())
    assert pause_doc["answer"] == "stop"
    assert pause_doc["answers"][0]["kind"] == "child"


def test_child_that_pauses_leaves_the_plan_lane_failed_naming_the_child(
    repo, home, fake_fleet, tmp_path
):
    child_raw = _child_raw(
        repo, max_cost_usd=5.0, name="paused-child", pause={"before": ["childbuild"]}
    )
    fake_fleet(_write_deliverable_argv(child_raw, cost=0.1))
    mission = mission_from_dict(
        {"cwd": str(repo), "max_cost_usd": 10.0, "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    first = run_mission(mission, home=home)
    assert first.paused["kind"] == "child"

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )

    assert resumed.ok is False
    plan_lane = next(lane for lane in resumed.lanes if lane["name"] == "plan")
    assert plan_lane["ok"] is False
    child_block = plan_lane["plan"]["child"]
    assert child_block["paused"] is True
    child_id = child_block["mission_id"]
    assert plan_lane["attempts"][-1]["error"] == f"plan: child paused: {child_id}"
    assert plan_lane["kinds"][-1] == "plan"
    assert child_id in resumed.children


# --- item 5: --unattended ----------------------------------------------------


def test_unattended_refuses_a_mission_with_a_plan_lane(repo, tmp_path):
    mission = mission_from_dict({"cwd": str(repo), "lanes": [_plan_lane()]}, base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match="lane 'plan' is a plan lane"):
        run_mission(mission, home=tmp_path / "home", dry_run=True, unattended=True)


# --- item 6: snapshot round-trip and backfill -------------------------------


def test_plan_lane_snapshot_round_trips_plan_depth_and_parent(tmp_path):
    mission = mission_from_dict(
        {"cwd": str(tmp_path), "lanes": [_plan_lane()]}, base_dir=tmp_path
    )
    mission.depth = 1
    mission.parent = {"mission_id": "some-parent-id", "lane": "plan"}
    raw = mission.to_dict()
    reloaded = Mission.from_snapshot(raw)
    assert reloaded.to_dict() == raw
    assert reloaded.depth == 1
    assert reloaded.parent == {"mission_id": "some-parent-id", "lane": "plan"}
    assert reloaded.lanes[0].plan is True


def test_backfill_snapshot_fills_missing_plan_depth_and_parent(tmp_path):
    mission = mission_from_dict(
        {"cwd": str(tmp_path), "lanes": [{"name": "a", "fleet": "claude", "prompt": "x"}]},
        base_dir=tmp_path,
    )
    raw = mission.to_dict()
    del raw["depth"]
    del raw["parent"]
    del raw["lanes"][0]["plan"]
    backfilled = golden_mod._backfill_snapshot(raw)
    reloaded = Mission.from_snapshot(backfilled)
    assert reloaded.depth == 0
    assert reloaded.parent is None
    assert reloaded.lanes[0].plan is False


# --- item 7: golden.projection -----------------------------------------------


def test_golden_projection_carries_plan_entry_only_for_a_plan_lane(
    repo, home, monkeypatch, tmp_path
):
    child_raw = _child_raw(repo, max_cost_usd=5.0)
    table = {
        "PLAN": _write_deliverable_argv(child_raw, cost=0.1),
        "OTHER": ["sh", "-c", f"echo '{envelope('ok')}'"],
        # The plan lane's own dry run of the child (inside `_plan_check_child`)
        # still calls `build_argv` for the child's one lane, even though a dry
        # run never spawns it -- this table entry is never actually run.
        "CHILDBUILD": ["sh", "-c", f"echo '{envelope('ok')}'"],
    }

    def pick(spec):
        return table[spec.prompt.split()[0]]

    monkeypatch.setattr(runner_mod, "build_argv", pick)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "max_cost_usd": 10.0,
            "lanes": [_plan_lane(), {"name": "other", "fleet": "claude", "prompt": "OTHER go"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)

    projection = golden_mod.projection(result)
    plan_entry = next(entry for entry in projection["lanes"] if entry["name"] == "plan")
    other_entry = next(entry for entry in projection["lanes"] if entry["name"] == "other")
    assert plan_entry["plan"] == {"refused": None, "dry_run_ok": True}
    assert "plan" not in other_entry
