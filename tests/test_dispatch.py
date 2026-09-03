"""Dispatch mechanics, exercised against real subprocesses.

The tests that matter here are the ones with a named failure they prevent:
exit 0 with an empty diff reading as success, a timeout leaving a live
grandchild behind, and an agent's transcript flooding the orchestrator.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.runner import dispatch


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=r, check=True)
    (r / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=r, check=True)
    return r


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "conductor-home"


def fake_fleet(monkeypatch, argv: list[str]) -> None:
    """Point every builder at a command we control, so dispatch mechanics can
    be tested without spending a token or needing a CLI installed."""
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: argv)


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


def test_exit_zero_with_no_bytes_moved_is_not_success(repo, home, monkeypatch):
    """The failure this whole module exists to catch: a run that reports
    success, exits 0, and changed nothing."""
    fake_fleet(monkeypatch, ["sh", "-c", "echo 'Done! All tests pass.'; exit 0"])
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.exit_code == 0
    assert result.verdict["no_op"] is True
    assert result.ok is False
    assert any("moved no bytes" in n for n in result.verdict["notes"])


def test_a_write_that_lands_a_commit_is_success(repo, home, monkeypatch):
    fake_fleet(
        monkeypatch,
        ["sh", "-c", "echo work > new.txt && git add -A && git commit -qm 'agent work'"],
    )
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.exit_code == 0
    assert result.verdict["commits_added"] == 1
    assert result.verdict["files_changed"] == 1
    assert result.verdict["no_op"] is False
    assert result.ok is True


def test_a_dirty_tree_counts_as_work_even_without_a_commit(repo, home, monkeypatch):
    fake_fleet(monkeypatch, ["sh", "-c", "echo uncommitted > scratch.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home)
    assert result.verdict["dirty_delta"] == 1
    assert result.verdict["no_op"] is False
    assert result.ok is True


def test_read_mode_does_not_require_bytes_to_move(repo, home, monkeypatch):
    """Research dispatches are supposed to change nothing."""
    fake_fleet(monkeypatch, ["sh", "-c", "echo 'here is my analysis'; exit 0"])
    result = dispatch(spec_for(repo, mode="read"), home=home)
    assert result.verdict["no_op"] is True
    assert result.ok is True


def test_nonzero_exit_is_failure_and_the_tail_comes_from_stderr(repo, home, monkeypatch):
    fake_fleet(monkeypatch, ["sh", "-c", "echo 'boom' >&2; exit 7"])
    result = dispatch(spec_for(repo), home=home)
    assert result.exit_code == 7
    assert result.ok is False
    assert "boom" in result.tail


def test_output_streams_to_disk_and_never_through_the_summary(repo, home, monkeypatch):
    """A verbose agent must not be able to flood the orchestrator."""
    fake_fleet(monkeypatch, ["sh", "-c", "for i in $(seq 1 5000); do echo line-$i; done"])
    result = dispatch(spec_for(repo), home=home)
    on_disk = Path(result.stdout_path).read_text()
    assert on_disk.count("\n") == 5000
    assert len(result.tail.splitlines()) <= 20
    assert "line-1\n" not in json.dumps(result.summary())


def test_timeout_kills_the_whole_process_group(repo, home, monkeypatch):
    """An orphaned grandchild still editing the repo would race whatever runs
    next, so the timeout must kill the tree, not just the child."""
    marker = repo / "grandchild.pid"
    script = f"sh -c 'echo $$ > {marker}; sleep 60' & sleep 60"
    fake_fleet(monkeypatch, ["sh", "-c", script])

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


def test_a_missing_binary_is_reported_not_raised(repo, home, monkeypatch):
    fake_fleet(monkeypatch, ["conductor-no-such-binary-xyz"])
    result = dispatch(spec_for(repo), home=home)
    assert result.ok is False
    assert result.error and "cannot spawn" in result.error


def test_failing_gate_sinks_an_otherwise_successful_run(repo, home, monkeypatch):
    fake_fleet(monkeypatch, ["sh", "-c", "echo x > f.txt"])
    result = dispatch(spec_for(repo, mode="write"), home=home, test_command="exit 1")
    assert result.verdict["no_op"] is False
    assert result.tests["exit_code"] == 1
    assert result.ok is False


def test_every_run_leaves_an_audit_trail(repo, home, monkeypatch):
    fake_fleet(monkeypatch, ["sh", "-c", "echo hi"])
    result = dispatch(spec_for(repo), home=home)
    run_dir = Path(result.run_dir)
    for name in ("prompt.txt", "argv.json", "stdout.log", "stderr.log", "result.json"):
        assert (run_dir / name).is_file(), name
    saved = json.loads((run_dir / "result.json").read_text())
    assert saved["run_id"] == result.run_id
    assert saved["ok"] == result.ok
