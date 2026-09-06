"""E7: human lanes.

A lane whose fleet is the operator: never dispatched, always tainted, and
resolved by pausing the mission for an operator's answer rather than a
fleet's own output -- see the module docstring's C2/D2/E1 cross-references
in mission.py's `_human_lane`, the scheduler's human-lane park, and
`run_mission`'s `_answer_human_pause`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.mission import Mission, MissionInvalid, mission_from_dict, run_mission


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def by_prompt(monkeypatch, table: dict[str, tuple[str, float | None]]) -> None:
    def pick(spec):
        answer, cost = table[spec.prompt.split()[0]]
        return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", pick)


def _snapshot(result) -> Mission:
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    return Mission.from_snapshot(raw)


def _pipeline(repo, extra: dict | None = None) -> dict:
    raw = {
        "cwd": str(repo),
        "concurrency": 1,
        "lanes": [
            {"name": "build", "fleet": "claude", "prompt": "BUILD"},
            {
                "name": "ask",
                "fleet": "human",
                "prompt": "Approve this: {{lanes.build.answer}}",
                "needs": ["build"],
            },
            {
                "name": "use",
                "fleet": "claude",
                "prompt": "USE {{lanes.ask.answer}}",
                "needs": ["ask"],
            },
        ],
    }
    if extra:
        raw |= extra
    return raw


# --- item 1: load-time validation and taint derivation -----------------------


def test_human_lane_refuses_fallback(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set fallback"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {
                        "name": "ask",
                        "fleet": "human",
                        "prompt": "approve?",
                        "fallback": [{"fleet": "claude"}],
                    }
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_cascade(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set cascade"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [{"name": "ask", "fleet": "human", "prompt": "approve?", "cascade": True}],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_stage(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set stage"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {"name": "ask", "fleet": "human", "prompt": "approve?", "stage": "review"}
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_branch(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set branch"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {"name": "ask", "fleet": "human", "prompt": "approve?", "branch": "sign-off"}
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_base(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set base"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "prompt": "x",
                "lanes": [
                    {"name": "build", "fleet": "codex"},
                    {
                        "name": "ask",
                        "fleet": "human",
                        "prompt": "approve?",
                        "needs": ["build"],
                        "base": "build",
                    },
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_resume(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set resume"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "prompt": "x",
                "lanes": [
                    {"name": "build", "fleet": "codex"},
                    {
                        "name": "ask",
                        "fleet": "human",
                        "prompt": "approve?",
                        "needs": ["build"],
                        "resume": "build",
                    },
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_a_test(tmp_path):
    """Even a mission-level `test` default, cascaded onto every lane like
    any other inherited field, must not reach a lane with nothing to gate."""
    with pytest.raises(MissionInvalid, match="may not set test"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "test": "pytest -q",
                "lanes": [{"name": "ask", "fleet": "human", "prompt": "approve?"}],
            },
            base_dir=tmp_path,
        )


def test_human_lane_refuses_every_other_attempt_key(tmp_path):
    with pytest.raises(MissionInvalid, match="may not set effort"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {"name": "ask", "fleet": "human", "prompt": "approve?", "effort": "hard"}
                ],
            },
            base_dir=tmp_path,
        )


def test_lane_may_not_build_on_a_human_lane(tmp_path):
    with pytest.raises(MissionInvalid, match="is a human lane, which holds no commit"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "prompt": "x",
                "lanes": [
                    {"name": "ask", "fleet": "human", "prompt": "approve?"},
                    {"name": "build", "fleet": "codex", "needs": ["ask"], "base": "ask"},
                ],
            },
            base_dir=tmp_path,
        )


def test_lane_may_not_resume_a_human_lane(tmp_path):
    with pytest.raises(MissionInvalid, match="is a human lane, which holds no session"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "prompt": "x",
                "lanes": [
                    {"name": "ask", "fleet": "human", "prompt": "approve?"},
                    {"name": "use", "fleet": "claude", "needs": ["ask"], "resume": "ask"},
                ],
            },
            base_dir=tmp_path,
        )


@pytest.mark.parametrize("field", ["diff", "test_touched", "verdict"])
def test_referencing_a_human_lanes_diff_test_touched_or_verdict_is_refused(tmp_path, field):
    with pytest.raises(MissionInvalid, match=f"is a human lane with no {field}"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {"name": "ask", "fleet": "human", "prompt": "approve?"},
                    {
                        "name": "use",
                        "fleet": "claude",
                        "needs": ["ask"],
                        "prompt": f"{{{{lanes.ask.{field}}}}}",
                    },
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_deliverable_rejects_extra_keys(tmp_path):
    with pytest.raises(MissionInvalid, match="may only set 'path'"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {
                        "name": "ask",
                        "fleet": "human",
                        "prompt": "approve?",
                        "deliverable": {"path": "x.txt", "schema": "s.json"},
                    }
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_deliverable_rejects_absolute_path(tmp_path):
    with pytest.raises(MissionInvalid, match="must be repo-relative"):
        mission_from_dict(
            {
                "cwd": str(tmp_path),
                "lanes": [
                    {
                        "name": "ask",
                        "fleet": "human",
                        "prompt": "approve?",
                        "deliverable": {"path": "/etc/passwd"},
                    }
                ],
            },
            base_dir=tmp_path,
        )


def test_human_lane_is_tainted_at_load_always(tmp_path):
    mission = mission_from_dict(
        {
            "cwd": str(tmp_path),
            "lanes": [{"name": "ask", "fleet": "human", "prompt": "approve?"}],
        },
        base_dir=tmp_path,
    )
    lane = mission.lanes[0]
    assert lane.human is True
    assert lane.taint is True
    assert lane.tainted is True
    assert lane.taint_from == ["human"]


def test_human_lane_snapshot_round_trips(tmp_path):
    mission = mission_from_dict(
        {
            "cwd": str(tmp_path),
            "prompt": "x",
            "lanes": [
                {"name": "build", "fleet": "codex"},
                {
                    "name": "ask",
                    "fleet": "human",
                    "prompt": "Approve {{lanes.build.answer}}?",
                    "needs": ["build"],
                    "deliverable": {"path": "sign.txt"},
                },
            ],
        },
        base_dir=tmp_path,
    )
    raw = mission.to_dict()
    reloaded = Mission.from_snapshot(raw)
    assert reloaded.to_dict() == raw
    assert reloaded.lanes[1].human is True
    assert reloaded.lanes[1].tainted is True


# --- items 2/3/4: pausing, answering, resuming, reporting --------------------


def test_human_lane_parks_the_mission_and_renders_the_ask(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(
        _pipeline(repo, {"notify": {"command": "true", "events": ["pause"]}}), base_dir=tmp_path
    )
    result = run_mission(mission, home=home)
    build, ask, use = result.lanes

    assert build["ok"] is True
    assert ask["ok"] is False
    assert ask["attempts"] == []
    assert ask["cost_usd"] == 0
    assert ask["skipped"] == "paused: waiting for the operator"
    assert use["skipped"].startswith("paused:")
    assert result.ok is False

    assert result.paused["kind"] == "human"
    assert result.paused["lane"] == "ask"
    ask_path = Path(result.paused["ask_path"])
    assert ask_path.is_file()
    ask_text = ask_path.read_text()
    assert "built" in ask_text
    assert "Approve this:" in ask_text
    assert "answer with --answer TEXT or --answer-file PATH" in result.paused["question"]

    pause_doc = json.loads((Path(result.mission_dir) / "pause.json").read_text())
    assert pause_doc["kind"] == "human"
    assert pause_doc["lane"] == "ask"
    assert pause_doc["ask_path"] == str(ask_path)
    assert pause_doc["answer"] is None
    assert pause_doc["answers"] == []

    assert len(result.notifications) == 1
    assert result.notifications[0]["event"] == "pause"
    assert result.notifications[0]["ok"] is True


def test_report_resume_line_names_a_human_lanes_actual_answer_forms(
    repo, home, monkeypatch, tmp_path
):
    """Review finding (Grok, item 4): a human pause's report.md must not tell
    the operator to resume with `--answer continue|stop` -- `continue` is
    refused on a human pause (`a human lane needs an answer`); the forms
    that actually work are `--answer TEXT` and `--answer-file PATH`."""
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    result = run_mission(mission, home=home)
    report = Path(result.report_path).read_text()
    assert "--answer continue|stop" not in report
    assert "--answer TEXT" in report or "--answer-file PATH" in report


def test_answer_text_resumes_and_the_answer_is_fenced_downstream(
    repo, home, monkeypatch, tmp_path
):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)

    captured: dict = {}

    def fake_use(spec):
        captured["prompt"] = spec.prompt
        return ["sh", "-c", f"echo '{envelope('used')}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_use)
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="ship it"
    )

    assert resumed.ok is True
    build, ask, use = resumed.lanes
    assert ask["ok"] is True
    assert ask["kept"] is True
    assert ask["attempts"] == []
    assert use["ok"] is True

    answer_path = Path(resumed.mission_dir) / "answers" / "ask.txt"
    assert answer_path.read_text() == "ship it"
    assert ask["answer_path"] == str(answer_path)

    assert "ship it" in captured["prompt"]
    assert "tainted: came from outside the operator's trust" in captured["prompt"]

    pause_doc = json.loads((Path(resumed.mission_dir) / "pause.json").read_text())
    assert pause_doc["answer"] == "answered"
    assert len(pause_doc["answers"]) == 1
    record = pause_doc["answers"][0]
    assert record["kind"] == "human"
    assert record["lane"] == "ask"
    assert record["answer_length"] == len("ship it")
    assert record["answered_at"]
    # The text itself is on disk once (answers/ask.txt), never a second time.
    assert "ship it" not in json.dumps(pause_doc)

    # Review finding (Grok, item 2): the per-lane receipt file is the durable
    # record salvage, golden recording, and any later inspector reads --
    # `settle` is the only writer of it, but a kept human lane never goes
    # through `settle`, so answering must not leave it saying the lane never
    # started.
    lane_receipt = json.loads((Path(resumed.mission_dir) / "lanes" / "ask.json").read_text())
    assert lane_receipt["ok"] is True
    assert lane_receipt["skipped"] is None
    assert lane_receipt["answer_path"] == str(answer_path)


def test_answer_file_resumes(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)

    answer_file = tmp_path / "answer.txt"
    answer_file.write_text("from a file\n")
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('used')}'"]
    )
    resumed = run_mission(
        _snapshot(first),
        home=home,
        resume_dir=Path(first.mission_dir),
        answer_file=str(answer_file),
    )

    assert resumed.ok is True
    answer_path = Path(resumed.mission_dir) / "answers" / "ask.txt"
    assert answer_path.read_text() == "from a file\n"


def test_answer_and_answer_file_are_mutually_exclusive(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    with pytest.raises(MissionInvalid, match="mutually exclusive"):
        run_mission(
            _snapshot(first),
            home=home,
            resume_dir=Path(first.mission_dir),
            answer="x",
            answer_file=str(tmp_path / "a.txt"),
        )


def test_deliverable_present_and_missing_at_answer_time(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    raw = _pipeline(repo)
    raw["lanes"][1]["deliverable"] = {"path": "sign-off.txt"}
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)

    with pytest.raises(MissionInvalid, match="does not exist"):
        run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="ok")

    (repo / "sign-off.txt").write_text("signed\n")
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('used')}'"]
    )
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="ok"
    )
    assert resumed.ok is True
    ask = next(lane for lane in resumed.lanes if lane["name"] == "ask")
    assert ask["deliverable_path"] is not None
    assert Path(ask["deliverable_path"]).read_text() == "signed\n"


def test_answer_continue_is_refused_on_a_human_pause(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    with pytest.raises(MissionInvalid, match="a human lane needs an answer"):
        run_mission(
            _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
        )


def test_answer_stop_on_a_human_pause(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    stopped = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="stop"
    )
    assert stopped.ok is False
    assert stopped.paused["kind"] == "human"
    assert stopped.paused["answer"] == "stop"
    ask = next(lane for lane in stopped.lanes if lane["name"] == "ask")
    assert ask["skipped"] == "paused: operator answered stop"

    pause_doc = json.loads((Path(stopped.mission_dir) / "pause.json").read_text())
    assert pause_doc["answer"] == "stop"
    assert pause_doc["answers"][0]["answer"] == "stop"


def test_second_resume_keeps_the_answered_human_lane_without_reasking(
    repo, home, monkeypatch, tmp_path
):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('used')}'"]
    )
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="ok"
    )
    assert resumed.ok is True

    def refuse(spec):
        raise AssertionError("nothing should dispatch on a no-op resume")

    monkeypatch.setattr(runner_mod, "build_argv", refuse)
    again = run_mission(_snapshot(resumed), home=home, resume_dir=Path(resumed.mission_dir))
    assert again.ok is True
    ask = next(lane for lane in again.lanes if lane["name"] == "ask")
    assert ask["kept"] is True
    assert (Path(resumed.mission_dir) / "asks" / "ask.txt").is_file()
    # A second, unrelated ask was never written.
    assert len(list((Path(resumed.mission_dir) / "asks").glob("*.txt"))) == 1


def test_report_shows_the_human_lane_as_answered(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('used')}'"]
    )
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="ok"
    )
    report = Path(resumed.report_path).read_text()
    assert "human, answered" in report


def test_answer_file_refused_on_a_lane_pause(repo, home, monkeypatch, tmp_path):
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(
        {
            "cwd": str(repo),
            "pause": {"before": ["build"]},
            "lanes": [{"name": "build", "fleet": "claude", "prompt": "BUILD"}],
        },
        base_dir=tmp_path,
    )
    first = run_mission(mission, home=home)
    answer_file = tmp_path / "a.txt"
    answer_file.write_text("x")
    with pytest.raises(MissionInvalid, match="only applies to a human lane"):
        run_mission(
            _snapshot(first),
            home=home,
            resume_dir=Path(first.mission_dir),
            answer_file=str(answer_file),
        )


# --- item 5: documentation and CLI --------------------------------------------


def test_cli_answer_file_resumes_a_human_lane(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission_path = tmp_path / "m.json"
    mission_path.write_text(json.dumps(_pipeline(repo)))
    assert main(["mission", str(mission_path)]) == 4
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    answer_file = tmp_path / "answer.txt"
    answer_file.write_text("approved via file")
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('used')}'"]
    )
    exit_code = main(["mission", "--resume", mission_id, "--answer-file", str(answer_file)])
    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert summary["ok"] is True


def test_readme_documents_human_lanes():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("### Human lanes", 1)[1].split("\n#### ", 1)[0]
    section = " ".join(section.split())
    assert '"fleet": "human"' in section
    assert "taint_from` records `human`" in section
    assert "answer with --answer TEXT or" in section
    assert "kind: \"human\"" in section
    assert "conductor mission --resume MISSION_ID --answer-file notes.txt" in section
    assert "never the text itself a second time" in section


def test_answered_human_lane_is_trusted_through_trusted_lane_on_its_answer_file(
    repo, home, monkeypatch, tmp_path
):
    """Grok's fourth E7 finding: the trust helper itself accepts an answered
    human lane on its answer file, and rejects a receipt whose answer_path
    points elsewhere or a lane whose answer file is gone."""
    from conductor.mission import LaneResult, _trusted_lane

    by_prompt(monkeypatch, {"BUILD": ("built", None)})
    mission = mission_from_dict(_pipeline(repo), base_dir=tmp_path)
    first = run_mission(mission, home=home)
    monkeypatch.setattr(
        runner_mod, "build_argv", lambda spec: ["sh", "-c", f"echo '{envelope('used')}'"]
    )
    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="ok"
    )
    mission_dir = Path(resumed.mission_dir)
    lane = next(lane for lane in mission.lanes if lane.name == "ask")
    receipt = LaneResult.from_dict(json.loads((mission_dir / "lanes" / "ask.json").read_text()))
    # The receipt on disk was rewritten by the answer (Grok's second finding).
    assert receipt.ok is True and receipt.skipped is None
    assert receipt.answer_path == str(mission_dir / "answers" / "ask.txt")
    assert _trusted_lane(mission, mission_dir, lane, receipt) is True
    elsewhere = LaneResult.from_dict({**receipt.to_dict(), "answer_path": str(tmp_path / "x")})
    assert _trusted_lane(mission, mission_dir, lane, elsewhere) is False
    (mission_dir / "answers" / "ask.txt").unlink()
    assert _trusted_lane(mission, mission_dir, lane, receipt) is False
