"""A stop request (Ctrl-C, `kill`) ends a run cleanly instead of orphaning
the fleet: caught live 2026-09-03, when killing `conductor mission` left
Sol running in a worktree that nothing would ever release.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conductor import cli as cli_mod
from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import mission_from_dict, run_mission
from conductor.runner import clear_stop, dispatch, kill_live_groups, request_stop
from conductor.verify import TestOutcome as GateOutcome
from conductor.verify import run_tests


@pytest.fixture(autouse=True)
def _fresh_stop():
    clear_stop()
    yield
    clear_stop()


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test dispatch", cwd=str(repo), mode="write")
    base.update(kw)
    return Spec(**base)


def test_a_stop_request_kills_the_fleet_and_releases_its_worktree(repo, home, fake_fleet):
    marker = repo / "grandchild.pid"
    fake_fleet(["sh", "-c", f"sh -c 'echo $$ > {marker}; sleep 60' & sleep 60"])
    threading.Timer(1.0, request_stop).start()

    started = time.monotonic()
    result = dispatch(spec_for(repo, timeout=120), home=home, isolate=True)
    elapsed = time.monotonic() - started

    assert elapsed < 30
    assert result.interrupted is True and result.timed_out is False
    assert result.ok is False
    assert result.error == "interrupted: stop requested; process group killed"
    assert result.failure() == result.error
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text().strip()), 0)
    # The worktree was released like any other run's; nothing left to gc.
    assert result.isolation["kept"] is False
    assert not Path(result.isolation["worktree"]).exists()


def test_a_stop_already_requested_refuses_to_spawn(repo, home, fake_fleet):
    fake_fleet(["sh", "-c", "touch spawned.txt"])
    request_stop()
    result = dispatch(spec_for(repo), home=home)
    assert result.interrupted is True and result.exit_code is None
    assert result.error == "interrupted: stop requested before the fleet was spawned"
    assert not (repo / "spawned.txt").exists()


def test_an_interrupted_capped_run_is_not_over_budget_and_not_unpriced(repo, home, fake_fleet):
    """A stop is not the cap firing, and an unpriced stopped run is no
    evidence about the cap either way; only the interruption is reported."""
    fake_fleet(["sh", "-c", "sleep 60"])
    threading.Timer(0.5, request_stop).start()
    result = dispatch(spec_for(repo, fleet="codex", cap_usd=5.0, timeout=60), home=home)
    assert result.interrupted is True
    assert result.budget["exceeded"] is False and result.budget["unpriced"] is False
    assert result.failure().startswith("interrupted")


def test_a_stopped_mission_skips_what_has_not_started_and_still_reports(
    repo, home, fake_fleet, tmp_path
):
    fake_fleet(["sh", "-c", "sleep 60"])
    threading.Timer(1.0, request_stop).start()
    mission = mission_from_dict(
        {
            "name": "stoppable",
            "cwd": str(repo),
            "concurrency": 1,
            "lanes": [
                {"name": "first", "fleet": "claude", "prompt": "a", "timeout": 60},
                {"name": "second", "fleet": "claude", "prompt": "b", "timeout": 60},
                {"name": "third", "fleet": "claude", "prompt": "c", "needs": ["first"]},
            ],
            "collate": {"fleet": "claude"},
            "max_cost_usd": 5.0,
            # Interrupt handling, not vendor diversity, is under test here.
            "self_judging": "allow",
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    first, second, third = result.lanes
    assert result.interrupted is True and result.ok is False
    # A stopped, unpriced run is not "unverifiable" spend: it was cut, not lost.
    assert result.budget["unverifiable"] is False and result.budget["unpriced_dispatches"] == 0
    assert first["unpriced_attempts"] == 0
    assert first["attempts"][0]["error"].startswith("interrupted")
    # `second` was queued behind the pool slot and refused at its own start;
    # `third` never left pending. Both say why and neither spawned a fleet.
    assert second["skipped"] == "interrupted: stop requested; claude not started"
    assert third["skipped"] == "interrupted: stop requested; not started"
    assert second["attempts"] == [] and third["attempts"] == []
    assert result.collate is None  # the judge is not spent on a run that was cut short
    report = Path(result.report_path).read_text()
    assert "**Interrupted**" in report
    assert "unpriced)" not in report
    assert (Path(result.mission_dir) / "lanes" / "third.json").is_file()


def test_wait_polls_the_stop_flag(monkeypatch):
    """The stop lands within one poll, whatever the run's timeout."""
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    monkeypatch.setattr(runner_mod, "POLL_S", 0.2)
    threading.Timer(0.3, request_stop).start()
    started = time.monotonic()
    code, timed_out, over_cap, interrupted, breaker = runner_mod._wait(
        proc, 60, None, None
    )
    assert time.monotonic() - started < 5
    assert interrupted and not timed_out and not over_cap
    assert breaker is None
    assert code != 0


def test_wait_after_kill_is_bounded(monkeypatch):
    """The final proc.wait() after _kill_live_group had no timeout and could
    hang the runner forever if the child never exited."""
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    waits: list[float | None] = []
    real_wait = subprocess.Popen.wait

    def tracked_wait(self, timeout=None):
        waits.append(timeout)
        if timeout is None:
            raise AssertionError("unbounded proc.wait() after kill")
        return real_wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", tracked_wait)
    monkeypatch.setattr(runner_mod, "POLL_S", 0.05)
    try:
        _code, timed_out, over_cap, interrupted, breaker = runner_mod._wait(
            proc, 0.01, None, None
        )
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            real_wait(proc, timeout=1)
        except subprocess.TimeoutExpired:
            pass

    assert timed_out and not over_cap and not interrupted
    assert breaker is None
    assert waits
    assert waits[-1] == runner_mod.KILL_WAIT_S


def test_reap_killed_returns_when_wait_times_out():
    class _Hang:
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="mock", timeout=timeout)

    started = time.monotonic()
    runner_mod._reap_killed(_Hang(), timeout=0.01)
    assert time.monotonic() - started < 1


def test_run_tests_registers_and_unregisters_its_process_group(repo, monkeypatch):
    events: list[tuple[str, int]] = []
    real_kill = runner_mod._kill_live_group
    monkeypatch.setattr(
        runner_mod, "_register_live_group", lambda pgid: events.append(("registered", pgid))
    )

    def kill(pgid: int) -> None:
        events.append(("killed", pgid))
        real_kill(pgid)

    monkeypatch.setattr(runner_mod, "_kill_live_group", kill)
    assert run_tests(str(repo), "true").passed
    assert [event for event, _ in events] == ["registered", "killed"]
    assert events[0][1] == events[1][1]


def test_kill_live_groups_kills_a_registered_sleeping_group():
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    runner_mod._register_live_group(proc.pid)
    kill_live_groups()
    proc.wait(timeout=5)
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


def test_a_second_signal_kills_live_groups_and_uses_the_signal_exit_status(monkeypatch):
    exits: list[int] = []
    killed: list[bool] = []
    monkeypatch.setattr(cli_mod, "kill_live_groups", lambda: killed.append(True))
    handler = cli_mod._stop_handler(exits.append)
    handler(signal.SIGINT, None)
    assert runner_mod.stop_requested() and not killed and not exits
    handler(signal.SIGINT, None)
    assert killed == [True] and exits == [130]


def test_live_group_registry_lock_is_reentrant_for_a_signal_handler():
    lock = runner_mod._LIVE_GROUPS_LOCK
    assert lock.acquire()
    try:
        assert lock.acquire(blocking=False)
        lock.release()
    finally:
        lock.release()


def test_verify_passes_the_stop_request_to_its_gate(repo, monkeypatch, capsys):
    seen: dict[str, object] = {}

    def gate(cwd: str, command: str, *, stop):
        seen.update(cwd=cwd, command=command, stop=stop)
        return GateOutcome(ran=True, exit_code=0)

    monkeypatch.setattr(cli_mod, "run_tests", gate)
    assert cli_mod.cmd_verify(argparse.Namespace(cwd=str(repo), test="true")) == 0
    capsys.readouterr()
    assert seen == {"cwd": str(repo), "command": "true", "stop": runner_mod.stop_requested}


def test_a_stop_during_the_gate_kills_the_suite_and_takes_the_commit_back(
    repo, home, fake_fleet, git_out
):
    """The gate can run 15 minutes; the operator's Ctrl-C must not wait for it."""
    fake_fleet(["sh", "-c", "echo work > work.txt"])
    marker = repo / "suite.pid"
    threading.Timer(1.0, request_stop).start()
    started = time.monotonic()
    result = dispatch(
        spec_for(repo, timeout=60),
        home=home,
        commit_message="feat: work",
        test_command=f"echo $$ > {marker}; sleep 60",
    )
    assert time.monotonic() - started < 30
    assert result.interrupted is True
    assert result.tests["interrupted"] is True and result.tests["exit_code"] is None
    assert result.error == "interrupted: stop requested during the gate; process group killed"
    assert result.ok is False
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text().strip()), 0)
    # The commit did not survive a gate that never passed; the work is staged.
    assert result.commit["committed"] is False
    assert git_out(repo, "log", "--oneline").count("\n") == 0
    assert "work.txt" in git_out(repo, "diff", "--cached", "--name-only")
