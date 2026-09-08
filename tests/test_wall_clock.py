"""F2: wall clock on the ledger -- `MissionResult.wall`, computed fresh each
run from durable sources (the scheduler's own clock, the run receipts, and
`pause.json`), never carried and added to across a resume."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conductor import mission as mission_mod
from conductor import runner as runner_mod
from conductor.mission import Mission, mission_from_dict, run_mission


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, *, action: str = ":", cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"{action}; echo '{envelope(answer, cost)}'"]


def test_two_lane_mission_wall_s_covers_lanes_s_and_idle_s_is_non_negative(
    repo, home, monkeypatch, tmp_path
):
    # `lanes_s` is a sum, so two lanes truly running in parallel can overlap
    # and push it past `wall_s` (a real span) -- `wall_s >= lanes_s` is only
    # guaranteed when nothing overlaps, so this mission dispatches serially.
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: say("ok"))
    mission = mission_from_dict(
        {
            "name": "wall-two-lanes",
            "cwd": str(repo),
            "concurrency": 1,
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A"},
                {"name": "b", "fleet": "claude", "prompt": "B"},
            ],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    wall = result.wall
    assert wall is not None
    assert wall["launched_at"] and wall["finished_at"]
    assert wall["wall_s"] >= wall["lanes_s"]
    assert wall["idle_s"] >= 0
    assert wall["paused_s"] == 0.0
    assert wall["gate_s"] == 0.0  # no `test` command declared on this mission
    assert wall["concurrency"] == 1

    report = Path(result.report_path).read_text()
    assert f"- wall: {wall['wall_s']}s" in report


def test_wall_s_times_concurrency_covers_lanes_s_when_lanes_overlap(
    repo, home, monkeypatch, tmp_path
):
    # F15 item 6: `wall_s >= lanes_s` is the false invariant a concurrent
    # mission can legitimately break (`lanes_s` is a sum across overlapping
    # lanes) -- `wall_s * concurrency >= lanes_s` is the one that actually
    # holds, whether or not the lanes overlapped enough to need the slack.
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: say("ok"))
    mission = mission_from_dict(
        {
            "name": "wall-concurrent",
            "cwd": str(repo),
            "concurrency": 2,
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A"},
                {"name": "b", "fleet": "claude", "prompt": "B"},
            ],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    wall = result.wall
    assert wall is not None
    assert wall["concurrency"] == 2
    assert wall["wall_s"] * wall["concurrency"] >= wall["lanes_s"]


def test_gate_s_sums_the_lanes_own_gate_duration(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: say("ok", action="echo change > out.txt")
    )
    mission = mission_from_dict(
        {
            "name": "wall-gate",
            "cwd": str(repo),
            "mode": "write",
            "test": "sleep 0.2",
            "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    # `lanes_s` is the fleet's own busy time (stamped before its gate ever
    # runs), so it and `gate_s` are disjoint pieces of the same wall clock.
    assert result.wall["gate_s"] >= 0.15
    assert result.wall["wall_s"] >= result.wall["lanes_s"] + result.wall["gate_s"]


def test_resume_of_a_pre_f2_mission_recovers_launched_at_from_the_mission_id(
    repo, home, monkeypatch, tmp_path
):
    """`launched_at` must never reset -- including the one resume that finds
    no prior `wall` block at all because the mission predates this field.
    The mission id's own stamp (the same anchor `spend._run_time` reads off
    a run id) is a real record of the first launch; fabricating "now" would
    silently discard however long the mission had already been alive."""
    real_datetime = mission_mod.datetime
    frozen = datetime(2026, 1, 1, tzinfo=UTC)

    class FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: say("ok"))
    mission = mission_from_dict(
        {"name": "pre-f2", "cwd": str(repo), "lanes": [{"fleet": "claude", "prompt": "x"}]},
        base_dir=tmp_path,
    )
    monkeypatch.setattr(mission_mod, "datetime", FrozenDatetime)
    try:
        first = run_mission(mission, home=home)
    finally:
        monkeypatch.setattr(mission_mod, "datetime", real_datetime)
    assert first.mission_id.startswith("20260101T000000Z-")

    # Simulate a receipt written before F2 shipped: no "wall" key at all.
    result_path = Path(first.mission_dir) / "result.json"
    data = json.loads(result_path.read_text())
    del data["wall"]
    result_path.write_text(json.dumps(data))

    raw_mission = json.loads((Path(first.mission_dir) / "mission.json").read_text())
    resumed = run_mission(
        Mission.from_snapshot(raw_mission), home=home, resume_dir=Path(first.mission_dir)
    )
    assert resumed.wall["launched_at"].startswith("2026-01-01T00:00:00")


def test_gate_s_sums_reproduce_and_setup_teardown_alongside_the_gate(home):
    """F15 item 4: `reproduce` (E16) and `lane_env.setup`/`teardown` (C4) are
    each their own `ran`/`duration_s` outcome, sibling to `tests` -- none of
    the three used to be counted in `gate_s` at all."""
    run_dir = home / "runs" / "20260101T000000Z-full"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "tests": {"ran": True, "exit_code": 0, "tail": "", "duration_s": 10.0},
                "reproduce": {"ran": True, "verdict": "reproduced", "duration_s": 20.0},
                "lane_env": {
                    "setup": {"ran": True, "exit_code": 0, "tail": "", "duration_s": 5.0},
                    "teardown": None,
                },
            }
        )
    )
    lane = mission_mod.LaneResult(
        name="a", ok=True, attempts=[{"run_id": "20260101T000000Z-full"}]
    )
    assert mission_mod._gate_seconds([lane], home) == 35.0


def test_gate_s_is_null_when_reproduce_ran_but_predates_duration_s(home):
    run_dir = home / "runs" / "20260101T000000Z-old-reproduce"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps({"reproduce": {"ran": True, "verdict": "reproduced"}})
    )
    lane = mission_mod.LaneResult(
        name="a", ok=True, attempts=[{"run_id": "20260101T000000Z-old-reproduce"}]
    )
    assert mission_mod._gate_seconds([lane], home) is None


def test_gate_s_is_null_when_a_receipt_ran_a_gate_but_predates_duration_s(home):
    """A receipt from before `TestOutcome.duration_s` existed still has
    `tests: {"ran": true, ...}` with no `duration_s` key -- the gate really
    ran, its time just was never measured. Summing it as 0 would understate
    `gate_s`, the exact "null, never 0" trap the spec calls out."""
    run_dir = home / "runs" / "20260101T000000Z-old"
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps({"tests": {"ran": True, "exit_code": 0, "tail": ""}})
    )
    lane = mission_mod.LaneResult(
        name="a", ok=True, attempts=[{"run_id": "20260101T000000Z-old"}]
    )
    assert mission_mod._gate_seconds([lane], home) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_gate_s_is_null_when_duration_s_is_not_finite(home, bad):
    """`_numeric` admitted NaN and infinity, so a gate that ran with
    `duration_s: NaN` made `gate_s` NaN instead of unknown (never 0)."""
    assert mission_mod._numeric(bad) is None
    tag = "nan" if bad != bad else "inf"
    run_id = f"20260101T000000Z-{tag}"
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps({"tests": {"ran": True, "exit_code": 0, "tail": "", "duration_s": bad}})
    )
    lane = mission_mod.LaneResult(name="a", ok=True, attempts=[{"run_id": run_id}])
    assert mission_mod._gate_seconds([lane], home) is None


def _receipt(home: Path, run_id: str, gate_s: float) -> None:
    """A run receipt whose gate ran for `gate_s` seconds and nothing else."""
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps({"tests": {"ran": True, "exit_code": 0, "tail": "", "duration_s": gate_s}})
    )


def _lane(
    name: str, run_id: str, duration_s: float | None, *, needs: list[str] | None = None
) -> mission_mod.LaneResult:
    attempt: dict = {"run_id": run_id}
    if duration_s is not None:
        attempt["duration_s"] = duration_s
    return mission_mod.LaneResult(
        name=name, ok=True, attempts=[attempt], needs=list(needs or [])
    )


def test_occupied_s_is_the_union_of_two_overlapping_lanes_not_their_sum(home):
    """W8: `lanes_s` sums, so two lanes that ran at the same time count
    their overlap twice -- `occupied_s` counts the wall clock during which
    at least one dispatch was running, so it is the union instead."""
    lanes = [
        _lane("a", "20260101T000000Z-a", 60.0),
        # starts 30s in, so 30s of the two lanes' 120s of work overlaps.
        _lane("b", "20260101T000030Z-b", 60.0),
    ]
    assert mission_mod._occupied_seconds(lanes, home) == 90.0
    lanes_s = sum(lane.attempts[0]["duration_s"] for lane in lanes)
    assert lanes_s == 120.0


def test_occupied_s_counts_a_lanes_gate_inside_its_own_interval(home):
    _receipt(home, "20260101T000000Z-a", 20.0)
    lanes = [_lane("a", "20260101T000000Z-a", 60.0)]
    assert mission_mod._occupied_seconds(lanes, home) == 80.0


def test_critical_path_s_follows_the_chain_and_a_parallel_reviewer_adds_nothing(home):
    """W8: build -> review -> fix is the chain the mission could not have
    run any faster than; a second reviewer hanging off the build in parallel
    is real lane work but is not on the path, so it must not lengthen it."""
    _receipt(home, "20260101T000000Z-build", 5.0)
    lanes = [
        _lane("build", "20260101T000000Z-build", 100.0),
        _lane("review", "20260101T001000Z-review", 30.0, needs=["build"]),
        _lane("fix", "20260101T002000Z-fix", 20.0, needs=["review"]),
        # off the path: same depth as `review`, and longer than it.
        _lane("review2", "20260101T001000Z-review2", 45.0, needs=["build"]),
    ]
    assert mission_mod._critical_path_seconds(lanes, home) == 105.0 + 30.0 + 20.0
    lanes_only = [lane for lane in lanes if lane.name != "review2"]
    assert mission_mod._critical_path_seconds(lanes_only, home) == 155.0


def test_critical_path_s_counts_a_skipped_lane_as_zero(home):
    lanes = [
        _lane("build", "20260101T000000Z-build", 100.0),
        mission_mod.LaneResult(
            name="review", ok=False, needs=["build"], skipped="build was not ok"
        ),
    ]
    assert mission_mod._critical_path_seconds(lanes, home) == 100.0


def test_an_attempt_without_duration_s_blanks_occupied_and_critical_path(home):
    """The receipt cannot say how long that dispatch ran, so the union and
    the path through it are unknown, not shorter -- None, never 0. `wall_s`
    is a measured span and is unaffected."""
    lanes = [
        _lane("a", "20260101T000000Z-a", 60.0),
        _lane("b", "20260101T000030Z-b", None, needs=["a"]),
    ]
    assert mission_mod._occupied_seconds(lanes, home) is None
    assert mission_mod._critical_path_seconds(lanes, home) is None


def test_lead_s_is_wall_minus_occupied_minus_paused_clamped_at_zero():
    assert mission_mod._lead_seconds(1000.0, 400.0, 100.0) == 500.0
    assert mission_mod._lead_seconds(100.0, 400.0, 0.0) == 0.0
    assert mission_mod._lead_seconds(1000.0, None, 100.0) is None
    assert mission_mod._lead_seconds(None, 400.0, 100.0) is None


def test_a_real_mission_carries_occupied_critical_path_and_lead(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: say("ok"))
    mission = mission_from_dict(
        {
            "name": "wall-w8",
            "cwd": str(repo),
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A"},
                {"name": "b", "fleet": "claude", "prompt": "B", "needs": ["a"]},
            ],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    wall = result.wall
    assert wall["occupied_s"] is not None
    assert wall["critical_path_s"] is not None
    # every figure on the block is rounded to a tenth independently, so the
    # identity holds to within one rounding step, not to the bit.
    expected = max(0.0, wall["wall_s"] - wall["occupied_s"] - wall["paused_s"])
    assert abs(wall["lead_s"] - expected) <= 0.15


def test_critical_path_s_counts_previous_attempts_after_a_resume_rerun(home):
    """A resume that reran a lane moves the first paid attempt under
    `previous_attempts`; the path is this mission's whole life, so that
    work still lengthens the chain. Weighting only `attempts[-1]` forgot
    it, and `lead_s` (from the whole-life figures) then disagreed."""
    lanes = [
        _lane("build", "20260101T000000Z-build", 100.0),
        mission_mod.LaneResult(
            name="review",
            ok=True,
            needs=["build"],
            previous_attempts=[{"run_id": "20260101T001000Z-review-1", "duration_s": 40.0}],
            attempts=[{"run_id": "20260101T002000Z-review-2", "duration_s": 30.0}],
        ),
    ]
    assert mission_mod._critical_path_seconds(lanes, home) == 170.0
