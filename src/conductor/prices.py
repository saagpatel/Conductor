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
import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path

from .paths import conductor_home

AS_OF = "2026-09-03"
log = logging.getLogger("conductor.prices")


def finite_positive(value: object) -> bool:
    """D14: a number a budget can actually fire on.

    A cap is compared against a spend, and both NaN (every comparison with
    it is False) and inf (no finite spend exceeds it) pass a plain `> 0`
    while leaving the cap permanently unenforceable. `True` is not a dollar
    figure either, however well it behaves as `1`.
    """
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def finite_nonnegative(value: object) -> bool:
    """`finite_positive`, but zero is a real answer: a free model's rate."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    note: str = ""
    # W6: "default" for a DEFAULT_PRICES entry, "override" for one the
    # operator's prices.json supplied -- carried onto every receipt this
    # entry prices, so a stale or wrong override is visible on the run it
    # affected rather than only in prices.json itself.
    source: str = "default"
    # A vendor whose rate steps up once the prompt passes a threshold, and
    # charges the WHOLE request at the higher rate rather than only the
    # tokens past the line: xAI doubles Grok 4.6 above 200K prompt tokens
    # ($2/$6 to $4/$12), and it can cross mid-run. Priced flat, a long
    # review is estimated at half what it cost and is judged against a cap
    # it has already passed (2026-09-08 review; AGENTS.md "Grok 4.6").
    # `long_context` is the same dataclass at the higher rate, so a table
    # can nest one step; a second step would nest again.
    long_context_tokens: int | None = None
    long_context: Price | None = None

    def tier(self, prompt_tokens: int) -> Price:
        """The rate this request actually bills at, given its prompt size."""
        if (
            self.long_context is not None
            and self.long_context_tokens is not None
            and prompt_tokens > self.long_context_tokens
        ):
            return self.long_context.tier(prompt_tokens)
        return self

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        # Everything the vendor counts as prompt: fresh input, cache reads,
        # and cache writes alike.
        rate = self.tier(input_tokens + cache_read_tokens + cache_write_tokens)
        if rate is not self:
            return rate.cost(input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)
        per_m = (
            input_tokens * self.input
            + output_tokens * self.output
            + cache_read_tokens * self.cache_read
            + cache_write_tokens * self.cache_write
        )
        return round(per_m / 1_000_000, 6)


def _std(
    input: float,
    output: float,
    note: str = "",
    source: str = "default",
    *,
    long_context_tokens: int | None = None,
    long_context: Price | None = None,
) -> Price:
    """The common vendor pattern: cache reads at 10% of input, cache writes at
    125% of input (Anthropic and OpenAI both publish exactly this)."""
    return Price(
        input=input,
        output=output,
        cache_read=round(input * 0.10, 6),
        cache_write=round(input * 1.25, 6),
        note=note,
        source=source,
        long_context_tokens=long_context_tokens,
        long_context=long_context,
    )


# Keyed by the longest stable prefix of the concrete model id the CLI is given.
# Antigravity and Cursor encode effort in the id (`gemini-3.8-flash-low`,
# `cursor-grok-4.6-xhigh`), so a prefix match is the lookup, not equality.
DEFAULT_PRICES: dict[str, Price] = {
    # Anthropic, first-party API rates.
    "claude-opus-5": _std(5.00, 25.00),
    "claude-sonnet-5": _std(2.00, 10.00),
    "claude-haiku-4-5": _std(1.00, 5.00),
    # OpenAI GPT-5.6 family, short-context standard tier as shown on the
    # OpenAI pricing page 2026-09-02 (operator screenshot): Sol $4 / $0.40
    # cached / $5 cache write / $20, Terra $2 / $0.20 / $2.50 / $12, Luna
    # $0.20 / $0.02 / $0.25 / $1.20. Long context (>~272K input) doubles the
    # input meters and adds 50% to output; modeled here.
    "gpt-5.6-sol": _std(
        4.00,
        20.00,
        "promotional rate, floor through 2026-11-21",
        long_context_tokens=272_000,
        long_context=_std(8.00, 30.00, ">272K prompt-token rate"),
    ),
    "gpt-5.6-terra": _std(
        2.00,
        12.00,
        long_context_tokens=272_000,
        long_context=_std(4.00, 18.00, ">272K prompt-token rate"),
    ),
    "gpt-5.6-luna": _std(
        0.20,
        1.20,
        long_context_tokens=272_000,
        long_context=_std(0.40, 1.80, ">272K prompt-token rate"),
    ),
    # Google Gemini Flash, introductory pricing through 2026-12-31; standard
    # pricing from 2027-01-01 is $1.50 / $7.50. Output includes thinking.
    "gemini-3.8-flash": Price(0.75, 3.75, 0.075, 0.0, "intro rate through 2026-12-31"),
    "gemini-3.7-flash": Price(0.75, 3.75, 0.075, 0.0, "intro rate through 2026-12-31"),
    # Cursor's first-party pool. Grok 4.6 inside Cursor bills at xAI's list
    # rate with no Cursor token surcharge; Composer 2.5 publishes no cache
    # discount, so cached input bills at the full input rate. Both draw from
    # a subscription's included usage before per-token billing applies, so
    # these are list-price equivalents, not necessarily marginal cost.
    "cursor-grok-4.6": Price(
        2.00,
        6.00,
        0.50,
        0.0,
        "xAI list rate; doubles above 200K prompt tokens",
        long_context_tokens=200_000,
        long_context=Price(4.00, 12.00, 1.00, 0.0, "xAI list rate; >200K prompt tokens"),
    ),
    "composer-2.5": Price(
        0.50,
        2.50,
        0.50,
        0.0,
        "standard tier; no cache discount. Cursor defaults to the fast tier ($3 / $15, "
        "2026-09-07 pricing page); confirm the tier before a Composer lane runs capped",
    ),
}


def _override_path() -> Path:
    return conductor_home() / "prices.json"


def load_prices(override: Path | None = None, errors: list[str] | None = None) -> dict[str, Price]:
    """Defaults, with the operator's override file merged on top.

    The override is a JSON object keyed like DEFAULT_PRICES; each value is
    either an object with `input`, `output`, and optional `cache_read`,
    `cache_write`, `note`, or null to drop a model from the table. A malformed
    file or entry never raises, so a typo in prices.json cannot stop a 3am
    run; it is logged as a warning, appended to `errors` when the caller
    passes a list (`conductor prices` prints them), and the affected entry
    falls back to its default.
    """
    table = dict(DEFAULT_PRICES)
    path = override or _override_path()
    if not path.is_file():
        return table
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _problem(errors, f"{path}: unreadable or not JSON ({exc}); using default prices")
        return table
    if not isinstance(raw, dict):
        _problem(errors, f"{path}: top level must be an object; using default prices")
        return table
    for key, value in raw.items():
        if value is None:
            table.pop(key, None)
            continue
        if not isinstance(value, dict):
            _problem(errors, f"{path}: '{key}' must be an object or null; entry ignored")
            continue
        try:
            inp = float(value["input"])
            out = float(value["output"])
            # A missing or null cache rate takes the vendor-standard default.
            cache_read = value.get("cache_read")
            cache_write = value.get("cache_write")
            # D14: `json.loads` accepts bare NaN and Infinity, and `float()`
            # takes "nan" and "-1" alike. A rate that is not a finite
            # non-negative number silently defeats every dollar figure it
            # touches: NaN makes `budget.over_cap` and `Ledger.add` compare
            # False forever, and a negative rate pays the operator back.
            bad = [
                name
                for name, rate in (
                    ("input", value["input"]),
                    ("output", value["output"]),
                    ("cache_read", cache_read),
                    ("cache_write", cache_write),
                )
                if rate is not None
                and (isinstance(rate, bool) or not finite_nonnegative(float(rate)))
            ]
            if bad:
                _problem(
                    errors,
                    f"{path}: '{key}' rate(s) {', '.join(bad)} must be finite and "
                    "non-negative; entry ignored",
                )
                continue
            price = _std(inp, out, str(value.get("note", "override")), source="override")
            if cache_read is not None:
                price = replace(price, cache_read=float(cache_read))
            if cache_write is not None:
                price = replace(price, cache_write=float(cache_write))
            table[key] = price
        except (KeyError, TypeError, ValueError) as exc:
            _problem(errors, f"{path}: '{key}' needs numeric input/output ({exc}); entry ignored")
    return table


def _problem(errors: list[str] | None, message: str) -> None:
    log.warning(message)
    if errors is not None:
        errors.append(message)


def lookup(model_id: str, table: dict[str, Price] | None = None) -> tuple[str, Price] | None:
    """Longest-prefix match of a concrete model id against the table."""
    prices = table if table is not None else load_prices()
    best: tuple[str, Price] | None = None
    for key, price in prices.items():
        if model_id == key or model_id.startswith(key + "-"):
            if best is None or len(key) > len(best[0]):
                best = (key, price)
    return best


def basis(model_id: str, table: dict[str, Price] | None = None) -> dict | None:
    """W6: the receipt-worthy explanation of an estimate -- which table key
    matched `model_id` (same longest-prefix `lookup` `estimate` itself
    uses), whether that entry is a default or an operator override, the
    table's own vintage, and the entry's note. `None` for an unpriced model,
    the same gap `estimate` itself reports as `None` rather than `$0.00`.
    """
    hit = lookup(model_id, table)
    if hit is None:
        return None
    key, price = hit
    return {"key": key, "source": price.source, "as_of": AS_OF, "note": price.note}


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
