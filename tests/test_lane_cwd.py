"""Roadmap item E26, per-lane `cwd`: a mission's `cwd` is only the default,
and a lane -- or one of its own attempts -- may name a different repository.
Dispatch, branch creation and rename, and the collisions a lane's diff can
conflict on all follow that lane's own resolved cwd.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


@pytest.fixture
def repo_a(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo-a")


@pytest.fixture
def repo_b(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo-b")


def _prompt_token(spec: Spec) -> str:
    return spec.prompt.split()[0]


# --- cascade and dispatch ----------------------------------------------------


def test_two_lanes_in_two_repositories_run_independently(
    repo_a, repo_b, home, monkeypatch, tmp_path, git_out
):
    def build(spec: Spec) -> list[str]:
        if _prompt_token(spec) == "A":
            return ["sh", "-c", "echo v1 > a_file.txt"]
        return ["sh", "-c", "echo v2 > b_file.txt"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {
                "name": "a",
                "fleet": "codex",
                "mode": "write",
                "prompt": "A",
                "cwd": str(repo_a),
                "commit": "feat: a",
            },
            {
                "name": "b",
                "fleet": "claude",
                "mode": "write",
                "prompt": "B",
                "cwd": str(repo_b),
                "commit": "feat: b",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    a, b = result.lanes

    assert result.ok is True
    assert a["cwd"] == str(repo_a.resolve()) and b["cwd"] == str(repo_b.resolve())
    # Each lane's commit landed in its own repository's object database (an
    # isolated worktree shares it with the repo it was cut from) and not the
    # other's.
    assert git_out(repo_a, "show", f"{a['tip_sha']}:a_file.txt").strip() == "v1"
    assert git_out(repo_b, "show", f"{b['tip_sha']}:b_file.txt").strip() == "v2"
    with pytest.raises(subprocess.CalledProcessError):
        git_out(repo_a, "show", f"{a['tip_sha']}:b_file.txt")
    with pytest.raises(subprocess.CalledProcessError):
        git_out(repo_b, "show", f"{b['tip_sha']}:a_file.txt")


def test_lane_relative_cwd_resolves_against_the_mission_files_directory(
    tmp_path, home, monkeypatch, git_out
):
    sub = make_repo(tmp_path / "sub-repo")
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo hi > out.txt"])
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {
                "name": "a",
                "fleet": "codex",
                "mode": "write",
                "prompt": "A",
                "cwd": "sub-repo",
                "commit": "feat: a",
            }
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)

    assert mission.lanes[0].attempts[0].cwd == str(sub.resolve())

    result = run_mission(mission, home=home)
    assert result.ok is True
    lane = result.lanes[0]
    assert lane["cwd"] == str(sub.resolve())
    assert git_out(sub, "show", f"{lane['tip_sha']}:out.txt") == "hi"


def test_a_lane_that_sets_no_cwd_falls_back_to_the_missions(
    repo, home, monkeypatch, tmp_path, git_out
):
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo hi > out.txt"])
    raw = {
        "cwd": str(repo),
        "prompt": "SPEC",
        "lanes": [
            {"name": "a", "fleet": "codex", "mode": "write", "prompt": "A", "commit": "feat: a"}
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)

    assert mission.lanes[0].attempts[0].cwd is None
    assert mission.lanes[0].attempts[0].effective_cwd(mission.cwd) == mission.cwd

    result = run_mission(mission, home=home)
    assert result.ok is True
    lane = result.lanes[0]
    assert lane["cwd"] == str(repo.resolve())
    assert git_out(repo, "show", f"{lane['tip_sha']}:out.txt") == "hi"


def test_mission_snapshot_round_trips_with_a_lane_level_cwd(repo_a, repo_b, home, tmp_path):
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {"name": "a", "fleet": "codex", "prompt": "A", "cwd": str(repo_a)},
            {"name": "b", "fleet": "claude", "prompt": "B", "cwd": str(repo_b)},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)

    assert reloaded.to_dict() == mission.to_dict() == snapshot
    assert reloaded.lanes[0].attempts[0].cwd == str(repo_a.resolve())
    assert reloaded.lanes[1].attempts[0].cwd == str(repo_b.resolve())


# --- branch names -------------------------------------------------------------


def test_the_same_branch_name_is_allowed_in_two_repositories(
    repo_a, repo_b, home, monkeypatch, tmp_path, git_out
):
    def build(spec: Spec) -> list[str]:
        if _prompt_token(spec) == "A":
            return ["sh", "-c", "echo v1 > a.txt"]
        return ["sh", "-c", "echo v2 > b.txt"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {
                "name": "a",
                "fleet": "codex",
                "mode": "write",
                "prompt": "A",
                "cwd": str(repo_a),
                "commit": "feat: a",
                "branch": "feat/shared",
            },
            {
                "name": "b",
                "fleet": "claude",
                "mode": "write",
                "prompt": "B",
                "cwd": str(repo_b),
                "commit": "feat: b",
                "branch": "feat/shared",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)  # must not raise
    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.lanes[0]["branch"] == "feat/shared"
    assert result.lanes[1]["branch"] == "feat/shared"
    assert git_out(repo_a, "rev-parse", "feat/shared")
    assert git_out(repo_b, "rev-parse", "feat/shared")


def test_the_same_branch_name_is_still_refused_twice_in_one_repository(repo_a, repo_b, tmp_path):
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {"name": "a", "fleet": "codex", "prompt": "A", "cwd": str(repo_a), "branch": "dup"},
            {"name": "b", "fleet": "claude", "prompt": "B", "cwd": str(repo_a), "branch": "dup"},
        ],
    }
    with pytest.raises(MissionInvalid, match="two lanes claim branch"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_check_branches_refuses_an_existing_branch_in_the_lanes_own_repository(
    repo_a, repo_b, home, tmp_path
):
    subprocess.run(["git", "branch", "taken"], cwd=repo_a, check=True)
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {"name": "a", "fleet": "codex", "prompt": "A", "cwd": str(repo_a), "branch": "taken"},
            {"name": "b", "fleet": "claude", "prompt": "B", "cwd": str(repo_b), "branch": "taken"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    with pytest.raises(MissionInvalid, match=f"already exists in {repo_a.resolve()}"):
        run_mission(mission, home=home)


# --- the resolver never crosses repositories ----------------------------------


def test_resolve_is_refused_at_load_when_sinks_span_more_than_one_cwd(repo_a, repo_b, tmp_path):
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {
                "name": "a",
                "fleet": "codex",
                "mode": "write",
                "prompt": "A",
                "cwd": str(repo_a),
                "commit": "feat: a",
            },
            {
                "name": "b",
                "fleet": "claude",
                "mode": "write",
                "prompt": "B",
                "cwd": str(repo_b),
                "commit": "feat: b",
            },
        ],
        "resolve": {"fleet": "cursor", "commit": "merge: reconcile"},
    }
    with pytest.raises(MissionInvalid, match="resolver never crosses repositories"):
        mission_from_dict(raw, base_dir=tmp_path)


# --- collisions stay within one repository ------------------------------------


def test_collisions_are_grouped_by_repository(repo_a, repo_b, home, monkeypatch, tmp_path):
    def build(spec: Spec) -> list[str]:
        token = _prompt_token(spec)
        if token == "A":
            return ["sh", "-c", "echo v1 > shared.txt"]
        if token == "B":
            return ["sh", "-c", "echo v2 > shared.txt"]
        return ["sh", "-c", "echo v3 > other.txt"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {
                "name": "a",
                "fleet": "codex",
                "mode": "write",
                "prompt": "A",
                "cwd": str(repo_a),
                "commit": "feat: a",
            },
            {
                "name": "b",
                "fleet": "claude",
                "mode": "write",
                "prompt": "B",
                "cwd": str(repo_a),
                "commit": "feat: b",
            },
            {
                "name": "c",
                "fleet": "cursor",
                "mode": "write",
                "prompt": "C",
                "cwd": str(repo_b),
                "commit": "feat: c",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is True
    # E19: a mission whose sinks span repositories prefixes every top-level
    # hotspot `<cwd>:` so two repositories' same-named files never merge;
    # each group keeps its own, unprefixed hotspot list (see below).
    assert result.collisions["hotspots"] == [f"{repo_a.resolve()}:shared.txt"]
    assert result.collisions["conflicts"]["pairs"] == [
        {"lanes": ["a", "b"], "conflicts": ["shared.txt"]}
    ]
    groups = {group["cwd"]: group for group in result.collisions["groups"]}
    assert groups[str(repo_a.resolve())]["lanes"] == ["a", "b"]
    assert groups[str(repo_b.resolve())]["lanes"] == ["c"]
    assert groups[str(repo_a.resolve())]["hotspots"] == ["shared.txt"]
    assert groups[str(repo_b.resolve())]["hotspots"] == []

    saved = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved["collisions"] == result.collisions


# --- report.md -----------------------------------------------------------------


def test_report_names_a_lanes_cwd_only_when_it_differs_from_the_missions(
    repo, repo_a, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        return ["sh", "-c", "echo hi > out.txt"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "cwd": str(repo),
        "prompt": "SPEC",
        "lanes": [
            {"name": "same", "fleet": "codex", "mode": "write", "prompt": "A", "commit": "x"},
            {
                "name": "other",
                "fleet": "claude",
                "mode": "write",
                "prompt": "B",
                "cwd": str(repo_a),
                "commit": "y",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    report = Path(result.report_path).read_text()
    other_section = report.split("## Lane `other`", 1)[1]
    same_section = report.split("## Lane `same`", 1)[1].split("## Lane `other`", 1)[0]
    assert f"cwd: `{repo_a.resolve()}`" in other_section
    assert "- cwd:" not in same_section


# --- resume checks a kept lane's tip in its own repository --------------------


def test_resume_checks_a_kept_lanes_tip_in_its_own_repository(
    repo_a, repo_b, home, monkeypatch, tmp_path
):
    """The mission sets no top-level `cwd`, so it defaults to `tmp_path`,
    which is not a git repository at all: if a resume ever checked a kept
    lane's committed tip against the mission's cwd instead of the lane's
    own (E26), git would refuse outright and wrongly force lane `a` to rerun.
    """
    calls = {"a": 0, "b": 0}

    def build(spec: Spec) -> list[str]:
        if _prompt_token(spec) == "A":
            calls["a"] += 1
            return ["sh", "-c", "echo v1 > out.txt"]
        calls["b"] += 1
        return ["sh", "-c", "exit 1"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "prompt": "SPEC",
        "lanes": [
            {
                "name": "a",
                "fleet": "codex",
                "mode": "write",
                "prompt": "A",
                "cwd": str(repo_a),
                "commit": "feat: a",
            },
            {
                "name": "b",
                "fleet": "claude",
                "mode": "write",
                "prompt": "B",
                "cwd": str(repo_b),
                "commit": "feat: b",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.lanes[0]["ok"] is True and calls["a"] == 1
    assert first.lanes[1]["ok"] is False and calls["b"] == 1

    snapshot = json.loads((Path(first.mission_dir) / "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    resumed = run_mission(reloaded, home=home, resume_dir=Path(first.mission_dir))

    assert calls["a"] == 1  # kept: not redispatched
    assert calls["b"] == 2  # rerun: it had failed
    assert resumed.lanes[0]["ok"] is True and resumed.lanes[0]["kept"] is True
    assert resumed.lanes[0]["cwd"] == str(repo_a.resolve())
