"""E9: a rolling spend ceiling read from the run receipts, an unattended
launch mode, and a lock scoped to the mission file.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conductor import golden
from conductor import mission as mission_mod
from conductor import runner as runner_mod
from conductor.ceiling import USD_PER_DAY, USD_PER_HOUR, rolling_spend
from conductor.mission import Mission, MissionInvalid, load_mission, mission_from_dict, run_mission


def envelope(answer: str, cost: float | None = None) -> str:
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


def _receipt(
    home: Path,
    run_id: str,
    *,
    fleet: str = "claude",
    model: str = "opus",
    cost: float | None,
    dry_run: bool = False,
) -> None:
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "fleet": fleet,
                "model": model,
                "ok": True,
                "dry_run": dry_run,
                "usage": None
                if cost is None
                else {"cost_usd": cost, "cost_basis": "reported", "total_tokens": 10},
            }
        )
    )


def _stamp(when: datetime) -> str:
    return when.strftime("%Y%m%dT%H%M%SZ")


def _lock_path(home: Path, source: str) -> Path:
    digest = hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest()[:16]
    return home / "locks" / f"{digest}.json"


# --- rolling_spend -----------------------------------------------------------


def test_rolling_spend_sums_the_right_windows_and_counts_unpriced_and_free(home: Path):
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    # Within the hour and the day, priced.
    _receipt(home, f"{_stamp(now - timedelta(minutes=30))}-claude-a", cost=2.0)
    # Within the day only, priced.
    _receipt(home, f"{_stamp(now - timedelta(minutes=90))}-claude-b", cost=3.0)
    # Within the day only, unpriced.
    _receipt(home, f"{_stamp(now - timedelta(hours=23))}-claude-c", cost=None)
    # Outside the day entirely: must not count anywhere.
    _receipt(home, f"{_stamp(now - timedelta(hours=25))}-claude-d", cost=100.0)
    # A free script run: priced at exactly $0, within the hour -- a run, never unpriced.
    _receipt(
        home,
        f"{_stamp(now - timedelta(minutes=5))}-script-e",
        fleet="script",
        model="sh",
        cost=0.0,
    )
    # A dry run: spent nothing, not a dispatch at all -- excluded outright.
    _receipt(home, f"{_stamp(now - timedelta(minutes=1))}-claude-f", cost=None, dry_run=True)

    spend = rolling_spend(home, now=now)
    assert spend.hour_usd == 2.0
    assert spend.day_usd == 5.0
    assert spend.runs_hour == 2
    assert spend.runs_day == 4
    assert spend.unpriced_hour == 0
    assert spend.unpriced_day == 1


def test_rolling_spend_on_an_empty_home_is_all_zero(home: Path):
    spend = rolling_spend(home)
    assert spend == rolling_spend(home)
    assert spend.hour_usd == 0.0 and spend.day_usd == 0.0
    assert spend.runs_hour == 0 and spend.runs_day == 0


# --- the ceiling check in run_mission -----------------------------------------


def _read_lane_mission(cwd: Path, *, ceiling: dict | None = None) -> Mission:
    raw = {
        "prompt": "x",
        "cwd": str(cwd),
        "lanes": [{"fleet": "claude", "mode": "read", "prompt": "R"}],
    }
    if ceiling is not None:
        raw["ceiling"] = ceiling
    return mission_from_dict(raw, base_dir=cwd)


def test_launch_over_the_hour_bound_is_refused_with_the_message(tmp_path, home):
    now = datetime.now(UTC)
    _receipt(home, f"{_stamp(now - timedelta(minutes=1))}-claude-big", cost=USD_PER_HOUR + 1)
    mission = _read_lane_mission(tmp_path)
    with pytest.raises(MissionInvalid, match=r"over the \$10\.00 per-hour ceiling"):
        run_mission(mission, home=home)


def test_launch_over_the_day_bound_is_refused_with_the_message(tmp_path, home):
    # Outside the hour window, so only the day bound can trip.
    now = datetime.now(UTC)
    _receipt(home, f"{_stamp(now - timedelta(hours=20))}-claude-big", cost=USD_PER_DAY + 1)
    mission = _read_lane_mission(tmp_path)
    with pytest.raises(MissionInvalid, match=r"over the \$25\.00 per-day ceiling"):
        run_mission(mission, home=home)


def test_null_disables_a_bound(tmp_path, home, monkeypatch):
    now = datetime.now(UTC)
    # Would trip the default per-hour ceiling if it were not disabled.
    _receipt(home, f"{_stamp(now - timedelta(minutes=1))}-claude-big", cost=USD_PER_HOUR + 1)
    fake_fleets(monkeypatch, {"claude": say("ok")})
    mission = _read_lane_mission(
        tmp_path, ceiling={"per_hour_usd": None, "per_day_usd": USD_PER_DAY}
    )
    result = run_mission(mission, home=home)
    assert result.ok is True


def test_under_both_bounds_runs_and_records_the_ceiling_block(tmp_path, home, monkeypatch):
    now = datetime.now(UTC)
    _receipt(home, f"{_stamp(now - timedelta(minutes=1))}-claude-a", cost=3.0)
    fake_fleets(monkeypatch, {"claude": say("ok")})
    mission = _read_lane_mission(tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True
    assert result.ceiling["per_hour_usd"] == USD_PER_HOUR
    assert result.ceiling["per_day_usd"] == USD_PER_DAY
    assert result.ceiling["hour_usd"] == 3.0
    assert result.ceiling["day_usd"] == 3.0
    assert result.ceiling["unpriced_hour"] == 0
    assert result.ceiling["unpriced_day"] == 0


def test_a_dry_run_never_checks_the_ceiling(tmp_path, home):
    now = datetime.now(UTC)
    _receipt(home, f"{_stamp(now - timedelta(minutes=1))}-claude-big", cost=USD_PER_DAY + 100)
    mission = _read_lane_mission(tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    assert result.ceiling is None


@pytest.mark.parametrize(
    "raw_ceiling",
    [
        {"per_hour_usd": 5.0},
        {"per_hour_usd": "five", "per_day_usd": None},
        {"per_hour_usd": -1, "per_day_usd": None},
        {"per_hour_usd": 0, "per_day_usd": None},
    ],
)
def test_a_malformed_ceiling_is_refused(tmp_path, raw_ceiling):
    with pytest.raises(MissionInvalid, match="ceiling"):
        _read_lane_mission(tmp_path, ceiling=raw_ceiling)


# --- unattended launches -------------------------------------------------------


def test_unattended_refuses_a_human_lane(tmp_path, home):
    mission = mission_from_dict(
        {"cwd": str(tmp_path), "lanes": [{"name": "ask", "fleet": "human", "prompt": "approve?"}]},
        base_dir=tmp_path,
    )
    with pytest.raises(MissionInvalid, match="human lane"):
        run_mission(mission, home=home, unattended=True)


def test_unattended_refuses_a_fix_stage_write_lane_outside_pause_before(tmp_path, home):
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(tmp_path),
            "lanes": [
                {
                    "name": "fix",
                    "fleet": "claude",
                    "stage": "fix",
                    "mode": "write",
                    "no_op_ok": True,
                }
            ],
        },
        base_dir=tmp_path,
    )
    with pytest.raises(MissionInvalid, match="pause.before"):
        run_mission(mission, home=home, unattended=True)


def test_unattended_refuses_an_unstaged_write_lane(tmp_path, home):
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(tmp_path),
            "lanes": [{"name": "w", "fleet": "claude", "mode": "write", "no_op_ok": True}],
        },
        base_dir=tmp_path,
    )
    with pytest.raises(MissionInvalid, match="no stage"):
        run_mission(mission, home=home, unattended=True)


def test_unattended_refuses_a_resolve_block(tmp_path, home):
    mission = mission_from_dict(
        {
            "prompt": "SPEC",
            "cwd": str(tmp_path),
            "lanes": [
                {
                    "name": "a",
                    "fleet": "codex",
                    "mode": "write",
                    "prompt": "A",
                    "commit": "feat: a",
                },
                {
                    "name": "b",
                    "fleet": "claude",
                    "mode": "write",
                    "prompt": "B",
                    "commit": "feat: b",
                },
            ],
            "resolve": {"fleet": "cursor", "commit": "merge: reconcile"},
        },
        base_dir=tmp_path,
    )
    with pytest.raises(MissionInvalid, match="resolve"):
        run_mission(mission, home=home, unattended=True)


def test_unattended_allows_a_read_only_mission(tmp_path, home, monkeypatch):
    fake_fleets(monkeypatch, {"claude": say("ok")})
    mission = mission_from_dict(
        {"cwd": str(tmp_path), "lanes": [{"fleet": "claude", "mode": "read", "prompt": "R"}]},
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home, unattended=True)
    assert result.ok is True
    assert result.unattended is True


def test_unattended_allows_a_shape_a_mission_with_pause_before_fix(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"claude": say("ok")})
    raw = {
        "prompt": "SPEC",
        "cwd": str(repo),
        "pause": {"before": ["fix"]},
        "lanes": [
            {
                "name": "build",
                "fleet": "claude",
                "stage": "build",
                "mode": "write",
                "prompt": "B",
                "commit": "feat: build",
                "no_op_ok": True,
            },
            {
                "name": "fix",
                "fleet": "claude",
                "stage": "fix",
                "mode": "write",
                "needs": ["build"],
                "base": "build",
                "prompt": "F",
                "commit": "feat: fix",
                "no_op_ok": True,
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, unattended=True)
    assert result.unattended is True
    assert result.paused is not None
    assert result.paused["lane"] == "fix"


# --- the mission-file lock ------------------------------------------------------


def _one_lane_source(tmp_path: Path) -> Path:
    source = tmp_path / "m.json"
    source.write_text(
        json.dumps(
            {
                "prompt": "x",
                "cwd": str(tmp_path),
                "lanes": [{"fleet": "claude", "mode": "read", "prompt": "R"}],
            }
        )
    )
    return source


def test_file_lock_refuses_a_second_launch_naming_the_first_mission_id(tmp_path, home):
    source = _one_lane_source(tmp_path)
    mission = load_mission(source)
    lock_path = _lock_path(home, mission.source)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
                "mission_id": "20260101T000000Z-earlier",
            }
        )
    )
    with pytest.raises(MissionInvalid, match="20260101T000000Z-earlier"):
        run_mission(mission, home=home, dry_run=True)


def test_file_lock_releases_on_return_and_on_exception(tmp_path, home, monkeypatch):
    source = _one_lane_source(tmp_path)
    mission = load_mission(source)
    lock_path = _lock_path(home, mission.source)

    run_mission(mission, home=home, dry_run=True)
    assert not lock_path.exists()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(mission_mod, "_execute_mission", _boom)
    with pytest.raises(RuntimeError, match="boom"):
        run_mission(mission, home=home, dry_run=True)
    assert not lock_path.exists()


def test_file_lock_replaces_a_stale_lock_with_a_note(tmp_path, home):
    source = _one_lane_source(tmp_path)
    mission = load_mission(source)
    lock_path = _lock_path(home, mission.source)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "started": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
                "mission_id": "stale-mission",
            }
        )
    )
    result = run_mission(mission, home=home, dry_run=True)
    assert "removed stale file lock" in "\n".join(result.notes)
    assert not lock_path.exists()


def test_file_lock_is_not_taken_for_an_empty_source(tmp_path, home):
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(tmp_path),
            "lanes": [{"fleet": "claude", "mode": "read", "prompt": "R"}],
        },
        base_dir=tmp_path,
    )
    assert mission.source == ""
    run_mission(mission, home=home, dry_run=True)
    assert not (home / "locks").exists()


# --- snapshot -------------------------------------------------------------------


def test_snapshot_round_trips_ceiling(tmp_path, home):
    raw = {
        "prompt": "x",
        "cwd": str(tmp_path),
        "ceiling": {"per_hour_usd": 3.0, "per_day_usd": None},
        "lanes": [{"fleet": "claude", "mode": "read", "prompt": "R"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    assert mission.ceiling == {"per_hour_usd": 3.0, "per_day_usd": None}
    result = run_mission(mission, home=home, dry_run=True)
    snapshot_raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    reloaded = Mission.from_snapshot(snapshot_raw)
    assert reloaded.ceiling == mission.ceiling


def test_an_old_snapshot_backfills_ceiling(tmp_path, home):
    raw = {
        "prompt": "x",
        "cwd": str(tmp_path),
        "lanes": [{"fleet": "claude", "mode": "read", "prompt": "R"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home, dry_run=True)
    snapshot_raw = json.loads((Path(result.mission_dir) / "mission.json").read_text())
    del snapshot_raw["ceiling"]  # simulate a recording made before E9 shipped
    backfilled = golden._backfill_snapshot(snapshot_raw)
    assert backfilled["ceiling"] is None
    reloaded = Mission.from_snapshot(backfilled)
    assert reloaded.ceiling is None
