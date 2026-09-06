"""E19: cross-repo collisions. A collision is the same path in the same
repository; each repository runs its own gate; the resolver never merges
across repositories. Builds on E26's per-lane `cwd`.
"""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
from pathlib import Path

import pytest

from conductor import golden
from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import Mission, mission_from_dict, run_mission


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


def antigravity_envelope(text: str) -> list[str]:
    payload = {
        "event": "result",
        "result": {
            "status": "SUCCESS",
            "response": text,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
    }
    return ["sh", "-c", f"printf '%s' {shlex.quote(json.dumps(payload))}"]


def _token(spec: Spec) -> str:
    return spec.prompt.split()[0]


# --- overlap never crosses repositories ---------------------------------------


def test_two_lanes_in_different_repositories_touching_the_same_path_produce_no_hotspot(
    repo_a, repo_b, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if _token(spec) == "A":
            return ["sh", "-c", "echo v1 > shared.txt"]
        return ["sh", "-c", "echo v2 > shared.txt"]

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

    assert result.ok is True
    assert result.resolve is None  # no resolve block: nothing to run
    assert result.collisions is not None
    assert result.collisions["hotspots"] == []
    groups = {g["cwd"]: g for g in result.collisions["groups"]}
    assert groups[str(repo_a.resolve())]["hotspots"] == []
    assert groups[str(repo_b.resolve())]["hotspots"] == []
    assert groups[str(repo_a.resolve())]["lanes"] == ["a"]
    assert groups[str(repo_b.resolve())]["lanes"] == ["b"]


def test_the_same_path_in_the_same_repository_still_produces_a_hotspot(
    repo_a, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if _token(spec) == "A":
            return ["sh", "-c", "echo v1 > shared.txt"]
        return ["sh", "-c", "echo v2 > shared.txt"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "prompt": "SPEC",
        "cwd": str(repo_a),
        "lanes": [
            {"name": "a", "fleet": "codex", "mode": "write", "prompt": "A", "commit": "feat: a"},
            {"name": "b", "fleet": "claude", "mode": "write", "prompt": "B", "commit": "feat: b"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is True
    # A single-repository mission's top level is exactly its one group,
    # unprefixed -- identical to what this produced before E19.
    assert result.collisions["hotspots"] == ["shared.txt"]
    assert result.collisions["groups"] == [
        {
            "cwd": str(repo_a.resolve()),
            "lanes": ["a", "b"],
            "hotspots": ["shared.txt"],
            "overlap": result.collisions["overlap"],
        }
    ]


def test_a_three_lane_mission_across_two_repositories_prefixes_top_level_hotspots(
    repo_a, repo_b, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        token = _token(spec)
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
    assert result.collisions["hotspots"] == [f"{repo_a.resolve()}:shared.txt"]
    groups = {g["cwd"]: g for g in result.collisions["groups"]}
    assert groups[str(repo_a.resolve())]["hotspots"] == ["shared.txt"]
    assert groups[str(repo_b.resolve())]["hotspots"] == []
    assert result.collisions["overlap"]["files"] == {
        f"{repo_a.resolve()}:shared.txt": ["a", "b"],
        f"{repo_b.resolve()}:other.txt": ["c"],
    }

    saved = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved["collisions"] == result.collisions


# --- the judge and the resolver each see only their own repository -----------


def test_the_collates_collisions_section_only_names_its_own_groups_paths(
    repo_a, repo_b, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "antigravity":
            return antigravity_envelope("noted")
        table = {
            "A": "echo v1 > shared.txt",
            "B": "echo v2 > shared.txt",
            "C": "echo v3 > shared.txt",
            "D": "echo v4 > shared.txt",
        }
        return ["sh", "-c", table[_token(spec)]]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "prompt": "SPEC",
        "cwd": str(repo_a),
        "lanes": [
            {"name": "a", "fleet": "codex", "mode": "write", "prompt": "A", "commit": "feat: a"},
            {"name": "b", "fleet": "claude", "mode": "write", "prompt": "B", "commit": "feat: b"},
            {
                "name": "c",
                "fleet": "cursor",
                "mode": "write",
                "prompt": "C",
                "cwd": str(repo_b),
                "commit": "feat: c",
            },
            {
                "name": "d",
                "fleet": "codex",
                "mode": "write",
                "prompt": "D",
                "cwd": str(repo_b),
                "commit": "feat: d",
            },
        ],
        "collate": {"fleet": "antigravity", "instructions": "Pick one."},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is True
    groups = {g["cwd"]: g for g in result.collisions["groups"]}
    assert groups[str(repo_a.resolve())]["hotspots"] == ["shared.txt"]
    assert groups[str(repo_b.resolve())]["hotspots"] == ["shared.txt"]
    assert result.collate["ok"] is True

    prompt = Path(result.mission_dir, "collate-prompt.txt").read_text()
    assert "## Collisions" in prompt
    # Only repo_a's hotspot line (its own lanes a, b) -- never repo_b's (c, d),
    # never a `<cwd>:`-prefixed path, and never repo_b's own directory.
    assert "- `shared.txt`: a, b" in prompt
    assert "c, d" not in prompt
    assert str(repo_b.resolve()) not in prompt


def test_resolve_dispatches_and_is_trusted_in_the_sinks_own_repository_not_the_missions(
    repo_a, home, monkeypatch, tmp_path
):
    """The mission sets no top-level `cwd`, so it defaults to `tmp_path`,
    which is not a git repository at all (E26). If the resolver's dispatch,
    its gate, or a resume's trust check on its committed tip ever used
    `mission.cwd` instead of the sinks' own repository, git would refuse
    outright and the resolve would fail or be wrongly discarded on resume."""

    def build(spec: Spec) -> list[str]:
        if spec.fleet == "cursor":
            return ["sh", "-c", "echo merged > resolved.txt"]
        if _token(spec) == "A":
            return ["sh", "-c", "echo v1 > shared.txt"]
        return ["sh", "-c", "echo v2 > shared.txt"]

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
        ],
        "resolve": {"fleet": "cursor", "commit": "merge: reconcile"},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.resolve["ran"] is True and result.resolve["ok"] is True
    assert result.resolve["hotspots"] == ["shared.txt"]
    tip = result.resolve["tip"]
    assert tip
    commit = subprocess.run(
        ["git", "cat-file", "-e", f"{tip}^{{commit}}"], cwd=repo_a, capture_output=True
    )
    assert commit.returncode == 0

    # A resume that reruns nothing must keep the resolve without redispatching
    # it -- `_resolve_is_trusted` must check that tip in repo_a, not tmp_path.
    snapshot = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)
    resumed = run_mission(reloaded, home=home, resume_dir=Path(result.mission_dir))
    assert resumed.resolve == result.resolve


def test_repositories_field_lists_every_distinct_lane_cwd(
    repo_a, repo_b, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo hi > out.txt"])
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

    assert result.repositories == sorted([str(repo_a.resolve()), str(repo_b.resolve())])


# --- golden: record and replay a cross-repo mission ---------------------------


def test_record_and_replay_a_two_repository_mission_scrubs_clean(
    repo_a, repo_b, home, monkeypatch, tmp_path
):
    def envelope(answer: str) -> str:
        return json.dumps({"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}})

    def build(spec: Spec) -> list[str]:
        answer = "read a" if _token(spec) == "A" else "read b"
        return ["sh", "-c", f"echo '{envelope(answer)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = {
        "name": "cross-repo-golden",
        "prompt": "SPEC",
        "cwd": str(repo_a),
        "lanes": [
            {
                "name": "a",
                "fleet": "claude",
                "mode": "read",
                "prompt": "A",
            },
            {
                "name": "b",
                "fleet": "cursor",
                "mode": "read",
                "prompt": "B",
                "cwd": str(repo_b),
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True

    fixture = golden.record(Path(result.mission_dir), tmp_path / "fixture", home=home)

    snapshot = json.loads((fixture / "mission.json").read_text())
    assert snapshot["cwd"] == "<cwd>"
    lane_cwds = {lane["name"]: lane["attempts"][0]["cwd"] for lane in snapshot["lanes"]}
    assert lane_cwds["a"] is None  # matched the mission's own cwd -- no placeholder needed
    assert lane_cwds["b"] == "<cwd2>"

    guard_patterns = [(str(repo_a.resolve()), "repo a"), (str(repo_b.resolve()), "repo b")]
    assert golden.scrub_guard(fixture, extra=guard_patterns) == []
    assert golden.check(fixture) == []


def test_scrub_guard_finds_a_leaked_second_repository_path_exactly_as_the_first(tmp_path):
    """E19: `scrub_guard` cannot recover a real path from an already-scrubbed
    fixture on its own -- it must be told a repository's real path the same
    way it is already told the user's home and the conductor home. Given
    two such patterns, it decodes and finds a leak of either one, hidden
    inside base64 the same way a DSSE payload hides one (E13)."""
    repo_a_path = str(tmp_path / "repo-a")
    repo_b_path = str(tmp_path / "repo-b")
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()

    leak_a = base64.b64encode(f"seen in {repo_a_path}/src".encode()).decode()
    leak_b = base64.b64encode(f"seen in {repo_b_path}/src".encode()).decode()
    (fixture_dir / "a.json").write_text(json.dumps({"payload": leak_a}))
    (fixture_dir / "b.json").write_text(json.dumps({"payload": leak_b}))

    findings = golden.scrub_guard(
        fixture_dir, extra=[(repo_a_path, "repo a"), (repo_b_path, "repo b")]
    )
    assert any("repo a" in f and "(base64)" in f for f in findings)
    assert any("repo b" in f and "(base64)" in f for f in findings)
