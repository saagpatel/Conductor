"""Roadmap item A3, judge hygiene: vendor per model, a judge never scoring
its own vendor, a quorum capped at three with a dissent slot, and a ranking
collate that runs both lane orders and escalates a split decision instead of
picking one.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import FLEETS, Spec, model_vendor
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def shell(output: str) -> list[str]:
    return ["sh", "-c", f"printf '%s' {shlex.quote(output)}"]


def claude_envelope(text: str) -> str:
    return json.dumps({"result": text, "usage": {"input_tokens": 1, "output_tokens": 1}})


def codex_stream(text: str) -> str:
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
    done = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return f"{item}\n{done}\n"


def antigravity_envelope(text: str) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": text,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )


def verdict_lane(name: str, fleet: str = "claude", model: str | None = None) -> dict:
    lane: dict = {"name": name, "fleet": fleet, "prompt": name.upper(), "verdict": ["correct"]}
    if model is not None:
        lane["model"] = model
    return lane


def rank_mission_raw(
    repo, *, collate_fleet: str = "antigravity", extra: dict | None = None
) -> dict:
    return {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": {"fleet": collate_fleet, "rank": True, **(extra or {})},
    }


# --- model_vendor ------------------------------------------------------------


def test_model_vendor_covers_every_fleet_model_and_the_default():
    expected = {
        "claude": {"opus": "anthropic", "sonnet": "anthropic", "haiku": "anthropic"},
        "codex": {"terra": "openai", "sol": "openai", "luna": "openai"},
        "antigravity": {"gemini-3.8-flash": "google", "gemini-3.7-flash": "google"},
        "cursor": {"grok-4.6": "xai", "composer-2.5": "cursor"},
    }
    for fleet, models in expected.items():
        for model, vendor in models.items():
            assert model_vendor(fleet, model) == vendor
        default_vendor = models[FLEETS[fleet].default_model]
        assert model_vendor(fleet, None) == default_vendor


# --- a judge never scores its own vendor --------------------------------------


def test_review_lane_without_base_refuses_when_its_prompt_templates_a_same_vendor_lane(
    tmp_path,
):
    """A `stage: review` lane with no `base` still judges whatever it reads
    through `{{lanes.X.answer}}` (and the other template fields). Skipping
    `base is None` left that pair unchecked."""
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "claude", "model": "opus", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "model": "sonnet",
                "stage": "review",
                "mode": "read",
                "needs": ["build"],
                "prompt": "Look at {{lanes.build.answer}}",
            },
        ],
    }
    with pytest.raises(MissionInvalid, match=r"'review'.*'build'.*anthropic"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_review_lane_without_base_reaches_into_a_fallback_prompt_template(tmp_path):
    """Every attempt, cascade included, is a judging surface: a fallback
    prompt that templates a same-vendor lane is the same refusal."""
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "claude", "model": "opus", "mode": "write"},
            {
                "name": "review",
                "fleet": "codex",
                "model": "sol",
                "stage": "review",
                "mode": "read",
                "needs": ["build"],
                "prompt": "R",
                "fallback": [
                    {
                        "fleet": "claude",
                        "model": "sonnet",
                        "mode": "read",
                        "prompt": "see {{lanes.build.answer}}",
                    }
                ],
            },
        ],
    }
    with pytest.raises(MissionInvalid, match=r"'review'.*'build'.*anthropic"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_review_lane_without_base_self_judging_allow_lifts_the_template_refusal(
    tmp_path,
):
    raw = {
        "prompt": "x",
        "self_judging": "allow",
        "lanes": [
            {"name": "build", "fleet": "claude", "model": "opus", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "model": "sonnet",
                "stage": "review",
                "mode": "read",
                "needs": ["build"],
                "prompt": "Look at {{lanes.build.answer}}",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.self_judging == "allow"


def test_a_non_review_lane_that_templates_another_lane_is_not_a_judge(tmp_path):
    """A lane that is neither `stage: review` nor a verdict lane may still
    read another lane's output; that is not self-judging."""
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "write"},
            {
                "name": "b",
                "fleet": "claude",
                "needs": ["a"],
                "prompt": "read {{lanes.a.answer}}",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[1].stage is None
    assert all(attempt.verdict is None for attempt in mission.lanes[1].attempts)


def test_verdict_lane_refuses_to_judge_its_bases_vendor(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "claude", "model": "opus", "mode": "write"},
            {
                "name": "review",
                "fleet": "claude",
                "model": "sonnet",
                "base": "build",
                "prompt": "R",
                "verdict": ["correct"],
            },
        ],
    }
    with pytest.raises(MissionInvalid, match=r"'review'.*'build'.*anthropic"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_verdict_lane_refusal_reaches_into_a_fallback(tmp_path):
    """The judged lane's primary attempt (claude) does not share a vendor
    with the judge (codex); its fallback (also codex) does. 'Could equal'
    means any attempt, fallbacks included."""
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "name": "build",
                "fleet": "claude",
                "model": "opus",
                "mode": "write",
                "fallback": [{"fleet": "codex", "model": "sol"}],
            },
            {
                "name": "review",
                "fleet": "codex",
                "model": "sol",
                "base": "build",
                "prompt": "R",
                "verdict": ["correct"],
            },
        ],
    }
    with pytest.raises(MissionInvalid, match=r"'review'.*'build'.*openai"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_collate_refuses_to_share_a_vendor_with_a_lane_it_collates_over(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [{"name": "a", "fleet": "claude"}],
        "collate": {"fleet": "claude"},
    }
    with pytest.raises(MissionInvalid, match=r"'collate'.*'a'.*anthropic"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_self_judging_allow_lifts_the_refusal_and_notes_it(repo, home, fake_fleet, tmp_path):
    fake_fleet(session_id=None)
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"name": "a", "fleet": "claude"}],
        "collate": {"fleet": "claude"},
        "self_judging": "allow",
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    note = "self-judging allowed by the mission: collate judges a on anthropic"
    assert result.self_judging == [note]
    report = Path(result.report_path).read_text()
    assert note in report


def test_self_judging_refuses_any_value_other_than_allow(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [{"name": "a", "fleet": "claude"}, {"name": "b", "fleet": "codex"}],
        "self_judging": "sometimes",
    }
    with pytest.raises(MissionInvalid, match="self_judging must be"):
        mission_from_dict(raw, base_dir=tmp_path)


# --- quorum: capped at three, a dissent slot, two vendors ---------------------


def test_quorum_of_refuses_more_than_three_lanes(tmp_path):
    lanes = [verdict_lane(n) for n in ("a", "b", "c", "d")]
    raw = {"prompt": "x", "lanes": lanes, "require": {"pass": 2, "of": ["a", "b", "c", "d"]}}
    with pytest.raises(MissionInvalid, match="at most three lanes"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_quorum_of_three_needs_a_dissent_slot(tmp_path):
    lanes = [verdict_lane("a"), verdict_lane("b", fleet="codex"), verdict_lane("c")]
    raw = {"prompt": "x", "lanes": lanes, "require": {"pass": 3, "of": ["a", "b", "c"]}}
    with pytest.raises(MissionInvalid, match="dissent slot"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_quorum_refuses_lanes_confined_to_one_vendor(tmp_path):
    lanes = [verdict_lane(n) for n in ("a", "b", "c")]
    raw = {"prompt": "x", "lanes": lanes, "require": {"pass": 2, "of": ["a", "b", "c"]}}
    with pytest.raises(MissionInvalid, match="span at least two vendors"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_two_vendor_three_lane_quorum_with_pass_two_loads(tmp_path):
    lanes = [verdict_lane("a"), verdict_lane("b", fleet="codex"), verdict_lane("c")]
    raw = {"prompt": "x", "lanes": lanes, "require": {"pass": 2, "of": ["a", "b", "c"]}}
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.require == {"pass": 2, "of": ["a", "b", "c"]}


# --- ranking collate -----------------------------------------------------------


def test_ranking_collate_agreement_yields_a_strongest_lane_and_two_priced_orders(
    repo, home, monkeypatch, tmp_path
):
    calls = {"antigravity": 0}
    answers = [{"strongest": "a", "reason": "cleaner"}, {"strongest": "a", "reason": "cleaner"}]

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        calls["antigravity"] += 1
        return shell(antigravity_envelope(json.dumps(answers[calls["antigravity"] - 1])))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    mission = mission_from_dict(rank_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True
    assert result.collate["ok"] is True
    assert result.collate["strongest"] == "a"
    orders = result.collate["orders"]
    assert len(orders) == 2
    assert all(order["strongest"] == "a" and order["invalid"] is None for order in orders)
    assert len({order["run_id"] for order in orders}) == 2
    assert result.collate["cost_usd"] is not None

    saved = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved["collate"]["strongest"] == "a"
    assert saved["collate"]["orders"] == orders


def test_ranking_collate_disagreement_escalates_instead_of_choosing(
    repo, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        # E4 dispatches both lane orders in parallel, so a shared call
        # counter can no longer tell forward from reverse deterministically;
        # the rendered prompt itself can.
        forward = "Candidates, in the order shown: a, b." in spec.prompt
        answer = {"strongest": "a", "reason": "x"} if forward else {"strongest": "b", "reason": "y"}
        return shell(antigravity_envelope(json.dumps(answer)))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    mission = mission_from_dict(rank_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is False
    assert result.collate["ok"] is False
    assert result.collate["strongest"] is None
    assert result.collate["error"] == "judges disagreed: a=1, b=1"


def test_ranking_collate_an_invalid_order_is_reported_and_not_ok(
    repo, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        # E4 dispatches both lane orders in parallel, so a shared call
        # counter can no longer tell forward from reverse deterministically;
        # the rendered prompt itself can.
        forward = "Candidates, in the order shown: a, b." in spec.prompt
        answer = (
            {"strongest": "a", "reason": "x"}
            if forward
            else {"strongest": "nonexistent-lane", "reason": "y"}
        )
        return shell(antigravity_envelope(json.dumps(answer)))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    mission = mission_from_dict(rank_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is False
    assert result.collate["ok"] is False
    assert result.collate["strongest"] is None
    assert (
        result.collate["error"]
        == "judge 1 order 2 invalid: unknown lane name 'nonexistent-lane'"
    )


def test_ranking_collate_cursor_loads(repo, tmp_path):
    """Grok (fleet cursor) cannot take --schema, but the rank contract
    embeds the schema as text and parse extracts JSON, so a cursor rank
    collate loads. The DispatchRefused in fleets.py is unchanged: passing
    a schema path to Spec.validate still refuses."""
    from conductor.fleets import DispatchRefused
    from conductor.mission import _rank_schema_for

    mission = mission_from_dict(rank_mission_raw(repo, collate_fleet="cursor"), base_dir=tmp_path)
    assert mission.collate.fleet == "cursor"
    assert _rank_schema_for("cursor", "/tmp/rank.schema.json") is None
    schema = tmp_path / "rank.schema.json"
    schema.write_text("{}")
    with pytest.raises(DispatchRefused, match="no structured-output flag"):
        Spec(
            fleet="cursor",
            prompt="x",
            cwd=str(repo),
            mode="read",
            schema=str(schema),
        ).validate()


def test_ranking_collate_refuses_a_single_lane_mission(repo, tmp_path):
    raw = {
        "cwd": str(repo),
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
        "collate": {"fleet": "antigravity", "rank": True},
    }
    with pytest.raises(MissionInvalid, match="at least two lanes"):
        mission_from_dict(raw, base_dir=tmp_path)


# --- snapshot round trip -------------------------------------------------------


def test_snapshot_round_trip_preserves_self_judging_and_rank(repo, home, tmp_path):
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": {"fleet": "antigravity", "rank": True},
        "self_judging": "allow",
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    assert reloaded.self_judging == "allow"
    assert reloaded.collate.rank is True
    assert reloaded.to_dict() == mission.to_dict() == snapshot


# --- documentation -------------------------------------------------------------


def test_readme_documents_judge_hygiene():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split(
        "#### Judge hygiene: vendor span, self-judging, and ranking", 1
    )[1].split("\n## ", 1)[0]
    assert "at most three lanes" in section
    assert "dissent slot" in section
    assert "span at least two vendors" in section
    assert '"self_judging": "allow"' in section
    assert "self-judging allowed by the mission" in section
    assert '"rank": true' in section
    assert "judges disagreed: <lane>=<n>, <lane>=<n>" in section
    assert "judge <j> order <k> invalid" in section
    assert "collate.judges[<i>]" in section
    assert "collate.tally" in section


def test_rank_answer_accepts_an_object_wrapped_in_prose():
    from conductor.mission import _parse_rank_answer

    text = 'Both lanes are close. {"strongest": "a", "reason": "a has the test"} is my call.'
    assert _parse_rank_answer(text, ["a", "b"], True, None) == ("a", "a has the test", None, None)
    assert _parse_rank_answer("no object here", ["a", "b"], True, None)[2] == (
        "answer contains no valid JSON object"
    )


def test_parse_rank_answer_refuses_two_different_ranking_objects():
    from conductor.mission import _parse_rank_answer

    text = (
        '{"strongest": "a", "reason": "a has the test"}\n\n'
        "For example:\n"
        '{"strongest": "b", "reason": "b is cheaper"}\n'
    )
    strongest, reason, invalid, scores = _parse_rank_answer(text, ["a", "b"], True, None)
    assert strongest is None and reason is None and scores is None
    assert invalid == (
        "the answer carries 2 different ranking objects; "
        "conductor cannot tell which is the judgment"
    )
