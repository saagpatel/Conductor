"""E24: cap grace, a per-lane, opt-in band on top of cap_usd so Claude Code's
own terminal message can finish instead of being cut off mid-summary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor import shape
from conductor.budget import Budget
from conductor.cli import main
from conductor.fleets import CAP_GRACE_CEILING_USD, DispatchRefused, Spec, build_argv
from conductor.mission import MissionInvalid, mission_from_dict, run_mission


def envelope(answer: str, cost: float | None = None) -> str:
    """A claude-shaped envelope: the one fleet that reports dollars."""
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


# --- Spec.validate refusals (item 1) ----------------------------------------


def test_grace_without_a_cap_is_refused():
    with pytest.raises(DispatchRefused, match="needs a cap_usd"):
        Spec(fleet="claude", prompt="x", cwd="/tmp", cap_grace_usd=0.25).validate()


def test_grace_is_refused_on_any_fleet_but_claude():
    with pytest.raises(DispatchRefused, match="claude fleet only"):
        Spec(
            fleet="codex", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=0.25
        ).validate()


@pytest.mark.parametrize("bad", [0, -0.1, float("inf"), float("nan")])
def test_grace_must_be_a_positive_finite_number(bad):
    with pytest.raises(DispatchRefused, match="positive finite"):
        Spec(
            fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=bad
        ).validate()


def test_grace_above_the_ceiling_is_refused():
    with pytest.raises(DispatchRefused, match="ceiling"):
        Spec(
            fleet="claude",
            prompt="x",
            cwd="/tmp",
            cap_usd=1.0,
            cap_grace_usd=CAP_GRACE_CEILING_USD + 0.01,
        ).validate()
    # Right at the ceiling is fine.
    Spec(
        fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=CAP_GRACE_CEILING_USD
    ).validate()


# --- the argv figure ---------------------------------------------------------


def test_the_claude_budget_flag_folds_in_the_grace():
    argv = build_argv(
        Spec(fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=0.25)
    )
    assert argv[argv.index("--max-budget-usd") + 1] == "1.25"
    # No grace: unchanged from before this item.
    argv = build_argv(Spec(fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0))
    assert argv[argv.index("--max-budget-usd") + 1] == "1"


# --- Budget.settle in each of the four states --------------------------------


def test_settle_within_cap_has_no_grace_used():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(0.90, killed=False, fleet_status="success")
    assert b.exceeded is False
    assert b.grace_used == 0.0


def test_settle_inside_the_band_is_not_over_budget():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(1.10, killed=False, fleet_status="success")
    assert b.exceeded is False
    assert b.grace_used == pytest.approx(0.10)


def test_settle_above_the_band_is_over_budget_and_grace_used_is_capped():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(2.0, killed=False, fleet_status="success")
    assert b.exceeded is True
    # The band can only ever absorb its own size, however far over it the run is.
    assert b.grace_used == pytest.approx(0.25)


def test_settle_killed_is_over_budget_whatever_the_grace():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(1.10, killed=True, fleet_status=None)
    assert b.exceeded is True


def test_settle_with_no_grace_or_unknown_cost_leaves_grace_used_none():
    b = Budget(cap_usd=1.0, enforcement="native")
    b.settle(1.10, killed=False, fleet_status="success")
    assert b.grace_used is None
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(None, killed=False, fleet_status="success", interrupted=True)
    assert b.grace_used is None


def test_to_dict_omits_grace_fields_when_no_grace_was_set():
    b = Budget(cap_usd=1.0, enforcement="native")
    b.settle(0.5, killed=False, fleet_status="success")
    assert "grace_usd" not in b.to_dict()
    assert "grace_used" not in b.to_dict()
    b2 = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b2.settle(1.1, killed=False, fleet_status="success")
    assert b2.to_dict()["grace_used"] == pytest.approx(0.10)


# --- mission-level and cascade-level refusals; no cascade to fallbacks ------


def test_mission_level_grace_is_refused(tmp_path: Path):
    raw = {
        "prompt": "x",
        "cap_grace_usd": 0.25,
        "lanes": [{"fleet": "claude", "cap_usd": 1.0}],
    }
    with pytest.raises(MissionInvalid, match="stated per lane"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_cascade_level_grace_is_refused(tmp_path: Path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.25},
        "lanes": [{"fleet": "claude", "cap_usd": 1.0, "mode": "write"}],
    }
    with pytest.raises(MissionInvalid, match="stated per lane"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_a_lanes_grace_never_cascades_to_its_fallback(tmp_path: Path):
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "fleet": "claude",
                "cap_usd": 1.0,
                "cap_grace_usd": 0.25,
                "fallback": [{"fleet": "claude", "cap_usd": 1.0}],
            }
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = m.lanes[0].attempts
    assert primary.cap_grace_usd == 0.25
    assert fallback.cap_grace_usd is None


def test_a_fallback_may_set_its_own_grace(tmp_path: Path):
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "fleet": "claude",
                "cap_usd": 1.0,
                "fallback": [{"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.1}],
            }
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = m.lanes[0].attempts
    assert primary.cap_grace_usd is None
    assert fallback.cap_grace_usd == pytest.approx(0.1)


# --- through the fake fleet: a mission-level view of the band ---------------


def test_a_claude_lane_finishing_inside_the_band_is_ok_and_reports_grace(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"claude": say("done", cost=1.10)})
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "lanes": [{"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.25}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.lanes[0]["ok"] is True
    attempt = result.lanes[0]["attempts"][0]
    assert attempt["over_cap"] is False
    assert attempt["grace_used"] == pytest.approx(0.10)
    assert "grace used $0.10" in Path(result.report_path).read_text()


def test_a_claude_lane_above_the_band_is_over_cap_as_today(repo, home, monkeypatch, tmp_path):
    fake_fleets(monkeypatch, {"claude": say("done", cost=1.30)})
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "lanes": [{"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.25}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.lanes[0]["ok"] is False
    attempt = result.lanes[0]["attempts"][0]
    assert attempt["over_cap"] is True
    assert "over budget" in attempt["failure"]


# --- the Shape A launcher: default grace, and --cap-grace-usd 0 ------------


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n")
    return spec


def test_cap_arithmetic_defaults_to_a_quarter_and_render_prints_it():
    caps = shape.cap_arithmetic(1, 1)
    assert caps.cap_grace_usd == 0.25
    assert "grace: $0.25" in caps.render()
    disabled = shape.cap_arithmetic(1, 1, cap_grace_usd=0)
    assert "grace: disabled" in disabled.render()


def test_cap_arithmetic_refuses_grace_above_the_ceiling():
    with pytest.raises(shape.ShapeInvalid, match="cap-grace-usd"):
        shape.cap_arithmetic(1, 1, cap_grace_usd=CAP_GRACE_CEILING_USD + 0.01)


def test_shape_a_cli_sets_the_default_grace_on_build_and_fix(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out),
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    build, _gemini, _grok, fix = raw["lanes"]
    assert build["cap_grace_usd"] == 0.25
    assert fix["cap_grace_usd"] == 0.25


def test_shape_a_cli_cap_grace_usd_zero_disables_it(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out), "--cap-grace-usd", "0",
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    build, _gemini, _grok, fix = raw["lanes"]
    assert "cap_grace_usd" not in build
    assert "cap_grace_usd" not in fix
