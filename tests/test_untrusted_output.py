"""E3: untrusted-output lanes.

A lane that reads with its full tool set (a research lane) produces text as
untrusted as what it read, even though the lane itself is never weakened.
`untrusted_output: true` marks that lane as a taint *source*: the propagation
walk in `mission_from_dict` treats a reference to its answer/diff/verdict/
test_touched/deliverable, or a `resume` of its session, exactly like a
reference to an already-tainted lane -- without touching the source lane's
own `tainted` state, tool set, or ability to hold a deliverable branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor.fleets import TAINT_DISALLOWED_TOOLS
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission

# --- load-time refusals -------------------------------------------------


def test_untrusted_output_must_be_a_boolean(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {"name": "a", "fleet": "claude", "untrusted_output": "yes", "prompt": "A"},
        ],
    }
    with pytest.raises(MissionInvalid, match="untrusted_output must be true or false"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_a_human_lane_may_not_set_untrusted_output(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "h",
                "fleet": "human",
                "untrusted_output": True,
                "prompt": "Ask the operator",
            },
        ],
    }
    with pytest.raises(MissionInvalid, match="a human lane may not set untrusted_output"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_untrusted_output_is_allowed_on_a_script_lane(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {"name": "s", "fleet": "script", "untrusted_output": True, "command": "true"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].untrusted_output is True
    assert mission.lanes[0].tainted is False


# --- propagation at load time --------------------------------------------


def test_an_untrusted_output_lane_is_not_itself_tainted(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    lane = mission.lanes[0]
    assert lane.untrusted_output is True
    assert lane.tainted is False
    assert lane.taint_from == []


def test_referencing_an_untrusted_output_lanes_answer_taints_the_referrer(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"},
            {
                "name": "build",
                "fleet": "claude",
                "needs": ["research"],
                "prompt": "Build from {{lanes.research.answer}}",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    by_name = {lane.name: lane for lane in mission.lanes}
    assert by_name["research"].tainted is False
    assert by_name["build"].tainted is True
    assert by_name["build"].taint_from == ["research"]
    # An untainted lane referencing nothing untrusted stays untainted.
    raw["lanes"][1]["prompt"] = "Build from nothing tainted"
    mission2 = mission_from_dict(raw, base_dir=tmp_path)
    by_name2 = {lane.name: lane for lane in mission2.lanes}
    assert by_name2["build"].tainted is False


def test_a_lane_resuming_an_untrusted_output_sessions_is_tainted(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "research",
                "fleet": "claude",
                "mode": "write",
                "untrusted_output": True,
                "prompt": "R",
            },
            {
                "name": "build",
                "fleet": "claude",
                "mode": "write",
                "needs": ["research"],
                "resume": "research",
                "prompt": "B",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    build = next(lane for lane in mission.lanes if lane.name == "build")
    assert build.tainted is True
    assert build.taint_from == ["research"]


def test_a_lane_both_tainted_and_untrusted_output_names_itself_once(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "research",
                "fleet": "claude",
                "taint": True,
                "untrusted_output": True,
                "prompt": "R",
            },
            {
                "name": "build",
                "fleet": "claude",
                "needs": ["research"],
                "prompt": "sees {{lanes.research.answer}} and {{lanes.research.diff}}",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    by_name = {lane.name: lane for lane in mission.lanes}
    assert by_name["research"].tainted is True
    assert by_name["research"].untrusted_output is True
    assert by_name["build"].taint_from == ["research"]


def test_an_untrusted_output_lane_may_hold_a_deliverable_branch(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "research",
                "fleet": "claude",
                "mode": "write",
                "untrusted_output": True,
                "branch": "research-output",
                "prompt": "R",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].branch == "research-output"


def test_collate_over_an_untrusted_output_lane_is_refused_off_claude_and_antigravity(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [{"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"}],
        "collate": {"fleet": "cursor"},
    }
    with pytest.raises(
        MissionInvalid, match="taint is enforceable on the claude and antigravity fleets only"
    ):
        mission_from_dict(raw, base_dir=tmp_path)


def test_collate_refusal_names_the_untrusted_output_lane(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [{"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"}],
        "collate": {"fleet": "cursor"},
    }
    with pytest.raises(MissionInvalid) as exc_info:
        mission_from_dict(raw, base_dir=tmp_path)
    assert "'research'" in str(exc_info.value)


# --- runtime: dispatch, receipts, report ---------------------------------


def _research_build_mission(repo, fake_fleet, tmp_path):
    fake_fleet(session_id=None)
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"},
            {
                "name": "build",
                "fleet": "claude",
                "needs": ["research"],
                "prompt": "Build from {{lanes.research.answer}}",
            },
        ],
    }
    return mission_from_dict(raw, base_dir=tmp_path)


def test_the_untrusted_output_lane_dispatches_untainted(repo, home, fake_fleet, tmp_path):
    mission = _research_build_mission(repo, fake_fleet, tmp_path)
    result = run_mission(mission, home=home)
    lane_research, lane_build = result.lanes
    assert lane_research["untrusted_output"] is True
    assert lane_research["tainted"] is False

    run_id = lane_research["attempts"][0]["run_id"]
    argv = json.loads((home / "runs" / run_id / "argv.json").read_text())
    real = argv[argv.index("fake-fleet") + 1 :]
    assert "--disallowedTools" not in real


def test_the_referring_lane_dispatches_tainted(repo, home, fake_fleet, tmp_path):
    mission = _research_build_mission(repo, fake_fleet, tmp_path)
    result = run_mission(mission, home=home)
    _, lane_build = result.lanes
    assert lane_build["tainted"] is True
    assert lane_build["taint_from"] == ["research"]

    run_id = lane_build["attempts"][0]["run_id"]
    argv = json.loads((home / "runs" / run_id / "argv.json").read_text())
    real = argv[argv.index("fake-fleet") + 1 :]
    assert "--disallowedTools" in real
    i = real.index("--disallowedTools")
    assert real[i + 1 : i + 1 + len(TAINT_DISALLOWED_TOOLS)] == list(TAINT_DISALLOWED_TOOLS)


def test_mission_result_and_lane_receipt_carry_untrusted_output(
    repo, home, fake_fleet, tmp_path
):
    mission = _research_build_mission(repo, fake_fleet, tmp_path)
    result = run_mission(mission, home=home)

    mission_receipt = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert mission_receipt["lanes"][0]["untrusted_output"] is True
    assert mission_receipt["lanes"][1]["untrusted_output"] is False

    lane_receipt = json.loads((Path(result.mission_dir) / "lanes" / "research.json").read_text())
    assert lane_receipt["untrusted_output"] is True


def test_report_shows_the_untrusted_output_column(repo, home, fake_fleet, tmp_path):
    mission = _research_build_mission(repo, fake_fleet, tmp_path)
    result = run_mission(mission, home=home)
    report = Path(result.report_path).read_text()
    assert "| untrusted_output |" in report
    assert "yes (from research)" in report


def test_collate_over_an_untrusted_output_lane_dispatches_tainted_on_claude(
    repo, home, fake_fleet, tmp_path
):
    fake_fleet(session_id=None)
    raw = {
        "cwd": str(repo),
        "lanes": [{"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"}],
        "collate": {"fleet": "claude"},
        "self_judging": "allow",
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.collate["tainted"] is True

    run_id = result.collate["run_id"]
    argv = json.loads((home / "runs" / run_id / "argv.json").read_text())
    real = argv[argv.index("fake-fleet") + 1 :]
    assert "--disallowedTools" in real


# --- snapshot round trip and backfill -------------------------------------


def test_untrusted_output_survives_a_snapshot_round_trip(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {"name": "research", "fleet": "claude", "untrusted_output": True, "prompt": "R"},
            {
                "name": "build",
                "fleet": "claude",
                "needs": ["research"],
                "prompt": "Build from {{lanes.research.answer}}",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    snapshot = mission.to_dict()
    restored = Mission.from_snapshot(snapshot)
    by_name = {lane.name: lane for lane in restored.lanes}
    assert by_name["research"].untrusted_output is True
    assert by_name["build"].untrusted_output is False
    assert by_name["build"].tainted is True
    assert by_name["build"].taint_from == ["research"]
    assert restored.to_dict() == snapshot


def test_an_old_snapshot_without_untrusted_output_backfills_false(tmp_path):
    from conductor.golden import _backfill_snapshot

    raw = {
        "cwd": "/tmp",
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    snapshot = mission.to_dict()
    del snapshot["lanes"][0]["untrusted_output"]

    backfilled = _backfill_snapshot(snapshot)
    restored = Mission.from_snapshot(backfilled)
    assert restored.lanes[0].untrusted_output is False


# --- README ---------------------------------------------------------------


def test_readme_documents_untrusted_output():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split(
        "### Untrusted output: marking a lane's own output as a taint source", 1
    )[1].split("\n## ", 1)[0]
    assert '"untrusted_output": true' in section
    assert "taint_from" in section
    assert "research" in section and "build" in section
