"""E23: `conductor salvage` -- the lead's by-hand gate step, as data.

Builds a real kept worktree the way `test_gate_diagnosis.py` does (a write
lane with no `commit` key, so any edit leaves the worktree dirty and
`worktrees.release` keeps it), then pins `salvage()`'s checks, its receipt,
`emit()`'s two refusals, and the follow-on mission it writes.
"""

from __future__ import annotations

import hashlib
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
from conductor.verify import diff_since


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


def _second_repo(tmp_path: Path, name: str = "other-repo") -> Path:
    """A second real repository, so a lane with its own `cwd` has somewhere
    to run that is not the mission's."""
    path = tmp_path / name
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "app.py").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


def test_salvage_uses_the_lanes_own_repository_not_the_missions(repo, home, tmp_path, fake_fleet):
    """D16: a lane may declare its own `cwd`; the kept worktree then belongs
    to that repository, not the mission's, and salvage must gate it instead
    of refusing it as foreign."""
    other = _second_repo(tmp_path)
    fake_fleet(_claude_ok_argv("echo edited >> app.py"))
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "cwd": str(other),
                    "test": "exit 1",
                }
            ],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)
    mission_id = Path(result.mission_dir).name

    salvaged = salvage(home, mission_id, "build")

    assert Path(salvaged.worktree).is_dir()
    assert _git(Path(salvaged.worktree), "rev-parse", "--show-toplevel") != str(repo.resolve())
    assert salvaged.gate["ran"] is True
    assert "app.py" in salvaged.diff


def test_salvage_gates_with_the_producing_attempts_test_command(repo, home, fake_fleet):
    """D16: the worktree comes from the last attempt, so its gate and timeout
    must too -- not the first declared attempt's."""
    fake_fleet(_claude_ok_argv("echo edited >> app.py"))
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "test": "exit 1",
                    "timeout": 120,
                    "fallback": [{"fleet": "claude", "model": "haiku", "test": "true"}],
                }
            ],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)
    mission_id = Path(result.mission_dir).name
    lane_raw = json.loads((home / "missions" / mission_id / "lanes" / "build.json").read_text())
    assert len(lane_raw["attempts"]) == 2  # the primary failed its gate; the fallback ran

    salvaged = salvage(home, mission_id, "build")

    assert salvaged.test_command == "true"
    assert salvaged.gate["exit_code"] == 0
    assert salvaged.own_gate["exit_code"] == 0


def test_salvage_refuses_a_lane_whose_environment_it_cannot_rebuild(repo, home, fake_fleet):
    """D16: `setup`, `include`, and `ports` are the lane environment the gate
    ran under; the transplant gates get none of it, so a verdict from them
    would be a verdict under a different contract."""
    fake_fleet(_claude_ok_argv("echo edited >> app.py"))
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "prompt": "x",
            "lanes": [
                {
                    "name": "build",
                    "fleet": "claude",
                    "mode": "write",
                    "test": "exit 1",
                    "setup": "true",
                }
            ],
        },
        base_dir=repo,
    )
    result = run_mission(mission, home=home)
    mission_id = Path(result.mission_dir).name

    with pytest.raises(
        SalvageInvalid,
        match="salvage cannot reconstruct setup for lane build; gate the kept worktree by hand",
    ):
        salvage(home, mission_id, "build")
    receipts = list((home / "missions" / mission_id / "salvage").glob("build-*.json"))
    assert len(receipts) == 1
    assert "cannot reconstruct setup" in json.loads(receipts[0].read_text())["refused"]


def test_salvage_hashes_the_bytes_it_gated_and_keeps_the_receipts_digest_as_lineage(
    repo, home, fake_fleet
):
    """D17: the lead repairs the kept worktree, then salvages. `diff_sha256`
    must name what was gated today; the run's own digest survives only as
    `lineage_diff_sha256`."""
    mission_id, lane = _run_lane(repo, home, fake_fleet, edit="echo edited >> app.py", test="true")
    lane_raw = json.loads((home / "missions" / mission_id / "lanes" / f"{lane}.json").read_text())
    run_diff = Path(lane_raw["diff_path"]).read_text()
    worktree = Path(lane_raw["attempts"][-1]["worktree"])
    (worktree / "app.py").write_text("repaired\n")

    result = salvage(home, mission_id, lane)

    assert "repaired" in result.diff
    assert result.diff == diff_since(str(worktree), result.base_sha)
    assert "repaired" not in run_diff
    assert result.diff_sha256 == hashlib.sha256(result.diff.encode()).hexdigest()
    assert result.lineage_diff_sha256 == hashlib.sha256(run_diff.encode()).hexdigest()
    assert result.diff_sha256 != result.lineage_diff_sha256
    receipt = json.loads(Path(result.receipt_path).read_text())
    assert receipt["diff_sha256"] == result.diff_sha256
    assert receipt["lineage_diff_sha256"] == result.lineage_diff_sha256


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


def test_emit_ceiling_defaults_to_none_and_forwards_an_explicit_value(repo, home, fake_fleet):
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="exit 1")
    result = salvage(home, mission_id, lane)
    _commit_the_salvage(result.worktree)
    caps = cap_arithmetic(1, 1)

    default_dict = emit(
        result, home / "followon-default.json", test=result.test_command, caps=caps, name="c1"
    )
    assert default_dict["ceiling"] == {"per_hour_usd": None, "per_day_usd": None}

    explicit_dict = emit(
        result,
        home / "followon-explicit.json",
        test=result.test_command,
        caps=caps,
        name="c2",
        ceiling={"per_hour_usd": 5.0, "per_day_usd": 10.0},
    )
    assert explicit_dict["ceiling"] == {"per_hour_usd": 5.0, "per_day_usd": 10.0}


def test_cli_salvage_emit_ceiling_flag(repo, home, fake_fleet, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_id, lane = _run_lane(repo, home, fake_fleet, test="true")
    result = salvage(home, mission_id, lane)
    _commit_the_salvage(result.worktree)
    capsys.readouterr()

    out = home / "followon.json"
    code = main(
        [
            "salvage", mission_id, "--lane", lane, "--emit", str(out),
            "--items", "1", "--modules", "1", "--ceiling", "5,10",
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    assert raw["ceiling"] == {"per_hour_usd": 5.0, "per_day_usd": 10.0}


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


def test_salvage_skips_the_clean_gate_when_the_lane_ran_under_test_policy_allow(
    repo, home, fake_fleet
):
    """Rule 3: a lane that ran under `test_policy: allow` is not judged on the
    clean gate by the harness, so salvage records it as skipped and judges on
    the own gate alone -- otherwise a spec that edits an existing test could
    never salvage green."""
    fake_fleet(_claude_ok_argv("mkdir -p tests && echo x > tests/test_new.py && echo y >> app.py"))
    lane = {
        "name": "build",
        "fleet": "claude",
        "mode": "write",
        "test": "test -f tests/test_new.py",
        "test_policy": "allow",
    }
    mission = mission_from_dict(
        {"cwd": str(repo), "prompt": "x", "lanes": [lane]}, base_dir=repo
    )
    run_mission(mission, home=home)
    mission_id = sorted((home / "missions").iterdir())[-1].name
    result = salvage(home, mission_id, "build")
    assert result.own_gate["exit_code"] == 0
    assert result.gate["ran"] is False
    assert "test_policy allow" in result.gate["tail"]


def test_a_worktree_edited_while_it_is_gated_is_refused_not_receipted(
    repo, home, fake_fleet, monkeypatch
):
    """Both gates read the kept worktree, so the digest taken before them is
    evidence about what they judged only for as long as those bytes held
    still. The re-read that catches an edit landing mid-salvage had no
    failing-path test: deleting it left the suite green (2026-09-08 audit)."""
    from conductor import salvage as salvage_mod

    mission_id, lane = _run_lane(repo, home, fake_fleet, test="true")
    lane_raw = json.loads((home / "missions" / mission_id / "lanes" / f"{lane}.json").read_text())
    worktree = Path(lane_raw["attempts"][-1]["worktree"])

    real_clean_gate = salvage_mod._clean_gate

    def edit_then_gate(*args, **kwargs):
        # The edit lands while the gates are running, exactly as a stray
        # editor or a second agent in the same worktree would.
        (worktree / "app.py").write_text("edited mid-salvage\n")
        return real_clean_gate(*args, **kwargs)

    monkeypatch.setattr(salvage_mod, "_clean_gate", edit_then_gate)

    with pytest.raises(SalvageInvalid, match="changed while it was being gated"):
        salvage(home, mission_id, lane)


def test_a_kept_worktree_with_no_readable_head_is_refused(repo, home, fake_fleet, monkeypatch):
    """`git_run` turns a timeout or an OSError into an empty stdout, and an
    empty `head_sha` on the receipt says the salvage gated nothing while the
    gate steps beside it say it passed."""
    from conductor import salvage as salvage_mod

    mission_id, lane = _run_lane(repo, home, fake_fleet, test="true")
    real_git_run = salvage_mod.git_run

    def no_head(cwd, *args, **kwargs):
        if args[:1] == ("rev-parse",) and "HEAD" in args:
            return real_git_run(cwd, "rev-parse", "--verify", "--quiet", "nope-not-a-ref")
        return real_git_run(cwd, *args, **kwargs)

    monkeypatch.setattr(salvage_mod, "git_run", no_head)

    with pytest.raises(SalvageInvalid, match="no readable HEAD"):
        salvage(home, mission_id, lane)


@pytest.mark.parametrize("lane", ["../escape", "a/b", "..", "", "-leading-dash"])
def test_a_lane_name_that_is_not_one_is_refused_before_it_becomes_a_path(home, lane):
    """`land` and `salvage` take a lane name from the command line and build
    three paths out of it. `Mission.validate` holds the pattern for a lane it
    loaded; these two check it themselves."""
    with pytest.raises(SalvageInvalid, match="is not a lane name"):
        salvage(home, "20260101T000000Z-m", lane)
