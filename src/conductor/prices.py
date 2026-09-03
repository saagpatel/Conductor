"""Per-model list prices, so a week of dispatches is measurable in dollars.

Only the claude fleet reports a dollar figure. Codex, Antigravity, and Cursor
report tokens (or, for Codex, nothing unless asked for its event stream), so
without a table here three of four fleets would show `cost_usd: null` and the
mission-level total would be a lie by omission.

Every figure is a public list price per million tokens, dated. The claude
fleet's own reported cost always wins over an estimate. Rates drift, so the
table can be overridden without a code change: `$CONDUCTOR_HOME/prices.json`
is merged over these defaults at load time and may add, replace, or (with a
null entry) remove a model.

Conventions the estimate relies on:
  * `input_tokens` excludes cache reads; `cache_read_tokens` counts them.
    outputs.py normalizes every fleet to this convention before estimating.
  * `output_tokens` includes reasoning/thinking tokens; vendors bill them at
    the output rate.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

AS_OF = "2026-09-03"


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    note: str = ""

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        per_m = (
            input_tokens * self.input
            + output_tokens * self.output
            + cache_read_tokens * self.cache_read
            + cache_write_tokens * self.cache_write
        )
        return round(per_m / 1_000_000, 6)


def _std(input: float, output: float, note: str = "") -> Price:
    """The common vendor pattern: cache reads at 10% of input, cache writes at
    125% of input (Anthropic and OpenAI both publish exactly this)."""
    return Price(
        input=input,
        output=output,
        cache_read=round(input * 0.10, 6),
        cache_write=round(input * 1.25, 6),
        note=note,
    )


# Keyed by the longest stable prefix of the concrete model id the CLI is given.
# Antigravity and Cursor encode effort in the id (`gemini-3.8-flash-low`,
# `cursor-grok-4.6-xhigh`), so a prefix match is the lookup, not equality.
DEFAULT_PRICES: dict[str, Price] = {
    # Anthropic, first-party API rates.
    "claude-opus-5": _std(5.00, 25.00),
    "claude-sonnet-5": _std(2.00, 10.00),
    "claude-haiku-4-5": _std(1.00, 5.00),
    # OpenAI GPT-5.6 family, post 2026-07-30 cuts. Sol is listed at its
    # standing rate; a promotional cut ($4/$20) was reported on 2026-08-21 by a
    # single source and is not assumed here, so Sol estimates err high.
    "gpt-5.6-sol": _std(5.00, 30.00, "standing list rate; promo may be lower"),
    "gpt-5.6-terra": _std(2.00, 12.00),
    "gpt-5.6-luna": _std(0.20, 1.20),
    # Google Gemini Flash, introductory pricing through 2026-12-31; standard
    # pricing from 2027-01-01 is $1.50 / $7.50. Output includes thinking.
    "gemini-3.8-flash": Price(0.75, 3.75, 0.075, 0.0, "intro rate through 2026-12-31"),
    "gemini-3.7-flash": Price(0.75, 3.75, 0.075, 0.0, "intro rate through 2026-12-31"),
    # Cursor's first-party pool. Grok 4.6 inside Cursor bills at xAI's list
    # rate with no Cursor token surcharge; Composer 2.5 publishes no cache
    # discount, so cached input bills at the full input rate. Both draw from
    # a subscription's included usage before per-token billing applies, so
    # these are list-price equivalents, not necessarily marginal cost.
    "cursor-grok-4.6": Price(2.00, 6.00, 0.50, 0.0, "xAI list rate; <200K context"),
    "composer-2.5": Price(0.50, 2.50, 0.50, 0.0, "standard tier; no cache discount"),
}


def _override_path() -> Path:
    home = Path(os.environ.get("CONDUCTOR_HOME", Path.home() / ".conductor"))
    return home / "prices.json"


def load_prices(override: Path | None = None) -> dict[str, Price]:
    """Defaults, with the operator's override file merged on top.

    The override is a JSON object keyed like DEFAULT_PRICES; each value is
    either an object with `input`, `output`, and optional `cache_read`,
    `cache_write`, `note`, or null to drop a model from the table. A malformed
    file is reported as an error entry rather than raised, so a typo in
    prices.json cannot stop a 3am run; the estimate falls back to defaults.
    """
    table = dict(DEFAULT_PRICES)
    path = override or _override_path()
    if not path.is_file():
        return table
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return table
    if not isinstance(raw, dict):
        return table
    for key, value in raw.items():
        if value is None:
            table.pop(key, None)
            continue
        if not isinstance(value, dict):
            continue
        try:
            inp = float(value["input"])
            out = float(value["output"])
            # A missing or null cache rate takes the vendor-standard default;
            # anything else unparseable skips the entry rather than raising.
            cache_read = value.get("cache_read")
            cache_write = value.get("cache_write")
            table[key] = Price(
                input=inp,
                output=out,
                cache_read=float(cache_read) if cache_read is not None else inp * 0.10,
                cache_write=float(cache_write) if cache_write is not None else inp * 1.25,
                note=str(value.get("note", "override")),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return table


def lookup(model_id: str, table: dict[str, Price] | None = None) -> tuple[str, Price] | None:
    """Longest-prefix match of a concrete model id against the table."""
    prices = table if table is not None else load_prices()
    best: tuple[str, Price] | None = None
    for key, price in prices.items():
        if model_id == key or model_id.startswith(key + "-"):
            if best is None or len(key) > len(best[0]):
                best = (key, price)
    return best


def estimate(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    table: dict[str, Price] | None = None,
) -> float | None:
    """List-price cost for one dispatch, or None when the model is unpriced.

    None is deliberate: a missing price must show up as a gap in the ledger,
    not as $0.00, or a week of runs on an unpriced model reads as free.
    """
    hit = lookup(model_id, table)
    if hit is None:
        return None
    return hit[1].cost(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
