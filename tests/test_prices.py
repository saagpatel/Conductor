"""Cost accounting for the three fleets that report tokens but no dollars.

The failure these prevent: a mission ledger where every codex, antigravity,
and cursor row reads `cost_usd: null`, so a week of runs sums to whatever the
claude fleet alone spent.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from conductor import prices
from conductor.cli import main
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
    table = load_prices(override, today=date(2026, 9, 8))
    assert table["gpt-5.6-sol"].input == 4.0
    # Unmentioned fields keep the default entry, including cache rates that
    # are not 10%/125% of input and the long-context tier.
    assert table["gpt-5.6-sol"].cache_read == DEFAULT_PRICES["gpt-5.6-sol"].cache_read
    assert table["gpt-5.6-sol"].cache_write == DEFAULT_PRICES["gpt-5.6-sol"].cache_write
    assert table["gpt-5.6-sol"].long_context_tokens == 272_000
    assert table["gpt-5.6-sol"].long_context is not None
    assert table["gpt-5.6-sol"].long_context.input == 8.00
    assert table["gpt-5.6-sol"].note == "promo"
    assert table["gpt-5.6-sol"].as_of == "2026-09-08"
    assert table["brand-new-model"].output == 2.0
    assert table["brand-new-model"].cache_read == 0.1  # _std; no default entry
    assert table["brand-new-model"].long_context is None
    assert "composer-2.5" not in table
    assert "gpt-5.6-terra" in table  # untouched defaults survive


def test_a_broken_override_file_falls_back_to_defaults_and_says_so(tmp_path: Path):
    """A typo in prices.json must not stop a 3am run, and must not be
    swallowed either: the problem is logged and handed back to the caller."""
    override = tmp_path / "prices.json"
    override.write_text("{not json")
    errors: list[str] = []
    assert load_prices(override, errors, today=date(2026, 9, 8)) == DEFAULT_PRICES
    assert len(errors) == 1 and "not JSON" in errors[0]
    override.write_text(json.dumps({"gpt-5.6-terra": {"input": "lots"}, "x": 5}))
    errors.clear()
    table = load_prices(override, errors, today=date(2026, 9, 8))
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


def test_a_rate_that_is_not_finite_and_non_negative_is_reported_and_not_loaded(tmp_path):
    """D14: `json.loads` accepts bare NaN and Infinity and `float()` takes
    "-1" happily. A NaN rate makes every dollar comparison downstream
    (`budget.over_cap`, `Ledger.add`) False forever, so a capped run spends
    without limit; a negative rate pays the operator back. Both are the
    operator's own typo, so they are reported and the default stands."""
    override = tmp_path / "prices.json"
    override.write_text(
        """{
          "claude-opus-5": {"input": NaN, "output": 25.0},
          "claude-sonnet-5": {"input": 2.0, "output": -10.0},
          "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": Infinity},
          "gemini-3.7-flash": {"input": 1.0, "output": 2.0, "cache_write": true},
          "cursor-grok-4.6": {"input": 3.0, "output": 9.0, "cache_read": 0}
        }"""
    )
    errors: list[str] = []
    table = load_prices(override, errors, today=date(2026, 9, 8))

    assert len(errors) == 4
    assert all("must be finite and non-negative" in message for message in errors)
    assert table["claude-opus-5"] == DEFAULT_PRICES["claude-opus-5"]
    assert table["claude-sonnet-5"] == DEFAULT_PRICES["claude-sonnet-5"]
    assert table["claude-haiku-4-5"] == DEFAULT_PRICES["claude-haiku-4-5"]
    assert table["gemini-3.7-flash"] == DEFAULT_PRICES["gemini-3.7-flash"]
    # Zero is a real rate (a free model), and the entry beside four bad ones
    # still loads.
    assert table["cursor-grok-4.6"].input == 3.0
    assert table["cursor-grok-4.6"].cache_read == 0.0


def test_basis_reports_the_matched_key_source_and_date():
    """W6: the receipt-worthy explanation of an estimate -- same longest-
    prefix lookup `estimate` itself uses."""
    hit = prices.basis("claude-sonnet-5-hard", DEFAULT_PRICES)
    assert hit == {
        "key": "claude-sonnet-5",
        "source": "default",
        "as_of": prices.AS_OF,
        "note": "",
    }
    assert prices.basis("nobody-knows-this", DEFAULT_PRICES) is None


def test_basis_reports_override_as_its_own_source(tmp_path: Path):
    override = tmp_path / "prices.json"
    override.write_text(json.dumps({"gpt-5.6-terra": {"input": 2.0, "output": 12.0}}))
    table = load_prices(override, today=date(2026, 9, 8))
    hit = prices.basis("gpt-5.6-terra-high", table)
    assert hit is not None
    assert hit["key"] == "gpt-5.6-terra"
    assert hit["source"] == "override"
    assert hit["as_of"] == "2026-09-08"


def test_finite_helpers_refuse_booleans_and_non_numbers():
    for bad in (True, False, float("nan"), float("inf"), "1.0", None):
        assert prices.finite_positive(bad) is False
        assert prices.finite_nonnegative(bad) is False
    assert prices.finite_positive(0) is False and prices.finite_nonnegative(0) is True
    assert prices.finite_positive(0.5) is True


def test_grok_bills_the_whole_request_at_the_long_context_rate_above_200k():
    """xAI doubles Grok 4.6 above 200K prompt tokens, and charges the whole
    request at the higher rate. Priced flat, a long review was estimated at
    half what it cost, so its cap was judged against the wrong band."""
    under = estimate(
        "cursor-grok-4.6-hard",
        input_tokens=100_000,
        output_tokens=10_000,
        table=DEFAULT_PRICES,
    )
    over = estimate(
        "cursor-grok-4.6-hard",
        input_tokens=300_000,
        output_tokens=10_000,
        table=DEFAULT_PRICES,
    )
    assert under == round(100_000 * 2.00 / 1e6 + 10_000 * 6.00 / 1e6, 6)
    # Every token at the doubled rate, not only the 100K past the line.
    assert over == round(300_000 * 4.00 / 1e6 + 10_000 * 12.00 / 1e6, 6)


def test_cache_reads_count_toward_the_long_context_threshold():
    """The threshold is on the prompt, and a cache read is prompt: 150K fresh
    input plus 100K of cache read is a 250K prompt, not a 150K one."""
    price = DEFAULT_PRICES["cursor-grok-4.6"]
    assert price.tier(150_000) is price
    assert price.tier(250_000) is price.long_context
    assert price.cost(150_000, 0, cache_read_tokens=100_000) == round(
        150_000 * 4.00 / 1e6 + 100_000 * 1.00 / 1e6, 6
    )


def test_gpt56_family_bills_long_context_rate_above_272k():
    """GPT-5.6 Sol, Terra, and Luna double input meters and add 50% to output
    when prompt tokens exceed 272K."""
    for model_id, base_in, base_out, long_in, long_out in [
        ("gpt-5.6-sol", 4.00, 20.00, 8.00, 30.00),
        ("gpt-5.6-terra", 2.00, 12.00, 4.00, 18.00),
        ("gpt-5.6-luna", 0.20, 1.20, 0.40, 1.80),
    ]:
        under = estimate(
            model_id,
            input_tokens=200_000,
            output_tokens=10_000,
            table=DEFAULT_PRICES,
        )
        over = estimate(
            model_id,
            input_tokens=300_000,
            output_tokens=10_000,
            table=DEFAULT_PRICES,
        )
        assert under == round(200_000 * base_in / 1e6 + 10_000 * base_out / 1e6, 6)
        assert over == round(300_000 * long_in / 1e6 + 10_000 * long_out / 1e6, 6)


def test_gpt56_long_context_tier_derives_cache_rates_and_sets_threshold():
    """Each GPT-5.6 model sets a 272K threshold and derives cache rates on its
    long-context tier via _std."""
    for model_id, expected_long_in in [
        ("gpt-5.6-sol", 8.00),
        ("gpt-5.6-terra", 4.00),
        ("gpt-5.6-luna", 0.40),
    ]:
        price = DEFAULT_PRICES[model_id]
        assert price.long_context_tokens == 272_000
        assert price.long_context is not None
        assert price.long_context.input == expected_long_in
        assert price.long_context.cache_read == round(expected_long_in * 0.10, 6)
        assert price.long_context.cache_write == round(expected_long_in * 1.25, 6)
        assert price.long_context.note == ">272K prompt-token rate"


def test_an_override_keeps_unmentioned_fields_including_the_long_context_tier(tmp_path: Path):
    """Rebuilding through _std dropped Grok's 200K doubling and rewrote its
    cache rates to 10%/125% of the new input. An operator who only touches
    input and output must keep everything they did not mention."""
    override = tmp_path / "prices.json"
    override.write_text(json.dumps({"cursor-grok-4.6": {"input": 3.0, "output": 9.0}}))
    grok = load_prices(override)["cursor-grok-4.6"]
    assert grok.input == 3.0
    assert grok.output == 9.0
    assert grok.cache_read == 0.50
    assert grok.cache_write == 0.0
    assert grok.long_context_tokens == 200_000
    assert grok.long_context is not None
    assert grok.long_context.input == 4.00
    assert grok.long_context.output == 12.00
    assert grok.long_context.cache_read == 1.00
    assert grok.tier(300_000) is grok.long_context


def test_an_override_can_set_the_long_context_tier(tmp_path: Path):
    override = tmp_path / "prices.json"
    override.write_text(
        json.dumps(
            {
                "cursor-grok-4.6": {
                    "input": 3.0,
                    "output": 9.0,
                    "long_context_tokens": 250_000,
                    "long_context": {"input": 6.0, "output": 18.0},
                }
            }
        )
    )
    grok = load_prices(override)["cursor-grok-4.6"]
    assert grok.long_context_tokens == 250_000
    assert grok.long_context is not None
    assert grok.long_context.input == 6.0
    assert grok.long_context.output == 18.0
    # Nested cache rates the override omitted keep the default nested values.
    assert grok.long_context.cache_read == 1.00
    assert grok.long_context.cache_write == 0.0
    assert grok.tier(200_000) is grok
    assert grok.tier(250_001) is grok.long_context


def test_a_malformed_long_context_tier_keeps_the_default_and_says_so(tmp_path: Path):
    override = tmp_path / "prices.json"
    override.write_text(
        json.dumps(
            {
                "cursor-grok-4.6": {
                    "input": 3.0,
                    "output": 9.0,
                    "long_context_tokens": -1,
                    "long_context": {"input": 6.0, "output": 18.0},
                }
            }
        )
    )
    errors: list[str] = []
    table = load_prices(override, errors, today=date(2026, 9, 8))
    grok = table["cursor-grok-4.6"]
    assert grok.input == 3.0
    assert grok.long_context_tokens == 200_000
    assert grok.long_context is not None
    assert grok.long_context.input == 4.00
    assert len(errors) == 1
    assert "long_context_tokens" in errors[0]
    assert "default tier kept" in errors[0]


def test_conductor_prices_prints_the_long_context_tier(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(tmp_path))
    assert main(["prices"]) == 0
    out = json.loads(capsys.readouterr().out)
    grok = out["usd_per_million_tokens"]["cursor-grok-4.6"]
    assert grok["long_context_tokens"] == 200_000
    assert grok["long_context"]["input"] == 4.0
    assert grok["long_context"]["output"] == 12.0
    sonnet = out["usd_per_million_tokens"]["claude-sonnet-5"]
    assert "long_context" not in sonnet
    assert "long_context_tokens" not in sonnet


def test_each_entry_carries_its_own_as_of_and_basis_reports_it():
    assert prices.AS_OF == "2026-09-03"
    assert DEFAULT_PRICES["claude-sonnet-5"].as_of == prices.AS_OF
    assert DEFAULT_PRICES["composer-2.5"].as_of == "2026-09-07"
    for key in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        assert DEFAULT_PRICES[key].as_of == "2026-09-08"
        assert DEFAULT_PRICES[key].long_context is not None
        assert DEFAULT_PRICES[key].long_context.as_of == "2026-09-08"
    assert prices.basis("composer-2.5", DEFAULT_PRICES)["as_of"] == "2026-09-07"
    assert prices.basis("claude-sonnet-5", DEFAULT_PRICES)["as_of"] == prices.AS_OF


def test_an_expired_price_warns_but_keeps_the_shipped_rate(tmp_path: Path):
    assert DEFAULT_PRICES["gemini-3.8-flash"].expires == "2026-12-31"
    assert DEFAULT_PRICES["gemini-3.7-flash"].expires == "2026-12-31"
    assert DEFAULT_PRICES["gpt-5.6-sol"].expires == "2026-11-21"
    assert DEFAULT_PRICES["claude-sonnet-5"].expires is None

    missing = tmp_path / "prices.json"
    still_valid: list[str] = []
    table = load_prices(missing, still_valid, today=date(2026, 11, 21))
    assert table["gpt-5.6-sol"].input == 4.00
    assert table["gemini-3.8-flash"].input == 0.75
    assert still_valid == []
    hit = prices.basis("gpt-5.6-sol", table, today=date(2026, 11, 21))
    assert hit is not None
    assert hit["expires"] == "2026-11-21"
    assert "expired" not in hit

    expired: list[str] = []
    table = load_prices(missing, expired, today=date(2027, 1, 2))
    assert table["gemini-3.8-flash"].input == 0.75
    assert table["gemini-3.7-flash"].output == 3.75
    assert table["gpt-5.6-sol"].input == 4.00
    assert len(expired) == 3
    assert all("still using the shipped rate" in message for message in expired)
    assert any("gemini-3.8-flash" in message for message in expired)
    assert any("gemini-3.7-flash" in message for message in expired)
    assert any("gpt-5.6-sol" in message for message in expired)
    gemini = prices.basis("gemini-3.8-flash", table, today=date(2027, 1, 2))
    assert gemini is not None
    assert gemini["expires"] == "2026-12-31"
    assert "expired" in gemini["expired"]
    sol = prices.basis("gpt-5.6-sol", table, today=date(2027, 1, 2))
    assert sol is not None
    assert sol["expired"].startswith("gpt-5.6-sol:")
    # An unexpired entry on the same day is silent.
    terra = prices.basis("gpt-5.6-terra", table, today=date(2027, 1, 2))
    assert terra is not None
    assert "expired" not in terra
    assert "expires" not in terra


