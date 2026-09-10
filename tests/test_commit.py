"""Conductor-owned commits.

Committing is conductor's job because the fleets disagree about whether they
can commit at all: Codex's workspace-write sandbox blocks writes to .git
(observed live 2026-09-03: "Commit is blocked: this workspace disallows writes
to `.git`"), while Claude Code, Cursor, and Antigravity all commit on their
own. Leaving it to each vendor makes "did it commit?" a property of the vendor.
"""

from __future__ import annotations

import os
import shlex
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


# --- F15 mission 2 item 3: exclude keeps a deliverable out of the commit ----


def test_exclude_leaves_the_named_file_untracked_and_commits_the_rest(repo: Path):
    (repo / "new.txt").write_text("work\n")
    (repo / "dispositions.json").write_text('{"dispositions": []}\n')
    out = commit_work(str(repo), "feat: agent work", exclude=("dispositions.json",))
    assert out.committed is True
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "?? dispositions.json" in status
    log = subprocess.run(
        ["git", "show", "--stat", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "new.txt" in log
    assert "dispositions.json" not in log


def test_exclude_alone_reports_nothing_to_commit(repo: Path):
    (repo / "dispositions.json").write_text('{"dispositions": []}\n')
    out = commit_work(
        str(repo), "chore: nothing but the deliverable", exclude=("dispositions.json",)
    )
    assert out.committed is False
    assert out.reason == "nothing to commit beyond the excluded deliverable"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert "?? dispositions.json" in status


def test_exclude_path_not_in_status_is_harmless(repo: Path):
    (repo / "new.txt").write_text("work\n")
    out = commit_work(str(repo), "feat: agent work", exclude=("never-written.json",))
    assert out.committed is True


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
    assert result.git_verdict["commits_added"] == 1
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
    assert result.git_verdict["dirty_delta"] == 1
    assert result.git_verdict["commits_added"] == 0


def test_over_budget_undo_restates_the_git_verdict(repo, tmp_path, monkeypatch):
    """The deliverable undo recopies commits_added/files_changed/dirty_delta/
    branch_after/branch_moved onto git_verdict; the budget undo used to
    recapture `after` for `_commit_bounds` and stop, so a cursor lane that
    committed then settled exceeded receipted both '1 commit added' and
    'no commit landed'."""
    envelope = (
        '{"type":"result","subtype":"success","is_error":false,"result":"PONG",'
        '"usage":{"inputTokens":1000000,"outputTokens":1000000}}'
    )
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: [
            "sh",
            "-c",
            "echo work > w.txt && git add -A && git commit -qm work && "
            f"printf '%s\\n' {shlex.quote(envelope)}",
        ],
    )
    result = dispatch(
        Spec(
            fleet="cursor",
            model="composer-2.5",
            prompt="write then blow the cap",
            cwd=str(repo),
            mode="write",
            cap_usd=1.0,
        ),
        isolate=True,
        home=tmp_path / "home",
    )
    assert result.ok is False
    assert result.budget["exceeded"] is True
    assert result.commit["committed"] is False
    assert result.tip_commit == result.base_commit
    assert result.git_verdict["commits_added"] == 0
    assert result.git_verdict["dirty_delta"] >= 1
    assert any("over budget" in note for note in result.git_verdict["notes"])


def test_a_crash_after_commit_leaves_the_commit_on_the_receipt_and_the_branch(
    repo, tmp_path, monkeypatch
):
    """A crash while conductor reads its own output is not evidence the work
    is bad. The receipt must name the sha that is on the branch; omitting it
    is the branch and the receipt disagreeing."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", "echo work > hello.txt"],
    )

    def boom(*_a, **_k):
        raise RuntimeError("capture crashed")

    monkeypatch.setattr(runner_mod, "_capture_deliverable", boom)
    result = dispatch(
        Spec(fleet="codex", prompt="write hello", cwd=str(repo), mode="write"),
        commit_message="feat: add hello",
        home=tmp_path / "home",
    )
    assert result.ok is False
    assert result.error.startswith("parse failed: RuntimeError:")
    assert result.commit is not None
    assert result.commit["committed"] is True
    assert result.commit["sha"]
    assert any("commit landed and was not judged" in n for n in result.git_verdict["notes"])
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"], cwd=repo, capture_output=True, text=True, check=True
    )
    assert log.stdout.strip() == "feat: add hello"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head == result.commit["sha"]


def test_a_crash_before_post_wait_kills_the_fleet_and_writes_a_receipt(
    repo, tmp_path, monkeypatch
):
    """An exception between Popen and post_wait used to release the worktree
    and propagate with the fleet still running and no result.json."""
    seen: dict[str, int] = {}

    def boom(proc, *_a, **_k):
        seen["pid"] = proc.pid
        raise RuntimeError("poll loop exploded")

    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", "sleep 60"],
    )
    monkeypatch.setattr(runner_mod, "_wait", boom)
    result = dispatch(
        Spec(fleet="codex", prompt="x", cwd=str(repo), mode="write"),
        home=tmp_path / "home",
    )
    assert result.ok is False
    assert result.spawned is True
    assert result.error.startswith("parse failed: RuntimeError: poll loop exploded")
    assert (Path(result.run_dir) / "result.json").exists()
    notes = " ".join(result.git_verdict["notes"])
    assert "receipt written after the run; the tree was not judged" in notes
    assert "pid" in seen
    with pytest.raises(ProcessLookupError):
        os.kill(seen["pid"], 0)

