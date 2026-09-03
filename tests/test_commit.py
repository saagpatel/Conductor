"""Conductor-owned commits.

Committing is conductor's job because the fleets disagree about whether they
can commit at all: Codex's workspace-write sandbox blocks writes to .git
(observed live 2026-09-03: "Commit is blocked: this workspace disallows writes
to `.git`"), while Claude Code, Cursor, and Antigravity all commit on their
own. Leaving it to each vendor makes "did it commit?" a property of the vendor.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.runner import dispatch
from conductor.verify import commit_work


@pytest.fixture
def repo(repo: Path) -> Path:
    (repo / "doomed.txt").write_text("delete me\n")
    subprocess.run(["git", "add", "doomed.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed doomed"], cwd=repo, check=True)
    return repo


def test_commit_lands_the_dispatchs_work(repo: Path):
    (repo / "new.txt").write_text("work\n")
    out = commit_work(str(repo), "feat: agent work")
    assert out.committed is True
    assert len(out.sha) == 40
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True, check=True
    )
    assert "feat: agent work" in log.stdout


def test_deletions_are_staged_but_never_silent(repo: Path):
    """A bulk stage that quietly swallows removed source files is the failure
    this reporting exists to prevent."""
    (repo / "doomed.txt").unlink()
    (repo / "added.txt").write_text("new\n")
    out = commit_work(str(repo), "chore: churn")
    assert out.committed is True
    assert out.deletions == ["doomed.txt"]


def test_nothing_to_commit_is_reported_not_faked(repo: Path):
    out = commit_work(str(repo), "chore: nothing")
    assert out.committed is False
    assert out.reason == "nothing to commit"


def test_dispatch_commits_what_a_sandboxed_fleet_could_not(repo, tmp_path, monkeypatch):
    """The Codex case: the fleet writes the file and cannot commit it."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", "echo HELLO > hello.txt"],
    )
    result = dispatch(
        Spec(fleet="codex", prompt="write hello", cwd=str(repo), mode="write"),
        commit_message="feat: add hello",
        home=tmp_path / "home",
    )
    assert result.commit["committed"] is True
    assert result.verdict["commits_added"] == 1
    assert result.ok is True


def test_a_requested_commit_that_did_not_happen_sinks_the_run(repo, tmp_path, monkeypatch):
    """The caller asked for landed work; an empty tree means it did not land."""
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo nothing-to-do"])
    result = dispatch(
        Spec(fleet="codex", prompt="do nothing", cwd=str(repo), mode="write"),
        commit_message="feat: nothing",
        home=tmp_path / "home",
    )
    assert result.commit["committed"] is False
    assert result.ok is False


def test_no_commit_requested_leaves_the_tree_alone(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo x > untracked.txt"]
    )
    result = dispatch(
        Spec(fleet="codex", prompt="write", cwd=str(repo), mode="write"),
        home=tmp_path / "home",
    )
    assert result.commit is None
    assert result.verdict["dirty_delta"] == 1
    assert result.verdict["commits_added"] == 0
