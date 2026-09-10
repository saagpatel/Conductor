"""Recover the last durable boundary, not the work a crashed process intended."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conductor import mission as m
from conductor.attempts import _attempt_from_run_receipt


class SimulatedCrash(BaseException):
    """Bypass the ordinary lane-error handler, as process loss would."""


@pytest.mark.parametrize("boundary", ["run", "lane", "mission"])
def test_resume_after_persistence_boundary_counts_each_saved_run_once(
    repo, home, fake_fleet, monkeypatch, boundary
):
    envelope = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                           "result": "reviewed", "total_cost_usd": 0.2,
                           "usage": {"input_tokens": 10, "output_tokens": 1}})
    fake_fleet(["sh", "-c", "printf '%s\\n' '" + envelope + "'"])
    mission = m.mission_from_dict({"cwd": str(repo), "max_cost_usd": 2,
                                  "lanes": [{"name": "review", "fleet": "claude",
                                             "prompt": "Review"}]}, base_dir=repo)
    original_write = Path.write_text

    def crash_after_write(path, text, *args, **kwargs):
        result = original_write(path, text, *args, **kwargs)
        is_boundary = (
            (boundary == "run" and path.name == "result.json" and path.parent.parent.name == "runs")
            or (boundary == "lane" and path.name == "review.json" and path.parent.name == "lanes")
            or (boundary == "mission" and path.name == "result.json"
                and path.parent.parent.name == "missions")
        )
        if is_boundary:
            raise SimulatedCrash(boundary)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_text", crash_after_write)
        with pytest.raises(SimulatedCrash):
            m.run_mission(mission, home=home)
    directory = next((home / "missions").iterdir())
    saved = {p.name for p in (home / "runs").iterdir()}
    assert len(saved) == 1
    assert not (directory / "running.json").exists()
    resumed = m.run_mission(mission, home=home, resume_dir=directory)
    assert resumed.ok
    expected_runs = 2 if boundary == "run" else 1
    assert len(list((home / "runs").iterdir())) == expected_runs
    assert resumed.cost_usd == pytest.approx(expected_runs * 0.2)
    assert resumed.resumed_from["kept"] == ([] if boundary == "run" else ["review"])
    again = m.run_mission(mission, home=home, resume_dir=directory)
    assert again.ok and again.cost_usd == resumed.cost_usd
    assert len(list((home / "runs").iterdir())) == expected_runs


@pytest.mark.parametrize("cost", [None, 0.0, 1.25, float("nan"), -1.0])
@pytest.mark.parametrize("state", ["normal", "cancelled", "timed_out", "dry_run"])
def test_live_recovery_and_resume_agree_at_each_cost_state(tmp_path, cost, state):
    receipt = {"spawned": True, "interrupted": False, "cancelled": state == "cancelled",
               "timed_out": state == "timed_out", "dry_run": state == "dry_run",
               "usage": {"cost_usd": cost}}
    ledger = m.Ledger(5)
    ledger.add(SimpleNamespace(**receipt))
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "result.json").write_text(json.dumps(receipt))
    recovered = _attempt_from_run_receipt("r1", receipt)
    lane = m.LaneResult(name="lane", ok=False, attempts=[recovered])
    resumed = m._run_receipt_accounting(tmp_path, {"lane": lane}, None)
    assert resumed == (ledger.spent, ledger.unpriced, ledger.unknown_cost)
    assert recovered.get("unpriced", False) == bool(ledger.unpriced)
    assert recovered.get("cost_unknown", False) == bool(ledger.unknown_cost)
    # A missing authoritative receipt still has the normalized summary.
    (run / "result.json").unlink()
    assert m._run_receipt_accounting(tmp_path, {"lane": lane}, None) == resumed
