"""Replay resumed Antigravity accounting through real dispatch receipts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conductor import prices, spend
from conductor.fleets import Spec
from conductor.runner import dispatch

# Counter-only reduction of two live turns recorded on 2026-09-08.
# The second terminal envelope equals the first plus the new steps.
FIRST = {
    "input_tokens": 224725,
    "output_tokens": 65052,
    "thinking_tokens": 41485,
    "cache_read_tokens": 1357382,
}
SECOND = {
    "input_tokens": 43087,
    "output_tokens": 2781,
    "thinking_tokens": 684,
    "cache_read_tokens": 958132,
}
SESSION = "accounting-replay-session"


def _stream(index: int, step_usage: dict, terminal_usage: dict) -> str:
    return "\n".join(
        json.dumps(event)
        for event in [
            {"event": "init", "conversation_id": SESSION},
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": SESSION,
                    "step_index": index,
                    "state": "DONE",
                    "step_type": "model",
                    "usage": step_usage,
                },
            },
            {
                "event": "result",
                "conversation_id": SESSION,
                "result": {"status": "SUCCESS", "response": "accounted", "usage": terminal_usage},
            },
        ]
    )


def test_resumed_dispatch_prices_only_new_steps_and_spend_counts_each_turn_once(
    repo: Path, home: Path, fake_fleet, monkeypatch
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    cumulative = {key: FIRST[key] + SECOND[key] for key in FIRST}
    expected_costs = []
    for number, counters, terminal in [(1, FIRST, FIRST), (2, SECOND, cumulative)]:
        stream = _stream(1 if number == 1 else 62, counters, terminal)
        fake_fleet([sys.executable, "-c", "import sys; print(sys.argv[1])", stream])
        result = dispatch(
            Spec(
                fleet="antigravity",
                model="gemini-3.8-flash",
                cwd=str(repo),
                prompt=f"accounting replay {number}",
                resume=SESSION if number == 2 else None,
                cap_usd=10.0,
            ),
            home=home,
        )
        assert result.ok, result.error
        assert result.session_id == SESSION
        assert Path(result.answer_path).read_text().strip() == "accounted"
        receipt = json.loads((Path(result.run_dir) / "result.json").read_text())
        assert receipt["usage"] == result.usage
        usage = receipt["usage"]
        assert usage["input_tokens"] == counters["input_tokens"]
        assert usage["output_tokens"] == counters["output_tokens"] + counters["thinking_tokens"]
        assert usage["thinking_tokens"] == counters["thinking_tokens"]
        assert usage["cache_read_tokens"] == counters["cache_read_tokens"]
        expected_cost = prices.estimate(
            result.model,
            input_tokens=counters["input_tokens"],
            output_tokens=counters["output_tokens"] + counters["thinking_tokens"],
            cache_read_tokens=counters["cache_read_tokens"],
            cache_write_tokens=0,
        )
        assert usage["cost_usd"] == pytest.approx(expected_cost)
        assert usage["cost_basis"] == "estimated"
        expected_costs.append(expected_cost)
    _, total, skipped = spend.summarize(home, since=None, until=None, by="model")
    assert skipped == 0
    assert total.runs == 2
    assert float(total.cost_usd) == pytest.approx(sum(expected_costs))
    assert total.tokens == sum(cumulative.values())
