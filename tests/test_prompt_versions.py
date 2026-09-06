"""E17: prompt versioning through golden replay."""

from __future__ import annotations

import json
from pathlib import Path

from conductor import golden, prompts
from conductor import mission as mission_mod
from conductor import runner as runner_mod
from conductor.cli import main
from conductor.mission import mission_from_dict, run_mission


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


def _two_lane_mission_raw(repo: Path) -> dict:
    return {
        "name": "prompt-version-smoke",
        "prompt": "write, then read it back",
        "cwd": str(repo),
        "lanes": [
            {"name": "write", "fleet": "claude", "mode": "write", "commit": "feat: golden"},
            {
                "name": "read",
                "fleet": "cursor",
                "mode": "read",
                "needs": ["write"],
                "prompt": "read the lane answer: {{lanes.write.answer}}",
            },
        ],
    }


def _record_smoke_mission(repo, home, monkeypatch, tmp_path) -> Path:
    fake_fleets(
        monkeypatch,
        {
            "claude": ["sh", "-c", f"echo x > x.txt && echo '{envelope('done', 0.01)}'"],
            "cursor": say("ok"),
        },
    )
    m = mission_from_dict(_two_lane_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(m, home=home)
    assert result.ok is True
    return Path(result.mission_dir)


# --- the id map --------------------------------------------------------


def test_prompt_versions_is_stable_across_two_calls():
    assert prompts.prompt_versions() == prompts.prompt_versions()


def test_prompt_versions_moves_when_a_text_moves(monkeypatch):
    before = prompts.prompt_versions()
    monkeypatch.setattr(
        mission_mod, "DEFAULT_COLLATE_INSTRUCTIONS", "a different collate default entirely"
    )
    after = prompts.prompt_versions()
    assert after["collate_default"] != before["collate_default"]
    assert after["resolve_default"] == before["resolve_default"]


# --- the ids on a mission result -----------------------------------------


def test_mission_result_carries_the_whole_prompt_version_catalog(repo, home, monkeypatch, tmp_path):
    fake_fleets(
        monkeypatch,
        {
            "claude": ["sh", "-c", f"echo x > x.txt && echo '{envelope('done', 0.01)}'"],
            "cursor": say("ok"),
        },
    )
    m = mission_from_dict(_two_lane_mission_raw(repo), base_dir=tmp_path)
    result = run_mission(m, home=home)
    assert result.ok is True
    assert result.prompt_versions == prompts.prompt_versions()


# --- prompt_sha256 in the projection, and its backfill -------------------


def test_projection_carries_prompt_sha256(repo, home, monkeypatch, tmp_path):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    expected = json.loads((fixture / "expected.json").read_text())
    shas = [a["prompt_sha256"] for lane in expected["lanes"] for a in lane["attempts"]]
    assert shas and all(isinstance(sha, str) for sha in shas)


def test_check_backfills_a_missing_prompt_sha256_then_fails_after_a_prompt_edit(
    repo, home, monkeypatch, tmp_path
):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)

    # A fixture recorded before this field existed: strip it back out.
    expected_path = fixture / "expected.json"
    expected = json.loads(expected_path.read_text())
    for lane in expected["lanes"]:
        for attempt in lane["attempts"]:
            del attempt["prompt_sha256"]
    expected_path.write_text(json.dumps(expected, indent=2))

    # check backfills the key from the recording itself and passes clean.
    assert golden.check(fixture) == []

    # A prompt this fixture's "read" lane actually used has since changed:
    # the rendered prompt at replay time no longer matches the recording.
    snapshot = json.loads((fixture / "mission.json").read_text())
    for lane in snapshot["lanes"]:
        if lane["name"] == "read":
            lane["attempts"][0]["prompt"] = "please read very carefully: {{lanes.write.answer}}"
    (fixture / "mission.json").write_text(json.dumps(snapshot, indent=2))

    diffs = golden.check(fixture)
    assert any("prompt_sha256" in line for line in diffs)
    assert any("lane read" in line and "rendered prompt differs" in line for line in diffs)


# --- version_drift ---------------------------------------------------------


def test_version_drift_names_a_moved_prompt_id(repo, home, monkeypatch, tmp_path):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)

    manifest_path = fixture / "golden.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["prompt_versions"]["collate_default"] = "deadbeefcafe"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    lines = golden.version_drift(fixture)
    assert any(
        line.startswith("prompt collate_default recorded deadbeefcafe, now ") for line in lines
    )


def test_version_drift_reports_unknown_when_the_fixture_predates_the_field(
    repo, home, monkeypatch, tmp_path
):
    mission_dir = _record_smoke_mission(repo, home, monkeypatch, tmp_path)
    fixture = golden.record(mission_dir, tmp_path / "fixture", home=home)
    manifest_path = fixture / "golden.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["prompt_versions"]
    manifest_path.write_text(json.dumps(manifest, indent=2))
    assert "prompt versions unknown" in golden.version_drift(fixture)


# --- the launcher's prompts/ convention ------------------------------------


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n2. two\n")
    return spec


def test_shape_a_writes_prompt_files_that_load_through_prompt_file(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out),
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    for lane in raw["lanes"]:
        assert "prompt" not in lane
        prompt_path = out.parent / lane["prompt_file"]
        assert prompt_path.is_file()
    mission = mission_from_dict(raw, base_dir=out.parent)
    assert all(lane.attempts[0].prompt for lane in mission.lanes)


def test_shape_a_inline_keeps_prompts_in_the_mission_file(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out), "--inline",
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    for lane in raw["lanes"]:
        assert "prompt_file" not in lane
        assert lane["prompt"]
    assert not (out.parent / "prompts").exists()
