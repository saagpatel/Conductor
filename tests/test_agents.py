"""D3: inline agent definitions.

`Spec.agent` lets a lane define its reviewer or fixer persona at dispatch
time: a system prompt and, optionally, a tool allow list, with nothing on
the operator's disk. Enforceable on Claude Code only (probed live,
docs/research/2026-09-06-live-probe-inline-agents.md); the run's own stream
-- never the model's answer -- is what proves the persona actually applied.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import attest
from conductor.cli import build_parser, cmd_dispatch, main
from conductor.fleets import DispatchRefused, Spec, build_argv
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission
from conductor.runner import dispatch
from docs import doc_section

AGENT = {
    "name": "reviewer",
    "description": "Cold reviewer",
    "prompt": "You are a skeptical reviewer.",
    "tools": ["Read", "Grep"],
}


def spec(**kw) -> Spec:
    base = dict(fleet="claude", prompt="do the thing", cwd="/tmp")
    base.update(kw)
    return Spec(**base)


# --- Spec.agent / argv (item 1) ---------------------------------------------


def test_claude_argv_carries_agents_and_agent_in_both_modes():
    for mode in ("read", "write"):
        argv = build_argv(spec(mode=mode, agent=AGENT))
        assert "--agents" in argv
        definition = json.loads(argv[argv.index("--agents") + 1])
        assert definition == {
            "reviewer": {
                "description": "Cold reviewer",
                "prompt": "You are a skeptical reviewer.",
                "tools": ["Read", "Grep"],
            }
        }
        assert argv[argv.index("--agent") + 1] == "reviewer"


def test_agent_without_tools_omits_the_tools_key():
    agent = {"name": "fixer", "description": "d", "prompt": "p"}
    argv = build_argv(spec(agent=agent))
    definition = json.loads(argv[argv.index("--agents") + 1])
    assert definition == {"fixer": {"description": "d", "prompt": "p"}}


def test_no_agent_means_no_agent_flags():
    argv = build_argv(spec())
    assert "--agents" not in argv
    assert "--agent" not in argv


@pytest.mark.parametrize("fleet,model", [("antigravity", None), ("cursor", "grok-4.6")])
def test_agent_is_refused_off_the_claude_fleet(fleet, model):
    with pytest.raises(DispatchRefused, match="agent is enforceable on the claude fleet only"):
        build_argv(spec(fleet=fleet, model=model, agent=AGENT))


def test_agent_must_be_an_object():
    with pytest.raises(DispatchRefused, match="agent must be an object"):
        build_argv(spec(agent="reviewer"))


@pytest.mark.parametrize("key", ["name", "description", "prompt"])
def test_agent_needs_each_required_field(key):
    bad = dict(AGENT)
    bad.pop(key)
    with pytest.raises(DispatchRefused, match=f"agent needs a non-empty '{key}'"):
        build_argv(spec(agent=bad))
    bad2 = {**AGENT, key: "   "}
    with pytest.raises(DispatchRefused, match=f"agent needs a non-empty '{key}'"):
        build_argv(spec(agent=bad2))


def test_agent_name_must_match_the_pattern():
    for bad_name in ("7reviewer", "re viewer", "re/viewer"):
        with pytest.raises(DispatchRefused, match="must match"):
            build_argv(spec(agent={**AGENT, "name": bad_name}))


def test_agent_tools_must_be_a_list_of_non_empty_strings():
    for bad_tools in ("Read", ["Read", ""], ["Read", 3], {}):
        with pytest.raises(DispatchRefused, match="agent tools must be a list"):
            build_argv(spec(agent={**AGENT, "tools": bad_tools}))


def test_agent_refuses_an_unknown_field():
    with pytest.raises(DispatchRefused, match="unknown field"):
        build_argv(spec(agent={**AGENT, "extra": "nope"}))


def test_cli_dispatch_agent_file_reaches_the_spec(repo, home, tmp_path, monkeypatch):
    import conductor.cli as cli_mod
    from conductor import runner as real_runner

    agent_file = tmp_path / "agent.json"
    agent_file.write_text(json.dumps(AGENT))
    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--agent-file",
            str(agent_file),
            "--dry-run",
        ]
    )

    captured: dict = {}
    real_dispatch = real_runner.dispatch

    def capturing(spec, **kwargs):
        captured["spec"] = spec
        kwargs["dry_run"] = True
        kwargs["home"] = home
        return real_dispatch(spec, **kwargs)

    monkeypatch.setattr(cli_mod, "dispatch", capturing)
    rc = cmd_dispatch(args)
    assert rc == 0
    assert captured["spec"].agent == AGENT


def test_cli_dispatch_agent_file_unreadable_is_refused(repo, capsys):
    parser = build_parser()
    args = parser.parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--cwd",
            str(repo),
            "--agent-file",
            str(repo / "does-not-exist.json"),
        ]
    )
    rc = cmd_dispatch(args)
    assert rc == 3
    out = json.loads(capsys.readouterr().err)
    assert "agent file unreadable" in out["refused"]


# --- runner: the init-event assertion (item 2) ------------------------------


def _init_line(agents: list[str], tools: list[str] | None = None) -> str:
    event: dict = {"type": "system", "subtype": "init", "agents": agents}
    if tools is not None:
        event["tools"] = tools
    return json.dumps(event)


def _result_line() -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "usage": {"inputTokens": 10, "outputTokens": 1},
        }
    )


def _sh(*lines: str, trailer: str = "") -> list[str]:
    printf = "printf '%s\\n' " + " ".join(shlex.quote(line) for line in lines)
    return ["sh", "-c", printf + (f"; {trailer}" if trailer else "")]


def test_agent_applied_is_ok_and_recorded_on_the_receipt(repo, home, fake_fleet):
    fake_fleet(_sh(_init_line(["reviewer"], ["Read", "Grep"]), _result_line()))
    result = dispatch(spec(cwd=str(repo), agent=AGENT), home=home)
    assert result.ok is True
    expected = {"name": "reviewer", "tools": ["Read", "Grep"], "applied": True}
    assert result.agent == expected
    assert result.summary()["agent"] == expected

    data = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert data["agent"] == expected

    envelope = json.loads(Path(result.attestation_path).read_text())
    key = attest.receipt_key(home)
    statement, reason = attest.verify(envelope, key)
    assert reason is None
    assert statement["agent"] == expected


def test_no_agent_means_a_null_agent_receipt(repo, home, fake_fleet):
    fake_fleet(_sh(_result_line()))
    result = dispatch(spec(cwd=str(repo)), home=home)
    assert result.agent is None


def test_agent_absent_from_init_agents_list_is_not_ok(repo, home, fake_fleet):
    fake_fleet(_sh(_init_line(["someone-else"], ["Read", "Grep"]), _result_line()))
    result = dispatch(spec(cwd=str(repo), agent=AGENT), home=home)
    assert result.ok is False
    assert result.agent == {"name": "reviewer", "tools": ["Read", "Grep"], "applied": False}
    assert result.error == (
        "agent 'reviewer' not applied: not present in the init event's agents list"
    )
    assert result.summary()["kind"] == "agent"


def test_agent_tools_mismatch_is_not_ok_naming_the_difference(repo, home, fake_fleet):
    fake_fleet(_sh(_init_line(["reviewer"], ["Read"]), _result_line()))
    result = dispatch(spec(cwd=str(repo), agent=AGENT), home=home)
    assert result.ok is False
    assert result.agent == {"name": "reviewer", "tools": ["Read", "Grep"], "applied": False}
    assert "['Read']" in result.error
    assert "['Grep', 'Read']" in result.error
    assert result.summary()["kind"] == "agent"


def test_no_init_event_and_exit_zero_is_not_ok(repo, home, fake_fleet):
    fake_fleet(_sh(_result_line()))
    result = dispatch(spec(cwd=str(repo), agent=AGENT), home=home)
    assert result.ok is False
    assert result.agent == {"name": "reviewer", "tools": ["Read", "Grep"], "applied": False}
    assert result.error == "agent 'reviewer' not applied: no init event"
    assert result.summary()["kind"] == "agent"


def test_no_init_event_and_nonzero_exit_keeps_exit_as_its_kind(repo, home, fake_fleet):
    fake_fleet(_sh(_result_line(), trailer="exit 7"))
    result = dispatch(spec(cwd=str(repo), agent=AGENT), home=home)
    assert result.ok is False
    assert result.exit_code == 7
    assert result.agent == {"name": "reviewer", "tools": ["Read", "Grep"], "applied": None}
    assert result.summary()["kind"] == "exit"


def test_empty_stream_with_exit_zero_keeps_its_own_kind_not_agent(repo, home, fake_fleet):
    # Regression: "the run otherwise looks complete" was implemented as
    # "exit 0 and no fleet-reported error", which an entirely empty stdout
    # also satisfies (FleetOutput() has no error, but no result event either).
    # The spec's bar is "exit 0 and a result event"; an empty stream must
    # keep its own failure (a read dispatch with no answer) with `applied`
    # null, not be recast as an `agent` failure.
    fake_fleet(["true"])
    result = dispatch(spec(cwd=str(repo), agent=AGENT), home=home)
    assert result.ok is False
    assert result.exit_code == 0
    assert result.agent == {"name": "reviewer", "tools": ["Read", "Grep"], "applied": None}
    assert result.summary()["kind"] == "no_answer"


# --- mission: cascading, agent_file, review-stage refusal (item 3) ---------


def test_agent_cascades_mission_to_lane_to_fallback(tmp_path):
    raw = {
        "cwd": "/tmp",
        "agent": AGENT,
        "lanes": [
            {
                "name": "a",
                "fleet": "claude",
                "prompt": "A",
                "fallback": [{"fleet": "claude", "model": "opus"}],
            }
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = mission.lanes[0].attempts
    assert primary.agent == AGENT
    assert fallback.agent == AGENT


def test_a_lane_or_fallback_may_override_the_mission_default(tmp_path):
    other = {**AGENT, "name": "fixer"}
    raw = {
        "cwd": "/tmp",
        "agent": AGENT,
        "lanes": [
            {
                "name": "a",
                "fleet": "claude",
                "prompt": "A",
                "agent": other,
                "fallback": [{"fleet": "claude", "model": "opus", "agent": AGENT}],
            }
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = mission.lanes[0].attempts
    assert primary.agent == other
    assert fallback.agent == AGENT


def test_agent_cascades_to_a_cascade_attempt(tmp_path):
    # The cascade fleet stays claude (agent is claude-only, per fleets.Spec);
    # the point here is that the cascade attempt inherits `agent` from the
    # lane's own primary the same way it inherits any other attempt key.
    raw = {
        "cwd": "/tmp",
        "cascade": {"fleet": "claude", "model": "haiku"},
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "write", "prompt": "A", "agent": AGENT}
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    lane = mission.lanes[0]
    assert lane.cascaded is True
    cascade_attempt, primary = lane.attempts
    assert cascade_attempt.model == "haiku"
    assert cascade_attempt.agent == AGENT
    assert primary.agent == AGENT


def test_agent_file_loads_a_definition_relative_to_the_mission_file(tmp_path):
    agent_file = tmp_path / "reviewer.json"
    agent_file.write_text(json.dumps(AGENT))
    raw = {
        "cwd": "/tmp",
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "A", "agent_file": "reviewer.json"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[0].agent == AGENT


def test_agent_and_agent_file_are_mutually_exclusive(tmp_path):
    agent_file = tmp_path / "reviewer.json"
    agent_file.write_text(json.dumps(AGENT))
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "a",
                "fleet": "claude",
                "prompt": "A",
                "agent": AGENT,
                "agent_file": "reviewer.json",
            }
        ],
    }
    with pytest.raises(MissionInvalid, match="agent and agent_file are mutually exclusive"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_review_lane_agent_with_write_tools_is_refused_naming_the_lane(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "critic",
                "fleet": "claude",
                "mode": "read",
                "stage": "review",
                "prompt": "R",
                "agent": {**AGENT, "tools": ["Read", "Edit", "Bash"]},
            }
        ],
    }
    with pytest.raises(
        MissionInvalid, match=r"lane 'critic': a read lane's persona may not carry write tools"
    ) as exc_info:
        mission_from_dict(raw, base_dir=tmp_path)
    assert "Edit" in str(exc_info.value) and "Bash" in str(exc_info.value)


def test_review_lane_agent_with_only_read_tools_is_fine(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "critic",
                "fleet": "claude",
                "mode": "read",
                "stage": "review",
                "prompt": "R",
                "agent": AGENT,
            }
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[0].agent == AGENT


def test_review_lane_agent_with_non_list_tools_is_mission_invalid_not_a_crash(tmp_path):
    # Regression: `set(attempt.agent["tools"]) & _AGENT_WRITE_TOOLS` in the
    # review-stage check ran before `Spec.validate`'s own shape check, so a
    # malformed `tools` (not a list) raised a bare TypeError instead of
    # MissionInvalid naming the lane.
    raw = {
        "cwd": "/tmp",
        "lanes": [
            {
                "name": "critic",
                "fleet": "claude",
                "mode": "read",
                "stage": "review",
                "prompt": "R",
                "agent": {**AGENT, "tools": 3},
            }
        ],
    }
    with pytest.raises(MissionInvalid, match="lane 'critic'"):
        mission_from_dict(raw, base_dir=tmp_path)


# --- mission: runtime, report, conductor runs, snapshot ---------------------


def _agent_mission_run(repo, home, fake_fleet, tmp_path):
    fake_fleet(session_id=None)
    raw = {
        "cwd": str(repo),
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "A", "agent": AGENT}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    return run_mission(mission, home=home)


def test_mission_dispatch_argv_carries_the_cascaded_agent(repo, home, fake_fleet, tmp_path):
    result = _agent_mission_run(repo, home, fake_fleet, tmp_path)
    run_id = result.lanes[0]["attempts"][0]["run_id"]
    argv = json.loads((home / "runs" / run_id / "argv.json").read_text())
    real = argv[argv.index("fake-fleet") + 1 :]
    assert real[real.index("--agent") + 1] == "reviewer"


def test_report_shows_the_agent_column(repo, home, fake_fleet, tmp_path):
    result = _agent_mission_run(repo, home, fake_fleet, tmp_path)
    report = Path(result.report_path).read_text()
    assert "| agent |" in report
    assert "| reviewer |" in report


def test_conductor_runs_shows_the_agent_name(repo, home, fake_fleet, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _agent_mission_run(repo, home, fake_fleet, tmp_path)
    run_id = result.lanes[0]["attempts"][0]["run_id"]

    assert main(["runs"]) == 0
    runs = json.loads(capsys.readouterr().out)
    by_id = {row["run_id"]: row for row in runs}
    assert by_id[run_id]["agent"] == "reviewer"


def test_agent_survives_a_snapshot_round_trip(tmp_path):
    raw = {
        "cwd": "/tmp",
        "lanes": [{"name": "a", "fleet": "claude", "prompt": "A", "agent": AGENT}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    snapshot = mission.to_dict()
    restored = Mission.from_snapshot(snapshot)
    assert restored.lanes[0].attempts[0].agent == AGENT
    assert restored.to_dict() == snapshot


# --- README ------------------------------------------------------------


def test_readme_documents_inline_agents():
    raw_section = doc_section("### Inline agents: a persona per lane")
    # Prose reflows across lines; collapse whitespace so a phrase that
    # happens to wrap mid-sentence still matches as one contiguous string.
    section = " ".join(raw_section.split())
    assert '"agent": {"name": "reviewer"' in section
    assert "[A-Za-z][A-Za-z0-9_-]{0,63}" in section
    assert "agent is enforceable on the claude fleet only" in section
    assert "fails open on an unknown name" in section
    assert "docs/research/2026-09-06-live-probe-inline-agents.md" in section
    assert "--agents" in section and "--agent-file" in section and "agent_file" in section
    assert "Edit`, `Write`, `NotebookEdit`, or `Bash`" in section
    assert "agent '<name>' not applied: no init event" in section
    assert '"applied": true | false | null' in section
    assert "docs/archive/roadmaps-closed.md" in section and "item D3" in section
