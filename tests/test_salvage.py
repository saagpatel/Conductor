"""E23: `conductor salvage` -- the lead's by-hand gate step, as data.

Builds a real kept worktree the way `test_gate_diagnosis.py` does (a write
lane with no `commit` key, so any edit leaves the worktree dirty and
`worktrees.release` keeps it), then pins `salvage()`'s checks, its receipt,
`emit()`'s two refusals, and the follow-on mission it writes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conductor.cli import main
from conductor.mission import mission_from_dict, run_mission
from conductor.report import report
from conductor.salvage import SalvageInvalid, emit, salvage
from conductor.shape import cap_arithmetic


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _claude_ok_argv(*shell: str) -> list[str]:
    payload = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"ok","usage":{"inputTokens":10,"outputTokens":1}}'
    )
    script = "; ".join(("printf '%s\\n' " + f"'{payload}'", *shell))
    return ["sh", "-c", script]


def _run_lane(
    repo: Path,
    home: Path,
    fake_fleet,
    *,
    edit: str = "echo x >> app.py",
    test: str | None = None,
    commit: str | None = None,
) -> tuple[str, str]:
    """A write lane, by default with no `commit` key: any fleet edit leaves
    the worktree dirty, so `worktrees.release` keeps it whatever the gate
    decides -- `test_gate_diagnosis.py`'s own fixture convention. Returns
    (mission_id, lane_name)."""
    fake_fleet(_claude_ok_argv(edit))
    lane: dict = {"name": "build", "fleet": "claude", "mode": "write"}
    if test is not None:
        lane["test"] = test
    if commit is not None:
        lane["commit"] = commit
    mission = mission_from_dict(
        {"cwd": str(repo), "prompt": "x", "lanes": [lane]}, base_dir=repo
    )
    result = run_mission(mission, home=home)
    mission_dir = Path(result.mission_dir)
    return mission_dir.name, "build"


def test_salvage_reruns_the_gate_from_a_scratch_copy_and_can_come_back_green(
    repo, home, tmp_path, fake_fleet
):
    """A flaky gate: the first invocation (the lane's own) fails and touches
    the marker; salvage's rerun, from a scratch worktree, is the second
    invocation and passes -- AGENTS.md rule 6's 'flaky test under load'."""
    marker = tmp_path / "flaky-marker"
    gate = f"test -f {marker} || (touch {marker} && exit 1)"
    mission_id, lane = _run_lane(repo, home, fake_fleet, edit="echo edited >> app.py", test=gate)

    result = salvage(home, mission_id, lane)

    assert result.mission == mission_id
    assert result.lane == lane
    assert Path(result.worktree).is_dir()
    assert result.base_sha and result.head_sha == result.base_sha
    assert result.dirty is True
    assert "app.py" in result.diff
    assert result.diff_sha256 is not None
    assert result.test_command == gate
    assert result.gate["ran"] is True
    assert result.gate["exit_code"] == 0
    assert result.own_gate["ran"] is True
    assert result.own_gate["exit_code"] == 0
    assert result.receipt_path and Path(result.receipt_path).is_file()

    receipt = json.loads(Path(result.receipt_path).read_text())
    assert receipt["mission"] == mission_id
    assert receipt["lane"] == lane
    assert receipt["gate"]["exit_code"] == 0
    assert "recorded_at" in receipt
    assert "refused" not in receipt

    # The kept worktree itself is untouched: still dirty, still at base_sha.
    assert _git(Path(result.worktree), "rev-parse", "HEAD") == result.base_sha
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=result.worktree, capture_output=True, text=True
    )
    assert status.stdout.strip()


def test_salvage_reports_a_red_gate_in_the_receipt_and_never_commits(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")

    result = salvage(home, mission_id, lane)

    assert result.gate["ran"] is True
    assert result.gate["exit_code"] == 1
    # Never committed, never wrote into the kept worktree beyond what the
    # lane itself already left there.
    assert _git(Path(result.worktree), "rev-parse", "HEAD") == result.base_sha
    assert _git(repo, "rev-list", "--count", "HEAD") == "1"  # the seed commit only


def test_salvage_refuses_an_unknown_mission(home):
    with pytest.raises(SalvageInvalid, match="mission 'no-such' does not exist"):
        salvage(home, "no-such", "build")
    # Nowhere to receipt: a refusal must not conjure `missions/no-such/` into
    # existence, or a typo becomes a mission `conductor missions` lists.
    assert not (home / "missions" / "no-such").exists()


def test_salvage_refuses_an_unknown_lane(repo, home, fake_fleet):
    mission_id, _ = _run_lane(repo, home, fake_fleet, test="exit 1")

    with pytest.raises(SalvageInvalid, match="lane 'nope' does not exist"):
        salvage(home, mission_id, "nope")
    receipts = list((home / "missions" / mission_id / "salvage").glob("nope-*.json"))
    assert len(receipts) == 1


def test_salvage_refuses_a_lane_that_was_not_kept(repo, home, fake_fleet):
    """A gate that always passes, with `commit` set, lands cleanly: the
    worktree is removed, not kept."""
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="true", commit="feat: change")

    with pytest.raises(SalvageInvalid, match="was not kept"):
        salvage(home, mission_id, lane)


def test_salvage_refuses_a_kept_worktree_missing_on_disk(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    lane_path = home / "missions" / mission_id / "lanes" / f"{lane}.json"
    lane_raw = json.loads(lane_path.read_text())
    shutil.rmtree(lane_raw["attempts"][-1]["worktree"])

    with pytest.raises(SalvageInvalid, match="missing on disk"):
        salvage(home, mission_id, lane)


def test_salvage_refuses_a_worktree_that_is_not_the_mission_repos(repo, home, tmp_path, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=other_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.invalid"], cwd=other_repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=other_repo, check=True)
    (other_repo / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=other_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=other_repo, check=True)

    lane_path = home / "missions" / mission_id / "lanes" / f"{lane}.json"
    lane_raw = json.loads(lane_path.read_text())
    lane_raw["attempts"][-1]["worktree"] = str(other_repo)
    lane_path.write_text(json.dumps(lane_raw))

    with pytest.raises(SalvageInvalid, match="not a git worktree"):
        salvage(home, mission_id, lane)


def test_salvage_refuses_an_empty_effective_test_command(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test=None)

    with pytest.raises(SalvageInvalid, match="no test command to salvage"):
        salvage(home, mission_id, lane)


def _commit_the_salvage(worktree: str, message: str = "feat: salvage") -> None:
    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=worktree, check=True)


def _discard_the_salvage(worktree: str) -> None:
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=worktree, check=True)
    subprocess.run(["git", "clean", "-fdx"], cwd=worktree, check=True)


def test_emit_refuses_a_dirty_worktree(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    result = salvage(home, mission_id, lane)

    caps = cap_arithmetic(1, 1)
    with pytest.raises(SalvageInvalid, match="dirty"):
        emit(
            result,
            home / "followon.json",
            test=result.test_command,
            caps=caps,
            name="salvage-x",
        )


def test_emit_refuses_before_the_lead_commits(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    result = salvage(home, mission_id, lane)
    _discard_the_salvage(result.worktree)

    caps = cap_arithmetic(1, 1)
    with pytest.raises(SalvageInvalid, match="equals base_sha"):
        emit(
            result,
            home / "followon.json",
            test=result.test_command,
            caps=caps,
            name="salvage-x",
        )


def test_emit_writes_a_loadable_no_build_followon_mission_with_the_diff_in_its_prompt(
    repo, home, fake_fleet
):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    result = salvage(home, mission_id, lane)
    _commit_the_salvage(result.worktree)

    caps = cap_arithmetic(1, 1)
    out = home / "followon.json"
    mission_dict = emit(
        result,
        out,
        test=result.test_command,
        caps=caps,
        name="salvage-followon",
        spec_prompt="Add {{mission.prompt}}-free spec text about app.py",
    )

    assert out.is_file()
    on_disk = json.loads(out.read_text())
    assert on_disk == mission_dict

    mission = mission_from_dict(mission_dict, base_dir=out.parent, source=str(out))
    names = {lane_obj.name for lane_obj in mission.lanes}
    assert names == {"review-gemini", "review-grok", "fix"}
    assert "build" not in names
    assert mission.cwd == str(Path(result.worktree).resolve())
    # The spec leads the mission prompt; the diff is never pasted into a
    # prompt (it is scanned as a template there) but written beside the file.
    assert mission.prompt.startswith("Add {{mission.prompt}}-free spec text about app.py")
    assert "<diff>" not in mission.prompt
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=result.worktree, capture_output=True, text=True
    ).stdout.strip()
    assert f"git show {head}" in mission.lanes[0].attempts[0].prompt
    assert "app.py" in (home / "salvage-followon-diff.patch").read_text()
    assert all(lane_obj.base is None for lane_obj in mission.lanes)
    fix_lane = next(lane_obj for lane_obj in mission.lanes if lane_obj.name == "fix")
    assert fix_lane.resume is None
    # Not one of item 3's listed deltas from shape_a, whose fix lane carries
    # `test_policy: allow` (rule 3): a follow-on fix that touches a test
    # fixture must not fail the harness's clean gate the same way an
    # ordinary Shape A fix is protected from it.
    assert fix_lane.attempts[0].test_policy == "allow"


def test_report_counts_salvage_receipts_per_mission(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    salvage(home, mission_id, lane)
    salvage(home, mission_id, lane)

    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == mission_id)
    assert row.salvaged == 2


def test_report_salvaged_is_zero_when_there_is_no_salvage_directory(repo, home, fake_fleet):
    mission_id, _ = _run_lane(repo, home, fake_fleet, test="exit 1")

    rpt = report(home)
    row = next(r for r in rpt.missions if r.mission == mission_id)
    assert row.salvaged == 0


def test_cli_salvage_exit_codes(repo, home, fake_fleet, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    mission_id, lane = _run_lane(repo, home, fake_fleet, edit="echo x >> app.py", test="exit 1")
    assert main(["salvage", mission_id, "--lane", lane, "--json"]) == 1
    capsys.readouterr()

    mission_id_green, lane_green = _run_lane(
        repo, home, fake_fleet, edit="echo y >> app.py", test="true"
    )
    assert main(["salvage", mission_id_green, "--lane", lane_green, "--json"]) == 0
    capsys.readouterr()

    assert main(["salvage", "no-such-mission", "--lane", "build", "--json"]) == 3
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_cli_salvage_json_matches_the_written_receipt_exactly(
    repo, home, fake_fleet, monkeypatch: pytest.MonkeyPatch, capsys
):
    """`--json` prints the receipt dict (item 4), meaning the same object
    `<home>/missions/<id>/salvage/<lane>-<stamp>.json` holds -- not an
    in-memory `SalvageResult.to_dict()` computed before the receipt path was
    known and missing the `recorded_at` the file itself carries."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")

    assert main(["salvage", mission_id, "--lane", lane, "--json"]) == 1
    printed = json.loads(capsys.readouterr().out)

    receipts = list((home / "missions" / mission_id / "salvage").glob(f"{lane}-*.json"))
    assert len(receipts) == 1
    on_disk = json.loads(receipts[0].read_text())
    assert printed == on_disk
    assert "recorded_at" in printed


def test_emit_loads_when_the_salvaged_change_carries_template_syntax(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    result = salvage(home, mission_id, lane)
    (Path(result.worktree) / "prompts.py").write_text('X = "{{lanes.build.diff}}"\n')
    _commit_the_salvage(result.worktree)
    out = home / "followon.json"
    emit(result, out, test=result.test_command, caps=cap_arithmetic(1, 1), name="tpl")
    mission = mission_from_dict(json.loads(out.read_text()), base_dir=out.parent, source=str(out))
    assert "{{lanes.build.diff}}" not in mission.prompt
    assert "{{lanes.build.diff}}" in (home / "tpl-diff.patch").read_text()


def test_salvage_runs_the_kept_trees_own_gate_beside_the_clean_gate(
    repo, home, fake_fleet, monkeypatch
):
    """The clean gate restores the test surface from the base, so a kept
    worktree's new test file is invisible to it; the own gate transplants
    everything and is where that file is judged. A gate that fails only when
    the new test file is present tells the two apart."""
    mission_id, lane = _run_lane(
        repo,
        home,
        fake_fleet,
        edit="mkdir -p tests && echo x > tests/test_new.py && echo y >> app.py",
        test="test ! -f tests/test_new.py",
    )
    result = salvage(home, mission_id, lane)
    assert result.gate["exit_code"] == 0
    assert result.own_gate["exit_code"] == 1
    receipt = json.loads(Path(result.receipt_path).read_text())
    assert receipt["own_gate"]["exit_code"] == 1
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    code = main(["salvage", mission_id, "--lane", lane])
    assert code == 1
