"""Listings: `conductor runs` and `conductor missions` must read receipts
without crashing on a bad one, and without stating a fact the bytes do not
support."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conductor.cli import (
    LIVENESS_STALE_S,
    _kind_from_legacy_receipt,
    main,
)


def _listing(home: Path, monkeypatch, capsys, argv: list[str]) -> list[dict]:
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def _run_dir(home: Path, name: str) -> Path:
    path = home / "runs" / name
    path.mkdir(parents=True)
    return path


def _mission_dir(home: Path, name: str) -> Path:
    path = home / "missions" / name
    path.mkdir(parents=True)
    return path


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2))


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


# --- item 1: missing exit_code is a row, not a traceback --------------------


def test_runs_lists_a_receipt_missing_exit_code_as_unreadable_and_keeps_going(
    home: Path, monkeypatch, capsys
):
    """Identity without an outcome is unreadable, not a KeyError that takes
    down every other run in the listing."""
    good = _run_dir(home, "20260908T010000Z-good")
    _write(
        good / "result.json",
        {
            "run_id": good.name,
            "ok": True,
            "fleet": "claude",
            "model": "sonnet",
            "exit_code": 0,
            "duration_s": 1.0,
        },
    )
    bad = _run_dir(home, "20260908T000000Z-no-exit")
    _write(
        bad / "result.json",
        {
            "run_id": bad.name,
            "ok": False,
            "fleet": "claude",
            "model": "sonnet",
        },
    )

    rows = _listing(home, monkeypatch, capsys, ["runs"])
    by_id = {row["run_id"]: row for row in rows}
    assert by_id[bad.name] == {"run_id": bad.name, "status": "unreadable"}
    assert by_id[good.name]["exit_code"] == 0
    assert by_id[good.name]["fleet"] == "claude"


def test_runs_lists_a_null_exit_code_on_a_complete_receipt(
    home: Path, monkeypatch, capsys
):
    """The key present with JSON null is an outcome, not a missing field."""
    run = _run_dir(home, "20260908T020000Z-null-exit")
    _write(
        run / "result.json",
        {
            "run_id": run.name,
            "ok": False,
            "fleet": "claude",
            "model": "sonnet",
            "exit_code": None,
        },
    )

    rows = _listing(home, monkeypatch, capsys, ["runs"])
    assert rows[0]["run_id"] == run.name
    assert rows[0]["exit_code"] is None
    assert "status" not in rows[0]


# --- item 2: null cost or duration is null, not round(None) -----------------


def test_runs_and_missions_render_null_cost_and_duration_as_null_not_zero(
    home: Path, monkeypatch, capsys
):
    run = _run_dir(home, "20260908T030000Z-unpriced")
    _write(
        run / "result.json",
        {
            "run_id": run.name,
            "ok": True,
            "fleet": "cursor",
            "model": "grok",
            "exit_code": 0,
            "duration_s": None,
        },
    )
    zero_run = _run_dir(home, "20260908T025000Z-free")
    _write(
        zero_run / "result.json",
        {
            "run_id": zero_run.name,
            "ok": True,
            "fleet": "script",
            "model": "none",
            "exit_code": 0,
            "duration_s": 0,
        },
    )
    mission = _mission_dir(home, "20260908T030000Z-mission")
    _write(
        mission / "result.json",
        {
            "mission_id": mission.name,
            "ok": True,
            "cost_usd": None,
            "duration_s": None,
        },
    )
    zero_mission = _mission_dir(home, "20260908T025000Z-zero")
    _write(
        zero_mission / "result.json",
        {
            "mission_id": zero_mission.name,
            "ok": True,
            "cost_usd": 0,
            "duration_s": 0,
        },
    )

    runs = {row["run_id"]: row for row in _listing(home, monkeypatch, capsys, ["runs"])}
    assert runs[run.name]["duration_s"] is None
    assert runs[zero_run.name]["duration_s"] == 0.0

    missions = {
        row["mission_id"]: row for row in _listing(home, monkeypatch, capsys, ["missions"])
    }
    assert missions[mission.name]["cost_usd"] is None
    assert missions[mission.name]["duration_s"] is None
    assert missions[zero_mission.name]["cost_usd"] == 0.0
    assert missions[zero_mission.name]["duration_s"] == 0.0


# --- item 3: collisions without hotspots ------------------------------------


def test_missions_lists_a_collisions_block_without_hotspots(
    home: Path, monkeypatch, capsys
):
    mission = _mission_dir(home, "20260908T040000Z-old-collisions")
    _write(
        mission / "result.json",
        {
            "mission_id": mission.name,
            "ok": True,
            "collisions": {"overlap": 1, "conflicts": []},
        },
    )
    empty = _mission_dir(home, "20260908T035000Z-no-hotspots")
    _write(
        empty / "result.json",
        {
            "mission_id": empty.name,
            "ok": True,
            "collisions": {"overlap": 0, "conflicts": [], "hotspots": []},
        },
    )

    rows = {
        row["mission_id"]: row for row in _listing(home, monkeypatch, capsys, ["missions"])
    }
    assert rows[mission.name]["hotspots"] is None
    assert rows[empty.name]["hotspots"] == 0


# --- item 4: legacy kind classifies without every Result field --------------


def test_kind_from_legacy_receipt_classifies_without_required_result_fields():
    """A pre-kind receipt is missing more than `kind`. Filling a Result
    only from keys that happen to match still TypeError'd on timeout/cwd/
    tail and returned null — the thing this helper exists to prevent."""
    data = {
        "run_id": "20260908T050000Z-claude-x",
        "fleet": "claude",
        "model": "sonnet",
        "ok": False,
        "exit_code": 3,
        "error": None,
        "interrupted": False,
        "cancelled": False,
        "timed_out": False,
    }
    assert _kind_from_legacy_receipt(data) == "exit"


def test_runs_recomputes_kind_on_a_sparse_pre_kind_receipt(
    home: Path, monkeypatch, capsys
):
    run = _run_dir(home, "20260908T050000Z-sparse")
    _write(
        run / "result.json",
        {
            "run_id": run.name,
            "fleet": "claude",
            "model": "sonnet",
            "ok": False,
            "exit_code": 3,
        },
    )

    rows = _listing(home, monkeypatch, capsys, ["runs"])
    assert rows[0]["kind"] == "exit"


# --- item 5: status follows pid and heartbeat, not heartbeat age alone ------


def test_runs_does_not_report_a_dead_pid_as_running(home: Path, monkeypatch, capsys):
    now = datetime.now(UTC)
    dead_fresh = _run_dir(home, "20260908T060000Z-dead-fresh")
    _write(
        dead_fresh / "liveness.json",
        {"at": _iso(now), "elapsed_s": 2.0, "pid": 9999999},
    )
    dead_stale = _run_dir(home, "20260908T055000Z-dead-stale")
    _write(
        dead_stale / "liveness.json",
        {
            "at": _iso(now - timedelta(seconds=LIVENESS_STALE_S + 20)),
            "elapsed_s": 90.0,
            "pid": 9999999,
        },
    )
    live_stale = _run_dir(home, "20260908T054000Z-live-stale")
    _write(
        live_stale / "liveness.json",
        {
            "at": _iso(now - timedelta(seconds=LIVENESS_STALE_S + 20)),
            "elapsed_s": 90.0,
            "pid": os.getpid(),
        },
    )
    live_fresh = _run_dir(home, "20260908T053000Z-live-fresh")
    _write(
        live_fresh / "liveness.json",
        {"at": _iso(now), "elapsed_s": 1.0, "pid": os.getpid()},
    )
    no_at = _run_dir(home, "20260908T052000Z-no-at")
    _write(no_at / "liveness.json", {"elapsed_s": 4.0, "pid": os.getpid()})
    bad_at = _run_dir(home, "20260908T051000Z-bad-at")
    _write(
        bad_at / "liveness.json",
        {"at": "not-a-timestamp", "elapsed_s": 4.0, "pid": os.getpid()},
    )

    rows = {row["run_id"]: row for row in _listing(home, monkeypatch, capsys, ["runs"])}

    assert rows[dead_fresh.name]["status"] == "dead"
    assert rows[dead_fresh.name]["pid_alive"] is False
    assert rows[dead_stale.name]["status"] == "dead"
    assert rows[live_stale.name]["status"] == "silent"
    assert rows[live_stale.name]["pid_alive"] is True
    assert rows[live_fresh.name]["status"] == "running"
    assert rows[no_at.name]["status"] == "silent"
    assert rows[no_at.name]["heartbeat_age_s"] is None
    assert rows[bad_at.name]["status"] == "silent"
    assert rows[bad_at.name]["heartbeat_age_s"] is None


# --- item 6: incomplete missions do not invent zero resumes or children -----


def test_incomplete_mission_reads_resumes_and_children_off_durable_surfaces(
    home: Path, monkeypatch, capsys
):
    parent = _mission_dir(home, "20260908T070000Z-parent")
    _write(parent / "mission.json", {"name": "parent", "lanes": []})
    _write(
        parent / "pause.json",
        {
            "kind": "budget",
            "lane": "a",
            "answer": None,
            "answers": [
                {"answer": "continue"},
                {"answer": "continue"},
                {"answer": "continue"},
            ],
        },
    )
    for suffix in ("child-a", "child-b"):
        child = _mission_dir(home, f"20260908T070000Z-{suffix}")
        _write(
            child / "mission.json",
            {
                "name": suffix,
                "parent": {"mission_id": parent.name, "lane": "plan"},
                "lanes": [],
            },
        )

    rows = {
        row["mission_id"]: row for row in _listing(home, monkeypatch, capsys, ["missions"])
    }
    assert rows[parent.name]["status"] == "incomplete"
    assert rows[parent.name]["resumes"] == 3
    assert rows[parent.name]["children"] == 2


def test_incomplete_mission_with_unreadable_pause_does_not_claim_zero_resumes(
    home: Path, monkeypatch, capsys
):
    mission = _mission_dir(home, "20260908T071000Z-garbled-pause")
    (mission / "pause.json").write_text('{"answer": ')

    rows = _listing(home, monkeypatch, capsys, ["missions"])
    assert rows[0]["resumes"] is None
    assert rows[0]["children"] == 0


# --- item 7: paused follows result.json the way report does -----------------


def test_missions_paused_follows_result_json_when_pause_file_is_unreadable(
    home: Path, monkeypatch, capsys
):
    parked = _mission_dir(home, "20260908T080000Z-parked")
    _write(
        parked / "result.json",
        {
            "mission_id": parked.name,
            "ok": False,
            "paused": {"kind": "budget", "lane": "a", "question": "continue?"},
        },
    )
    (parked / "pause.json").write_text('{"answer": ')

    finished = _mission_dir(home, "20260908T075000Z-done")
    _write(
        finished / "result.json",
        {"mission_id": finished.name, "ok": True, "paused": None},
    )

    rows = {
        row["mission_id"]: row for row in _listing(home, monkeypatch, capsys, ["missions"])
    }
    assert rows[parked.name]["paused"] is True
    assert rows[finished.name]["paused"] is False


def test_incomplete_mission_with_unreadable_pause_is_not_listed_as_not_paused(
    home: Path, monkeypatch, capsys
):
    mission = _mission_dir(home, "20260908T081000Z-crash-mid-pause")
    (mission / "pause.json").write_text("{")

    rows = _listing(home, monkeypatch, capsys, ["missions"])
    assert rows[0]["paused"] is None
    assert rows[0]["status"] == "incomplete"
