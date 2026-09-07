"""Dispatch mechanics, exercised against real subprocesses.

The tests that matter here are the ones with a named failure they prevent:
exit 0 with an empty diff reading as success, a timeout leaving a live
grandchild behind, and an agent's transcript flooding the orchestrator.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import mission_from_dict, run_mission
from conductor.runner import dispatch
from conductor.verdicts import Criterion


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test dispatch", cwd=str(repo))
    base.update(kw)
    return Spec(**base)


def test_dry_run_spawns_nothing_and_records_the_argv(repo: Path, home: Path):
    result = dispatch(spec_for(repo, fleet="codex", model="sol"), dry_run=True, home=home)
    argv = json.loads((Path(result.run_dir) / "argv.json").read_text())
    assert argv[0] == "codex"
    assert "gpt-5.6-sol" in argv
    assert result.exit_code is None
    assert not (Path(result.run_dir) / "stdout.log").exists()


def test_exit_zero_with_no_bytes_moved_is_not_success(repo, home, fake_fleet):
    """The failure this whole module exists to catch: a run that reports
    success, exits 0, and changed nothing."""
    fake_fleet(["sh", "-c", "echo 'Done! All tests pass.'; exit 0"])
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.exit_code == 0
    assert result.git_verdict["no_op"] is True
    assert result.ok is False
    assert any("moved no bytes" in n for n in result.git_verdict["notes"])


def test_a_write_that_lands_a_commit_is_success(repo, home, fake_fleet):
    fake_fleet(
        ["sh", "-c", "echo work > new.txt && git add -A && git commit -qm 'agent work'"],
    )
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.exit_code == 0
    assert result.git_verdict["commits_added"] == 1
    assert result.git_verdict["files_changed"] == 1
    assert result.git_verdict["no_op"] is False
    assert result.ok is True


def test_a_dirty_tree_counts_as_work_even_without_a_commit(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo uncommitted > scratch.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.git_verdict["dirty_delta"] == 1
    assert result.git_verdict["no_op"] is False
    assert result.ok is True


def test_editing_an_already_dirty_file_is_not_a_no_op(repo, home, fake_fleet):
    (repo / "seed.txt").write_text("dirty before\n")
    fake_fleet(["sh", "-c", "printf 'dirty after\\n' > seed.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.git_verdict["dirty_delta"] == 0
    assert result.git_verdict["no_op"] is False
    assert result.ok is True


def test_read_mode_does_not_require_bytes_to_move(repo, home, fake_fleet):
    """Research dispatches are supposed to change nothing."""
    fake_fleet(["sh", "-c", "echo 'here is my analysis'; exit 0"])
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.git_verdict["no_op"] is True
    assert result.ok is True


def test_a_read_dispatch_that_edited_the_tree_is_not_ok(repo, home, fake_fleet):
    """Caught live 2026-09-03: agy created a file in read mode, because its
    plan mode is silently ignored alongside --disable-slash-commands. Whatever
    a fleet's flags promise, the bytes decide."""
    fake_fleet(["sh", "-c", "echo leaked > leak.txt; echo 'analysis done'"])
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.exit_code == 0
    assert result.git_verdict["dirty_delta"] == 1
    assert result.ok is False
    assert result.summary()["failure"] == "read dispatch moved bytes"


def test_a_read_dispatch_that_changes_an_already_dirty_file_moves_bytes(repo, home, fake_fleet):
    (repo / "seed.txt").write_text("dirty before\n")
    fake_fleet(["sh", "-c", "printf 'dirty after\\n' > seed.txt; echo analysis"])
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.git_verdict["dirty_delta"] == 0
    assert result.summary()["failure"] == "read dispatch moved bytes"


def test_commit_on_a_dirty_nonisolated_checkout_is_refused_before_spawn(
    repo, home, fake_fleet, git_out
):
    (repo / "seed.txt").write_text("operator edit\n")
    (repo / "operator.txt").write_text("untracked\n")
    before = git_out(repo, "status", "--porcelain")
    fake_fleet(["sh", "-c", "echo spawned > spawned.txt"])
    result = dispatch(
        spec_for(repo, mode="write"), home=home, commit_message="feat: unsafe sweep"
    )
    assert result.spawned is False and result.exit_code is None
    assert result.error == "commit refused: the checkout has uncommitted changes; use --isolate"
    assert git_out(repo, "status", "--porcelain") == before
    assert not (repo / "spawned.txt").exists()


def test_commit_from_a_dirty_checkout_proceeds_when_isolated(repo, home, fake_fleet):
    (repo / "seed.txt").write_text("operator edit\n")
    fake_fleet(["sh", "-c", "echo isolated > isolated.txt"])
    result = dispatch(
        spec_for(repo, mode="write"),
        home=home,
        commit_message="feat: isolated",
        isolate=True,
    )
    assert result.spawned is True and result.ok is True
    assert (repo / "seed.txt").read_text() == "operator edit\n"
    assert not (repo / "isolated.txt").exists()


def test_nonzero_exit_is_failure_and_the_tail_comes_from_stderr(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo 'boom' >&2; exit 7"])
    result = dispatch(spec_for(repo), home=home)
    assert result.exit_code == 7
    assert result.ok is False
    assert "boom" in result.tail


def test_output_streams_to_disk_and_never_through_the_summary(repo, home, fake_fleet):
    """A verbose agent must not be able to flood the orchestrator."""
    fake_fleet(["sh", "-c", "for i in $(seq 1 5000); do echo line-$i; done"])
    result = dispatch(spec_for(repo), home=home)
    on_disk = Path(result.stdout_path).read_text()
    assert on_disk.count("\n") == 5000
    assert len(result.tail.splitlines()) <= 20
    assert "line-1\n" not in json.dumps(result.summary())


def test_timeout_kills_the_whole_process_group(repo, home, fake_fleet):
    """An orphaned grandchild still editing the repo would race whatever runs
    next, so the timeout must kill the tree, not just the child."""
    marker = repo / "grandchild.pid"
    script = f"sh -c 'echo $$ > {marker}; sleep 60' & sleep 60"
    fake_fleet(["sh", "-c", script])

    started = time.monotonic()
    result = dispatch(spec_for(repo, timeout=2), home=home)
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.ok is False
    assert elapsed < 30
    # Give the kill a moment to land, then prove the grandchild is gone.
    time.sleep(0.5)
    pid = int(marker.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, signal.SIGKILL if False else 0)


def test_a_grandchild_that_outlives_the_fleet_is_reaped(repo, home, fake_fleet):
    """agy's print timeout returns while its shell child keeps running and
    keeps editing the tree (seen live 2026-09-03). The fleet exiting cleanly
    must still leave no process behind."""
    marker = repo / "straggler.pid"
    # $! is the background job's own pid; a subshell's $$ would be the parent's.
    fake_fleet(["sh", "-c", f"sleep 60 & echo $! > {marker}; sleep 0.2; exit 0"])
    result = dispatch(spec_for(repo), home=home)
    assert result.exit_code == 0
    pid = int(marker.read_text().strip())
    # SIGKILL is asynchronous; give it a moment, then the sleeper must be gone
    # (a killed-but-unreaped child of a dead parent is reparented and reaped
    # by launchd/init, so signal 0 raises rather than finding a zombie).
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_same_second_same_prompt_same_fleet_get_distinct_run_ids(
    repo, home, fake_fleet, monkeypatch
):
    """Two lanes of one mission can collide on the timestamped id; the run
    directory (and so the worktree branch) must still be unique."""
    fake_fleet(["sh", "-c", "echo hi"])
    frozen = runner_mod.datetime.now(runner_mod.UTC)

    class FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(runner_mod, "datetime", FrozenDatetime)
    a = dispatch(spec_for(repo), home=home)
    b = dispatch(spec_for(repo), home=home)
    assert a.run_id != b.run_id
    assert b.run_id == f"{a.run_id}-2"
    assert Path(a.run_dir).is_dir() and Path(b.run_dir).is_dir()


def test_a_missing_binary_is_reported_not_raised(repo, home, fake_fleet):
    fake_fleet(["conductor-no-such-binary-xyz"])
    result = dispatch(spec_for(repo), home=home)
    assert result.ok is False
    assert result.error and "cannot spawn" in result.error


def test_failing_gate_sinks_an_otherwise_successful_run(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo x > f.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home, test_command="exit 1")
    assert result.git_verdict["no_op"] is False
    assert result.tests["exit_code"] == 1
    assert result.ok is False


def test_a_fleet_that_reports_its_own_failure_is_believed_over_exit_zero(repo, home, fake_fleet):
    """agy exits 1 on its print timeout today; if a future version exits 0
    with status ERROR, the status must still sink the run."""
    envelope = '{"status":"ERROR","response":"","error":"timeout waiting for response"}'
    fake_fleet(["sh", "-c", f"echo '{envelope}'; exit 0"])
    result = dispatch(spec_for(repo, fleet="antigravity", mode="read"), home=home)
    assert result.exit_code == 0
    assert result.fleet_status == "ERROR"
    assert result.fleet_error == "timeout waiting for response"
    assert result.ok is False
    assert result.summary()["error"] == "timeout waiting for response"


def test_tokens_without_dollars_are_priced_from_the_table(repo, home, fake_fleet):
    """Cursor and Antigravity report tokens but no cost; Codex reports usage
    only in its event stream. All three must land with a dollar figure."""
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,"result":"PONG",'
        '"usage":{"inputTokens":1000000,"outputTokens":1000000}}'
    )
    fake_fleet(["sh", "-c", f"echo '{envelope}'"])
    result = dispatch(spec_for(repo, fleet="cursor", model="composer-2.5"), home=home)
    assert result.usage["cost_basis"] == "estimated"
    assert result.usage["cost_usd"] == 0.5 + 2.5
    assert result.summary()["cost_usd"] == 3.0


def test_a_reported_cost_is_never_overwritten_by_an_estimate(repo, home, fake_fleet):
    envelope = (
        '{"result":"PONG","total_cost_usd":0.231398,'
        '"usage":{"input_tokens":2,"cache_creation_input_tokens":57836,"output_tokens":5}}'
    )
    fake_fleet(["sh", "-c", f"echo '{envelope}'"])
    result = dispatch(spec_for(repo, fleet="claude", model="haiku"), home=home)
    assert result.usage["cost_basis"] == "reported"
    assert result.usage["cost_usd"] == 0.231398


def test_an_invalid_reported_cost_is_noted_and_only_valid_tokens_are_estimated(
    repo, home, fake_fleet
):
    envelope = (
        '{"result":"PONG","total_cost_usd":-1,'
        '"usage":{"input_tokens":1000,"output_tokens":1000}}'
    )
    fake_fleet(["sh", "-c", f"echo '{envelope}'"])
    result = dispatch(spec_for(repo, fleet="claude", model="haiku"), home=home)
    assert result.usage["cost_basis"] == "estimated"
    assert any("reported cost must be" in note for note in result.git_verdict["notes"])


def test_codex_answer_falls_back_to_its_last_message_file(repo, home, monkeypatch):
    """If the event stream carried nothing, the -o file is the next evidence."""

    def fake_build(spec):
        return ["sh", "-c", f"printf 'PONG-FROM-FILE' > '{spec.last_message}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    result = dispatch(spec_for(repo, fleet="codex"), home=home)
    assert Path(result.answer_path).read_text() == "PONG-FROM-FILE"


def test_a_gate_that_hangs_is_a_failure_not_a_pass(repo, home, fake_fleet, monkeypatch):
    """A timed-out gate has no exit code; 'no exit code' must not read as 0."""
    fake_fleet(["sh", "-c", "echo x > f.txt"])
    monkeypatch.setattr(
        runner_mod,
        "run_tests",
        lambda cwd, cmd, **kw: runner_mod.TestOutcome(ran=True, timed_out=True, tail="timed out"),
    )
    result = dispatch(spec_for(repo, mode="write"), home=home, test_command="sleep 999")
    assert result.tests["timed_out"] is True
    assert result.tests["exit_code"] is None
    assert result.ok is False


def test_the_summary_says_why_a_run_is_not_ok(repo, home, fake_fleet):
    """Seven things can sink a run; the orchestrator should not have to
    reconstruct which one from the raw fields."""
    fake_fleet(["sh", "-c", "echo 'Done!'; exit 0"])
    no_op = dispatch(spec_for(repo, mode="write"), home=home)
    assert no_op.summary()["failure"] == "write dispatch moved no bytes"
    fake_fleet(["sh", "-c", "exit 7"])
    assert dispatch(spec_for(repo), home=home).summary()["failure"] == "exit code 7"
    fake_fleet(["sh", "-c", "echo x > f.txt"])
    gate = dispatch(spec_for(repo, mode="write"), home=home, test_command="exit 3")
    assert gate.summary()["failure"] == "gate exited 3"
    fake_fleet(["sh", "-c", "echo fine"])
    assert dispatch(spec_for(repo), home=home).summary()["failure"] is None


def test_every_run_leaves_an_audit_trail(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo hi"])
    result = dispatch(spec_for(repo), home=home)
    run_dir = Path(result.run_dir)
    for name in ("prompt.txt", "argv.json", "stdout.log", "stderr.log", "result.json"):
        assert (run_dir / name).is_file(), name
    saved = json.loads((run_dir / "result.json").read_text())
    assert saved["run_id"] == result.run_id
    assert saved["ok"] == result.ok


# --- F3: no gate on a read lane that moved no source bytes -------------------


def test_read_lane_with_no_bytes_moved_skips_the_gate(repo, home, fake_fleet):
    """A gate command that would sink the run (`exit 1`) never runs at all:
    the bytes comparison taken before either gate shows a no-op, so both are
    skipped and the receipt says so instead of silently never running."""
    fake_fleet(["sh", "-c", "echo 'here is my analysis'; exit 0"])
    result = dispatch(spec_for(repo, mode="read"), home=home, test_command="exit 1")
    assert result.git_verdict["no_op"] is True
    assert result.tests is None
    assert result.gate == {"skipped": "read lane, source unchanged", "command": "exit 1"}
    assert result.ok is True


def test_read_lane_that_moves_a_file_outside_its_deliverable_is_still_gated(
    repo, home, fake_fleet
):
    """The skip is exactly the read-only check's own exemption: a second file
    beyond the declared deliverable still fails, and the gate still ran."""
    fake_fleet(
        ["sh", "-c", "echo out > report.txt; echo extra > other.txt; echo 'analysis done'"]
    )
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}),
        home=home,
        test_command="exit 0",
    )
    assert result.gate is None
    assert result.tests is not None
    assert result.failure() == "read dispatch moved bytes"


def test_read_lane_with_an_e1_deliverable_and_a_gate_skips_the_gate(repo, home, fake_fleet):
    """Moving exactly the declared deliverable is the other shape the skip
    covers, not just a literal no-op."""
    fake_fleet(["sh", "-c", "echo out > report.txt; echo 'analysis done'"])
    result = dispatch(
        spec_for(repo, mode="read", deliverable={"path": "report.txt"}),
        home=home,
        test_command="exit 1",
    )
    assert result.git_verdict["deliverable_only"] is True
    assert result.tests is None
    assert result.gate == {"skipped": "read lane, source unchanged", "command": "exit 1"}
    assert result.ok is True


def test_read_lane_whose_tree_vanished_is_not_ok(repo, home, fake_fleet):
    """F15: a read lane whose fake fleet deletes the whole working tree must
    not read as a no-op skip -- the tree vanishing is a distinct, always
    failing outcome, not the F3 "source unchanged" shape."""
    fake_fleet(["sh", "-c", f"rm -rf {repo}; exit 0"])
    result = dispatch(spec_for(repo, mode="read"), home=home, test_command="exit 0")
    assert result.ok is False
    assert result.failure() == "the working tree vanished during the dispatch; no work landed"
    assert result.gate != {"skipped": "read lane, source unchanged", "command": "exit 0"}
    assert any("vanished" in n for n in result.git_verdict["notes"])


def test_write_lane_with_the_same_gate_still_runs_it(repo, home, fake_fleet):
    """The skip is read-lane only: a write lane that happens to move no bytes
    still pays for its gate, exactly as before."""
    fake_fleet(["sh", "-c", "echo 'nothing changed'; exit 0"])
    result = dispatch(spec_for(repo, mode="write"), home=home, test_command="exit 1")
    assert result.gate is None
    assert result.tests is not None
    assert result.tests["exit_code"] == 1


def test_report_line_says_gate_skipped_for_a_read_lane(repo, home, monkeypatch, tmp_path):
    def build(spec: Spec) -> list[str]:
        return ["sh", "-c", "echo 'here is my analysis'; exit 0"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "cwd": str(repo),
        "prompt": "SPEC",
        "test": "exit 1",
        "lanes": [{"name": "reader", "fleet": "claude", "mode": "read", "prompt": "R"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True

    report = Path(result.report_path).read_text()
    section = report.split("## Lane `reader`", 1)[1]
    assert "gate skipped (read lane)" in section


# --- D9/D15: a fleet's own malformed output is a receipt, not a crash --------


def test_a_nan_usage_figure_is_dropped_and_the_run_is_still_priced(repo, home, fake_fleet):
    """D9: `json.loads` accepts a bare `NaN`, so a fleet can hand conductor
    one in the middle of the only usage object the run will ever produce.
    Before the guard, `int(nan)` raised after the spend and the run directory
    was left holding nothing but stdout.log."""
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"usage":{"input_tokens":NaN,"output_tokens":1000,"cache_read_input_tokens":5}}'
    )
    fake_fleet(["sh", "-c", f"printf '%s\\n' '{envelope}'; echo work > new.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home)

    assert result.ok is True
    assert result.usage["input_tokens"] == 0  # the unusable figure, dropped
    assert result.usage["output_tokens"] == 1000
    assert result.usage["cache_read_tokens"] == 5
    assert result.usage["cost_usd"] > 0 and result.usage["cost_basis"] == "estimated"
    receipt = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert receipt["usage"]["output_tokens"] == 1000


def test_a_crash_after_the_spend_is_a_receipt_with_the_cost_so_far(
    repo, home, fake_fleet, monkeypatch
):
    """D9: whatever raises while conductor reads a fleet's output, the run
    was still paid for. A receipt with the price and the reason is what
    `spend` and `report` can see; a bare `runs/<id>/stdout.log` is not."""
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"usage":{"input_tokens":1000000,"output_tokens":0},"total_cost_usd":0.25}'
    )
    fake_fleet(["sh", "-c", f"printf '%s\\n' '{envelope}'"])

    def boom(text, criteria):
        raise TypeError("unhashable type: 'list'")

    monkeypatch.setattr(runner_mod, "parse_verdict", boom)
    result = dispatch(
        spec_for(repo, mode="read", verdict=[Criterion(id="c1", question="did the thing?")]),
        home=home,
    )

    assert result.ok is False
    assert result.error.startswith("parse failed: TypeError:")
    assert result.to_dict()["kind"] == "parse"
    receipt = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert receipt["kind"] == "parse" and receipt["ok"] is False
    assert "unhashable" in (Path(result.run_dir) / "parse-error.txt").read_text()


def test_a_deliverable_schema_that_is_a_json_array_is_a_failed_check(repo, home, fake_fleet):
    """D9: `_schema_mismatch` reads `required` and `properties` off the
    schema. A top-level array parses as JSON and then raised on `.get`."""
    schema = repo / "schema.json"
    schema.write_text('["required", "properties"]')
    assert runner_mod._schema_mismatch({"a": 1}, json.loads(schema.read_text())) == (
        "schema file is not a JSON object"
    )


def test_a_truncated_agy_stream_fails_even_with_a_green_gate(repo, home, fake_fleet):
    """D15: exit 0, bytes moved, gate green -- and a stream that stopped
    mid-step. Every other check passes, so without a terminal-event check
    the lane settles as ok on a turn that never finished."""
    step = json.dumps(
        {
            "event": "step_update",
            "step_update": {"step_index": 1, "state": "DONE", "usage": {"input_tokens": 900}},
        }
    )
    fake_fleet(["sh", "-c", f"printf '%s\\n' {json.dumps(step)}; echo work > new.txt"])
    result = dispatch(
        spec_for(repo, fleet="antigravity", mode="write"), home=home, test_command="true"
    )

    assert result.exit_code == 0
    assert result.git_verdict["no_op"] is False
    assert result.gate_passed is True
    assert result.ok is False
    assert result.failure() == "fleet stream ended without a terminal event"
    assert result.to_dict()["kind"] == "transport"
    assert result.usage["input_tokens"] == 900  # the steps' own usage is still priced


# --- W4: teardown runs after the tree was judged -----------------------------


def test_a_teardown_that_changes_the_tree_is_reported_as_cleanup_required(
    repo, home, fake_fleet
):
    """W4: the git verdict is taken before teardown runs, so a teardown that
    touches a tracked file leaves behind a tree the receipt's counts no
    longer describe. The counts are restated from the tree the run actually
    leaves, the note says so, and `cleanup_required` marks the checkout --
    but teardown's outcome is still a note, so `ok` does not move."""
    fake_fleet(["sh", "-c", "echo work > new.txt && git add -A && git commit -qm 'agent work'"])
    result = dispatch(
        spec_for(repo, mode="write", teardown="echo tampered >> seed.txt"),
        home=home,
        isolate=True,
    )
    assert result.ok is True, result.failure()
    assert result.cleanup_required is True
    assert result.isolation["clean"] is False
    assert result.git_verdict["dirty_delta"] == 1
    assert any(
        "teardown changed the tree after it was judged: seed.txt" in note
        for note in result.git_verdict["notes"]
    )


def test_a_teardown_that_changes_nothing_leaves_the_verdict_as_judged(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "echo work > new.txt && git add -A && git commit -qm 'agent work'"])
    result = dispatch(
        spec_for(repo, mode="write", teardown="true"),
        home=home,
        isolate=True,
    )
    assert result.ok is True, result.failure()
    assert result.cleanup_required is False
    assert result.isolation["clean"] is True
    assert result.git_verdict["dirty_delta"] == 0
    assert not any(
        "teardown changed the tree" in note for note in result.git_verdict["notes"]
    )
