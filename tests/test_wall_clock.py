"""F2: wall clock on the ledger -- `MissionResult.wall`, computed fresh each
run from durable sources (the scheduler's own clock, the run receipts, and
`pause.json`), never carried and added to across a resume."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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

    report = Path(result.report_path).read_text()
    assert f"- wall: {wall['wall_s']}s" in report


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
