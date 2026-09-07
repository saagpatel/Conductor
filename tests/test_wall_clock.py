"""F2: wall clock on the ledger -- `MissionResult.wall`, computed fresh each
run from durable sources (the scheduler's own clock, the run receipts, and
`pause.json`), never carried and added to across a resume."""

from __future__ import annotations

import json
from pathlib import Path

from conductor import runner as runner_mod
from conductor.mission import mission_from_dict, run_mission


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
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: say("ok"))
    mission = mission_from_dict(
        {
            "name": "wall-two-lanes",
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
