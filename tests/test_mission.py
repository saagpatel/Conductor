"""Missions: fan-out, fallback, budget, isolation, collate, and the report.

Fleets are faked at the argv boundary so each test costs nothing and needs no
CLI installed; the mechanics under test (threads, worktrees, ledger, files)
are all real.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.mission import (
    MissionInvalid,
    load_mission,
    mission_from_dict,
    run_mission,
)


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


def envelope(answer: str, cost: float | None = None) -> str:
    """A claude-shaped envelope: the one fleet that reports dollars."""
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]


def codex_say(answer: str) -> list[str]:
    """Codex speaks one JSON event per line, never a single envelope."""
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": answer}})
    done = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}})
    return ["sh", "-c", f"echo '{item}'; echo '{done}'"]


# --- loading ---------------------------------------------------------------


def test_fields_cascade_mission_to_lane_to_fallback(tmp_path: Path):
    (tmp_path / "spec.md").write_text("Do the thing from the file.\n")
    (tmp_path / "shape.json").write_text('{"type":"object"}')
    raw = {
        "name": "cascade",
        "prompt_file": "spec.md",
        "cwd": ".",
        "mode": "write",
        "effort": "hard",
        "test": "pytest -q",
        "commit": "feat: thing",
        "lanes": [
            {"fleet": "codex", "model": "sol", "fallback": [{"fleet": "claude", "model": "opus"}]},
            {
                "fleet": "antigravity",
                "effort": "cheap",
                "prompt": "lane override",
                "schema": "shape.json",
            },
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    assert m.cwd == str(tmp_path)
    codex, agy = m.lanes
    assert codex.name == "codex-sol" and agy.name == "antigravity"
    primary, fallback = codex.attempts
    assert primary.prompt.startswith("Do the thing") and primary.effort == "hard"
    assert primary.mode == "write" and primary.test == "pytest -q" and primary.commit
    # The fallback inherits everything from the primary except what it sets.
    assert fallback.fleet == "claude" and fallback.model == "opus"
    assert fallback.prompt == primary.prompt and fallback.effort == "hard"
    assert fallback.mode == "write" and fallback.commit == "feat: thing"
    # The second lane overrides prompt and effort, resolves schema relative to the file.
    assert agy.attempts[0].prompt == "lane override" and agy.attempts[0].effort == "cheap"
    assert agy.attempts[0].schema == str(tmp_path / "shape.json")
    # Write lanes isolate by default.
    assert primary.isolated() is True


def test_a_fallback_that_switches_fleet_drops_the_inherited_model(tmp_path: Path):
    """Caught live 2026-09-03: an antigravity fallback inherited "luna" from
    its codex primary and was refused at load. Model names are fleet-local."""
    raw = {
        "prompt": "x",
        "lanes": [{"fleet": "codex", "model": "luna", "fallback": [{"fleet": "antigravity"}]}],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = m.lanes[0].attempts
    assert primary.model == "luna"
    assert fallback.fleet == "antigravity" and fallback.model is None
    # Same fleet keeps the model; an explicit model on the fallback wins.
    raw["lanes"][0]["fallback"] = [{"fleet": "codex"}, {"fleet": "codex", "model": "sol"}]
    attempts = mission_from_dict(raw, base_dir=tmp_path).lanes[0].attempts
    assert [a.model for a in attempts] == ["luna", "luna", "sol"]


def test_a_mission_level_model_cascades_to_its_lanes(tmp_path: Path):
    """A mission has no fleet, so its model is not a fleet switch and must
    reach the lanes; validation refuses it on any lane it does not fit."""
    raw = {"prompt": "x", "model": "sol", "lanes": [{"fleet": "codex"}, {"fleet": "codex"}]}
    m = mission_from_dict(raw, base_dir=tmp_path)
    assert [lane.attempts[0].model for lane in m.lanes] == ["sol", "sol"]
    raw["lanes"].append({"fleet": "claude"})
    with pytest.raises(MissionInvalid, match="claude.*may not run model 'sol'"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_toml_missions_load_too(tmp_path: Path):
    path = tmp_path / "m.toml"
    path.write_text(
        'name = "t"\nprompt = "hi"\n[[lanes]]\nfleet = "codex"\n[[lanes]]\nfleet = "cursor"\n'
    )
    m = load_mission(path)
    assert [lane.name for lane in m.lanes] == ["codex", "cursor"]


def test_routing_policy_is_enforced_at_load_before_any_spend(tmp_path: Path):
    raw = {"prompt": "x", "lanes": [{"fleet": "cursor", "model": "opus"}]}
    with pytest.raises(MissionInvalid, match="lane 'cursor-opus'.*Claude Code"):
        mission_from_dict(raw, base_dir=tmp_path)
    raw = {
        "prompt": "x",
        "lanes": [{"fleet": "codex"}],
        "collate": {"fleet": "cursor", "model": "sol"},
    }
    with pytest.raises(MissionInvalid, match="collate"):
        mission_from_dict(raw, base_dir=tmp_path)


@pytest.mark.parametrize(
    "raw,message",
    [
        ({"prompt": "x"}, "lanes"),
        ({"prompt": "x", "lanes": []}, "at least one lane"),
        ({"lanes": [{"fleet": "codex"}]}, "no prompt"),
        ({"prompt": "x", "fleet": "codex", "lanes": [{"fleet": "codex"}]}, "each lane"),
        (
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex", "name": "a"}, {"fleet": "cursor", "name": "a"}],
            },
            "duplicate",
        ),
        ({"prompt": "x", "require": "most", "lanes": [{"fleet": "codex"}]}, "require"),
        ({"prompt": "x", "concurrency": 0, "lanes": [{"fleet": "codex"}]}, "concurrency"),
        ({"prompt": "x", "max_cost_usd": -1, "lanes": [{"fleet": "codex"}]}, "max_cost_usd"),
        ({"prompt": "x", "concurrency": "two", "lanes": [{"fleet": "codex"}]}, "must be numbers"),
        ({"prompt": "x", "max_cost_usd": "5 dollars", "lanes": [{"fleet": "codex"}]}, "numbers"),
        ({"prompt": "x", "lanes": [{"fleet": "codex", "name": "a/b"}]}, "names a file"),
        ({"prompt": "x", "lanes": [{"fleet": "codex", "name": "../up"}]}, "names a file"),
        ({"prompt": "x", "test": ["pytest"], "lanes": [{"fleet": "codex"}]}, "must be a string"),
        ({"prompt": "x", "commit": 7, "lanes": [{"fleet": "codex"}]}, "must be a string"),
        ({"prompt": "x", "isolate": "yes", "lanes": [{"fleet": "codex"}]}, "true or false"),
    ],
)
def test_invalid_missions_are_refused_with_a_reason(tmp_path: Path, raw, message):
    with pytest.raises(MissionInvalid, match=message):
        mission_from_dict(raw, base_dir=tmp_path)


def test_same_fleet_twice_gets_distinct_lane_names(tmp_path: Path):
    raw = {
        "prompt": "x",
        "lanes": [{"fleet": "codex"}, {"fleet": "codex"}, {"fleet": "codex", "model": "sol"}],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    assert [lane.name for lane in m.lanes] == ["codex", "codex-2", "codex-sol"]


# --- running ---------------------------------------------------------------


def test_fan_out_respects_the_concurrency_cap(repo, home, monkeypatch, tmp_path):
    item = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})
    fake_fleets(
        monkeypatch,
        {
            "codex": ["sh", "-c", f"sleep 0.6; echo '{item}'"],
            "cursor": ["sh", "-c", f"sleep 0.6; echo '{envelope('ok')}'"],
            "antigravity": ["sh", "-c", f"sleep 0.6; echo '{envelope('ok')}'"],
        },
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "codex"}, {"fleet": "cursor"}, {"fleet": "antigravity"}],
    }

    serial = mission_from_dict({**raw, "concurrency": 1}, base_dir=tmp_path)
    t = time.monotonic()
    r1 = run_mission(serial, home=home)
    serial_s = time.monotonic() - t

    wide = mission_from_dict({**raw, "concurrency": 3}, base_dir=tmp_path)
    t = time.monotonic()
    r2 = run_mission(wide, home=home)
    wide_s = time.monotonic() - t

    assert r1.ok and r2.ok
    assert serial_s >= 1.8
    assert wide_s < serial_s / 2


def test_a_failed_primary_escalates_down_its_fallback_list(repo, home, monkeypatch, tmp_path):
    """Write mode: the primary exits 0 having changed nothing (the classic
    false success); the fallback actually writes."""
    fake_fleets(
        monkeypatch,
        {
            "codex": ["sh", "-c", "echo 'Done! Refactored everything.'"],
            "claude": ["sh", "-c", "echo real > real.txt"],
        },
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude", "model": "opus"}]}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    lane = result.lanes[0]
    assert lane["ok"] is True
    assert [a["attempt"] for a in lane["attempts"]] == ["codex", "claude/opus"]
    assert lane["attempts"][0]["no_op"] is True and lane["attempts"][0]["ok"] is False
    assert lane["attempts"][1]["commits"] == 1
    assert result.ok is True
    report = Path(result.report_path).read_text()
    assert "| codex | codex | False |" in report and "| codex | claude/opus | True |" in report


def test_exhausted_fallbacks_fail_the_lane_and_the_mission(repo, home, monkeypatch, tmp_path):
    fake_fleets(monkeypatch, {"codex": ["sh", "-c", "exit 3"], "claude": ["sh", "-c", "exit 4"]})
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude"}]}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is False
    assert [a["exit_code"] for a in result.lanes[0]["attempts"]] == [3, 4]


def test_require_any_passes_on_one_good_lane(repo, home, monkeypatch, tmp_path):
    fake_fleets(monkeypatch, {"codex": ["sh", "-c", "exit 1"], "cursor": say("fine")})
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "codex"}, {"fleet": "cursor"}]}
    assert (
        run_mission(mission_from_dict({**raw, "require": "all"}, base_dir=tmp_path), home=home).ok
        is False
    )
    assert (
        run_mission(mission_from_dict({**raw, "require": "any"}, base_dir=tmp_path), home=home).ok
        is True
    )


def test_budget_stops_further_spend_and_says_so(repo, home, monkeypatch, tmp_path):
    fake_fleets(monkeypatch, {"claude": say("pricey", cost=0.05), "cursor": say("cheap")})
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "concurrency": 1,
        "max_cost_usd": 0.04,
        "lanes": [{"fleet": "claude"}, {"fleet": "cursor"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    first, second = result.lanes
    # The whole $0.04 was lane one's cap; a fleet that spent $0.05 anyway is
    # over budget, and nothing more starts.
    assert first["ok"] is False and first["cost_usd"] == 0.05
    assert first["attempts"][0]["failure"] == "over budget: $0.0500 against a $0.0400 cap"
    assert second["ok"] is False and second["attempts"] == []
    assert "budget exhausted" in second["skipped"]
    assert result.budget["exceeded"] is True
    assert result.ok is False
    assert "Budget exceeded" in Path(result.report_path).read_text()


def test_write_lanes_are_isolated_and_the_checkout_stays_untouched(
    repo, home, monkeypatch, tmp_path, git_out
):
    fake_fleets(
        monkeypatch,
        {"codex": ["sh", "-c", "echo a > a.txt"], "antigravity": ["sh", "-c", "echo b > b.txt"]},
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: lane work",
        "lanes": [{"fleet": "codex"}, {"fleet": "antigravity"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is True
    branches = [lane["attempts"][-1]["branch"] for lane in result.lanes]
    assert len(set(branches)) == 2 and all(b.startswith("conductor/") for b in branches)
    assert git_out(repo, "status", "--porcelain") == ""
    assert git_out(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "a.txt" in git_out(repo, "ls-tree", "--name-only", branches[0])
    assert "b.txt" in git_out(repo, "ls-tree", "--name-only", branches[1])
    assert "a.txt" not in git_out(repo, "ls-tree", "--name-only", "main")


def test_collate_sees_every_lane_answer_and_its_cost_counts(repo, home, monkeypatch, tmp_path):
    fake_fleets(
        monkeypatch,
        {
            "codex": codex_say("codex says four"),
            "cursor": say("cursor says four"),
            "claude": say("both agree: four", cost=0.02),
        },
    )
    raw = {
        "prompt": "what is 2+2",
        "cwd": str(repo),
        "lanes": [{"fleet": "codex"}, {"fleet": "cursor"}],
        "collate": {
            "fleet": "claude",
            "model": "sonnet",
            "effort": "cheap",
            "instructions": "Pick one.",
        },
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.ok is True
    assert result.collate["ok"] is True
    collated = Path(result.collate["answer_path"]).read_text()
    assert collated == "both agree: four"
    prompt = (home / "runs" / result.collate["run_id"] / "prompt.txt").read_text()
    assert "what is 2+2" in prompt
    assert "codex says four" in prompt and "cursor says four" in prompt
    assert "Pick one." in prompt
    assert result.collate["cost_usd"] == 0.02
    assert result.cost_usd >= 0.02  # lanes add their own estimated cents
    report = Path(result.report_path).read_text()
    assert "## Collated" in report and "both agree: four" in report
    assert (Path(result.mission_dir) / "answers" / "codex.txt").read_text() == "codex says four"


def test_same_second_same_name_missions_get_distinct_directories(repo, home, tmp_path, monkeypatch):
    from conductor import mission as mission_mod

    frozen = mission_mod.datetime.now(mission_mod.UTC)

    class FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(mission_mod, "datetime", FrozenDatetime)
    raw = {"name": "twin", "prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "codex"}]}
    a = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, dry_run=True)
    b = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, dry_run=True)
    assert a.mission_id != b.mission_id
    assert b.mission_id == f"{a.mission_id}-2"
    assert Path(a.report_path).is_file() and Path(b.report_path).is_file()


def test_a_crashing_lane_does_not_take_the_mission_down(repo, home, monkeypatch, tmp_path):
    """One lane's exception becomes that lane's failure; the other lane still
    runs and the mission still writes its receipt."""
    from conductor import mission as mission_mod

    fake_fleets(monkeypatch, {"codex": codex_say("fine"), "cursor": say("fine")})
    real_copy = mission_mod.shutil.copyfile

    def explode(src, dst):
        if "codex" in str(dst):
            raise OSError("disk on fire")
        return real_copy(src, dst)

    monkeypatch.setattr(mission_mod.shutil, "copyfile", explode)
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "codex"}, {"fleet": "cursor"}]}
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    codex, cursor = result.lanes
    assert codex["ok"] is False and "lane crashed: OSError: disk on fire" in codex["skipped"]
    assert cursor["ok"] is True
    assert result.ok is False
    assert (Path(result.mission_dir) / "result.json").is_file()
    assert "lane crashed" in Path(result.report_path).read_text()


def test_cap_usd_cascades_and_a_lane_can_tighten_it(tmp_path: Path):
    raw = {
        "prompt": "x",
        "cwd": str(tmp_path),
        "cap_usd": 2.0,
        "lanes": [
            {"fleet": "codex"},
            {"fleet": "cursor", "cap_usd": 0.5, "fallback": [{"fleet": "antigravity"}]},
        ],
        "collate": {"fleet": "claude"},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[0].cap_usd == 2.0
    assert [a.cap_usd for a in mission.lanes[1].attempts] == [0.5, 0.5]
    assert mission.collate.cap_usd == 2.0
    with pytest.raises(MissionInvalid, match="positive"):
        mission_from_dict({**raw, "cap_usd": -1}, base_dir=tmp_path)
    with pytest.raises(MissionInvalid):
        mission_from_dict({**raw, "cap_usd": "lots"}, base_dir=tmp_path)


def test_what_remains_of_the_mission_budget_caps_the_next_dispatch(
    repo, home, monkeypatch, tmp_path
):
    """The ledger's remainder is each dispatch's cap, so the budget's
    overshoot is bounded in dollars, not just in dispatch count."""
    from conductor import mission as mission_mod

    fake_fleets(monkeypatch, {"claude": say("pricey", cost=0.6)})
    caps: list[float | None] = []
    real = mission_mod.dispatch

    def spy(spec, **kw):
        caps.append(spec.cap_usd)
        return real(spec, **kw)

    monkeypatch.setattr(mission_mod, "dispatch", spy)
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "concurrency": 1,
        "max_cost_usd": 1.0,
        "lanes": [{"fleet": "claude"}, {"fleet": "claude", "cap_usd": 0.3}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    # Lane one had the whole budget; lane two got the $0.40 left, tightened
    # further by its own $0.30, and its $0.60 spend is judged against that.
    assert caps == [1.0, 0.3]
    first, second = result.lanes
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["attempts"][0]["failure"] == "over budget: $0.6000 against a $0.3000 cap"


def test_dry_run_records_argv_and_spawns_nothing(repo, home, tmp_path):
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "codex", "model": "luna"}, {"fleet": "cursor"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, dry_run=True)
    assert result.ok is True and result.dry_run is True
    for lane in result.lanes:
        run_dir = home / "runs" / lane["attempts"][0]["run_id"]
        assert (run_dir / "argv.json").is_file()
        assert not (run_dir / "stdout.log").exists()
    argv = json.loads(
        (home / "runs" / result.lanes[0]["attempts"][0]["run_id"] / "argv.json").read_text()
    )
    assert "gpt-5.6-luna" in argv
