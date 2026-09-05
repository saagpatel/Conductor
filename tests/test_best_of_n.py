"""Roadmap item B5: per-dispatch cancel, early cancel under require: any,
mechanical ranking of sink lanes, and judging only the top candidates.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import (
    LaneResult,
    Mission,
    MissionInvalid,
    mission_from_dict,
    rank_lanes,
    run_mission,
)
from conductor.runner import dispatch


def _line(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"))


def _claude_assistant_usage() -> str:
    return _line(
        {
            "type": "assistant",
            "session_id": "claude-session",
            "message": {
                "id": "message-1",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "cache_creation_input_tokens": 10_000,
                },
                "content": [],
            },
        }
    )


def _claude_envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def _antigravity_strongest(strongest: str) -> str:
    payload = {
        "event": "result",
        "result": {
            "status": "SUCCESS",
            "response": json.dumps({"strongest": strongest, "reason": "cheapest"}),
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
    }
    return json.dumps(payload)


def _snapshot(result) -> Mission:
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    return Mission.from_snapshot(raw)


# --- item 1: per-dispatch cancel --------------------------------------------


def test_cancel_kills_the_fleet_priced_not_gated_not_committed(
    repo, home, fake_fleet, monkeypatch
):
    fake_fleet(["sh", "-c", f"echo '{_claude_assistant_usage()}'; sleep 60"])
    monkeypatch.setattr(runner_mod, "POLL_S", 0.2)
    marker = repo / "gate-ran"
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()

    result = dispatch(
        Spec(fleet="claude", model="sonnet", prompt="x", cwd=str(repo), timeout=30),
        home=home,
        isolate=True,
        commit_message="feat: x",
        test_command=f"touch {marker}",
        cancel=cancel,
        cancel_reason="lane b already passed",
    )

    assert result.error == "cancelled: lane b already passed"
    assert result.cancelled is True
    assert result.ok is False
    assert result.tests is None and not marker.exists()
    assert result.commit is None
    assert result.usage is not None and result.usage["cost_usd"] > 0
    assert result.isolation["kept"] is False
    assert not Path(result.isolation["worktree"]).exists()
    assert result.summary()["cancelled"] is True
    saved = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert saved["cancelled"] is True and saved["error"] == result.error


def test_a_cancel_set_after_a_clean_exit_changes_nothing(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", f"printf '%s\\n' {json.dumps(_claude_envelope('ok'))}"])
    cancel = threading.Event()
    cancel.set()  # already set before the fast fleet even spawns

    result = dispatch(
        Spec(fleet="claude", prompt="x", cwd=str(repo)), home=home, cancel=cancel
    )

    assert result.ok is True
    assert result.cancelled is False
    assert result.error is None


# --- item 2: early cancel under require: any --------------------------------


def test_early_cancel_refused_without_require_any(tmp_path):
    with pytest.raises(MissionInvalid, match="early_cancel needs require: any"):
        mission_from_dict(
            {"prompt": "x", "early_cancel": True, "lanes": [{"fleet": "claude"}]},
            base_dir=tmp_path,
        )


def _early_cancel_mission(repo: Path) -> dict:
    return {
        "cwd": str(repo),
        "concurrency": 2,
        "require": "any",
        "early_cancel": True,
        "lanes": [
            {"name": "fast", "fleet": "claude", "prompt": "FAST"},
            {"name": "slow", "fleet": "codex", "prompt": "SLOW", "timeout": 30},
            {"name": "pending", "fleet": "claude", "prompt": "PENDING", "needs": ["slow"]},
        ],
    }


def _early_cancel_build(spec: Spec) -> list[str]:
    if spec.fleet == "codex":
        return ["sh", "-c", "sleep 60"]
    return ["sh", "-c", f"printf '%s\\n' {json.dumps(_claude_envelope('ok'))}"]


def test_early_cancel_kills_a_running_lane_and_skips_a_pending_one(
    repo, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_argv", _early_cancel_build)
    monkeypatch.setattr(runner_mod, "POLL_S", 0.2)
    mission = mission_from_dict(_early_cancel_mission(repo), base_dir=tmp_path)

    result = run_mission(mission, home=home)

    by_name = {lane["name"]: lane for lane in result.lanes}
    assert result.ok is True
    assert by_name["fast"]["ok"] is True
    reason = "cancelled: lane fast already passed"
    assert by_name["slow"]["ok"] is False and by_name["slow"]["skipped"] == reason
    assert by_name["pending"]["ok"] is False and by_name["pending"]["skipped"] == reason
    assert by_name["pending"]["attempts"] == []
    assert result.early_cancel["winner"] == "fast"
    assert set(result.early_cancel["cancelled"]) == {"slow", "pending"}

    report = Path(result.report_path).read_text()
    assert "Early cancel: lane fast passed; cancelled" in report
    assert f"Skipped: {reason}" in report

    saved = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert saved["early_cancel"]["winner"] == "fast"


def test_early_cancel_snapshot_round_trips(repo, home, tmp_path):
    raw = {
        "cwd": str(repo),
        "require": "any",
        "early_cancel": True,
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "x"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)

    assert reloaded.early_cancel is True
    assert reloaded.to_dict() == mission.to_dict() == snapshot


def test_resuming_an_ok_early_cancel_mission_keeps_the_cancelled_lanes(
    repo, home, monkeypatch, tmp_path
):
    calls = {"claude": 0, "codex": 0}

    def build(spec: Spec) -> list[str]:
        calls[spec.fleet] += 1
        return _early_cancel_build(spec)

    monkeypatch.setattr(runner_mod, "build_argv", build)
    monkeypatch.setattr(runner_mod, "POLL_S", 0.2)
    mission = mission_from_dict(_early_cancel_mission(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True
    calls_before = dict(calls)

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir)
    )

    assert calls == calls_before  # nothing re-dispatched
    assert resumed.ok is True
    by_name = {lane["name"]: lane for lane in resumed.lanes}
    assert by_name["slow"]["kept"] is True
    assert by_name["pending"]["kept"] is True
    assert by_name["fast"]["kept"] is True


# --- item 3: mechanical ranking ----------------------------------------------


def _lane(name: str, **fields: object) -> LaneResult:
    base: dict = dict(
        name=name,
        ok=True,
        test_touched="no",
        cost_usd=0.0,
        attempts=[{"tests": 0, "attempt": "a"}],
    )
    base.update(fields)
    return LaneResult(**base)


def test_rank_lanes_orders_by_every_tiebreak_in_turn(tmp_path):
    small = tmp_path / "small.patch"
    small.write_text("x" * 5)
    big = tmp_path / "big.patch"
    big.write_text("x" * 50)

    # 1. ok before not.
    order = ["a", "b"]
    lanes = [_lane("b", ok=False), _lane("a")]
    assert [row["lane"] for row in rank_lanes(lanes, order)] == ["a", "b"]

    # 2. a passing verdict before a failing or absent one.
    lanes = [
        _lane("c"),
        _lane("b", verdict={"passed": False}),
        _lane("a", verdict={"passed": True}),
    ]
    assert [row["lane"] for row in rank_lanes(lanes, ["a", "b", "c"])][0] == "a"

    # 3. an untouched test surface before a touched one.
    lanes = [_lane("b", test_touched="yes (1 files: x)"), _lane("a", test_touched="no")]
    assert [row["lane"] for row in rank_lanes(lanes, ["a", "b"])] == ["a", "b"]

    # 4. gate exit 0 before nonzero before none.
    lanes = [
        _lane("c", attempts=[{"tests": None, "attempt": "a"}]),
        _lane("b", attempts=[{"tests": 1, "attempt": "a"}]),
        _lane("a", attempts=[{"tests": 0, "attempt": "a"}]),
    ]
    assert [row["lane"] for row in rank_lanes(lanes, ["a", "b", "c"])] == ["a", "b", "c"]

    # 5. a smaller patch before a larger one; no patch ranks last.
    lanes = [
        _lane("c", diff_path=None),
        _lane("b", diff_path=str(big)),
        _lane("a", diff_path=str(small)),
    ]
    assert [row["lane"] for row in rank_lanes(lanes, ["a", "b", "c"])] == ["a", "b", "c"]

    # 6. lower cost before higher.
    lanes = [_lane("b", cost_usd=0.2), _lane("a", cost_usd=0.1)]
    assert [row["lane"] for row in rank_lanes(lanes, ["a", "b"])] == ["a", "b"]

    # 7. mission order as the final tie-break.
    lanes = [_lane("b"), _lane("a")]
    assert [row["lane"] for row in rank_lanes(lanes, ["a", "b"])] == ["a", "b"]

    # A skipped lane never appears in the ranking.
    lanes = [_lane("skip", skipped="needs x"), _lane("keep")]
    names = [row["lane"] for row in rank_lanes(lanes, ["skip", "keep"])]
    assert names == ["keep"]

    # A single-sink mission still gets a one-row ranking with every field.
    ranked = rank_lanes([_lane("solo")], ["solo"])
    assert ranked == [
        {
            "lane": "solo",
            "rank": 1,
            "ok": True,
            "verdict": None,
            "test_touched": "no",
            "gate_exit": 0,
            "patch_bytes": None,
            "cost_usd": 0.0,
        }
    ]


def test_mission_result_and_report_carry_the_ranking(repo, home, monkeypatch):
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", f"printf '%s\\n' {json.dumps(_claude_envelope('ok'))}"],
    )
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "require": "any",
            "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}],
        },
        base_dir=repo,
    )

    result = run_mission(mission, home=home)

    assert len(result.ranking) == 1
    row = result.ranking[0]
    assert row["lane"] == "a" and row["rank"] == 1 and row["ok"] is True
    assert row["verdict"] is None and row["test_touched"] == "no"
    assert row["gate_exit"] is None and row["patch_bytes"] is None
    assert row["cost_usd"] >= 0

    report = Path(result.report_path).read_text()
    assert "## Ranking" in report
    assert "| 1 | a | True |" in report

    saved = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved["ranking"] == result.ranking


# --- item 4: collate.candidates ----------------------------------------------


def test_collate_candidates_refused_below_two(repo, tmp_path):
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "codex", "prompt": "B"},
        ],
        "collate": {"fleet": "antigravity", "candidates": 1},
    }
    with pytest.raises(MissionInvalid, match="collate candidates must be at least 2"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_collate_candidates_judges_only_the_top_two_of_four(
    repo, home, monkeypatch, tmp_path
):
    # Ranking by cost ascending (every other tie-break is equal): b, d, c, a.
    costs = {"A": 0.4, "B": 0.1, "C": 0.3, "D": 0.2}

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "antigravity":
            return ["sh", "-c", f"printf '%s\\n' {json.dumps(_antigravity_strongest('b'))}"]
        key = spec.prompt
        out = _claude_envelope(f"ok {key}", costs[key])
        return ["sh", "-c", f"printf '%s\\n' {json.dumps(out)}"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "require": "any",
            "lanes": [
                {"name": "a", "fleet": "claude", "prompt": "A"},
                {"name": "b", "fleet": "claude", "prompt": "B"},
                {"name": "c", "fleet": "claude", "prompt": "C"},
                {"name": "d", "fleet": "claude", "prompt": "D"},
            ],
            "collate": {"fleet": "antigravity", "rank": True, "candidates": 2},
        },
        base_dir=tmp_path,
    )

    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.collate["ok"] is True
    assert result.collate["strongest"] == "b"
    assert set(result.collate["candidates"]) == {"b", "d"}

    schema = json.loads(Path(result.mission_dir, "collate-rank.schema.json").read_text())
    assert set(schema["properties"]["strongest"]["enum"]) == {"b", "d"}

    prompt = Path(result.mission_dir, "collate-prompt-forward.txt").read_text()
    assert "Lane `b`" in prompt and "Lane `d`" in prompt
    assert "Lane `a`" not in prompt and "Lane `c`" not in prompt
    assert "omitted by ranking: c, a" in prompt


# --- item 5: documentation ---------------------------------------------------


def test_readme_documents_early_cancel_ranking_and_candidates():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split(
        "#### Early cancel, mechanical ranking, and best-of-n", 1
    )[1].split("\n## ", 1)[0]
    assert "early_cancel needs require: any" in section
    assert "cancelled: lane <name> already passed" in section
    assert 'early_cancel: {"winner": "<lane>", "cancelled":' in section
    assert "ok before not" in section
    assert "exit code of 0 before nonzero before none" in section
    assert "Generative Verifiers" in section
    assert "docs/ROADMAP-2026-09.md" in section and "item B5" in section
    assert '"candidates": <int>' in section
    assert "at least 2 or refused at load" in section
    assert "omitted by ranking" in section
    assert 'candidates: ["<lane>", ...]' in section
