"""B2: cache-friendly prompts -- identical leading bytes, and cache
accounting carried in one place from a dispatch's usage up to the mission.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.fleets import Spec, build_argv
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def envelope(answer: str, *, cache_read: int = 0, cache_write: int = 0, cost: float = 0.1) -> str:
    """A claude-shaped envelope reporting cache reads and writes."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
            "total_cost_usd": cost,
        }
    )


def say(answer: str, *, exit_code: int = 0, **kw) -> list[str]:
    output = shlex.quote(envelope(answer, **kw))
    return ["sh", "-c", f"printf '%s\\n' {output}; exit {exit_code}"]


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


def _snapshot(result) -> Mission:
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    return Mission.from_snapshot(raw)


# --- item 1: the Claude argv pins ------------------------------------------


def test_claude_argv_pins_system_prompt_snapshot_and_dynamic_section_exclusion():
    for mode in ("read", "write"):
        argv = build_argv(Spec(fleet="claude", prompt="x", cwd="/tmp", mode=mode))
        assert argv[argv.index("--system-prompt-snapshot") + 1] == "on"
        assert "--exclude-dynamic-system-prompt-sections" in argv
    other = build_argv(Spec(fleet="codex", prompt="x", cwd="/tmp"))
    assert "--system-prompt-snapshot" not in other
    assert "--exclude-dynamic-system-prompt-sections" not in other


# --- item 2: prefix / prefix_file -------------------------------------------


def test_prefix_and_prefix_file_are_mutually_exclusive(tmp_path):
    (tmp_path / "prefix.txt").write_text("Shared context.\n")
    raw = {
        "prompt": "x",
        "prefix": "inline",
        "prefix_file": "prefix.txt",
        "lanes": [{"fleet": "claude"}],
    }
    with pytest.raises(MissionInvalid, match="mutually exclusive"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_prefix_file_loads_relative_to_the_mission_file(tmp_path):
    (tmp_path / "prefix.txt").write_text("Shared context.\n")
    raw = {"prompt": "x", "prefix_file": "prefix.txt", "lanes": [{"fleet": "claude"}]}
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.prefix == "Shared context.\n"


def test_prefix_refuses_a_template_reference(tmp_path):
    raw = {"prompt": "x", "prefix": "See {{mission.prompt}}.", "lanes": [{"fleet": "claude"}]}
    with pytest.raises(MissionInvalid, match="prefix must not contain template references"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_prefix_round_trips_through_the_snapshot(repo, home, monkeypatch, tmp_path):
    fake_fleets(monkeypatch, {"claude": say("ok")})
    raw = {
        "prompt": "x",
        "prefix": "Shared repo context.",
        "cwd": str(repo),
        "lanes": [{"fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    assert snapshot["prefix"] == "Shared repo context."
    reloaded = Mission.from_snapshot(snapshot)
    assert reloaded.prefix == "Shared repo context."


def test_every_lane_prompt_and_the_collate_start_with_the_prefix(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"claude": say("lane answer")})
    raw = {
        "prompt": "Do the thing.",
        "prefix": "Repo-wide context, shared by every lane.",
        "cwd": str(repo),
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "claude", "prompt": "B"},
        ],
        "collate": {"fleet": "claude", "model": "haiku"},
        "self_judging": "allow",
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    expected_start = "Repo-wide context, shared by every lane.\n\n"
    for lane in result.lanes:
        run_dir = Path(lane["attempts"][0]["run_dir"])
        prompt = (run_dir / "prompt.txt").read_text()
        assert prompt.startswith(expected_start)
    collate_prompt = (Path(result.mission_dir) / "collate-prompt.txt").read_text()
    assert collate_prompt.startswith(expected_start)


# --- item 3: cache accounting -----------------------------------------------


def test_cache_tokens_sum_per_lane_and_into_the_mission_with_hit_rate(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"claude": say("ok", cache_read=40, cache_write=10)})
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "claude"}]}
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    lane = result.lanes[0]
    assert lane["cache_read_tokens"] == 40
    assert lane["cache_write_tokens"] == 10
    assert result.cache == {
        "input_tokens": 10,
        "cache_read_tokens": 40,
        "cache_write_tokens": 10,
        "hit_rate": round(40 / 60, 3),
    }
    report = Path(result.report_path).read_text()
    assert "Cache: 40 read, 10 written, 10 uncached; hit rate 66.7%" in report


def test_a_mission_with_nothing_read_or_written_has_a_null_hit_rate(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"claude": ["sh", "-c", "exit 0"]})
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "claude"}]}
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.cache == {
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "hit_rate": None,
    }
    assert "hit rate -" in Path(result.report_path).read_text()


def test_resume_carries_the_lanes_cache_sums(repo, home, monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_build(spec: Spec) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return say("failed", cache_read=10, cache_write=5, cost=0.1, exit_code=7)
        return say("ok", cache_read=40, cache_write=20, cost=0.2)

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    raw = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "claude"}]}
    first = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert first.ok is False
    assert first.lanes[0]["cache_read_tokens"] == 10

    resumed = run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))
    lane = resumed.lanes[0]
    assert lane["ok"] is True
    assert lane["cache_read_tokens"] == 50
    assert lane["cache_write_tokens"] == 25
    assert resumed.cache == {
        "input_tokens": 20,
        "cache_read_tokens": 50,
        "cache_write_tokens": 25,
        "hit_rate": round(50 / 95, 3),
    }


def test_conductor_spend_reports_the_cache_write_column(home, monkeypatch, capsys):
    run_id = "20260101T000000Z-claude-cached"
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "fleet": "claude",
                "model": "sonnet",
                "ok": True,
                "usage": {
                    "cost_usd": 0.1,
                    "cost_basis": "reported",
                    "total_tokens": 100,
                    "cache_read_tokens": 40,
                    "cache_write_tokens": 15,
                },
            }
        )
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[-1]["cache_write_tokens"] == 15
    assert main(["spend"]) == 0
    text = capsys.readouterr().out
    assert "cache_write_tokens" in text
    assert text.splitlines()[-1].split()[-2] == "15"


# --- README --------------------------------------------------------------


def test_readme_documents_cache_friendly_prompts():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("#### Cache-friendly prompts", 1)[1].split("\n### ", 1)[0]
    section = " ".join(section.split())  # the README wraps at 80 columns
    assert "--system-prompt-snapshot on" in section
    assert "--exclude-dynamic-system-prompt-sections" in section
    assert "Don't Break the Cache" in section
    assert "docs/ROADMAP-2026-09.md" in section and "item B2" in section
    assert "operator hooks injecting per-lane context made a $0.05 reply cost $0.24" in section
    assert "prefix_file" in section
    assert "prefix must not contain template references" in section
    assert "{{mission.prompt}}" in section


def test_readme_documents_cache_accounting():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("### Cache accounting", 1)[1].split("\n### ", 1)[0]
    section = " ".join(section.split())  # the README wraps at 80 columns
    assert "cache_write_tokens" in section
    assert '"input_tokens", "cache_read_tokens"' in section
    assert "hit_rate" in section
    assert "Cache: <read> read, <write> written, <input> uncached; hit rate <pct>" in section
