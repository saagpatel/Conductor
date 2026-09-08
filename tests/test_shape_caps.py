"""Cap arithmetic defects: finite dollars, follow-on budget, unsized lanes."""

from __future__ import annotations

import json
import math

import pytest

from conductor import shape
from conductor.fleets import CAP_GRACE_CEILING_USD
from conductor.prices import finite_nonnegative, finite_positive

# --- item 1: parse_ceiling refuses non-finite dollar bounds --------------------


@pytest.mark.parametrize(
    "value",
    [
        "inf,10",
        "10,inf",
        "+inf,10",
        "-inf,10",
        "Infinity,10",
        "nan,5",
        "5,nan",
        "NaN,5",
        "1e999,10",
    ],
)
def test_parse_ceiling_refuses_non_finite_bounds(value):
    """`float("inf") <= 0` and every comparison against NaN are False, so
    `--ceiling inf,10` and `--ceiling nan,5` used to return a dict the
    mission loader then refused -- after `prompts/*.md` was already on
    disk -- and that a Python caller cannot even `json.dumps`."""
    with pytest.raises(shape.ShapeInvalid, match="--ceiling"):
        shape.parse_ceiling(value)


def test_parse_ceiling_finite_positive_bounds_still_load_and_dump():
    parsed = shape.parse_ceiling("5,10")
    assert parsed == {"per_hour_usd": 5.0, "per_day_usd": 10.0}
    json.dumps(parsed)
    assert finite_positive(parsed["per_hour_usd"])
    assert finite_positive(parsed["per_day_usd"])


@pytest.mark.parametrize("value", ["5,", ",5", " ,5", "5, ", "true,5", "5,True"])
def test_parse_ceiling_empty_parts_and_bool_tokens_are_not_numbers(value):
    """Empty parts and bool-shaped tokens already fail `float()`; they stay
    refused under the same `--ceiling` message as a non-numeric token.
    `parse_ceiling` takes a string, so a Python `bool` never arrives;
    `finite_positive` would refuse one if it did (`True` is not a dollar)."""
    with pytest.raises(shape.ShapeInvalid, match="--ceiling"):
        shape.parse_ceiling(value)


def test_parse_ceiling_whitespace_around_finite_numbers_is_still_accepted():
    assert shape.parse_ceiling(" 5, 10 ") == {"per_hour_usd": 5.0, "per_day_usd": 10.0}
    assert shape.parse_ceiling("0.5,1.25") == {"per_hour_usd": 0.5, "per_day_usd": 1.25}


# --- item 2: cap_arithmetic refuses nan (and the rest of the dollar rule) ------


def test_cap_arithmetic_refuses_nan_grace():
    """The only float that reaches `cap_arithmetic` is `cap_grace_usd`.
    `--items`, `--modules`, `--tests-items` and `--findings` are ints.
    NaN fails every comparison, so it walked through `< 0 or > ceiling`."""
    with pytest.raises(shape.ShapeInvalid, match="cap-grace-usd"):
        shape.cap_arithmetic(1, 1, cap_grace_usd=float("nan"))


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), True, False, -0.01])
def test_cap_arithmetic_refuses_non_dollar_grace(bad):
    with pytest.raises(shape.ShapeInvalid, match="cap-grace-usd"):
        shape.cap_arithmetic(1, 1, cap_grace_usd=bad)


def test_cap_arithmetic_zero_grace_still_disables_the_band():
    caps = shape.cap_arithmetic(1, 1, cap_grace_usd=0)
    assert caps.cap_grace_usd == 0
    assert caps.graced_lanes == 0
    assert finite_nonnegative(caps.cap_grace_usd)
    assert not finite_positive(caps.cap_grace_usd)


def test_cap_arithmetic_grace_at_the_ceiling_still_loads():
    caps = shape.cap_arithmetic(1, 1, cap_grace_usd=CAP_GRACE_CEILING_USD)
    assert caps.cap_grace_usd == CAP_GRACE_CEILING_USD


# --- items 3 and 4: follow-on budget and unsized-lane checks -------------------


def test_followon_budget_is_the_lanes_it_emits_not_mission_minus_build():
    """Subtracting `build_cap` from `mission_budget` left the build lane's
    grace behind, and with `adversarial=True` the whole adversarial cap
    for a lane `shape_a_followon` never emits."""
    caps = shape.cap_arithmetic(2, 1)
    expected = round(
        caps.gemini_cap
        + caps.followon_grok_cap
        + caps.fix_cap
        + caps.followon_graced_lanes * caps.cap_grace_usd
        + shape.USD_MISSION_SLACK,
        2,
    )
    assert caps.followon_graced_lanes == 2
    assert caps.followon_budget == expected
    assert caps.followon_budget != round(caps.mission_budget - caps.build_cap, 2)
    # The leftover was exactly the build lane's grace.
    leftover = round(caps.mission_budget - caps.build_cap - caps.followon_budget, 2)
    assert leftover == caps.cap_grace_usd

    adversarial = shape.cap_arithmetic(2, 1, adversarial=True)
    assert adversarial.followon_budget == caps.followon_budget
    assert adversarial.followon_budget != round(
        adversarial.mission_budget - adversarial.build_cap, 2
    )
    extra = round(
        adversarial.mission_budget - adversarial.build_cap - adversarial.followon_budget, 2
    )
    assert extra == round(adversarial.adversarial_cap + 2 * adversarial.cap_grace_usd, 2)


def test_followon_budget_with_opus_adds_that_lane_and_its_grace_only():
    plain = shape.cap_arithmetic(2, 1)
    with_opus = shape.cap_arithmetic(2, 1, opus_review=True)
    assert with_opus.followon_graced_lanes == 3
    assert with_opus.followon_budget == round(
        plain.followon_budget + with_opus.opus_cap + with_opus.cap_grace_usd, 2
    )


def test_followon_refuses_adversarial_caps_even_when_the_caller_omits_the_flag(repo):
    """`shape_a_followon` passes only `opus_review=`; the helper must still
    see `caps.adversarial` and refuse, or item 3's dollars ship silently."""
    with pytest.raises(shape.ShapeInvalid, match="adversarial"):
        shape.shape_a_followon(
            worktree=repo,
            salvage_sha="abc123",
            diff="",
            test="true",
            caps=shape.cap_arithmetic(1, 1, adversarial=True),
            name="salvaged",
        )


def test_refuse_unsized_lanes_unknown_flag_is_shape_invalid_not_attribute_error():
    with pytest.raises(shape.ShapeInvalid, match="not a lane flag"):
        shape._refuse_unsized_lanes(shape.cap_arithmetic(1, 1), not_a_flag=True)


def test_refuse_unsized_lanes_checks_omitted_flags_against_the_class():
    """The helper learns the flag set from `CapArithmetic.LANE_FLAGS`, not
    from the caller's kwargs. Passing nothing is the forgotten-flag case."""
    with pytest.raises(shape.ShapeInvalid, match="adversarial"):
        shape._refuse_unsized_lanes(shape.cap_arithmetic(1, 1, adversarial=True))
    with pytest.raises(shape.ShapeInvalid, match="opus_review"):
        shape._refuse_unsized_lanes(shape.cap_arithmetic(1, 1, opus_review=True))
    shape._refuse_unsized_lanes(shape.cap_arithmetic(1, 1))


# --- item 5: follow-on Grok is the read-only cap, matching the prompt ---------


def test_followon_caps_grok_at_the_read_only_price_when_the_original_ran_the_suite(repo):
    """The natural caller reuses the original mission's caps, including
    `grok_runs_suite=True`. The follow-on still sends `GROK_READ_ONLY_PROMPT`,
    so the cap is forced to the read-only figure rather than refusing caps
    it cannot honour: `grok_cap` itself is what `shape_a` uses for both."""
    caps = shape.cap_arithmetic(1, 1, grok_runs_suite=True)
    assert caps.grok_cap == shape.USD_GROK_SUITE
    assert caps.followon_grok_cap == shape.USD_GROK_READ
    raw = shape.shape_a_followon(
        worktree=repo,
        salvage_sha="abc123",
        diff="",
        test="true",
        caps=caps,
        name="salvaged",
    )
    grok = next(lane for lane in raw["lanes"] if lane["name"] == "review-grok")
    assert grok["cap_usd"] == shape.USD_GROK_READ
    assert grok["cap_usd"] != caps.grok_cap
    assert "DO NOT RUN THE TEST SUITE" in grok["prompt"]
    assert raw["max_cost_usd"] == caps.followon_budget
    # The budget used $1.50, not the suite-running $2.00.
    assert caps.followon_budget == round(
        caps.gemini_cap
        + shape.USD_GROK_READ
        + caps.fix_cap
        + caps.followon_graced_lanes * caps.cap_grace_usd
        + shape.USD_MISSION_SLACK,
        2,
    )


# --- item 7: every dollar property rounds the same way ------------------------


def test_review_caps_rounds_like_every_other_dollar_property(monkeypatch):
    """Current constants are dyadic, so the missing `round` was invisible.
    A $0.10 term puts a long float into `max_cost_usd` and the printout.
    0.1 + 0.2 is the classic non-dyadic pair (`0.1 + 0.1 == 0.2`)."""
    monkeypatch.setattr(shape, "USD_GEMINI_READ", 0.1)
    monkeypatch.setattr(shape, "USD_GROK_READ", 0.2)
    caps = shape.cap_arithmetic(1, 1)
    raw_sum = 0.1 + 0.2
    assert raw_sum != 0.3
    assert caps.review_caps == 0.3
    assert caps.review_caps == round(caps.gemini_cap + caps.grok_cap, 2)
    for name in (
        "build_cap",
        "fix_cap",
        "gemini_cap",
        "grok_cap",
        "followon_grok_cap",
        "adversarial_cap",
        "opus_cap",
        "review_caps",
        "mission_budget",
        "followon_budget",
    ):
        value = getattr(caps, name)
        assert value == round(value, 2), name
        assert math.isfinite(value)
