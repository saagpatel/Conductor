"""An export bundle scrubs every repository a mission touched, not just one.

E26 lets a lane declare its own `cwd`, so a cross-repo mission has
repositories beyond `mission.cwd`. `golden.record` has always paired each of
them with a `<cwd2>`, `<cwd3>`, ... placeholder; `export.export` did not, so
those absolute paths travelled in the bundle -- and `scrub_guard`, which
cannot recover a real path it was never told about, reported the bundle clean.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conductor import export
from conductor import runner as runner_mod
from conductor.mission import mission_from_dict, run_mission


def _second_repo(tmp_path: Path) -> Path:
    path = tmp_path / "other-repo-with-a-distinctive-name"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "test")):
        subprocess.run(["git", "config", key, value], cwd=path, check=True)
    (path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=path, check=True)
    return path


def test_a_lanes_own_repository_is_a_placeholder_in_the_bundle(repo, home, monkeypatch, tmp_path):
    other = _second_repo(tmp_path)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo done in $PWD"]
    )
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "here", "fleet": "claude", "prompt": "READ it"},
            {"name": "there", "fleet": "claude", "prompt": "READ it", "cwd": str(other)},
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)

    snapshot = json.loads((home / "missions" / result.mission_id / "mission.json").read_text())
    recorded = {
        attempt.get("cwd")
        for lane in snapshot.get("lanes") or []
        for attempt in lane.get("attempts") or []
    }
    assert str(other) in recorded, "the fixture must actually produce a second-repository attempt"

    out = tmp_path / "bundle"
    export.export(home, result.mission_id, out)

    hits = [
        f"{path.relative_to(out)}"
        for path in sorted(p for p in out.rglob("*") if p.is_file())
        if str(other) in path.read_text(errors="replace")
    ]
    assert hits == [], f"the second repository's absolute path survived into {hits}"


def test_truncated_mission_json_still_scrubs_every_repository(repo, home, monkeypatch, tmp_path):
    """Item 7: export used to fall back to `{}` on a truncated mission.json
    and skip the cross-repo placeholder map. result.json still names every
    repository; recover from there rather than shipping a foreign path."""
    other = _second_repo(tmp_path)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo done in $PWD"]
    )
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "here", "fleet": "claude", "prompt": "READ it"},
            {"name": "there", "fleet": "claude", "prompt": "READ it", "cwd": str(other)},
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    (home / "missions" / result.mission_id / "mission.json").write_text("{not json")

    out = tmp_path / "bundle"
    export_result = export.export(home, result.mission_id, out)

    assert export_result.scope["scrub"]["mission_json"] == "unparseable"
    assert export_result.scope["scrub"]["cwd_from"] == "result.json"
    hits = [
        f"{path.relative_to(out)}"
        for path in sorted(p for p in out.rglob("*") if p.is_file())
        if str(other) in path.read_text(errors="replace")
    ]
    assert hits == [], f"the second repository's absolute path survived into {hits}"
