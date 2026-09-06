"""Cost accounting for the three fleets that report tokens but no dollars.

The failure these prevent: a mission ledger where every codex, antigravity,
and cursor row reads `cost_usd: null`, so a week of runs sums to whatever the
claude fleet alone spent.
"""

from __future__ import annotations

import json
from pathlib import Path

from conductor import prices
from conductor.prices import DEFAULT_PRICES, Price, estimate, load_prices, lookup


def test_every_permitted_model_id_is_priced_at_every_effort():
    """The allowlist and the price table must agree, or a permitted model
    silently runs unpriced. E6: the script fleet is exempt -- it costs
    nothing by construction (runner.dispatch prices it at a hardcoded
    0.0, never estimated from tokens), so "sh" is deliberately absent from
    the price table rather than priced at zero."""
    from conductor.fleets import EFFORTS, FLEETS

    for fleet in FLEETS.values():
        if fleet.cap == "none":
            continue
        for model in fleet.models:
            for effort in EFFORTS:
                model_id = model.id_for(effort)
                assert lookup(model_id, DEFAULT_PRICES) is not None, (fleet.name, model_id)


def test_effort_suffixes_resolve_to_the_family_price():
    key, _ = lookup("gemini-3.8-flash-high", DEFAULT_PRICES)
    assert key == "gemini-3.8-flash"
    key, _ = lookup("cursor-grok-4.6-xhigh", DEFAULT_PRICES)
    assert key == "cursor-grok-4.6"


def test_prefix_match_does_not_cross_a_family_boundary():
    """gpt-5.6-sol must not match gpt-5.6-sol-something-else's sibling, and a
    bare unknown id must not match anything."""
    assert lookup("gpt-5.6", DEFAULT_PRICES) is None
    assert lookup("gemini-3.8-flashy", DEFAULT_PRICES) is None
    assert lookup("claude-opus-4-6", DEFAULT_PRICES) is None


def test_estimate_prices_all_four_token_classes():
    table = {"m": Price(input=1.0, output=10.0, cache_read=0.1, cache_write=1.25)}
    cost = estimate(
        "m-low",
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        table=table,
    )
    assert cost == 1.0 + 1.0 + 0.1 + 1.25


def test_an_unpriced_model_is_none_not_zero():
    """None shows as a gap in the ledger; 0.0 would read as free."""
    assert estimate("nobody-knows-this", input_tokens=10, output_tokens=10) is None


def test_the_measured_codex_pong_costs_about_five_cents():
    """22347 input (6912 cached), 6 output on terra: ~$0.0316 + $0.0014."""
    cost = estimate(
        "gpt-5.6-terra",
        input_tokens=22347 - 6912,
        output_tokens=6,
        cache_read_tokens=6912,
        table=DEFAULT_PRICES,
    )
    assert 0.03 < cost < 0.04


def test_override_file_replaces_adds_and_removes(tmp_path: Path):
    override = tmp_path / "prices.json"
    override.write_text(
        json.dumps(
            {
                "gpt-5.6-sol": {"input": 4.0, "output": 20.0, "note": "promo"},
                "brand-new-model": {"input": 1.0, "output": 2.0},
                "composer-2.5": None,
            }
        )
    )
    table = load_prices(override)
    assert table["gpt-5.6-sol"].input == 4.0
    assert table["gpt-5.6-sol"].cache_read == 0.4  # derived when not given
    assert table["brand-new-model"].output == 2.0
    assert "composer-2.5" not in table
    assert "gpt-5.6-terra" in table  # untouched defaults survive


def test_a_broken_override_file_falls_back_to_defaults_and_says_so(tmp_path: Path):
    """A typo in prices.json must not stop a 3am run, and must not be
    swallowed either: the problem is logged and handed back to the caller."""
    override = tmp_path / "prices.json"
    override.write_text("{not json")
    errors: list[str] = []
    assert load_prices(override, errors) == DEFAULT_PRICES
    assert len(errors) == 1 and "not JSON" in errors[0]
    override.write_text(json.dumps({"gpt-5.6-terra": {"input": "lots"}, "x": 5}))
    errors.clear()
    table = load_prices(override, errors)
    assert table["gpt-5.6-terra"] == DEFAULT_PRICES["gpt-5.6-terra"]
    assert len(errors) == 2
    assert any("gpt-5.6-terra" in e for e in errors) and any("'x'" in e for e in errors)


def test_a_null_cache_rate_in_an_override_takes_the_default(tmp_path: Path):
    override = tmp_path / "prices.json"
    override.write_text(
        json.dumps({"gpt-5.6-terra": {"input": 2, "output": 12, "cache_read": None}})
    )
    table = load_prices(override)
    assert table["gpt-5.6-terra"].cache_read == 0.2
    assert table["gpt-5.6-terra"].cache_write == 2.5


def test_override_path_follows_conductor_home(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(tmp_path))
    (tmp_path / "prices.json").write_text(json.dumps({"gpt-5.6-luna": {"input": 9, "output": 9}}))
    assert prices.load_prices()["gpt-5.6-luna"].input == 9.0
