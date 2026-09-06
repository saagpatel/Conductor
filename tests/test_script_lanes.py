"""E6: script lanes -- `fleet: "script"` runs a shell command in the lane's
worktree instead of a model: same receipt, timeout, bytes verdict, gate,
clean gate, commit, stage semantics, and graph ordering as a model lane, and
costs nothing in a way the ledger can verify rather than fail closed.

No fake fleet is needed anywhere here: the command really is `sh`, so a real
dispatch costs nothing and needs no network.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from conductor import golden
from conductor.fleets import FLEETS, VENDORS, DispatchRefused, Spec, build_argv, model_vendor
from conductor.mission import Attempt, MissionInvalid, mission_from_dict, run_mission
from conductor.runner import dispatch


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _spec(repo: Path, **changes) -> Spec:
    fields = {
        "fleet": "script",
        "prompt": "",
        "cwd": str(repo),
        "mode": "write",
        "command": "true",
    }
    fields.update(changes)
    return Spec(**fields)


# --- 1. the fleet and the spec (item 1) -------------------------------------


def test_script_fleet_is_registered_with_one_model_and_vendor():
    fleet = FLEETS["script"]
    assert fleet.binary == "sh"
    assert fleet.cap == "none"
    assert [m.name for m in fleet.models] == ["sh"]
    assert "script" in VENDORS


def test_build_argv_runs_the_command_through_sh():
    argv = build_argv(_spec(Path("/tmp"), command="echo hi"))
    assert argv == ["sh", "-c", "echo hi"]


def test_dispatch_refuses_a_missing_command(tmp_path):
    with pytest.raises(DispatchRefused, match="non-empty command"):
        _spec(tmp_path, command=None).validate()
    with pytest.raises(DispatchRefused, match="non-empty command"):
        _spec(tmp_path, command="   ").validate()


def test_dispatch_refuses_a_model_other_than_sh(tmp_path):
    with pytest.raises(DispatchRefused, match="may not run model"):
        _spec(tmp_path, model="opus").validate()


@pytest.mark.parametrize(
    "field, value",
    [
        ("schema", "/tmp/does-not-matter.json"),
        ("resume", "some-session"),
        ("cap_usd", 1.0),
        ("cap_grace_usd", 0.1),
        ("agent", {"name": "a", "description": "d", "prompt": "p"}),
        ("taint", True),
    ],
)
def test_dispatch_refuses_fields_with_no_tool_surface(tmp_path, field, value):
    if field == "cap_grace_usd":
        kwargs = {"cap_usd": 1.0, field: value}
    else:
        kwargs = {field: value}
    with pytest.raises(DispatchRefused):
        _spec(tmp_path, **kwargs).validate()


def test_dispatch_refuses_a_verdict(tmp_path):
    from conductor.verdicts import Criterion

    with pytest.raises(DispatchRefused):
        _spec(tmp_path, verdict=[Criterion(id="a", question="a?")]).validate()


def test_empty_prompt_is_allowed_on_script_but_not_other_fleets(tmp_path):
    _spec(tmp_path, prompt="").validate()  # does not raise
    with pytest.raises(DispatchRefused, match="empty prompt"):
        Spec(fleet="claude", prompt="", cwd=str(tmp_path)).validate()


def test_outputs_parser_reports_no_session_or_tokens_and_a_fixed_zero_cost():
    from conductor import outputs

    out = outputs.parse("script", "line one\nline two\n")
    assert out.answer == "line one\nline two"
    assert out.usage.total_tokens == 0
    assert out.usage.cost_usd == 0.0
    assert out.session_id is None
    assert out.error is None


def test_model_vendor_script_is_its_own_vendor():
    assert model_vendor("script", None) == "script"


# --- 2. the ledger state: free, not unpriced (item 2 + item 4) -------------


def test_script_dispatch_is_free_not_unpriced(repo, home):
    (repo / "seed.txt").write_text("changed\n")
    result = dispatch(
        _spec(repo, command="echo work >> seed.txt && git add -A && git commit -qm work"),
        home=home,
    )
    assert result.ok is True
    assert result.usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "thinking_tokens": 0,
        "cost_usd": 0.0,
        "cost_basis": None,
        "total_tokens": 0,
    }
    assert result.budget == {
        "cap_usd": None,
        "enforcement": "none",
        "exceeded": False,
        "unpriced": False,
        "free": True,
        "observed_usd": 0.0,
    }
    assert result.failure() is None


# --- 3. load rules (item 3) -------------------------------------------------


def test_script_lane_needs_a_command(tmp_path):
    raw = {"prompt": "x", "lanes": [{"name": "a", "fleet": "script"}]}
    with pytest.raises(MissionInvalid, match="needs 'command'"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_command_on_a_non_script_fleet_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [{"name": "a", "fleet": "claude", "command": "true"}],
    }
    with pytest.raises(MissionInvalid, match="command may only be set on a script attempt"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_script_lane_resume_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "a", "fleet": "claude", "mode": "read"},
            {"name": "b", "fleet": "script", "command": "true", "needs": ["a"], "resume": "a"},
        ],
    }
    with pytest.raises(MissionInvalid, match="a script lane may not set resume"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_resuming_a_script_lane_from_elsewhere_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "a", "fleet": "script", "command": "true"},
            {"name": "b", "fleet": "claude", "needs": ["a"], "resume": "a"},
        ],
    }
    with pytest.raises(MissionInvalid, match="holds no session to resume"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_script_lane_cascade_true_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [{"name": "a", "fleet": "script", "command": "true", "cascade": True}],
    }
    with pytest.raises(MissionInvalid, match="a script lane may not set cascade"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_a_mission_wide_cascade_never_attaches_to_a_script_lane(tmp_path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "claude", "model": "haiku"},
        "lanes": [{"name": "a", "fleet": "script", "command": "true"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    lane = mission.lanes[0]
    assert lane.script is True
    assert len(lane.attempts) == 1
    assert lane.attempts[0].fleet == "script"


def test_script_lane_taint_is_refused(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [{"name": "a", "fleet": "script", "command": "true", "taint": True}],
    }
    with pytest.raises(MissionInvalid, match="a script lane may not set taint"):
        mission_from_dict(raw, base_dir=tmp_path)


@pytest.mark.parametrize(
    "field, value",
    [
        ("effort", "hard"),
        ("model", "sh"),
        ("cap_usd", 1.0),
        ("cap_grace_usd", 0.1),
        ("schema", None),
        ("verdict", ["a"]),
        ("agent", {"name": "a", "description": "d", "prompt": "p"}),
    ],
)
def test_script_attempt_denies_model_shaped_fields(tmp_path, field, value):
    lane: dict = {"name": "a", "fleet": "script", "command": "true"}
    if field == "schema":
        schema_path = tmp_path / "schema.json"
        schema_path.write_text("{}")
        lane["schema"] = str(schema_path)
    else:
        lane[field] = value
    raw = {"prompt": "x", "lanes": [lane]}
    with pytest.raises(MissionInvalid, match=f"may not set {field}"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_mission_wide_cap_usd_default_does_not_reach_a_script_attempt(tmp_path):
    raw = {
        "prompt": "x",
        "cap_usd": 5.0,
        "lanes": [
            {"name": "a", "fleet": "claude"},
            {"name": "b", "fleet": "script", "command": "true"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    by_name = {lane.name: lane for lane in mission.lanes}
    assert by_name["a"].attempts[0].cap_usd == 5.0
    assert by_name["b"].attempts[0].cap_usd is None
    # And Attempt.spec() forces it off regardless, as a second line of defense.
    assert by_name["b"].attempts[0].spec(str(tmp_path)).cap_usd is None


def test_script_lane_mode_defaults_to_write_and_may_be_read(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "a", "fleet": "script", "command": "true"},
            {"name": "b", "fleet": "script", "command": "true", "mode": "read"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    by_name = {lane.name: lane for lane in mission.lanes}
    assert by_name["a"].attempts[0].mode == "write"
    assert by_name["b"].attempts[0].mode == "read"


def test_script_lane_prompt_is_optional(tmp_path):
    raw = {"lanes": [{"name": "a", "fleet": "script", "command": "true"}]}
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[0].prompt == ""


def test_a_model_lane_may_fall_back_to_a_script_attempt(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "name": "a",
                "fleet": "claude",
                "fallback": [{"fleet": "script", "command": "true"}],
            }
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    lane = mission.lanes[0]
    assert lane.script is False
    assert lane.attempts[1].fleet == "script"
    assert lane.attempts[1].command == "true"


def test_policy_may_name_script_as_an_allowed_vendor(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"build": {"vendors": ["script"]}},
        "lanes": [{"name": "a", "fleet": "script", "command": "true", "stage": "build"}],
    }
    mission_from_dict(raw, base_dir=tmp_path)  # does not raise


def test_policy_refuses_a_script_lane_off_policy(tmp_path):
    raw = {
        "prompt": "x",
        "policy": {"build": {"vendors": ["anthropic"]}},
        "lanes": [{"name": "a", "fleet": "script", "command": "true", "stage": "build"}],
    }
    with pytest.raises(MissionInvalid, match="is on vendor 'script'"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_hygiene_flags_a_script_review_lane_over_a_script_build_lane(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [
            {"name": "build", "fleet": "script", "command": "true", "stage": "build"},
            {
                "name": "review",
                "fleet": "script",
                "command": "true",
                "mode": "read",
                "stage": "review",
                "base": "build",
                "needs": ["build"],
            },
        ],
    }
    with pytest.raises(MissionInvalid, match="both on vendor 'script'"):
        mission_from_dict(raw, base_dir=tmp_path)


# --- 4. the run and the receipt (item 4) ------------------------------------


def test_write_build_lane_lands_a_commit_and_reads_free(repo, home):
    mission = mission_from_dict(
        {
            "name": "script-build",
            "prompt": "x",
            "max_cost_usd": 5.0,
            "lanes": [
                {
                    "name": "build",
                    "fleet": "script",
                    "stage": "build",
                    "command": "echo work >> seed.txt && git add -A && git commit -qm work",
                }
            ],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    lane = next(iter(result.lanes))
    attempt = lane["attempts"][0]
    assert attempt["fleet"] == "script"
    assert attempt["free"] is True
    assert attempt["unpriced"] is False
    assert attempt["cost_usd"] == 0.0
    assert lane["clean"] is True
    assert result.budget["spent_usd"] == 0.0
    assert result.budget["unverifiable"] is False
    assert result.budget["unpriced_dispatches"] == 0
    receipt = json.loads(Path(result.mission_dir, "lanes", "build.json").read_text())
    assert receipt["attempts"][0]["free"] is True


def test_read_review_lane_answer_is_stdout_with_no_diff(repo, home):
    result = dispatch(
        _spec(repo, mode="read", command="printf 'finding one\\nfinding two\\n'"),
        home=home,
    )
    assert result.ok is True
    assert Path(result.answer_path).read_text() == "finding one\nfinding two"
    assert result.diff_path is None


def test_read_lane_that_writes_a_file_fails(repo, home):
    result = dispatch(_spec(repo, mode="read", command="echo x > new.txt"), home=home)
    assert result.ok is False
    assert "read dispatch moved bytes" in result.failure()


def test_write_lane_that_moves_no_bytes_fails(repo, home):
    result = dispatch(_spec(repo, mode="write", command="true"), home=home)
    assert result.ok is False
    assert "moved no bytes" in result.failure()


def test_non_zero_exit_fails_as_exit_code_n(repo, home):
    result = dispatch(_spec(repo, mode="read", command="exit 7"), home=home)
    assert result.exit_code == 7
    assert result.ok is False
    assert result.failure() == "exit code 7"


def test_prompt_reaches_stdin(repo, home):
    result = dispatch(
        _spec(repo, mode="write", prompt="hello from the prompt", command="cat > stdin.txt"),
        home=home,
    )
    assert result.ok is True
    assert (repo / "stdin.txt").read_text() == "hello from the prompt"


def test_empty_prompt_closes_stdin_at_once(repo, home):
    result = dispatch(
        _spec(repo, mode="read", prompt="", command="cat > /dev/null; echo done"),
        home=home,
    )
    assert result.ok is True
    assert result.exit_code == 0


def test_fix_stage_script_lane_obeys_the_reproduce_gate(repo, home, git_out):
    (repo / "app.txt").write_text("bad\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "app.txt")
    base = _git(repo, "rev-parse", "HEAD")
    check_script = (
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('app.txt').read_text() == 'good\\n' else 1)\n"
    )
    fix_cmd = (
        "echo good > app.txt && mkdir -p tests && printf '%s' "
        + shlex.quote(check_script)
        + " > tests/check.py"
    )
    result = dispatch(
        _spec(repo, stage="fix", test_policy="allow", command=fix_cmd),
        home=home,
        test_command="python tests/check.py",
        commit_message="fix: address review",
    )
    assert result.reproduce["verdict"] == "reproduced"
    assert result.ok is True
    assert result.commit["committed"] is True
    assert git_out(repo, "rev-parse", "HEAD") != base


def test_ceilings_are_zero_on_the_spec_the_runner_builds():
    attempt = Attempt(fleet="script", prompt="", command="true")
    spec = attempt.spec("/tmp")
    assert spec.stall_timeout == 0
    assert spec.loop_limit == 0
    assert spec.max_tool_calls == 0
    assert spec.tool_idle_timeout == 0


def test_no_breaker_block_on_a_script_receipt(repo, home):
    result = dispatch(_spec(repo, mode="read", command="true"), home=home)
    assert result.breaker is None


# --- 5. record and replay (item 4/5 pin) ------------------------------------


def test_record_and_replay_a_script_lane(repo, home, tmp_path):
    mission = mission_from_dict(
        {
            "name": "script-golden",
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "script",
                    "stage": "build",
                    "command": "echo work >> seed.txt && git add -A && git commit -qm work",
                }
            ],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)
    assert result.ok is True
    fixture = golden.record(Path(result.mission_dir), tmp_path / "fixture", home=home)
    assert golden.check(fixture) == []
