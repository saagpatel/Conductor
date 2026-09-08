"""Roadmap item E4, judge sittings: M judges instead of one on a rank
collate, each dispatched in both lane orders in parallel, unanimity across
every judge and every order naming the winner, and a tally (receipt,
`tally.json`, `tally.md`) recording every vote.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import (
    MissionInvalid,
    _collate_is_trusted,
    _parse_rank_answer,
    mission_from_dict,
    run_mission,
)
from conductor.spend import _collate_run_ids as spend_collate_run_ids
from docs import doc_section


def shell(output: str) -> list[str]:
    return ["sh", "-c", f"printf '%s' {shlex.quote(output)}"]


def claude_envelope(text: str) -> str:
    return json.dumps({"result": text, "usage": {"input_tokens": 1, "output_tokens": 1}})


def codex_stream(text: str) -> str:
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
    done = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return f"{item}\n{done}\n"


def antigravity_envelope(answer: dict) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": json.dumps(answer),
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )


def is_forward(spec: Spec, names: list[str]) -> bool:
    """Which lane order this dispatch's rendered prompt actually carries --
    read from the prompt itself rather than call order, since a sitting's
    2M dispatches run concurrently and give no other reliable signal."""
    return f"Candidates, in the order shown: {', '.join(names)}." in spec.prompt


def sitting_mission_raw(
    repo, *, judges: list[dict] | None = None, extra: dict | None = None
) -> dict:
    collate: dict = {"fleet": "antigravity", "rank": True}
    if judges is not None:
        collate["judges"] = judges
    collate.update(extra or {})
    return {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": collate,
    }


# --- load-time refusals and shape ------------------------------------------


def test_collate_judges_need_rank_at_load(repo, tmp_path):
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": {"fleet": "antigravity", "judges": [{"fleet": "antigravity"}]},
    }
    with pytest.raises(MissionInvalid, match="collate judges need rank"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_collate_judges_unknown_key_names_the_index(repo, tmp_path):
    raw = sitting_mission_raw(
        repo, judges=[{"fleet": "antigravity"}, {"fleet": "antigravity", "bogus": 1}]
    )
    with pytest.raises(MissionInvalid, match=r"collate judges\[1\].*bogus"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_two_judge_collate_loads(repo, tmp_path):
    raw = sitting_mission_raw(
        repo, judges=[{"fleet": "antigravity", "model": "gemini-3.7-flash", "cap_usd": 3.0}]
    )
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert len(mission.collate.judges) == 1
    judge = mission.collate.judges[0]
    assert judge.fleet == "antigravity"
    assert judge.model == "gemini-3.7-flash"
    assert judge.cap_usd == 3.0


def test_judge_cap_usd_defaults_to_the_collates_own(repo, tmp_path):
    raw = sitting_mission_raw(
        repo,
        judges=[{"fleet": "antigravity", "model": "gemini-3.7-flash"}],
        extra={"cap_usd": 5.0},
    )
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.collate.judges[0].cap_usd == 5.0


def test_hygiene_flags_a_second_judge_on_a_lanes_vendor(repo, tmp_path):
    raw = sitting_mission_raw(repo, judges=[{"fleet": "claude", "model": "opus"}])
    with pytest.raises(
        MissionInvalid, match=r"'collate\.judges\[0\]' judges 'a', both on vendor 'anthropic'"
    ):
        mission_from_dict(raw, base_dir=tmp_path)


# --- the fan-out and the verdict --------------------------------------------


def test_two_judge_unanimous_sitting_names_strongest_and_sums_cost(
    repo, home, monkeypatch, tmp_path
):
    names = ["a", "b"]

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        answer = {"strongest": "a", "reason": "cleaner" if is_forward(spec, names) else "still a"}
        return shell(antigravity_envelope(answer))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = sitting_mission_raw(
        repo, judges=[{"fleet": "antigravity", "model": "gemini-3.7-flash"}]
    )
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is True
    collate = result.collate
    assert collate["ok"] is True
    assert collate["strongest"] == "a"
    assert len(collate["judges"]) == 1
    assert collate["judges"][0]["fleet"] == "antigravity"
    assert collate["judges"][0]["model"].startswith("gemini-3.7-flash")

    run_ids = {order["run_id"] for order in collate["orders"]}
    run_ids |= {order["run_id"] for order in collate["judges"][0]["orders"]}
    assert len(run_ids) == 4

    assert collate["cost_usd"] is not None
    assert collate["judges"][0]["cost_usd"] is not None
    assert collate["cost_usd"] >= collate["judges"][0]["cost_usd"]

    tally = collate["tally"]
    assert tally["agreement"] == "unanimous"
    assert tally["votes"] == {"a": 4, "b": 0}
    assert len(tally["judges"]) == 2

    tally_json = json.loads((Path(result.mission_dir) / "tally.json").read_text())
    assert tally_json == tally
    tally_md = (Path(result.mission_dir) / "tally.md").read_text()
    assert "Agreement: unanimous" in tally_md
    assert "| judge | fleet | model | forward | reverse | agrees | a | b |" in tally_md

    report = Path(result.report_path).read_text()
    assert "Agreement: unanimous" in report


def test_split_sitting_escalates_with_vote_count_error(repo, home, monkeypatch, tmp_path):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        if spec.model == "gemini-3.7-flash":
            answer = {"strongest": "b", "reason": "b"}
        else:
            answer = {"strongest": "a", "reason": "a"}
        return shell(antigravity_envelope(answer))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = sitting_mission_raw(
        repo, judges=[{"fleet": "antigravity", "model": "gemini-3.7-flash"}]
    )
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is False
    assert result.collate["ok"] is False
    assert result.collate["strongest"] is None
    assert result.collate["error"] == "judges disagreed: a=2, b=2"
    assert result.collate["tally"]["agreement"] == "split"


def test_unanimous_none_sitting_is_agreement_none_not_invalid(repo, home, monkeypatch, tmp_path):
    """Every judge in every order saying no candidate is usable is a
    completed sitting, not a broken one."""

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        answer = {
            "strongest": "none",
            "reason": "src/x.py:1 neither candidate does what the spec asked",
        }
        return shell(antigravity_envelope(answer))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    mission = mission_from_dict(sitting_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.collate["ok"] is True
    assert result.collate["strongest"] is None
    assert result.collate["error"] is None
    tally = result.collate["tally"]
    assert tally["agreement"] == "none"
    assert tally["votes"] == {"a": 0, "b": 0}
    assert tally["none_votes"] == 2


def test_invalid_second_judge_order_escalates_naming_judge_2(repo, home, monkeypatch, tmp_path):
    names = ["a", "b"]

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        if spec.model == "gemini-3.7-flash" and not is_forward(spec, names):
            answer = {"strongest": "nonexistent-lane", "reason": "y"}
        else:
            answer = {"strongest": "a", "reason": "x"}
        return shell(antigravity_envelope(answer))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = sitting_mission_raw(
        repo, judges=[{"fleet": "antigravity", "model": "gemini-3.7-flash"}]
    )
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is False
    assert result.collate["ok"] is False
    assert (
        result.collate["error"]
        == "judge 2 order 2 invalid: unknown lane name 'nonexistent-lane'"
    )
    assert result.collate["tally"]["agreement"] == "invalid"


# --- scores ------------------------------------------------------------------


def test_parse_rank_answer_accepts_valid_scores():
    text = json.dumps({"strongest": "a", "reason": "x", "scores": {"a": 8, "b": 3}})
    strongest, reason, invalid, scores = _parse_rank_answer(text, ["a", "b"], True, None)
    assert (strongest, reason, invalid) == ("a", "x", None)
    assert scores == {"a": 8, "b": 3}


def test_parse_rank_answer_refuses_an_out_of_range_score():
    text = json.dumps({"strongest": "a", "reason": "x", "scores": {"a": 11}})
    strongest, reason, invalid, scores = _parse_rank_answer(text, ["a", "b"], True, None)
    assert strongest is None
    assert scores is None
    assert invalid == "score for 'a' must be an integer 1 to 10"


def test_scores_are_recorded_and_mean_scores_computed(repo, home, monkeypatch, tmp_path):
    names = ["a", "b"]

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "claude":
            return shell(claude_envelope("a answer"))
        if spec.fleet == "codex":
            return shell(codex_stream("b answer"))
        forward = is_forward(spec, names)
        answer = {
            "strongest": "a",
            "reason": "x",
            "scores": {"a": 8, "b": 4} if forward else {"a": 6, "b": 2},
        }
        return shell(antigravity_envelope(answer))

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = sitting_mission_raw(repo)
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.collate["ok"] is True
    orders = result.collate["orders"]
    assert orders[0]["scores"] == {"a": 8, "b": 4}
    assert orders[1]["scores"] == {"a": 6, "b": 2}
    mean_scores = result.collate["tally"]["mean_scores"]
    assert mean_scores == {
        "a": {"mean": 7.0, "n": 2},
        "b": {"mean": 3.0, "n": 2},
    }


# --- resume trust and spend accounting --------------------------------------


def _run_order(run_id: str) -> dict:
    return {"run_id": run_id, "strongest": "a", "reason": "x", "invalid": None, "scores": None}


def _two_run_orders(prefix: str) -> list[dict]:
    return [_run_order(f"{prefix}-fwd"), _run_order(f"{prefix}-rev")]


def test_collate_is_trusted_refuses_a_two_judge_receipt_missing_the_second_judges_run_ids(
    tmp_path,
):
    incomplete = {
        "collate": {
            "ok": True,
            "rank": True,
            "strongest": "a",
            "orders": _two_run_orders("j1"),
            "judges": [{"orders": [{"run_id": "j2-fwd"}, {"run_id": None}]}],
        }
    }
    assert _collate_is_trusted(tmp_path, incomplete) is False


def test_collate_is_trusted_accepts_a_complete_two_judge_receipt(tmp_path):
    complete = {
        "collate": {
            "ok": True,
            "rank": True,
            "strongest": "a",
            "orders": _two_run_orders("j1"),
            "judges": [{"orders": _two_run_orders("j2")}],
        }
    }
    assert _collate_is_trusted(tmp_path, complete) is True


def test_spend_collate_run_ids_returns_every_judges_run_ids():
    collate = {
        "orders": [{"run_id": "j1-fwd"}, {"run_id": "j1-rev"}],
        "judges": [
            {"orders": [{"run_id": "j2-fwd"}, {"run_id": "j2-rev"}]},
            {"orders": [{"run_id": "j3-fwd"}, {"run_id": "j3-rev"}]},
        ],
    }
    assert spend_collate_run_ids(collate) == {
        "j1-fwd",
        "j1-rev",
        "j2-fwd",
        "j2-rev",
        "j3-fwd",
        "j3-rev",
    }


# --- documentation -----------------------------------------------------------


def test_readme_documents_judge_sittings():
    section = doc_section("##### Judge sittings")
    assert '"judges"' in section
    assert "collate.tally" in section
    assert "tally.json" in section
    assert "tally.md" in section
    assert "scores" in section
