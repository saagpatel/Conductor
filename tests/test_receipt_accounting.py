"""Bounded receipt accounting: CostFacts and a single-read report parse."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from conductor.budget import CostFacts, budget_cost, cost_facts, unpriced_dispatch
from conductor.report import _read_run as report_read_run
from conductor.spend import _number, _run_from_receipt


@pytest.mark.parametrize(
    ("kwargs", "cost_usd", "budget_unpriced", "cancelled_unknown"),
    [
        ({"cost_usd": 1.25, "spawned": True}, 1.25, False, False),
        ({"cost_usd": 0, "spawned": True}, 0.0, False, False),
        ({"cost_usd": 2.5, "spawned": True, "cancelled": True}, 2.5, False, False),
        ({"cost_usd": 2.5, "spawned": True, "interrupted": True}, 2.5, False, False),
        ({"cost_usd": 2.5, "spawned": True, "timed_out": True}, 2.5, False, False),
        ({"cost_usd": 4.0, "spawned": True, "dry_run": True}, None, False, False),
        (
            {"cost_usd": None, "spawned": True, "cancelled": True, "dry_run": True},
            None,
            False,
            False,
        ),
        ({"cost_usd": None, "spawned": True}, None, True, False),
        ({"cost_usd": True, "spawned": True}, None, True, False),
        ({"cost_usd": False, "spawned": True}, None, True, False),
        ({"cost_usd": "1.0", "spawned": True}, None, True, False),
        ({"cost_usd": float("nan"), "spawned": True}, None, True, False),
        ({"cost_usd": float("inf"), "spawned": True}, None, True, False),
        ({"cost_usd": -0.01, "spawned": True}, None, True, False),
        ({"cost_usd": None}, None, False, False),
        ({"cost_usd": None, "spawned": True, "interrupted": True}, None, False, False),
        ({"cost_usd": None, "spawned": True, "timed_out": True}, None, False, False),
        ({"cost_usd": None, "spawned": True, "cancelled": True}, None, False, True),
        (
            {
                "cost_usd": None,
                "spawned": True,
                "cancelled": True,
                "interrupted": True,
            },
            None,
            False,
            True,
        ),
        ({"cost_usd": None, "spawned": False, "cancelled": True}, None, False, False),
    ],
)
def test_cost_facts_preserves_todays_budget_rule(
    kwargs: dict, cost_usd: float | None, budget_unpriced: bool, cancelled_unknown: bool
):
    facts = cost_facts(**kwargs)
    assert facts == CostFacts(cost_usd, budget_unpriced, cancelled_unknown)
    if kwargs.get("dry_run"):
        assert facts.cost_usd is None
        assert facts.budget_unpriced is False
        assert facts.cancelled_unknown is False
    else:
        assert facts.cost_usd == budget_cost(kwargs["cost_usd"])
        assert (
            unpriced_dispatch(
                spawned=kwargs.get("spawned", False),
                interrupted=kwargs.get("interrupted", False),
                cancelled=kwargs.get("cancelled", False),
                timed_out=kwargs.get("timed_out", False),
                cost_usd=kwargs["cost_usd"],
            )
            is budget_unpriced
        )


def test_cost_facts_is_immutable():
    facts = cost_facts(cost_usd=1.0, spawned=True)
    with pytest.raises(FrozenInstanceError):
        facts.cost_usd = 2.0  # type: ignore[misc]


def test_number_reuses_budget_admission_and_keeps_the_original_decimal():
    assert _number(None) is None
    assert _number(0) == Decimal(str(0))
    assert _number(1.25) == Decimal(str(1.25))
    for bad in (True, False, "1.0", float("nan"), float("inf"), -1):
        with pytest.raises(ValueError):
            _number(bad)


def test_run_from_receipt_skips_the_whole_receipt_on_an_invalid_cost():
    raw = {
        "run_id": "20260101T000000Z-claude-badcost",
        "fleet": "claude",
        "model": "sonnet",
        "ok": True,
        "usage": {"cost_usd": True, "cost_basis": "reported", "total_tokens": 5},
    }
    assert _run_from_receipt(raw) is None


def test_report_read_run_takes_price_and_metadata_from_the_first_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A receipt that changes between reads must not mix cost from one
    version with stage and duration from another."""
    run_id = "20260101T000000Z-claude-build"
    first = {
        "run_id": run_id,
        "fleet": "claude",
        "model": "claude-sonnet-5",
        "ok": True,
        "stage": "build",
        "lane": "build",
        "mission": "m1",
        "kind": None,
        "duration_s": 12.0,
        "usage": {
            "cost_usd": 1.25,
            "cost_basis": "reported",
            "total_tokens": 10,
            "input_tokens": 4,
        },
        "breaker": {"tool_calls": 3, "tripped": None},
        "interrupted": False,
        "cancelled": False,
        "dry_run": False,
    }
    second = {
        "run_id": run_id,
        "fleet": "cursor",
        "model": "grok-4.6",
        "ok": False,
        "stage": "review",
        "lane": "review",
        "mission": "other",
        "kind": "cap",
        "duration_s": 99.0,
        "usage": {
            "cost_usd": 8.5,
            "cost_basis": "estimated",
            "total_tokens": 999,
            "input_tokens": 80,
        },
        "breaker": {"tool_calls": 99, "tripped": None},
        "interrupted": True,
        "cancelled": True,
        "dry_run": False,
    }
    path = tmp_path / "runs" / run_id / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(first))

    real_read = Path.read_text
    reads = {"n": 0}

    def flipping(self: Path, *args: object, **kwargs: object) -> str:
        if self.resolve() == path.resolve():
            reads["n"] += 1
            return json.dumps(first if reads["n"] == 1 else second)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flipping)

    run = report_read_run(path, {})
    assert run is not None
    assert run.cost_usd == Decimal("1.25")
    assert run.estimated is False
    assert run.tokens == 10
    assert run.tool_calls == 3
    assert run.fleet == "claude"
    assert run.model == "claude-sonnet-5"
    assert run.ok is True
    assert run.stage == "build"
    assert run.lane == "build"
    assert run.mission == "m1"
    assert run.kind is None
    assert run.duration_s == 12.0
    assert run.input_tokens == 4
    assert run.interrupted is False
    assert run.cancelled is False
    assert reads["n"] == 1
