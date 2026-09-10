"""Rank/collate contract hygiene: the seven sitting-path defects.

Each test fails on the pre-fix behaviour. Prompt fingerprints for
DEFAULT_COLLATE_INSTRUCTIONS and _rank_contract move with items 1 and 7.
"""

from __future__ import annotations

import json

import pytest

from conductor.fleets import DispatchRefused, Spec, supports_schema_flag
from conductor.mission import (
    DEFAULT_COLLATE_INSTRUCTIONS,
    DEFAULT_RESOLVE_INSTRUCTIONS,
    LaneResult,
    Ledger,
    _build_rank_tally,
    _collate_body,
    _collate_is_trusted,
    _parse_rank_answer,
    _rank_contract,
    _rank_schema,
    _rank_schema_for,
    _rendered_verdict,
    _resolve_is_trusted,
    _resolve_prompt,
    _run_collate,
    _run_rank_collate,
    mission_from_dict,
)


def _none_record() -> dict:
    return {
        "strongest": "none",
        "reason": "src/x.py:1 neither meets the bar",
        "invalid": None,
        "scores": None,
    }


def _named_record(name: str, scores: dict[str, int] | None = None) -> dict:
    return {
        "strongest": name,
        "reason": f"src/{name}.py:1",
        "invalid": None,
        "scores": scores,
    }


def _rank_mission(repo, tmp_path, *, extra: dict | None = None):
    collate = {"fleet": "antigravity", "rank": True, **(extra or {})}
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": collate,
    }
    return mission_from_dict(raw, base_dir=tmp_path)


def _boom():
    calls: list[object] = []

    def dispatcher(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("rank collate dispatched")

    return calls, dispatcher


# --- item 1: ranking contract and "none" as a valid outcome -----------------


def test_rank_contract_invites_none_and_meets_the_prompt_rules():
    text = _rank_contract(["a", "b"])
    assert "either answer is complete" in text.lower()
    assert 'set strongest to "none"' in text
    assert "Which lane's result is strongest" not in text
    assert "correctness" in text
    assert "spec asked" in text
    assert "not on style" in text
    assert "cite a file and line" in text.lower() or "cite a file and line" in text
    assert "Write that object once and end there" in text
    assert "Put the entire ranking in this reply" in text
    for banned in ("at least", "find the", "hunt", "defect:"):
        assert banned not in text.lower()
    schema = _rank_schema(["a", "b"])
    assert schema["properties"]["strongest"]["enum"] == ["a", "b", "none"]


def test_parse_rank_answer_accepts_none_as_a_valid_outcome():
    text = json.dumps(
        {"strongest": "none", "reason": "src/x.py:1 neither candidate is usable"}
    )
    strongest, reason, invalid, scores = _parse_rank_answer(text, ["a", "b"], True, None)
    assert strongest == "none"
    assert invalid is None
    assert scores is None
    assert "src/x.py:1" in reason


def test_parse_rank_answer_still_refuses_an_unknown_lane_name():
    text = json.dumps({"strongest": "nonexistent-lane", "reason": "y"})
    strongest, reason, invalid, scores = _parse_rank_answer(text, ["a", "b"], True, None)
    assert strongest is None and scores is None
    assert invalid == "unknown lane name 'nonexistent-lane'"


def test_build_rank_tally_unanimous_none_is_agreement_none_not_a_vote():
    none = _none_record()
    tally = _build_rank_tally(["a", "b"], [(none, none)], [(None, None)])
    assert tally["agreement"] == "none"
    assert tally["votes"] == {"a": 0, "b": 0}
    assert tally["none_votes"] == 2
    assert tally["judges"][0]["agrees"] is True


def test_build_rank_tally_mixed_none_and_a_named_lane_is_split():
    tally = _build_rank_tally(
        ["a", "b"],
        [(_named_record("a"), _none_record())],
        [(None, None)],
    )
    assert tally["agreement"] == "split"
    assert tally["votes"] == {"a": 1, "b": 0}
    assert tally["none_votes"] == 1


# --- item 2: drop the schema flag for fleets that cannot take it ------------


def test_supports_schema_flag_matches_the_fleets_py_refusals():
    assert supports_schema_flag("cursor", "read") is False
    assert supports_schema_flag("script", "write") is False
    assert supports_schema_flag("antigravity", "read") is False
    assert supports_schema_flag("antigravity", "write") is True
    assert supports_schema_flag("claude", "read") is True
    assert supports_schema_flag("codex", "read") is True


def test_rank_schema_for_drops_the_flag_when_the_fleet_cannot_take_it():
    path = "/tmp/rank.schema.json"
    assert _rank_schema_for("cursor", path) is None
    assert _rank_schema_for("script", path) is None
    assert _rank_schema_for("antigravity", path) is None
    assert _rank_schema_for("claude", path) == path
    assert _rank_schema_for("codex", path) == path


def test_cursor_schema_on_spec_validate_is_still_refused(repo, tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text("{}")
    with pytest.raises(DispatchRefused, match="no structured-output flag"):
        Spec(
            fleet="cursor",
            prompt="x",
            cwd=str(repo),
            mode="read",
            schema=str(schema),
        ).validate()


def test_cursor_rank_collate_and_extra_judge_load(repo, tmp_path):
    mission = _rank_mission(repo, tmp_path, extra={"judges": [{"fleet": "cursor"}]})
    assert mission.collate.fleet == "antigravity"
    assert mission.collate.judges[0].fleet == "cursor"
    cursor = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A"},
                {"name": "b", "fleet": "codex", "prompt": "B"},
            ],
            "collate": {"fleet": "cursor", "rank": True},
        },
        base_dir=tmp_path,
    )
    assert cursor.collate.fleet == "cursor"


# --- item 3: a mean says how many values it is over -------------------------


def test_mean_scores_carry_n_on_the_judge_block_and_the_overall_block():
    forward = _named_record("a", {"a": 8, "b": 4})
    reverse = _named_record("a", {"a": 6})
    tally = _build_rank_tally(["a", "b"], [(forward, reverse)], [("antigravity", "g")])
    assert tally["mean_scores"]["a"] == {"mean": 7.0, "n": 2}
    assert tally["mean_scores"]["b"] == {"mean": 4.0, "n": 1}
    assert tally["judges"][0]["scores"]["a"] == {"mean": 7.0, "n": 2}
    assert tally["judges"][0]["scores"]["b"] == {"mean": 4.0, "n": 1}
    assert tally["mean_scores"]["b"]["n"] == tally["judges"][0]["scores"]["b"]["n"]


def test_missing_scores_stay_none_not_zero():
    forward = _named_record("a", {"a": 8})
    reverse = _named_record("a")
    tally = _build_rank_tally(["a", "b"], [(forward, reverse)], [(None, None)])
    assert tally["mean_scores"]["a"] == {"mean": 8.0, "n": 1}
    assert tally["mean_scores"]["b"] is None
    assert tally["judges"][0]["scores"]["b"] is None


# --- item 4: fewer than two candidates does not dispatch --------------------


def test_rank_collate_zero_candidates_does_not_dispatch(repo, tmp_path):
    mission = _rank_mission(repo, tmp_path)
    calls, dispatcher = _boom()
    out = _run_rank_collate(
        mission,
        [],
        mission.collate,
        Ledger(max_cost_usd=10.0),
        tmp_path,
        tmp_path,
        dispatcher=dispatcher,
    )
    assert calls == []
    assert out["ok"] is False
    assert out["strongest"] is None
    assert out["error"] == "rank collate has no candidates to compare"
    assert out["orders"] == []
    assert out["cost_usd"] is None
    assert not (tmp_path / "collate-rank.schema.json").exists()


def test_rank_collate_one_candidate_names_it_without_dispatch(repo, tmp_path):
    mission = _rank_mission(repo, tmp_path)
    calls, dispatcher = _boom()
    out = _run_rank_collate(
        mission,
        [LaneResult(name="a", ok=True)],
        mission.collate,
        Ledger(max_cost_usd=10.0),
        tmp_path,
        tmp_path,
        dispatcher=dispatcher,
    )
    assert calls == []
    assert out["ok"] is True
    assert out["strongest"] == "a"
    assert out["error"] is None
    assert out["orders"] == []
    assert out["cost_usd"] is None
    assert not (tmp_path / "collate-rank.schema.json").exists()


def test_run_collate_rank_with_one_ranked_sink_does_not_dispatch(repo, tmp_path):
    mission = _rank_mission(repo, tmp_path, extra={"candidates": 2})
    calls, dispatcher = _boom()
    out = _run_collate(
        mission,
        [LaneResult(name="a", ok=True), LaneResult(name="b", ok=True)],
        Ledger(max_cost_usd=10.0),
        tmp_path,
        tmp_path,
        ranking=[{"lane": "a"}],
        dispatcher=dispatcher,
    )
    assert calls == []
    assert out["ok"] is True
    assert out["strongest"] == "a"
    assert out["candidates"] == ["a"]


# --- item 5: malformed verdict renders, it does not crash -------------------


def test_rendered_verdict_distinguishes_absent_from_malformed():
    assert _rendered_verdict(None) == "(no verdict)"
    assert _rendered_verdict({}) == "(malformed verdict)"
    assert _rendered_verdict({"passed": True}) == "(malformed verdict)"
    assert _rendered_verdict("not a dict") == "(malformed verdict)"  # type: ignore[arg-type]


def test_rendered_verdict_drops_extra_keys_and_still_renders():
    good = {
        "passed": True,
        "reported": "pass",
        "criteria": [],
        "failed": [],
        "summary": "ok",
    }
    rendered = _rendered_verdict(good)
    assert rendered not in ("(no verdict)", "(malformed verdict)")
    assert _rendered_verdict({**good, "extra": 1}) == rendered


# --- item 6: untrusted judges value is not a crash --------------------------


def _two_run_orders(prefix: str) -> list[dict]:
    return [
        {"run_id": f"{prefix}-fwd", "strongest": "a", "reason": "x", "invalid": None},
        {"run_id": f"{prefix}-rev", "strongest": "a", "reason": "x", "invalid": None},
    ]


def test_collate_is_trusted_refuses_a_non_list_judges_value(tmp_path):
    base = {
        "ok": True,
        "rank": True,
        "strongest": "a",
        "orders": _two_run_orders("j1"),
    }
    assert _collate_is_trusted(tmp_path, {"collate": {**base, "judges": True}}) is False
    assert _collate_is_trusted(tmp_path, {"collate": {**base, "judges": 3}}) is False
    assert _collate_is_trusted(tmp_path, {"collate": {**base, "judges": "ab"}}) is False


def test_collate_is_trusted_accepts_empty_judges_list(tmp_path):
    complete = {
        "collate": {
            "ok": True,
            "rank": True,
            "strongest": "a",
            "orders": _two_run_orders("j1"),
            "judges": [],
        }
    }
    assert _collate_is_trusted(tmp_path, complete) is True


def test_collate_is_trusted_accepts_unanimous_none(tmp_path):
    complete = {
        "collate": {
            "ok": True,
            "rank": True,
            "strongest": None,
            "orders": _two_run_orders("j1"),
            "judges": [],
            "tally": {"agreement": "none", "votes": {"a": 0, "b": 0}},
        }
    }
    assert _collate_is_trusted(tmp_path, complete) is True


def test_resolve_is_trusted_has_no_ungarded_judges_iteration(repo, tmp_path):
    """_resolve_is_trusted already isinstance-guards its dict and does not
    iterate a judges value. A truthy non-list on an unrelated key is not a
    crash."""
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A", "mode": "write"},
                {"name": "b", "fleet": "codex", "prompt": "B", "mode": "write"},
            ],
            "resolve": {"fleet": "cursor"},
        },
        base_dir=tmp_path,
    )
    prior = {"resolve": {"ran": False, "reason": "no hotspots", "judges": True}}
    assert _resolve_is_trusted(mission, prior) is True


# --- item 7: prose collate presents both orders in one dispatch -------------


def test_default_collate_instructions_require_both_orders_and_drop_the_false_mitigation():
    text = DEFAULT_COLLATE_INSTRUCTIONS
    assert "as listed" in text.lower()
    assert "reverse order" in text.lower()
    assert "inconclusive" in text.lower()
    assert "carries no meaning" not in text.lower()
    assert "either answer is complete" in text.lower()
    assert "put the entire comparison in this reply" in text.lower()
    for banned in ("at least", "find the", "hunt"):
        assert banned not in text.lower()


def test_collate_body_lists_each_lane_once_instructions_carry_the_second_order(repo, tmp_path):
    """Instruction text is enough: the body already names every candidate.
    Duplicating the patches would approach the token cost of a second
    dispatch, which is not this change's to make."""
    mission = _rank_mission(repo, tmp_path)
    lanes = [
        LaneResult(name="a", ok=True, attempts=[{"attempt": "primary"}]),
        LaneResult(name="b", ok=True, attempts=[{"attempt": "primary"}]),
    ]
    body = _collate_body(mission, lanes, mission.collate)
    assert body.count("### Lane `a`") == 1
    assert body.count("### Lane `b`") == 1
    assert "reverse order" not in body.lower()


# --- resolve default: pin what conductor sends ------------------------------


def _resolve_composed(repo, tmp_path, *, strongest: str | None) -> str:
    """The text `_resolve_prompt` actually concatenates, not the constant
    alone. Default instructions are what a resolve block with no custom
    text sends."""
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "implement the spec",
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A", "mode": "write"},
                {"name": "b", "fleet": "codex", "prompt": "B", "mode": "write"},
            ],
            "resolve": {"fleet": "cursor"},
        },
        base_dir=tmp_path,
    )
    patch_a = tmp_path / "a.diff"
    patch_b = tmp_path / "b.diff"
    patch_a.write_text("--- a\n+++ a\n")
    patch_b.write_text("--- b\n+++ b\n")
    lanes = [
        LaneResult(name="a", ok=True, diff_path=str(patch_a)),
        LaneResult(name="b", ok=True, diff_path=str(patch_b)),
    ]
    collisions = {
        "hotspots": ["hot.py"],
        "overlap": {"files": {"hot.py": ["a", "b"]}},
    }
    assert mission.resolve.instructions == DEFAULT_RESOLVE_INSTRUCTIONS
    return _resolve_prompt(mission, lanes, collisions, mission.resolve, strongest)


def test_resolve_prompt_names_the_strongest_lane_when_a_rank_collate_named_one(
    repo, tmp_path
):
    prompt = _resolve_composed(repo, tmp_path, strongest="a")
    assert "The collate judged lane `a` the strongest candidate." in prompt
    assert "what was kept from which lane" in prompt
    assert DEFAULT_RESOLVE_INSTRUCTIONS.strip() in prompt


def test_resolve_prompt_states_the_mission_order_fallback_when_none_is_named(
    repo, tmp_path
):
    prompt = _resolve_composed(repo, tmp_path, strongest=None)
    assert "The collate judged lane" not in prompt
    assert "first candidate lane in mission order" in prompt
    assert "what was kept from which lane" in prompt
    assert DEFAULT_RESOLVE_INSTRUCTIONS.strip() in prompt
