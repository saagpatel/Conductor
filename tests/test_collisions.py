"""Roadmap item D1, conflict-aware collate: a per-file collision picture
(`conductor.collisions`) computed over every sink lane's diff and clean tip,
folded into the mission result, the report, and the collate's own prompt,
plus a dedicated resolver lane dispatched only once the sinks actually
collide.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.collisions import merge_conflicts, overlap, touched_files
from conductor.fleets import Spec
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


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


def antigravity_strongest(strongest: str) -> list[str]:
    return antigravity_envelope(json.dumps({"strongest": strongest, "reason": "cheapest"}))


def _pick(table: dict[str, list[str]]):
    def build(spec: Spec) -> list[str]:
        return table[spec.prompt.split()[0]]

    return build


# --- touched_files ------------------------------------------------------------


def test_touched_files_added_deleted_renamed_and_binary():
    added = "diff --git a/new.txt b/new.txt\nnew file mode 100644\nindex 0000000..e69de29\n"
    deleted = "diff --git a/old.txt b/old.txt\ndeleted file mode 100644\nindex e69de29..0000000\n"
    renamed = (
        "diff --git a/old.txt b/new.txt\n"
        "similarity index 100%\n"
        "rename from old.txt\n"
        "rename to new.txt\n"
    )
    binary = (
        "diff --git a/image.png b/image.png\n"
        "new file mode 100644\n"
        "index 0000000..abcd123\n"
        "Binary files /dev/null and b/image.png differ\n"
    )
    assert touched_files(added) == ["new.txt"]
    assert touched_files(deleted) == ["old.txt"]
    assert touched_files(renamed) == ["new.txt", "old.txt"]
    assert touched_files(binary) == ["image.png"]
    assert touched_files(added + deleted + renamed) == ["new.txt", "old.txt"]
    assert touched_files("") == []


# --- overlap --------------------------------------------------------------------


def test_overlap_with_two_lanes_on_one_file():
    diffs = {
        "a": "diff --git a/shared.txt b/shared.txt\n@@ -0,0 +1 @@\n+a\n",
        "b": "diff --git a/shared.txt b/shared.txt\n@@ -0,0 +1 @@\n+b\n",
    }
    assert overlap(diffs) == {
        "files": {"shared.txt": ["a", "b"]},
        "hotspots": ["shared.txt"],
        "lanes": {"a": 1, "b": 1},
    }


def test_overlap_with_three_lanes_only_two_sharing():
    diffs = {
        "a": "diff --git a/shared.txt b/shared.txt\n",
        "b": "diff --git a/shared.txt b/shared.txt\ndiff --git a/only_b.txt b/only_b.txt\n",
        "c": "diff --git a/only_c.txt b/only_c.txt\n",
    }
    out = overlap(diffs)
    assert out["hotspots"] == ["shared.txt"]
    assert out["files"] == {
        "only_b.txt": ["b"],
        "only_c.txt": ["c"],
        "shared.txt": ["a", "b"],
    }
    assert out["lanes"] == {"a": 1, "b": 1, "c": 0}


def test_overlap_with_no_shared_files_has_no_hotspots():
    diffs = {"a": "diff --git a/a.txt b/a.txt\n", "b": "diff --git a/b.txt b/b.txt\n"}
    out = overlap(diffs)
    assert out["hotspots"] == []
    assert out["lanes"] == {"a": 0, "b": 0}


# --- merge_conflicts --------------------------------------------------------------


def test_merge_conflicts_reports_a_real_conflict_and_a_clean_pair(repo, git_out):
    git_out(repo, "checkout", "-b", "a")
    (repo / "conflict.txt").write_text("A\n")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", "a")
    tip_a = git_out(repo, "rev-parse", "HEAD")

    git_out(repo, "checkout", "main")
    git_out(repo, "checkout", "-b", "b")
    (repo / "conflict.txt").write_text("B\n")
    (repo / "clean.txt").write_text("clean\n")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", "b")
    tip_b = git_out(repo, "rev-parse", "HEAD")

    out = merge_conflicts(str(repo), {"a": tip_a, "b": tip_b})

    assert out["pairs"] == [{"lanes": ["a", "b"], "conflicts": ["conflict.txt"]}]
    assert out["files"] == {"conflict.txt": [["a", "b"]]}


def test_merge_conflicts_records_an_error_for_a_missing_tip_without_raising(repo, git_out):
    tip = git_out(repo, "rev-parse", "HEAD")
    missing = "f" * 40

    out = merge_conflicts(str(repo), {"a": tip, "b": missing})

    assert len(out["pairs"]) == 1
    pair = out["pairs"][0]
    assert pair["lanes"] == ["a", "b"]
    assert "error" in pair and "conflicts" not in pair
    assert out["files"] == {}


def test_merge_conflicts_pairs_every_combination_of_three_lanes(repo, git_out):
    tip = git_out(repo, "rev-parse", "HEAD")

    out = merge_conflicts(str(repo), {"a": tip, "b": tip, "c": tip})

    assert [tuple(p["lanes"]) for p in out["pairs"]] == [("a", "b"), ("a", "c"), ("b", "c")]
    assert all(p["conflicts"] == [] for p in out["pairs"])


# --- collisions on a mission result -----------------------------------------------

TWO_SINK_HOTSPOT = {
    "prompt": "SPEC",
    "lanes": [
        {"name": "a", "fleet": "codex", "mode": "write", "prompt": "A", "commit": "feat: a"},
        {"name": "b", "fleet": "claude", "mode": "write", "prompt": "B", "commit": "feat: b"},
    ],
}
HOTSPOT_BUILD = {
    "A": ["sh", "-c", "echo v1 > shared.txt"],
    "B": ["sh", "-c", "echo v2 > shared.txt"],
}
DIFFERENT_FILES_BUILD = {
    "A": ["sh", "-c", "echo v1 > a.txt"],
    "B": ["sh", "-c", "echo v2 > b.txt"],
}


def test_mission_result_and_report_carry_a_hotspot(repo, home, monkeypatch, tmp_path):
    monkeypatch.setattr(runner_mod, "build_argv", _pick(HOTSPOT_BUILD))
    mission = mission_from_dict(TWO_SINK_HOTSPOT | {"cwd": str(repo)}, base_dir=tmp_path)

    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.collisions is not None
    assert result.collisions["hotspots"] == ["shared.txt"]
    assert result.collisions["overlap"]["files"] == {"shared.txt": ["a", "b"]}
    assert result.collisions["conflicts"]["pairs"] == [
        {"lanes": ["a", "b"], "conflicts": ["shared.txt"]}
    ]

    report = Path(result.report_path).read_text()
    assert "## Collisions" in report
    assert "`shared.txt`: a, b (conflict: a, b)" in report

    saved = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved["collisions"] == result.collisions

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))


def test_collate_prompt_gets_the_collisions_section_when_hotspots_exist(
    repo, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "antigravity":
            return antigravity_envelope("pick a")
        return HOTSPOT_BUILD[spec.prompt.split()[0]]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = TWO_SINK_HOTSPOT | {
        "cwd": str(repo),
        "collate": {"fleet": "antigravity", "instructions": "Pick one."},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)

    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.collate["ok"] is True
    prompt = Path(result.mission_dir, "collate-prompt.txt").read_text()
    assert "## Collisions" in prompt
    assert "`shared.txt`: a, b" in prompt


def test_cli_missions_reports_the_hotspot_count(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(runner_mod, "build_argv", _pick(HOTSPOT_BUILD))
    mission = mission_from_dict(TWO_SINK_HOTSPOT | {"cwd": str(repo)}, base_dir=tmp_path)
    result = run_mission(mission, home=home)

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["missions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(item for item in rows if item["mission_id"] == result.mission_id)
    assert row["hotspots"] == 1
    assert row["resolve"] is None


def test_different_files_leave_no_hotspot_or_collate_section(
    repo, home, monkeypatch, tmp_path, capsys
):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "antigravity":
            return antigravity_envelope("pick a")
        return DIFFERENT_FILES_BUILD[spec.prompt.split()[0]]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = TWO_SINK_HOTSPOT | {
        "cwd": str(repo),
        "collate": {"fleet": "antigravity", "instructions": "Pick one."},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)

    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.collisions is not None
    assert result.collisions["hotspots"] == []

    report = Path(result.report_path).read_text()
    assert "## Collisions" not in report

    prompt = Path(result.mission_dir, "collate-prompt.txt").read_text()
    assert "## Collisions" not in prompt

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["missions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(item for item in rows if item["mission_id"] == result.mission_id)
    assert row["hotspots"] == 0


# --- resolve -----------------------------------------------------------------------

RESOLVE_HOTSPOT = TWO_SINK_HOTSPOT | {"resolve": {"fleet": "cursor", "commit": "merge: reconcile"}}
RESOLVE_DIFFERENT_FILES = TWO_SINK_HOTSPOT | {
    "resolve": {"fleet": "cursor", "commit": "merge: reconcile"}
}


def test_resolve_runs_when_hotspots_exist_and_commits(repo, home, monkeypatch, tmp_path):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "cursor":
            return ["sh", "-c", "echo merged > resolved.txt"]
        return HOTSPOT_BUILD[spec.prompt.split()[0]]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    mission = mission_from_dict(RESOLVE_HOTSPOT | {"cwd": str(repo)}, base_dir=tmp_path)

    result = run_mission(mission, home=home)

    assert result.ok is True
    assert result.resolve["ran"] is True
    assert result.resolve["ok"] is True
    assert result.resolve["hotspots"] == ["shared.txt"]
    assert result.resolve["branch"]
    assert result.resolve["tip"]

    saved = json.loads(Path(result.mission_dir, "result.json").read_text())
    assert saved["resolve"] == result.resolve

    prompt = Path(result.mission_dir, "resolve-prompt.txt").read_text()
    assert "## Collisions" in prompt and "shared.txt" in prompt
    assert "Lane `a`'s patch" in prompt and "Lane `b`'s patch" in prompt

    report = Path(result.report_path).read_text()
    assert "## Resolve" in report
    assert f"branch `{result.resolve['branch']}`" in report


def test_resolve_skipped_with_reason_when_there_are_no_hotspots(
    repo, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_argv", _pick(DIFFERENT_FILES_BUILD))
    mission = mission_from_dict(RESOLVE_DIFFERENT_FILES | {"cwd": str(repo)}, base_dir=tmp_path)

    result = run_mission(mission, home=home)

    assert result.resolve == {"ran": False, "reason": "no hotspots"}
    report = Path(result.report_path).read_text()
    assert "## Resolve" in report and "Skipped: no hotspots" in report


def test_resolve_refused_on_a_one_sink_mission(tmp_path):
    raw = {
        "prompt": "x",
        "lanes": [{"fleet": "codex"}],
        "resolve": {"fleet": "claude"},
    }
    with pytest.raises(MissionInvalid, match="resolve needs at least two sink lanes"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_resolve_refuses_an_unknown_fleet(tmp_path):
    raw = TWO_SINK_HOTSPOT | {"resolve": {"fleet": "not-a-fleet"}}
    with pytest.raises(MissionInvalid, match="resolve"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_resolve_dispatches_nothing_on_a_dry_run(repo, home, tmp_path):
    mission = mission_from_dict(RESOLVE_HOTSPOT | {"cwd": str(repo)}, base_dir=tmp_path)

    result = run_mission(mission, home=home, dry_run=True)

    assert result.resolve == {"ran": False, "reason": "dry run"}
    assert not (Path(result.mission_dir) / "resolve-prompt.txt").exists()


def test_resolve_prompt_names_the_rank_collates_strongest_lane(
    repo, home, monkeypatch, tmp_path
):
    def build(spec: Spec) -> list[str]:
        if spec.fleet == "antigravity":
            return antigravity_strongest("a")
        if spec.fleet == "cursor":
            return ["sh", "-c", "echo merged > resolved.txt"]
        return HOTSPOT_BUILD[spec.prompt.split()[0]]

    monkeypatch.setattr(runner_mod, "build_argv", build)
    raw = RESOLVE_HOTSPOT | {
        "cwd": str(repo),
        "collate": {"fleet": "antigravity", "rank": True},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)

    result = run_mission(mission, home=home)

    assert result.collate["ok"] is True and result.collate["strongest"] == "a"
    assert result.resolve["ran"] is True

    prompt = Path(result.mission_dir, "resolve-prompt.txt").read_text()
    assert "The collate judged lane `a` the strongest candidate." in prompt


def test_resolve_snapshot_round_trips(repo, home, tmp_path):
    raw = RESOLVE_HOTSPOT | {
        "cwd": str(repo),
        "resolve": {"fleet": "cursor", "commit": "merge: x", "max_chars": 500},
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)

    result = run_mission(mission, home=home, dry_run=True)
    snapshot = json.loads(Path(result.mission_dir, "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot)

    assert reloaded.to_dict() == mission.to_dict() == snapshot
    assert reloaded.resolve.fleet == "cursor" and reloaded.resolve.max_chars == 500


# --- documentation ---------------------------------------------------------------


def test_readme_documents_collisions_and_resolve():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split(
        "#### Conflict-aware collate: collisions and a resolver lane", 1
    )[1].split("\n### ", 1)[0]
    assert "27.7% conflict rate across 107k" in section
    assert "git merge-tree --write-tree --name-only" in section
    assert '"hotspots": <count or null>' in section
    assert "no hotspots" in section
    assert '"resolve": "ok" | "failed" | "skipped" | null' in section
