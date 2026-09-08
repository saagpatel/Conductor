"""C2: the pause primitive.

A mission may declare pause points -- `pause.before` naming lanes, and
`pause.spend_usd` a ledger threshold -- that park the mission for the
operator instead of a fleet's own output ever being trusted to ask for one.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor.cli import main
from conductor.mission import (
    Ledger,
    Mission,
    MissionInvalid,
    mission_from_dict,
    run_mission,
)
from conductor.runner import clear_stop, request_stop


@pytest.fixture(autouse=True)
def _fresh_stop():
    clear_stop()
    yield
    clear_stop()


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def by_prompt(monkeypatch, table: dict[str, tuple[str, float | None]]) -> None:
    """Fake fleets chosen by the first word of the prompt, so each lane in a
    flat pipeline can be given its own scripted answer and cost."""

    def pick(spec):
        answer, cost = table[spec.prompt.split()[0]]
        return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", pick)


def _snapshot(result) -> Mission:
    raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    return Mission.from_snapshot(raw)


PIPELINE = {
    "concurrency": 1,
    "pause": {"before": ["fix"]},
    "lanes": [
        {"name": "build", "fleet": "claude", "prompt": "BUILD"},
        {"name": "review", "fleet": "claude", "prompt": "REVIEW", "needs": ["build"]},
        {"name": "fix", "fleet": "claude", "prompt": "FIX", "needs": ["review"]},
    ],
}


# --- item 1: validation -----------------------------------------------------


def test_pause_refused_when_empty(tmp_path):
    with pytest.raises(MissionInvalid, match="pause needs"):
        mission_from_dict(
            {"prompt": "x", "lanes": [{"fleet": "codex", "name": "a"}], "pause": {}},
            base_dir=tmp_path,
        )


def test_pause_before_refused_naming_an_unknown_lane(tmp_path):
    with pytest.raises(MissionInvalid, match="unknown lane 'zzz'"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex", "name": "a"}],
                "pause": {"before": ["zzz"]},
            },
            base_dir=tmp_path,
        )


def test_pause_spend_usd_must_be_positive(tmp_path):
    with pytest.raises(MissionInvalid, match="must be positive"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex", "name": "a"}],
                "pause": {"spend_usd": 0},
            },
            base_dir=tmp_path,
        )


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "nan", "inf"])
def test_pause_spend_usd_refuses_a_boolean_or_non_finite_threshold(tmp_path, bad):
    """`float(True)` is 1.0, and `spent >= NaN` is never true, so a boolean
    or non-finite threshold loaded as a pause that could never fire.
    `max_cost_usd` already refuses this shape."""
    with pytest.raises(MissionInvalid, match="pause.spend_usd must be a positive finite number"):
        mission_from_dict(
            {
                "prompt": "x",
                "lanes": [{"fleet": "codex", "name": "a"}],
                "pause": {"spend_usd": bad},
            },
            base_dir=tmp_path,
        )


def test_pause_spend_usd_must_be_below_max_cost_usd(tmp_path):
    with pytest.raises(MissionInvalid, match="must be below max_cost_usd"):
        mission_from_dict(
            {
                "prompt": "x",
                "max_cost_usd": 5,
                "lanes": [{"fleet": "codex", "name": "a"}],
                "pause": {"spend_usd": 5},
            },
            base_dir=tmp_path,
        )


def test_pause_snapshot_round_trips(tmp_path):
    mission = mission_from_dict(
        {
            "prompt": "x",
            "max_cost_usd": 5,
            "lanes": [
                {"fleet": "codex", "name": "a"},
                {"fleet": "codex", "name": "b"},
            ],
            "pause": {"before": ["b"], "spend_usd": 1},
        },
        base_dir=tmp_path,
    )
    raw = mission.to_dict()
    reloaded = Mission.from_snapshot(raw)
    assert reloaded.pause == {"before": ["b"], "spend_usd": 1.0}
    assert reloaded.to_dict() == raw


# --- item 2: parking the mission --------------------------------------------


def test_pause_before_parks_the_mission_and_skips_the_named_lane(
    repo, home, monkeypatch, tmp_path
):
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    build, review, fix = result.lanes

    assert build["ok"] is True and review["ok"] is True
    assert fix["attempts"] == []
    assert fix["skipped"] == "paused: lane fix is a pause point; not started"
    assert result.ok is False

    expected_question = "Lane 'fix' is a pause point; continue the mission?"
    assert result.paused == {
        "kind": "lane",
        "lane": "fix",
        "spent_usd": None,
        "threshold": None,
        "question": expected_question,
    }

    pause_doc = json.loads((Path(result.mission_dir) / "pause.json").read_text())
    assert pause_doc["kind"] == "lane" and pause_doc["lane"] == "fix"
    assert pause_doc["answer"] is None and pause_doc["answers"] == []
    assert pause_doc["question"] == expected_question

    result_doc = json.loads((Path(result.mission_dir) / "result.json").read_text())
    assert result_doc["paused"] == result.paused
    assert result_doc["collate"] is None

    report = Path(result.report_path).read_text()
    assert f"**Paused**: {expected_question}" in report
    assert (
        f"Resume with: conductor mission --resume {result.mission_id} --answer continue|stop"
        in report
    )


def test_cli_exits_4_when_a_mission_parks(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission_path = tmp_path / "m.json"
    mission_path.write_text(json.dumps(PIPELINE | {"cwd": str(repo)}))
    assert main(["mission", str(mission_path)]) == 4


# --- item 3: resuming with an answer ----------------------------------------


def test_resume_without_an_answer_is_refused_with_the_question(
    repo, home, monkeypatch, tmp_path
):
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    with pytest.raises(MissionInvalid, match="is paused: Lane 'fix' is a pause point"):
        run_mission(_snapshot(first), home=home, resume_dir=Path(first.mission_dir))


def test_answer_continue_runs_the_parked_lane_without_repaying_finished_ones(
    repo, home, monkeypatch, tmp_path
):
    calls = {"BUILD": 0, "REVIEW": 0, "FIX": 0}

    def fake_build(spec):
        key = spec.prompt.split()[0]
        calls[key] += 1
        return ["sh", "-c", f"echo '{envelope(key.lower())}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert calls == {"BUILD": 1, "REVIEW": 1, "FIX": 0}

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )

    assert calls == {"BUILD": 1, "REVIEW": 1, "FIX": 1}  # build and review were not paid again
    assert resumed.ok is True
    assert [lane["kept"] for lane in resumed.lanes] == [True, True, False]
    assert resumed.paused is None

    pause_doc = json.loads((Path(resumed.mission_dir) / "pause.json").read_text())
    assert pause_doc["answer"] == "continue"
    assert len(pause_doc["answers"]) == 1
    assert pause_doc["answers"][0]["kind"] == "lane"
    assert pause_doc["answers"][0]["lane"] == "fix"
    assert pause_doc["answers"][0]["answer"] == "continue"
    assert pause_doc["answers"][0]["answered_at"]


def test_paused_s_covers_the_wait_and_launched_at_carries_from_the_first_run(
    repo, home, monkeypatch, tmp_path
):
    """F2: `wall.paused_s` sums the real wait between a park and the resume
    that answers it, read from `pause.json`'s own `asked_at`/`answered_at`
    -- and `wall.launched_at` never resets across that same resume."""
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.wall is not None
    assert first.wall["paused_s"] == 0.0

    sleep_s = 0.2
    time.sleep(sleep_s)

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )
    assert resumed.ok is True
    assert resumed.wall["paused_s"] >= sleep_s
    assert resumed.wall["launched_at"] == first.wall["launched_at"]


def test_idle_s_carries_across_a_resume_like_the_rest_of_wall(
    repo, home, monkeypatch, tmp_path
):
    """F15 item 5: `idle_s` used to reset to this process's own scheduler
    time on every resume while `wall_s`, `paused_s`, `gate_s`, and `lanes_s`
    all spanned the mission's whole life -- a resumed mission's `idle_s`
    must be at least what the first run already recorded."""
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.wall is not None

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )
    assert resumed.ok is True
    assert resumed.wall["idle_s"] >= first.wall["idle_s"]


def test_answer_stop_dispatches_nothing(repo, home, monkeypatch, tmp_path):
    calls = {"BUILD": 0, "REVIEW": 0, "FIX": 0}

    def fake_build(spec):
        key = spec.prompt.split()[0]
        calls[key] += 1
        return ["sh", "-c", f"echo '{envelope(key.lower())}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    first = run_mission(mission, home=home)

    stopped = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="stop"
    )

    assert calls == {"BUILD": 1, "REVIEW": 1, "FIX": 0}  # fix was never dispatched
    assert stopped.ok is False
    assert stopped.lanes[2]["skipped"] == "paused: operator answered stop"
    assert stopped.paused["kind"] == "lane" and stopped.paused["lane"] == "fix"
    assert stopped.paused["answer"] == "stop"

    pause_doc = json.loads((Path(stopped.mission_dir) / "pause.json").read_text())
    assert pause_doc["answer"] == "stop"
    assert pause_doc["answers"][0]["answer"] == "stop"


# --- the spend threshold -----------------------------------------------------


def test_pause_spend_usd_parks_between_two_lanes_and_does_not_refire_after_continue(
    repo, home, monkeypatch, tmp_path
):
    calls = {"A": 0, "B": 0}

    def fake_build(spec):
        key = spec.prompt.split()[0]
        calls[key] += 1
        return ["sh", "-c", f"echo '{envelope('done', cost=0.5)}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    raw = {
        "cwd": str(repo),
        "concurrency": 1,
        "pause": {"spend_usd": 0.3},
        "lanes": [
            {"name": "a", "fleet": "claude", "prompt": "A"},
            {"name": "b", "fleet": "claude", "prompt": "B", "needs": ["a"]},
        ],
    }
    first = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    a, b = first.lanes

    assert a["ok"] is True and a["cost_usd"] == pytest.approx(0.5)
    assert b["attempts"] == []
    assert b["skipped"] == "paused: spend $0.5000 reached pause.spend_usd $0.3000; not started"
    assert first.ok is False
    assert first.paused["kind"] == "spend"
    assert first.paused["spent_usd"] == pytest.approx(0.5)
    assert first.paused["threshold"] == pytest.approx(0.3)
    assert calls == {"A": 1, "B": 0}

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )

    assert calls == {"A": 1, "B": 1}  # a was kept, not paid again
    assert resumed.ok is True
    assert resumed.lanes[1]["ok"] is True
    assert resumed.paused is None


# --- item 4: CLI refusals and `conductor missions` --------------------------


def test_answer_needs_resume(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    mission_path = tmp_path / "m.json"
    mission_path.write_text(
        json.dumps({"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "codex"}]})
    )
    assert main(["mission", str(mission_path), "--answer", "continue"]) == 3
    assert "--answer needs --resume" in capsys.readouterr().err


def test_answer_on_a_mission_that_is_not_paused_is_refused(
    repo, home, monkeypatch, tmp_path, fake_fleet, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    fake_fleet(["sh", "-c", f"echo '{envelope('ok')}'"])
    mission_path = tmp_path / "m.json"
    mission_path.write_text(
        json.dumps({"prompt": "x", "cwd": str(repo), "lanes": [{"fleet": "claude"}]})
    )
    assert main(["mission", str(mission_path)]) == 0
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    assert main(["mission", "--resume", mission_id, "--answer", "continue"]) == 3
    assert "is not paused" in capsys.readouterr().err


def test_conductor_missions_reports_paused_then_not(
    repo, home, monkeypatch, tmp_path, fake_fleet, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    fake_fleet(["sh", "-c", f"echo '{envelope('ok')}'"])
    mission_path = tmp_path / "m.json"
    mission_path.write_text(
        json.dumps(
            {
                "prompt": "x",
                "cwd": str(repo),
                "pause": {"before": ["a"]},
                "lanes": [{"name": "a", "fleet": "claude", "prompt": "x"}],
            }
        )
    )
    assert main(["mission", str(mission_path)]) == 4
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    assert main(["missions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(r for r in rows if r["mission_id"] == mission_id)
    assert row["paused"] is True

    assert main(["mission", "--resume", mission_id, "--answer", "continue"]) == 0
    capsys.readouterr()

    assert main(["missions"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(r for r in rows if r["mission_id"] == mission_id)
    assert row["paused"] is False


# --- item 5: documentation ---------------------------------------------------


def test_readme_documents_the_pause_primitive():
    readme = Path(__file__).parents[1] / "README.md"
    raw_section = readme.read_text().split("### Pausing for the operator", 1)[1].split(
        "\n## ", 1
    )[0]
    section = " ".join(raw_section.split())  # prose wraps lines; match across breaks
    assert '"pause": {"before": ["publish"], "spend_usd": 3}' in section
    assert "never a request a fleet's own output can make" in section
    assert "already paid for and keeps running to its own end" in section
    assert '"kind": "lane"' in section
    assert "conductor mission --resume MISSION_ID --answer continue" in section
    assert "conductor mission --resume MISSION_ID --answer stop" in section
    assert "exits **4**" in section
    assert "docs/ROADMAP-2026-09.md" in section and "item C2" in section
    assert "LangGraph `interrupt()`" in section and "Microsoft request/response events" in section


# --- review fixes -------------------------------------------------------


def test_a_park_writes_the_ledgers_own_figures_into_pause_json(
    repo, home, monkeypatch, tmp_path
):
    """A parked mission's only artifact is `pause.json`, and it can park
    while other lanes are still dispatching, so the ledger as it stood at
    the park is written there under `budget` -- the same document the
    finished mission's own `budget` block carries."""
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", 0.25), "REVIEW": ("reviewed", 0.5), "FIX": ("fixed", None)},
    )
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.paused is not None

    pause_doc = json.loads((Path(result.mission_dir) / "pause.json").read_text())
    budget = pause_doc["budget"]
    assert set(budget) == set(Ledger(max_cost_usd=None).to_dict())
    assert budget["spent_usd"] == 0.75
    # The lanes that ran are done by the time a `before` pause parks the
    # mission, so nothing is outstanding here; the point is the figures are
    # on the artifact at all, at the moment of the write.
    assert budget["in_flight_dispatches"] == 0
    assert budget["outstanding_cap_usd"] == 0.0
    assert budget["worst_case_usd"] == 0.75
    # The keys that were always there are untouched.
    assert pause_doc["kind"] == "lane" and pause_doc["lane"] == "fix"
    assert pause_doc["answer"] is None and pause_doc["answers"] == []


def test_a_pause_file_without_a_budget_key_still_resumes(repo, home, monkeypatch, tmp_path):
    """Every `pause.json` written before the `budget` key existed lacks it,
    and a resume reads that file for its question, its answer history, and
    its `asked_at`. An older file must still answer, still resume, and still
    record the answer."""
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission = mission_from_dict(PIPELINE | {"cwd": str(repo)}, base_dir=tmp_path)
    first = run_mission(mission, home=home)

    pause_path = Path(first.mission_dir) / "pause.json"
    old_doc = {
        key: value
        for key, value in json.loads(pause_path.read_text()).items()
        if key != "budget"
    }
    pause_path.write_text(json.dumps(old_doc, indent=2))

    resumed = run_mission(
        _snapshot(first), home=home, resume_dir=Path(first.mission_dir), answer="continue"
    )
    assert resumed.ok is True
    assert resumed.paused is None
    answered = json.loads(pause_path.read_text())
    assert answered["answer"] == "continue"
    assert answered["answers"][0]["lane"] == "fix"


def test_stop_requested_while_parking_is_interrupted_not_paused(
    repo, home, monkeypatch, tmp_path
):
    """Spec item 2: 'A stop request that arrives while the mission is
    parking is handled as today (interrupted).' A pause point can fire while
    another lane is still running (already dispatched, so it is not
    cancelled); if a stop signal arrives during that wait, the mission must
    come out interrupted, not parked waiting on an operator answer."""

    def fake_build(spec):
        key = spec.prompt.split()[0]
        if key == "SLOW":
            return ["sh", "-c", f"sleep 1; echo '{envelope('slow')}'"]
        return ["sh", "-c", f"echo '{envelope('trigger')}'"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    raw = {
        "cwd": str(repo),
        "concurrency": 2,
        "pause": {"before": ["trigger"]},
        "lanes": [
            {"name": "slow", "fleet": "claude", "prompt": "SLOW"},
            {"name": "trigger", "fleet": "claude", "prompt": "TRIGGER"},
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    threading.Timer(0.3, request_stop).start()
    result = run_mission(mission, home=home)

    assert result.interrupted is True
    assert result.paused is None
    assert not (Path(result.mission_dir) / "pause.json").exists()
    assert result.ok is False


def test_answer_stop_is_resolved_not_still_waiting(repo, home, monkeypatch, tmp_path, capsys):
    """Spec item 3 vs item 4: an operator `stop` answer is a resolved,
    terminal result carrying the record -- it is not the 'neither ok nor
    failed; it is waiting' state that earns exit 4 and a 'resume with an
    answer' line in report.md. Only a genuinely unanswered park (no
    `answer` key on `paused`) is still waiting."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    by_prompt(
        monkeypatch,
        {"BUILD": ("built", None), "REVIEW": ("reviewed", None), "FIX": ("fixed", None)},
    )
    mission_path = tmp_path / "m.json"
    mission_path.write_text(json.dumps(PIPELINE | {"cwd": str(repo)}))
    assert main(["mission", str(mission_path)]) == 4
    mission_id = json.loads(capsys.readouterr().out)["mission_id"]

    exit_code = main(["mission", "--resume", mission_id, "--answer", "stop"])
    summary = json.loads(capsys.readouterr().out)

    assert exit_code == 1  # resolved (the operator said stop), not still waiting
    assert summary["paused"]["answer"] == "stop"
    report = Path(summary["report_path"]).read_text()
    assert "Resume with: conductor mission --resume" not in report
