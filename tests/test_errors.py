"""C5: structured error kinds, fallback.on, and retry.

`error_kind` is exercised on hand-built `Result`s first, since every kind and
precedence rule can be pinned there without spending on a fake dispatch; one
live-shaped dispatch (a fake Claude envelope) then proves the wiring actually
reaches a real `Result`. The mission-level pieces (`fallback.on`, `retry`,
`MissionResult.errors`) are exercised through `run_mission`, faking fleets at
the argv boundary the way `test_mission.py` and `test_cascade.py` do.
"""

from __future__ import annotations

import json
import shlex
import threading
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.errors import KINDS, error_kind
from conductor.fleets import Spec
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission
from conductor.runner import Result, dispatch


@pytest.fixture(autouse=True)
def _fresh_stop():
    runner_mod.clear_stop()
    yield
    runner_mod.clear_stop()


def _result(**overrides) -> Result:
    base: dict = dict(
        run_id="r1",
        fleet="claude",
        model="m",
        effort="standard",
        mode="read",
        cwd="/tmp/repo",
        timeout=60,
        exit_code=0,
        timed_out=False,
        duration_s=1.0,
        run_dir="/tmp/r1",
        stdout_path="/tmp/r1/stdout.log",
        stderr_path="/tmp/r1/stderr.log",
        tail="",
        spawned=True,
        answer_path="/tmp/r1/answer.txt",
        git_verdict={"checked": True, "no_op": True},
    )
    base.update(overrides)
    return Result(**base)


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test dispatch", cwd=str(repo))
    base.update(kw)
    return Spec(**base)


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


# --- error_kind on hand-built Results ---------------------------------------


def test_ok_result_has_no_kind():
    assert error_kind(_result()) is None


@pytest.mark.parametrize(
    "name,overrides,expected",
    [
        (
            "interrupted flag",
            dict(interrupted=True, error="interrupted: stop requested; process group killed"),
            "interrupted",
        ),
        (
            "interrupted via _bail's error text (no flag set)",
            dict(spawned=False, error="interrupted: stop requested during setup; killed"),
            "interrupted",
        ),
        (
            "cancelled",
            dict(cancelled=True, error="cancelled: another lane already passed"),
            "cancelled",
        ),
        ("cap: budget exceeded", dict(budget={"exceeded": True, "cap_usd": 1.0}), "cap"),
        ("cap: budget unpriced", dict(budget={"unpriced": True}), "cap"),
        (
            "cap kill that also timed out is cap, not timeout",
            dict(budget={"exceeded": True, "cap_usd": 1.0}, timed_out=True),
            "cap",
        ),
        (
            "breaker",
            dict(breaker={"tripped": "looping: x repeated 6 times"}, error="looping: x; killed"),
            "breaker",
        ),
        ("timeout", dict(timed_out=True), "timeout"),
        ("setup timed out", dict(spawned=False, error="setup timed out"), "setup"),
        ("setup failed", dict(spawned=False, error="setup failed: exit 2"), "setup"),
        (
            "refused: conductor declined to spawn",
            dict(spawned=False, error="isolation failed: worktree add failed"),
            "refused",
        ),
        (
            "refused: an include refusal",
            dict(
                spawned=False,
                error="include: secret.env is tracked; the worktree already has it",
            ),
            "refused",
        ),
        ("rate_limit", dict(fleet_error="Rate limit exceeded, please retry"), "rate_limit"),
        (
            "a fleet error mentioning a rate limit is rate_limit, not fleet_error",
            dict(fleet_error="upstream request failed (429 too many requests)"),
            "rate_limit",
        ),
        ("transport", dict(fleet_error="ECONNRESET while streaming the response"), "transport"),
        (
            "transport: conductor's own cut-short marker",
            dict(fleet_error="claude stream ended without a result event"),
            "transport",
        ),
        (
            "refusal: claude error_ subtype with refusal wording",
            dict(
                fleet="claude",
                fleet_error="I must refuse this request",
                fleet_status="error_during_execution",
            ),
            "refusal",
        ),
        (
            "refusal: universal 'I can't help' text on a non-claude fleet",
            dict(fleet="cursor", fleet_error="I can't help with that."),
            "refusal",
        ),
        (
            "claude budget/turn-limit subtypes are not refusals",
            dict(
                fleet="claude",
                fleet_error="Reached maximum budget ($0.01)",
                fleet_status="error_max_budget_usd",
            ),
            "fleet_error",
        ),
        ("fleet_error: generic", dict(fleet_error="something went wrong upstream"), "fleet_error"),
        ("exit", dict(exit_code=3), "exit"),
        (
            "gate",
            dict(
                exit_code=0,
                tests={"ran": True, "exit_code": 1, "timed_out": False, "interrupted": False},
            ),
            "gate",
        ),
        (
            "no_op",
            dict(mode="write", exit_code=0, git_verdict={"checked": True, "no_op": True}),
            "no_op",
        ),
        (
            "read_moved_bytes",
            dict(mode="read", exit_code=0, git_verdict={"checked": True, "no_op": False}),
            "read_moved_bytes",
        ),
        (
            "no_answer",
            dict(
                mode="read",
                exit_code=0,
                answer_path=None,
                git_verdict={"checked": True, "no_op": True},
            ),
            "no_answer",
        ),
        (
            "commit",
            dict(
                mode="write",
                exit_code=0,
                git_verdict={"checked": True, "no_op": False},
                commit={"committed": False, "reason": "gate failed: exit 1"},
            ),
            "commit",
        ),
        (
            "unknown: a generic error text that matches nothing structured",
            dict(
                mode="write",
                exit_code=0,
                error="verdict invalid: schema mismatch",
                git_verdict={},
            ),
            "unknown",
        ),
    ],
)
def test_error_kind_covers_every_kind_and_precedence(name, overrides, expected):
    result = _result(**overrides)
    assert result.ok is False, f"{name}: fixture must actually be a failure"
    assert error_kind(result) == expected, name
    assert expected in KINDS


def test_a_breaker_kill_under_a_generous_cap_is_breaker_not_cap():
    """`budget.settle()` (budget.py) sets `exceeded=True` for *any* kill,
    breaker or cap watcher alike (`killed=capped or breaker_reason is not
    None` in runner.py), so a breaker-killed run with a cap nowhere near hit
    still carries `budget["exceeded"] is True` (see
    test_breakers.py::test_stall_dispatch_is_killed_priced_receipted_and_never_gated,
    `cap_usd=10.0`, `budget["exceeded"] is True`, cost far under $10). `cap`
    must mean the run was actually stopped for its cap, not merely that some
    other breaker-triggered kill also set the shared flag."""
    result = _result(
        breaker={"tripped": "stalled: no output for 2s"},
        budget={"exceeded": True, "cap_usd": 10.0, "observed_usd": 0.02},
        error="stalled: no output for 2s; process group killed",
    )
    assert result.ok is False
    assert error_kind(result) == "breaker"


def test_a_genuine_cap_kill_is_still_cap_even_with_a_breaker_dict_present():
    """The fix for the case above must not swallow a real cap kill: when the
    observed spend actually cleared the cap, `cap` still wins over `breaker`,
    matching the spec's own 'a cap kill that also timed out is cap' rule."""
    result = _result(
        breaker={"tripped": "stalled: no output for 2s"},
        budget={"exceeded": True, "cap_usd": 1.0, "observed_usd": 5.0},
        error="over budget ($5.00 > $1.00); process group killed",
    )
    assert error_kind(result) == "cap"


def test_kinds_are_exhaustive_over_the_documented_order():
    assert KINDS == (
        "interrupted",
        "cancelled",
        "cap",
        "breaker",
        "timeout",
        "setup",
        "refused",
        "agent",
        "deliverable",
        "taint",
        "rate_limit",
        "transport",
        "refusal",
        "fleet_error",
        "exit",
        "gate",
        "gate_test_surface",
        "no_op",
        "read_moved_bytes",
        "no_answer",
        "commit",
        "unknown",
    )


def test_error_kind_from_a_live_rate_limited_claude_dispatch(repo, home, fake_fleet):
    fake_fleet(
        [
            "sh",
            "-c",
            "printf '%s\\n' "
            "'{\"type\":\"result\",\"subtype\":\"error_during_execution\",\"is_error\":true,"
            "\"result\":\"\",\"error\":\"Rate limit exceeded, please retry later\"}'; exit 1",
        ]
    )
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.ok is False
    assert result.fleet_error and "rate limit" in result.fleet_error.lower()
    assert error_kind(result) == "rate_limit"
    assert result.summary()["kind"] == "rate_limit"
    assert result.to_dict()["kind"] == "rate_limit"


def test_ok_dispatch_carries_a_null_kind(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo 'here is my analysis'"])
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.ok is True
    assert result.summary()["kind"] is None
    assert result.to_dict()["kind"] is None


# --- fallback.on -------------------------------------------------------------


def test_fallback_on_names_unknown_kind_refused_at_load(repo, tmp_path):
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude", "on": ["not_a_kind"]}]}],
    }
    with pytest.raises(MissionInvalid, match="on names unknown kind"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_fallback_on_skips_a_fallback_that_does_not_handle_the_kind(
    repo, home, monkeypatch, tmp_path
):
    """codex's primary exits 0 having changed nothing (kind: no_op); the
    first fallback only answers `gate` and must be passed over; the second
    (no `on`, so it answers everything) actually lands the work."""
    fake_fleets(
        monkeypatch,
        {
            "codex": ["sh", "-c", "echo 'Done! Refactored everything.'"],
            "cursor": ["sh", "-c", "exit 1"],
            "claude": ["sh", "-c", "echo work > real.txt && git add -A && git commit -qm work"],
        },
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "lanes": [
            {
                "fleet": "codex",
                "fallback": [
                    {"fleet": "cursor", "on": ["gate"]},
                    {"fleet": "claude"},
                ],
            }
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    lane = result.lanes[0]
    assert lane["ok"] is True
    assert [a["attempt"] for a in lane["attempts"]] == ["codex", "claude"]
    assert lane["kinds"] == ["no_op", None]
    assert any(
        "skipped fallback cursor" in note and "does not handle no_op" in note
        for note in result.notes
    ), result.notes


def test_fallback_on_snapshot_round_trips(repo, home, tmp_path):
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude", "on": ["gate", "no_op"]}]}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.lanes[0].attempts[1].on == ["gate", "no_op"]
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    assert reloaded.to_dict() == mission.to_dict() == snapshot


# --- retry -------------------------------------------------------------------


def test_retry_validation(repo, tmp_path):
    base = {"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "codex"}]}
    with pytest.raises(MissionInvalid, match="retry.attempts is required"):
        mission_from_dict({**base, "retry": {"kinds": ["timeout"]}}, base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match="retry.attempts must be an integer from 1 to 5"):
        mission_from_dict({**base, "retry": {"attempts": 6}}, base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match="retry.kinds names unknown kind"):
        mission_from_dict(
            {**base, "retry": {"attempts": 1, "kinds": ["bogus"]}}, base_dir=tmp_path
        )
    with pytest.raises(MissionInvalid, match="retry.backoff_s must be at least 0"):
        mission_from_dict(
            {**base, "retry": {"attempts": 1, "backoff_s": -1}}, base_dir=tmp_path
        )
    mission = mission_from_dict({**base, "retry": {"attempts": 2}}, base_dir=tmp_path)
    assert mission.retry == {"kinds": ["rate_limit", "transport"], "attempts": 2, "backoff_s": 0.0}


_TRANSPORT_ENVELOPE = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"result":"","error":"ECONNRESET while streaming"}'
)
_OK_ENVELOPE = (
    '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
    '"usage":{"inputTokens":1,"outputTokens":1}}'
)


def _flaky_claude_script(counter: Path, fail_times: int) -> str:
    """Fails with a transport-shaped error the first `fail_times` calls
    (tracked in a counter file so every dispatch of the same argv can tell
    which try it is), then succeeds."""
    return f"""
n=$(cat {counter})
n=$((n + 1))
echo $n > {counter}
if [ "$n" -le {fail_times} ]; then
  printf '%s\\n' {shlex.quote(_TRANSPORT_ENVELOPE)}
  exit 1
else
  printf '%s\\n' {shlex.quote(_OK_ENVELOPE)}
  exit 0
fi
"""


def test_retry_same_attempt_on_transient_kind_then_succeeds(repo, home, monkeypatch, tmp_path):
    counter = tmp_path / "count"
    counter.write_text("0")
    script = _flaky_claude_script(counter, fail_times=2)
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", script])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "retry": {"kinds": ["transport"], "attempts": 2, "backoff_s": 0},
        "lanes": [{"fleet": "claude"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    lane = result.lanes[0]
    assert lane["ok"] is True
    assert len(lane["attempts"]) == 3
    assert lane["kinds"] == ["transport", "transport", None]
    first_run_id = lane["attempts"][0]["run_id"]
    assert lane["attempts"][1]["retry_of"] == first_run_id and lane["attempts"][1]["retry"] == 1
    assert lane["attempts"][2]["retry_of"] == first_run_id and lane["attempts"][2]["retry"] == 2
    assert result.errors == {"transport": 2}


def test_retry_stop_during_backoff_ends_it_as_interrupted(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: [
            "sh",
            "-c",
            "printf '%s\\n' "
            "'{\"type\":\"result\",\"subtype\":\"error_during_execution\",\"is_error\":true,"
            "\"result\":\"\",\"error\":\"ECONNRESET while streaming\"}'; exit 1",
        ],
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "retry": {"kinds": ["transport"], "attempts": 3, "backoff_s": 5},
        "lanes": [{"fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    threading.Timer(1.0, runner_mod.request_stop).start()
    result = run_mission(mission, home=home)
    lane = result.lanes[0]
    assert result.interrupted is True
    assert lane["ok"] is False
    assert lane["attempts"][-1]["kind"] == "interrupted"
    assert lane["kinds"][-1] == "interrupted"
    assert len(lane["attempts"]) == 1  # the retry backoff was cut short before redispatching


def test_retry_dry_run_does_not_retry(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "exit 1"])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "retry": {"kinds": ["transport"], "attempts": 3, "backoff_s": 0},
        "lanes": [{"fleet": "claude"}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, dry_run=True)
    assert len(result.lanes[0]["attempts"]) == 1


# --- errors on the mission result, the report, and the CLI -------------------


def test_mission_errors_summary_report_line_and_cli(repo, home, monkeypatch, tmp_path, capsys):
    fake_fleets(
        monkeypatch,
        {
            "codex": ["sh", "-c", "exit 3"],
            "claude": ["sh", "-c", "echo work > r.txt && git add -A && git commit -qm w"],
        },
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "write",
        "commit": "feat: x",
        "lanes": [{"fleet": "codex", "fallback": [{"fleet": "claude"}]}],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.errors == {"exit": 1}
    assert result.summary()["mission_id"] == result.mission_id  # sanity: summary still works

    report = Path(result.report_path).read_text()
    assert "Errors: exit x1" in report
    assert "(kind: exit)" in report

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["missions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(item for item in rows if item["mission_id"] == result.mission_id)
    assert row["errors"] == {"exit": 1}


def test_conductor_runs_shows_the_kind(repo, home, monkeypatch, capsys):
    fake_fleets(monkeypatch, {"claude": ["sh", "-c", "exit 3"]})
    dispatch(spec_for(repo, mode="read"), home=home)

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["runs"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["kind"] == "exit"


def test_conductor_runs_recomputes_kind_for_a_pre_c5_receipt(repo, home, monkeypatch, capsys):
    """`kind` is documented as 'computed, not stored, so an old receipt read
    back still classifies' -- but `cmd_runs` (cli.py) reads it with a plain
    `data.get("kind")` off the JSON on disk, so a receipt written before this
    field existed (no `kind` key at all) must still classify, not read back
    as `null` for a dispatch that was not ok."""
    fake_fleets(monkeypatch, {"claude": ["sh", "-c", "exit 3"]})
    dispatch(spec_for(repo, mode="read"), home=home)

    run_dir = next((home / "runs").iterdir())
    result_file = run_dir / "result.json"
    data = json.loads(result_file.read_text())
    assert data["ok"] is False
    del data["kind"]  # simulate a receipt written before C5 shipped
    result_file.write_text(json.dumps(data))

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["runs"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["kind"] == "exit"


# --- README ------------------------------------------------------------------


def test_readme_documents_error_kinds_fallback_on_and_retry():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("### Structured error kinds", 1)[1].split(
        "\n## ", 1
    )[0]
    assert (
        "interrupted, cancelled, cap, breaker, timeout, setup, refused, agent,"
        in section
    )
    assert (
        "transport, refusal, fleet_error, exit, gate, gate_test_surface, no_op," in section
    )
    assert "read_moved_bytes, no_answer, commit, unknown" in section
    assert "cap kill that also timed out is `cap`, not `timeout`" in section
    assert "rate limit is `rate_limit`, not `fleet_error`" in section
    assert "I can't help` or `I cannot help`" in section
    assert "skipped fallback <label>: does not handle <kind>" in section
    assert '"on": ["rate_limit", "transport"]' in section
    assert (
        '{"retry": {"kinds": ["rate_limit", "transport"], "attempts": 2, "backoff_s": 1}}'
        in section
    )
    assert "attempts` (1 to 5) is required" in section
    assert "retry_of` naming the first" in section
    assert "A dry run never retries." in section
    assert "Errors: <kind> x<n>, ..." in section
    assert "(kind: <kind>)" in section
    assert "docs/ROADMAP-2026-09.md` item C5" in section
    assert "error_handlers" in section
