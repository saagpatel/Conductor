"""E10 second spec item 1: the approved child's cap is clamped at launch.

A cold read of approvals.py (2026-09-08) found the clamp unreachable. The
launch reruns `_plan_check_child` in full against the live ledger first, and
that check refuses a child whose `max_cost_usd` is over the parent's
remaining -- so `min(child.max_cost_usd, parent_remaining)` could only ever
be a no-op, and a parent that spent while the pause waited failed a plan the
operator had just approved instead of launching it smaller.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor.approvals import _plan_check_child
from conductor.mission import Ledger, mission_from_dict


def _child_file(tmp_path: Path, cwd: Path, *, max_cost_usd: float) -> str:
    child = {
        "name": "child",
        "cwd": str(cwd),
        "max_cost_usd": max_cost_usd,
        "lanes": [{"name": "work", "fleet": "script", "command": "true"}],
    }
    path = tmp_path / "child.json"
    path.write_text(json.dumps(child))
    return str(path)


def _parent(cwd: Path) -> object:
    return mission_from_dict(
        {
            "name": "parent",
            "cwd": str(cwd),
            "max_cost_usd": 10.0,
            "lanes": [{"name": "work", "fleet": "script", "command": "true"}],
        },
        base_dir=cwd,
    )


def test_the_park_refuses_a_child_over_the_parents_remaining(tmp_path: Path, repo: Path, home):
    """At park time there is nothing to clamp to yet: an over-budget child is
    a plan the operator should not be asked to approve."""
    parent = _parent(repo)
    ledger = Ledger(max_cost_usd=10.0)

    plan, refusal = _plan_check_child(
        parent,
        _child_file(tmp_path, repo, max_cost_usd=20.0),
        ledger=ledger,
        base=home,
    )

    assert refusal is not None
    assert "over the parent's remaining" in refusal
    assert plan["refused"] == refusal


def test_the_launch_clamps_a_child_the_parent_can_no_longer_fully_cover(
    tmp_path: Path, repo: Path, home
):
    """A mission parks while other lanes are still dispatching, so the
    parent's remaining can drop between the park and the operator's answer.
    The launch takes the lesser figure rather than failing the plan."""
    parent = _parent(repo)
    ledger = Ledger(max_cost_usd=10.0)

    plan, refusal = _plan_check_child(
        parent,
        _child_file(tmp_path, repo, max_cost_usd=20.0),
        ledger=ledger,
        base=home,
        clamp_budget=True,
    )

    assert refusal is None
    assert plan["refused"] is None
    assert plan["dry_run_ok"] is True
    assert plan["child_max_cost_usd"] == 20.0


def test_a_parent_with_nothing_left_still_refuses_at_launch(
    tmp_path: Path, repo: Path, home, monkeypatch
):
    """There is no budget to clamp to. The refusal stands even under
    `clamp_budget`."""
    parent = _parent(repo)
    ledger = Ledger(max_cost_usd=10.0)
    monkeypatch.setattr(Ledger, "remaining", lambda self: 0.0)

    _plan, refusal = _plan_check_child(
        parent,
        _child_file(tmp_path, repo, max_cost_usd=1.0),
        ledger=ledger,
        base=home,
        clamp_budget=True,
    )

    assert refusal is not None
    assert "over the parent's remaining" in refusal


@pytest.mark.parametrize("clamp", [False, True])
def test_every_other_gate_still_refuses_at_launch(tmp_path: Path, repo: Path, home, clamp):
    """`clamp_budget` widens exactly one comparison. A child with no cap at
    all is still refused either way -- there is nothing to take a minimum
    of."""
    child = {
        "name": "child",
        "cwd": str(repo),
        "lanes": [{"name": "work", "fleet": "script", "command": "true"}],
    }
    path = tmp_path / "child.json"
    path.write_text(json.dumps(child))

    _plan, refusal = _plan_check_child(
        _parent(repo), str(path), ledger=Ledger(max_cost_usd=10.0), base=home, clamp_budget=clamp
    )

    assert refusal == "child has no max_cost_usd; a planned mission's budget must be bounded"
