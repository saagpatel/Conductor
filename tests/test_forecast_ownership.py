"""Forecast composition against real on-disk receipt shapes, without vendors."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from decimal import Decimal

from conductor.forecast import apply_caps, forecast, history
from conductor.mission import mission_from_dict


def _receipt(home, suffix, cost, *, stamp="20260901T000000Z", **fields):
    run_id = f"{stamp}-{suffix}"
    path = home / "runs" / run_id / "result.json"
    path.parent.mkdir(parents=True)
    raw = {"run_id": run_id, "ok": True, "fleet": "claude", "model": "claude-sonnet-5",
           "usage": {"cost_usd": cost, "cost_basis": "reported"}, **fields}
    path.write_text(json.dumps(raw))
    return run_id


def _mission(tmp_path, lanes, **fields):
    raw = {"prompt": "x", "cwd": str(tmp_path), "lanes": lanes, **fields}
    return raw, mission_from_dict(raw, base_dir=tmp_path)


def test_cascade_forecast_and_raise_use_the_declared_builder(home, tmp_path):
    for i, cost in enumerate([6, 8, 10]):
        _receipt(home, f"anthropic-{i}", cost, stage="build")
        _receipt(home, f"xai-{i}", 0.2, stage="build", fleet="cursor",
                 model="cursor-grok-4.6-high")
    raw, mission = _mission(
        tmp_path,
        [{"name": "build", "fleet": "claude", "model": "sonnet", "stage": "build",
          "mode": "write", "cap_usd": 2}],
        cascade={"fleet": "cursor", "model": "grok-4.6", "cap_usd": 0.5}, max_cost_usd=20,
    )
    fc = forecast(mission, home)
    assert fc.lanes[0].vendor == "anthropic"
    assert fc.lanes[0].cap_usd == 2
    assert fc.lanes[0].p80_usd == Decimal("10")
    apply_caps(raw, fc)
    assert raw["lanes"][0]["cap_usd"] == 10
    assert raw["cascade"]["cap_usd"] == 0.5
    assert raw["max_cost_usd"] == 28


def test_forecast_distinguishes_unknown_prices_from_empty_history(home, tmp_path):
    for i in range(3):
        _receipt(home, str(i), None)
    _, mission = _mission(tmp_path, [{"fleet": "claude"}, {"fleet": "antigravity"}])
    fc = forecast(mission, home)
    unknown, empty = [row.to_dict() for row in fc.lanes]
    assert unknown["runs"] == empty["runs"] == 0
    assert unknown.get("unpriced_runs") == 3
    assert empty.get("unpriced_runs") == 0
    assert unknown["warn"] is False
    assert unknown["p80_usd"] is None
    assert len(fc.warnings) == 1 and "unknown prices" in fc.warnings[0]


def test_forecast_keeps_estimates_and_real_zero_costs(home, tmp_path):
    _receipt(home, "zero", 0)
    _receipt(home, "reported", 4)
    _receipt(home, "estimated", 6, usage={"cost_usd": 6, "cost_basis": "estimated"})
    _, mission = _mission(tmp_path, [{"fleet": "claude"}])
    row = forecast(mission, home).lanes[0].to_dict()
    assert row["runs"] == 3
    assert row["p80_usd"] == 6
    assert row.get("estimated_runs") == 1
    assert row.get("zero_cost_runs") == 1
    assert row.get("unpriced_runs") == 0


def test_naive_forecast_bound_has_the_same_utc_meaning_as_spend(home, tmp_path):
    _receipt(home, "before", 1, stamp="20260831T235959Z")
    _receipt(home, "after", 4)
    since = datetime(2026, 9, 1)
    assert history(home, since=since) == history(home, since=since.replace(tzinfo=UTC))
    assert history(home, since=since) == {("anthropic", None): [Decimal("4")]}
    _, mission = _mission(tmp_path, [{"fleet": "claude"}])
    assert forecast(mission, home, since=since).lanes[0].runs == 1


def test_forecast_excludes_auxiliaries_but_keeps_lanes_and_standalone_runs(home, tmp_path):
    ids = {name: _receipt(home, name, cost) for name, cost in
           [("lane", 10), ("standalone", 20), ("collate", 30), ("order", 40), ("resolve", 50)]}
    snapshot = home / "missions" / "m" / "result.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps({
        "lanes": [{"name": "a", "attempts": [{"run_id": ids["lane"]}]}],
        "collate": {"run_id": ids["collate"], "orders": [{"run_id": ids["order"]}]},
        "resolve": {"run_id": ids["resolve"]},
    }))
    assert sorted(history(home)[("anthropic", None)]) == [Decimal("10"), Decimal("20")]
    _, mission = _mission(tmp_path, [{"fleet": "claude", "cap_usd": 5}])
    row = forecast(mission, home).lanes[0]
    assert row.runs == 2 and row.p80_usd is None


def test_cap_application_matches_generated_names_and_inherited_caps(home, tmp_path):
    for i, cost in enumerate([6, 8, 10]):
        _receipt(home, str(i), cost)
    raw, mission = _mission(
        tmp_path,
        [{"fleet": "claude"}, {"fleet": "claude"},
         {"fleet": "script", "command": "true"},
         {"fleet": "claude", "name": "named", "cap_usd": 5}],
        cap_usd=4, max_cost_usd=20,
    )
    fc = forecast(mission, home)
    raised = apply_caps(raw, fc)
    assert [row["lane"] for row in raised] == ["claude", "claude-2", "named"]
    assert [raw["lanes"][i]["cap_usd"] for i in (0, 1, 3)] == [10, 10, 10]
    assert "cap_usd" not in raw["lanes"][2]
    assert raw["max_cost_usd"] == 37
    # A repeated application preserves both the money and its explanation.
    once = copy.deepcopy(raw)
    assert apply_caps(raw, fc) == []
    assert raw == once
    assert raw["caps"]["claude"]["rule_2_usd"] == 4


def test_generated_name_matching_skips_human_and_script_positions(home, tmp_path):
    for i in range(3):
        _receipt(home, str(i), 6)
    raw, mission = _mission(tmp_path, [
        {"fleet": "human", "name": "operator"},
        {"fleet": "script", "command": "true"},
        {"fleet": "claude", "cap_usd": 2},
    ])
    before = copy.deepcopy(raw["lanes"][:2])
    assert [row["lane"] for row in apply_caps(raw, forecast(mission, home))] == ["claude"]
    assert raw["lanes"][:2] == before
    assert set(raw["caps"]) == {"claude"}
