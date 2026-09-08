"""The resume seed reads receipts by the same rule the live ledger writes them.

`Ledger.add` grew a finite, non-negative guard and an exclusion for a run
conductor cancelled. `_run_receipt_spend`, which seeds a resumed mission's
budget from the same receipts on disk, had neither, so the mission that
resumed disagreed with the mission that ran (2026-09-08 review).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from conductor.attempts import _read_previous_lanes
from conductor.mission import LaneResult, _run_receipt_spend, budget_cost, mission_from_dict


def _run(base: Path, run_id: str, receipt: dict) -> None:
    directory = base / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps(receipt))


def _lane(run_id: str) -> dict[str, LaneResult]:
    """The `previous` map a resume passes in: one settled lane, one attempt."""
    return {"build": LaneResult(name="build", ok=True, attempts=[{"run_id": run_id}])}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.25, 1.25),
        (0, 0.0),
        (True, None),
        (False, None),
        ("1.0", None),
        (None, None),
        (float("nan"), None),
        (float("inf"), None),
        (-0.01, None),
    ],
)
def test_budget_cost_admits_only_a_finite_non_negative_number(value, expected):
    result = budget_cost(value)
    if expected is None:
        assert result is None
    else:
        assert result == expected


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1.5", "true"])
def test_a_receipt_with_an_unusable_cost_is_unpriced_on_resume_not_summed(
    tmp_path: Path, bad: str
):
    """json round-trips NaN and Infinity, and `NaN >= max` is False, so a
    summed one turns the budget off rather than tripping it."""
    _run(
        tmp_path,
        "r1",
        {"spawned": True, "usage": {"cost_usd": json.loads(bad)}},
    )

    spent, unpriced = _run_receipt_spend(tmp_path, _lane("r1"), None)

    assert math.isfinite(spent) and spent == 0.0
    assert unpriced == 1


def test_a_usable_cost_is_still_summed_on_resume(tmp_path: Path):
    _run(tmp_path, "r1", {"spawned": True, "usage": {"cost_usd": 2.5}})

    spent, unpriced = _run_receipt_spend(tmp_path, _lane("r1"), None)

    assert spent == 2.5
    assert unpriced == 0


def test_a_cancelled_unpriced_run_is_not_counted_unpriced_on_resume(tmp_path: Path):
    """`Ledger.add` drops a cancelled unpriced dispatch from both totals: it
    cannot have spent past the cap it was cut off inside. The resume seed
    counted it unpriced, so resuming an early-cancelled mission could refuse
    to start anything at all as `budget unverifiable`."""
    _run(tmp_path, "r1", {"spawned": True, "cancelled": True, "usage": {}})

    spent, unpriced = _run_receipt_spend(tmp_path, _lane("r1"), None)

    assert spent == 0.0
    assert unpriced == 0


def test_an_interrupted_unpriced_run_is_still_not_counted_unpriced(tmp_path: Path):
    _run(tmp_path, "r1", {"spawned": True, "interrupted": True, "usage": {}})

    assert _run_receipt_spend(tmp_path, _lane("r1"), None) == (0.0, 0)


def test_a_plain_unpriced_run_is_still_counted_unpriced(tmp_path: Path):
    """The exclusions are for runs conductor itself stopped. A dispatch that
    simply came back without a price is exactly what `unpriced` is for."""
    _run(tmp_path, "r1", {"spawned": True, "usage": {}})

    assert _run_receipt_spend(tmp_path, _lane("r1"), None) == (0.0, 1)


def test_a_missing_lane_receipt_still_seeds_spend_from_the_run_on_disk(tmp_path: Path):
    """`lanes/<name>.json` is written only when a lane ends. A hard-killed
    mission leaves run receipts with no lane receipt; resume used to seed
    spent=0 for those lanes and spend the same dollars again."""
    home = tmp_path
    mission_dir = home / "missions" / "m1"
    (mission_dir / "lanes").mkdir(parents=True)
    _run(
        home,
        "r1",
        {
            "run_id": "r1",
            "spawned": True,
            "mission": "m1",
            "lane": "build",
            "usage": {"cost_usd": 3.0},
        },
    )
    mission = mission_from_dict(
        {"prompt": "x", "lanes": [{"name": "build", "fleet": "claude"}]},
        base_dir=tmp_path,
    )

    previous, notes, unknown = _read_previous_lanes(mission_dir, mission, base=home)
    spent, unpriced = _run_receipt_spend(home, previous, None)

    assert unknown == 0
    assert spent == 3.0
    assert unpriced == 0
    assert previous["build"].attempts[0]["run_id"] == "r1"
    assert any("recovered 1 run" in note for note in notes)


def test_a_lane_receipt_and_its_run_are_not_double_counted(tmp_path: Path):
    home = tmp_path
    mission_dir = home / "missions" / "m1"
    lanes = mission_dir / "lanes"
    lanes.mkdir(parents=True)
    (lanes / "build.json").write_text(
        json.dumps(
            {
                "name": "build",
                "ok": True,
                "attempts": [
                    {
                        "run_id": "r1",
                        "fleet": "claude",
                        "attempt": "claude",
                        "ok": True,
                        "exit_code": 0,
                        "no_op": True,
                        "commits": 0,
                        "duration_s": 1.0,
                    }
                ],
            }
        )
    )
    _run(
        home,
        "r1",
        {
            "run_id": "r1",
            "spawned": True,
            "mission": "m1",
            "lane": "build",
            "usage": {"cost_usd": 3.0},
        },
    )
    mission = mission_from_dict(
        {"prompt": "x", "lanes": [{"name": "build", "fleet": "claude"}]},
        base_dir=tmp_path,
    )

    previous, notes, unknown = _read_previous_lanes(mission_dir, mission, base=home)
    spent, unpriced = _run_receipt_spend(home, previous, None)

    assert unknown == 0
    assert spent == 3.0
    assert unpriced == 0
    assert not any("recovered" in note for note in notes)


def test_a_lane_that_never_started_is_not_accounting_unknown(tmp_path: Path):
    home = tmp_path
    mission_dir = home / "missions" / "m1"
    (mission_dir / "lanes").mkdir(parents=True)
    mission = mission_from_dict(
        {
            "prompt": "x",
            "lanes": [
                {"name": "build", "fleet": "claude"},
                {"name": "review", "fleet": "claude"},
            ],
        },
        base_dir=tmp_path,
    )

    previous, notes, unknown = _read_previous_lanes(mission_dir, mission, base=home)

    assert previous == {}
    assert notes == []
    assert unknown == 0
