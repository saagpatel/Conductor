"""E14: cost forecast and warn -- before a mission dispatches anything,
compare each lane's cap against what the same vendor and stage has actually
cost on this machine, and warn (never refuse) when the cap sits under the
history.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from conductor.cli import main
from conductor.forecast import forecast, history
from conductor.mission import Attempt, Lane, Mission, mission_from_dict, run_mission


def _write_receipt(
    home: Path,
    run_id: str,
    *,
    fleet: str,
    model: str,
    cost: float | None,
    stage: str | None = None,
    dry_run: bool = False,
) -> None:
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    raw: dict[str, object] = {
        "run_id": run_id,
        "fleet": fleet,
        "model": model,
        "ok": True,
        "mode": "read",
        "usage": {"cost_usd": cost, "cost_basis": "reported", "total_tokens": 10},
        "breaker": {"tool_calls": 0, "tripped": None},
        "interrupted": False,
        "dry_run": dry_run,
    }
    if stage is not None:
        raw["stage"] = stage
    (directory / "result.json").write_text(json.dumps(raw))


def _seed(home: Path, costs: list[float], *, stage: str | None = None) -> None:
    for i, cost in enumerate(costs):
        _write_receipt(
            home,
            f"2026010{i + 1}T000000Z-claude-{i}",
            fleet="claude",
            model="claude-sonnet-5",
            cost=cost,
            stage=stage,
        )


def _lane(
    name: str,
    *,
    fleet: str = "claude",
    model: str | None = "sonnet",
    cap_usd: float | None = None,
    stage: str | None = None,
    human: bool = False,
    script: bool = False,
) -> Lane:
    return Lane(
        name=name,
        attempts=[Attempt(fleet=fleet, model=model, cap_usd=cap_usd)],
        stage=stage,
        human=human,
        script=script,
    )


def _mission(lanes: list[Lane]) -> Mission:
    return Mission(name="m", cwd="/tmp/does-not-matter", lanes=lanes)


def _say(cost: float) -> list[str]:
    """A claude-shaped envelope: the one fleet that reports dollars."""
    payload = json.dumps(
        {"result": "ok", "total_cost_usd": cost, "usage": {"input_tokens": 1, "output_tokens": 1}}
    )
    return ["sh", "-c", f"echo '{payload}'"]


# --- history -----------------------------------------------------------------


def test_history_groups_by_vendor_and_stage(home: Path):
    _write_receipt(
        home, "20260101T000000Z-a", fleet="claude", model="claude-sonnet-5", cost=1.0, stage="build"
    )
    _write_receipt(
        home, "20260101T010000Z-b", fleet="claude", model="claude-opus-5", cost=2.0, stage="build"
    )
    _write_receipt(
        home, "20260101T020000Z-c", fleet="claude", model="claude-sonnet-5", cost=3.0, stage="fix"
    )
    grouped = history(home)
    assert grouped[("anthropic", "build")] == [Decimal("1.0"), Decimal("2.0")]
    assert grouped[("anthropic", "fix")] == [Decimal("3.0")]


def test_history_drops_unpriced_and_dry_runs(home: Path):
    _write_receipt(home, "20260101T000000Z-a", fleet="claude", model="claude-sonnet-5", cost=None)
    _write_receipt(
        home, "20260101T010000Z-b", fleet="claude", model="claude-sonnet-5", cost=5.0, dry_run=True
    )
    _write_receipt(home, "20260101T020000Z-c", fleet="claude", model="claude-sonnet-5", cost=4.0)
    grouped = history(home)
    assert grouped[("anthropic", None)] == [Decimal("4.0")]


# --- forecast ------------------------------------------------------------


def test_forecast_gives_no_percentiles_under_three_runs(home: Path):
    _seed(home, [1.0, 2.0], stage="build")
    mission = _mission([_lane("build", cap_usd=1.0, stage="build")])
    fc = forecast(mission, home)
    lane_fc = fc.lanes[0]
    assert lane_fc.runs == 2
    assert lane_fc.median_usd is None
    assert lane_fc.p80_usd is None
    assert lane_fc.warn is False
    assert fc.warnings == []


def test_forecast_median_and_p80_at_five_runs(home: Path):
    _seed(home, [1.0, 2.0, 3.0, 4.0, 5.0], stage="build")
    mission = _mission([_lane("build", cap_usd=100.0, stage="build")])
    lane_fc = forecast(mission, home).lanes[0]
    assert lane_fc.runs == 5
    # nearest-rank: p50 -> ceil(2.5) = 3rd smallest; p80 -> ceil(4.0) = 4th smallest
    assert lane_fc.median_usd == Decimal("3.0")
    assert lane_fc.p80_usd == Decimal("4.0")
    assert lane_fc.warn is False  # cap is well above p80


def test_forecast_warns_only_when_cap_is_under_p80(home: Path):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    low = _mission([_lane("build", cap_usd=5.0, stage="build")])
    high = _mission([_lane("build", cap_usd=100.0, stage="build")])
    low_fc = forecast(low, home).lanes[0]
    high_fc = forecast(high, home).lanes[0]
    assert low_fc.p80_usd == Decimal("8.0")
    assert low_fc.warn is True
    assert high_fc.warn is False
    assert forecast(low, home).warnings == [
        "lane 'build': cap $5.00 is under the $8.00 80th percentile of 5 anthropic build runs"
    ]
    assert forecast(high, home).warnings == []


def test_forecast_names_an_unstaged_lane_as_unstaged_not_none(home: Path):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage=None)
    mission = _mission([_lane("plain", cap_usd=5.0, stage=None)])
    (warning,) = forecast(mission, home).warnings
    assert warning.endswith("80th percentile of 5 anthropic unstaged runs")
    assert "None" not in warning


def test_forecast_resolves_a_lane_with_no_model_to_the_fleet_default_vendor(home: Path):
    """A lane that omits `model` takes the fleet default at dispatch. Grouping
    it by the fleet name (`_vendor(..., "")`) misses every receipt that
    lane writes, which is keyed by the default model's vendor."""
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    mission = _mission([_lane("build", model=None, cap_usd=5.0, stage="build")])
    fc = forecast(mission, home)
    lane_fc = fc.lanes[0]
    assert lane_fc.vendor == "anthropic"
    assert lane_fc.runs == 5
    assert lane_fc.warn is True
    assert fc.warnings == [
        "lane 'build': cap $5.00 is under the $8.00 80th percentile of 5 anthropic build runs"
    ]


def test_forecast_skips_human_and_script_lanes(home: Path):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    mission = _mission(
        [
            _lane("op", fleet="human", model=None, human=True, cap_usd=1.0, stage="build"),
            _lane("script-lane", fleet="script", cap_usd=1.0, stage="build", script=True),
            _lane("build", cap_usd=1.0, stage="build"),
        ]
    )
    fc = forecast(mission, home)
    assert [lane_fc.lane for lane_fc in fc.lanes] == ["build"]


def test_forecast_handles_a_lane_with_no_cap(home: Path):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    mission = _mission([_lane("build", cap_usd=None, stage="build")])
    lane_fc = forecast(mission, home).lanes[0]
    assert lane_fc.cap_usd is None
    assert lane_fc.warn is False
    assert forecast(mission, home).warnings == []


# --- wired into run_mission -----------------------------------------------


def test_low_cap_mission_runs_to_completion_with_warning_and_forecast_block(
    repo, home, monkeypatch, tmp_path, fake_fleet
):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0])
    fake_fleet(argv=_say(0.5))
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "claude", "model": "sonnet", "cap_usd": 5.0}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok
    assert any("cap $5.00 is under the $8.00 80th percentile" in note for note in result.notes)
    assert result.forecast["warnings"]
    lane_forecast = result.forecast["lanes"][0]
    assert lane_forecast["warn"] is True
    assert lane_forecast["runs"] == 5


def test_dry_run_carries_the_forecast_too(repo, home, tmp_path):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "claude", "model": "sonnet", "cap_usd": 5.0}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    assert result.forecast["warnings"]
    assert any("80th percentile" in note for note in result.notes)


# --- the CLI ---------------------------------------------------------------


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n")
    return spec


def test_shape_a_prints_the_forecast_line(repo, home, monkeypatch, tmp_path, capsys):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    # F17: --no-forecast-cap keeps the warning this test is about; without it
    # the launcher raises the cap and there is nothing left to warn about.
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "3", "--modules", "1", "--out", str(out), "--no-forecast-cap",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "forecast:" in printed
    assert "build:" in printed
    assert "warning: lane 'build'" in printed
    assert "80th percentile of 5 anthropic build runs" in printed


def test_readme_documents_cost_forecast():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("#### Cost forecast", 1)[1].split("\n### ", 1)[0]
    section = " ".join(section.split())  # the README wraps at 80 columns
    assert "nearest-rank method" in section
    assert "floor of three runs" in section
    assert "never a refusal" in section
    assert '"lanes": [...], "warnings": [...]' in section
    assert "Human and script lanes" in section


# --- F17: the launcher raises a warned lane's cap to the forecast p80 --------


def _shape_a(repo, tmp_path, out, *extra: str) -> int:
    spec = _spec(tmp_path)
    return main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "3", "--modules", "1", "--out", str(out), *extra,
        ]
    )


def _out(tmp_path: Path) -> Path:
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    return out


def test_shape_a_raises_a_warned_cap_to_the_p80_and_the_budget_with_it(
    repo, home, monkeypatch, tmp_path, capsys
):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    out = _out(tmp_path)
    assert _shape_a(repo, tmp_path, out) == 0
    printed = capsys.readouterr().out
    raw = json.loads(out.read_text())
    build = next(lane for lane in raw["lanes"] if lane["name"] == "build")
    assert build["cap_usd"] == 8.0  # the $8.00 p80, rounded up to the whole dollar
    assert raw["max_cost_usd"] == 15.75 + 4.0  # rule 2's budget plus the same $4.00
    assert raw["caps"]["build"] == {
        "rule_2_usd": 4.0,
        "forecast_p80_usd": 8.0,
        "forecast_runs": 5,
        "cap_usd": 8.0,
        "basis": "forecast p80",
    }
    assert "cap raised: build $4.00 -> $8.00 (forecast p80 $8.00, 5 runs)" in printed
    # The last budget number on screen is what was written, not the rule-2
    # figure `caps.render()` still prints above the raises.
    assert (
        "written mission budget: $19.75 (rule 2 $15.75 + $4.00 forecast raises)"
        in printed
    )
    assert raw["max_cost_usd"] == 19.75
    # The raised mission still loads, caps block and all.
    assert mission_from_dict(raw, base_dir=out.parent).name == "widget"


def test_no_forecast_cap_leaves_the_caps_alone_and_records_the_declined_p80(
    repo, home, monkeypatch, tmp_path, capsys
):
    _seed(home, [5.0, 6.0, 7.0, 8.0, 9.0], stage="build")
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    out = _out(tmp_path)
    assert _shape_a(repo, tmp_path, out, "--no-forecast-cap") == 0
    printed = capsys.readouterr().out
    raw = json.loads(out.read_text())
    build = next(lane for lane in raw["lanes"] if lane["name"] == "build")
    assert build["cap_usd"] == 4.0
    assert raw["max_cost_usd"] == 15.75
    assert raw["caps"]["build"] == {
        "rule_2_usd": 4.0,
        "forecast_p80_usd": 8.0,
        "forecast_runs": 5,
        "cap_usd": 4.0,
        "basis": "rule 2",
    }
    assert "cap raised:" not in printed
    assert "written mission budget: $15.75" in printed
    assert "forecast raises" not in printed


def test_a_home_with_no_history_moves_nothing_and_records_a_null_p80(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    out = _out(tmp_path)
    assert _shape_a(repo, tmp_path, out) == 0
    printed = capsys.readouterr().out
    raw = json.loads(out.read_text())
    assert next(lane for lane in raw["lanes"] if lane["name"] == "build")["cap_usd"] == 4.0
    assert raw["max_cost_usd"] == 15.75
    assert {row["basis"] for row in raw["caps"].values()} == {"rule 2"}
    assert all(row["forecast_p80_usd"] is None for row in raw["caps"].values())
    assert sorted(raw["caps"]) == ["build", "fix", "review-gemini", "review-grok"]
    assert "cap raised:" not in printed
    assert "written mission budget: $15.75" in printed
