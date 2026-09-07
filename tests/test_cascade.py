"""Roadmap item B3, the cheap-first cascade: a mission-level `cascade`
attempt prepended to every qualifying lane, escalation tracked per lane and
summarized on the mission result.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def envelope(answer: str, cost: float) -> str:
    payload = {
        "result": answer,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "total_cost_usd": cost,
    }
    return json.dumps(payload)


# --- loading -----------------------------------------------------------------


def test_cascade_is_prepended_only_to_build_lanes_in_a_staged_mission(tmp_path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "codex"},
        "lanes": [
            {"name": "build", "fleet": "claude", "stage": "build", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "stage": "review",
                "mode": "read",
                "prompt": "R",
            },
            {
                "name": "fix",
                "fleet": "claude",
                "stage": "fix",
                "mode": "write",
                "no_op_ok": True,
            },
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    build, review, fix = m.lanes
    assert [a.fleet for a in build.attempts] == ["codex", "claude"]
    assert [a.fleet for a in review.attempts] == ["claude"]
    assert [a.fleet for a in fix.attempts] == ["claude"]


def test_cascade_is_prepended_to_every_write_lane_when_the_mission_has_no_stages(tmp_path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "codex"},
        "lanes": [
            {"name": "w", "fleet": "claude", "mode": "write"},
            {"name": "r", "fleet": "claude", "mode": "read"},
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    w, r = m.lanes
    assert [a.fleet for a in w.attempts] == ["codex", "claude"]
    assert [a.fleet for a in r.attempts] == ["claude"]


def test_cascade_false_opts_a_lane_out(tmp_path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "codex"},
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "write"},
            {"name": "b", "fleet": "claude", "mode": "write", "cascade": False},
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    a, b = m.lanes
    assert [x.fleet for x in a.attempts] == ["codex", "claude"]
    assert [x.fleet for x in b.attempts] == ["claude"]


def test_a_bad_cascade_opt_out_value_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "codex"},
        "lanes": [{"fleet": "claude", "mode": "write", "cascade": "no"}],
    }
    with pytest.raises(MissionInvalid, match="cascade must be true or false"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_cascade_must_be_an_object_with_a_fleet(tmp_path):
    with pytest.raises(MissionInvalid, match="cascade must be an object"):
        mission_from_dict(
            {"prompt": "x", "cascade": "codex", "lanes": [{"fleet": "claude", "mode": "write"}]},
            base_dir=tmp_path,
        )
    with pytest.raises(MissionInvalid, match="cascade: fleet is required"):
        mission_from_dict(
            {
                "prompt": "x",
                "cascade": {"cap_usd": 1},
                "lanes": [{"fleet": "claude", "mode": "write"}],
            },
            base_dir=tmp_path,
        )


def test_cascade_inheritance_matches_fallback_inheritance(tmp_path):
    base_raw = {
        "prompt": "x",
        "mode": "write",
        "commit": "feat: x",
        "test": "pytest -q",
        "lanes": [{"fleet": "claude", "model": "opus"}],
    }
    # Different fleet, no model set on the cascade: fleet-local model must
    # not carry across, exactly like a fallback that switches fleet.
    m = mission_from_dict({**base_raw, "cascade": {"fleet": "codex"}}, base_dir=tmp_path)
    cascade, primary = m.lanes[0].attempts
    assert cascade.fleet == "codex" and cascade.model is None
    assert cascade.mode == "write" and cascade.commit == "feat: x" and cascade.test == "pytest -q"
    assert primary.fleet == "claude" and primary.model == "opus"

    # Different fleet, cascade sets its own model: that model is kept.
    m2 = mission_from_dict(
        {**base_raw, "cascade": {"fleet": "codex", "model": "sol"}}, base_dir=tmp_path
    )
    assert m2.lanes[0].attempts[0].model == "sol"

    # Same fleet as the lane's primary: the model inherits, same as a fallback.
    m3 = mission_from_dict({**base_raw, "cascade": {"fleet": "claude"}}, base_dir=tmp_path)
    assert m3.lanes[0].attempts[0].model == "opus"


def test_a_cascade_on_the_reviewers_vendor_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "claude", "model": "haiku"},
        "lanes": [
            {"name": "build", "fleet": "codex", "stage": "build", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "model": "opus",
                "stage": "review",
                "mode": "read",
                "base": "build",
                "prompt": "R",
            },
        ],
    }
    with pytest.raises(MissionInvalid, match=r"'review'.*'build'.*anthropic"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_a_cascade_outside_the_stages_policy_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"build": {"vendors": ["openai"]}},
        "cascade": {"fleet": "claude"},
        "lanes": [{"name": "build", "fleet": "codex", "stage": "build", "mode": "write"}],
    }
    with pytest.raises(MissionInvalid, match=r"policy allows openai for stage build"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_cascade_snapshot_round_trips_through_mission_json(repo, home, tmp_path):
    raw = {
        "cwd": str(repo),
        "prompt": "x",
        "mode": "write",
        "cascade": {"fleet": "codex", "cap_usd": 0.25},
        "lanes": [
            {"name": "a", "fleet": "claude"},
            {"name": "b", "fleet": "claude", "cascade": False},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    assert [a.fleet for a in reloaded.lanes[0].attempts] == ["codex", "claude"]
    assert [a.fleet for a in reloaded.lanes[1].attempts] == ["claude"]
    assert reloaded.cascade == {"fleet": "codex", "cap_usd": 0.25}
    assert reloaded.to_dict() == mission.to_dict() == snapshot


# --- running -------------------------------------------------------------


def test_cascade_escalation_across_two_lanes(repo, home, monkeypatch, tmp_path, capsys):
    """Two write lanes share a cascade attempt on a cheap fleet; it passes on
    one lane and fails on the other, which then escalates to its own (real)
    fleet."""

    def fake_build(spec):
        if spec.fleet == "cursor":
            if "lane-a" in spec.prompt:
                return [
                    "sh",
                    "-c",
                    f"echo cheap > cheap.txt && echo '{envelope('cheap', 0.01)}'",
                ]
            return ["sh", "-c", "exit 1"]
        return ["sh", "-c", f"echo real > real.txt && echo '{envelope('real', 0.20)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)

    raw = {
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "cascade": {"fleet": "cursor"},
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "do lane-a thing"},
            {"name": "b", "fleet": "claude", "prompt": "do lane-b thing"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    lane_a = next(lane for lane in result.lanes if lane["name"] == "a")
    lane_b = next(lane for lane in result.lanes if lane["name"] == "b")
    assert lane_a["ok"] is True and lane_a["escalated"] is False
    assert [a["attempt"] for a in lane_a["attempts"]] == ["cursor"]
    assert lane_b["ok"] is True and lane_b["escalated"] is True
    assert [a["attempt"] for a in lane_b["attempts"]] == ["cursor", "claude"]

    assert result.escalation == {
        "lanes": 2,
        "cheap_ok": 1,
        "escalated": 1,
        "rate": 0.5,
        "cascade_usd": 0.01,
        "escalated_usd": 0.2,
    }
    summary = result.summary()
    assert summary["escalation"] == result.escalation
    rows = {row["name"]: row for row in summary["lanes"]}
    assert rows["a"]["escalated"] is False
    assert rows["b"]["escalated"] is True

    report = Path(result.report_path).read_text()
    assert (
        "Cascade: 1 of 2 lanes passed on cursor; 1 escalated "
        "($0.0100 on the cheap attempts, $0.2000 after)" in report
    )

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["missions"]) == 0
    rows_out = json.loads(capsys.readouterr().out)
    assert rows_out[0]["escalation"] == result.escalation


def test_a_resume_keeps_the_escalated_flag_on_the_kept_lane(repo, home, monkeypatch, tmp_path):
    def fake_build(spec):
        if spec.fleet == "cursor":
            if "lane-a" in spec.prompt:
                return [
                    "sh",
                    "-c",
                    f"echo cheap > cheap.txt && echo '{envelope('cheap', 0.01)}'",
                ]
            return ["sh", "-c", "exit 1"]
        return ["sh", "-c", f"echo real > real.txt && echo '{envelope('real', 0.20)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)

    raw = {
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "cascade": {"fleet": "cursor"},
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "do lane-a thing"},
            {"name": "b", "fleet": "claude", "prompt": "do lane-b thing"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True

    snapshot = json.loads(Path(first.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    second = run_mission(reloaded, home=home, resume_dir=Path(first.mission_dir))

    assert "b" in second.resumed_from["kept"]
    lane_b = next(lane for lane in second.lanes if lane["name"] == "b")
    assert lane_b["escalated"] is True


def test_readme_documents_the_cascade_and_its_escalation_block():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("### Cheap-first cascade", 1)[1].split("\n### ", 1)[0]
    assert '"cascade": false' in section
    assert "applies to every lane whose `stage` is `build`" in section
    assert "every write lane" in section
    assert "except `model` when the fleet" in section and "as for fallbacks" in section
    assert '"escalated"' in section and '"rate": 0.25' in section
    assert "Cascade: <cheap_ok> of" in section
    assert "cut cost 31% at 0.91 micro-F1" in section and "UCCI" in section
    assert "Is Escalation Worth" in section and "arxiv.org/pdf/2605.06350" in section
    assert "docs/ROADMAP-2026-09.md" in section and "item B3" in section


def test_escalation_counts_only_lanes_the_cascade_actually_reached(tmp_path):
    """Cross-vendor review of B3: an opted-out lane's own primary is not a
    cheap attempt, so it must not count toward the escalation block."""
    from conductor.mission import LaneResult, _escalation_summary

    raw = {
        "prompt": "x",
        "cascade": {"fleet": "codex"},
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "write"},
            {"name": "b", "fleet": "claude", "mode": "write", "cascade": False},
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    assert [lane.cascaded for lane in m.lanes] == [True, False]
    results = [
        LaneResult(name="a", ok=True, attempts=[{"ok": True, "cost_usd": 0.1}]),
        LaneResult(name="b", ok=True, attempts=[{"ok": True, "cost_usd": 5.0}]),
    ]
    esc = _escalation_summary(m, results)
    assert esc["lanes"] == 1
    assert esc["cheap_ok"] == 1
    assert esc["cascade_usd"] == 0.1


def test_a_resume_trusts_a_kept_lane_when_git_cannot_run_under_load(
    repo, home, monkeypatch, tmp_path
):
    """The load flake on record: a kept lane's trust check spawns git, and a
    spawn refused under load (EAGAIN) read as 'commit missing' until the
    check learned to tell GIT_UNRUN from a real no."""
    from conductor import mission as mission_mod
    from conductor.verify import GIT_UNRUN

    def fake_build(spec):
        return ["sh", "-c", f"echo real > real.txt && echo '{envelope('real', 0.20)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    raw = {
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "do lane-a thing"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True

    real_git_run = mission_mod.git_run
    refused: list[tuple[str, ...]] = []

    def git_refused_under_load(cwd, *args, **kwargs):
        if args[:1] == ("cat-file",):
            refused.append(args)
            return subprocess.CompletedProcess(["git", *args], GIT_UNRUN, "", "EAGAIN")
        return real_git_run(cwd, *args, **kwargs)

    monkeypatch.setattr(mission_mod, "git_run", git_refused_under_load)
    snapshot = json.loads(Path(first.mission_dir, "mission.json").read_text())
    second = run_mission(
        Mission.from_snapshot(snapshot), home=home, resume_dir=Path(first.mission_dir)
    )

    assert refused, "the trust check consulted git for the tip commit"
    assert second.resumed_from["kept"] == ["a"]
    assert second.resumed_from["rerun"] == []
    assert any("git could not run" in note for note in second.notes)


def test_a_resume_still_reruns_a_lane_whose_tip_git_says_is_missing(
    repo, home, monkeypatch, tmp_path
):
    from conductor import mission as mission_mod

    def fake_build(spec):
        return ["sh", "-c", f"echo real > real.txt && echo '{envelope('real', 0.20)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    raw = {
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "do lane-a thing"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True

    real_git_run = mission_mod.git_run

    def git_says_no(cwd, *args, **kwargs):
        if args[:1] == ("cat-file",):
            return subprocess.CompletedProcess(["git", *args], 128, "", "missing")
        return real_git_run(cwd, *args, **kwargs)

    monkeypatch.setattr(mission_mod, "git_run", git_says_no)
    snapshot = json.loads(Path(first.mission_dir, "mission.json").read_text())
    second = run_mission(
        Mission.from_snapshot(snapshot), home=home, resume_dir=Path(first.mission_dir)
    )
    assert "a" in second.resumed_from["rerun"]
