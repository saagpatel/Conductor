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

from conductor.mission import LaneResult, _run_receipt_spend, budget_cost


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
