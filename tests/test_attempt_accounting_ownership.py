"""Behavioral regressions from the ownership audit; no vendor dispatches."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conductor import attempts, attest
from conductor.mission import (
    Attempt,
    Lane,
    LaneResult,
    Ledger,
    Mission,
    MissionInvalid,
    _run_receipt_spend,
    mission_from_dict,
    run_mission,
)


@pytest.mark.parametrize("field,value", [
    ("timeout", True), ("timeout", 1.5), ("cap_usd", True),
])
def test_mission_preserves_numeric_types_until_validation(tmp_path, field, value):
    with pytest.raises(MissionInvalid, match=field):
        mission_from_dict(
            {"prompt": "x", "lanes": [{"fleet": "claude", field: value}]},
            base_dir=tmp_path,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_mission_refuses_nonfinite_or_boolean_retry_delay(tmp_path, value):
    with pytest.raises(MissionInvalid, match="backoff_s"):
        mission_from_dict(
            {"prompt": "x", "retry": {"attempts": 1, "backoff_s": value},
             "lanes": [{"fleet": "claude"}]},
            base_dir=tmp_path,
        )


def _cascade() -> Mission:
    return Mission(
        name="m", cwd=".", cascade={"fleet": "cursor"},
        lanes=[Lane(name="build", attempts=[Attempt(fleet="cursor"), Attempt(fleet="claude")],
                    cascaded=True, stage="build")],
    )


def test_cascade_counts_every_retry_in_its_declared_phase():
    result = LaneResult(name="build", ok=True, escalated=True, attempts=[
        {"run_id": "cheap", "ok": False, "cost_usd": 1.0},
        {"run_id": "cheap-retry", "retry_of": "cheap", "ok": False, "cost_usd": 2.0},
        {"run_id": "primary", "ok": False, "cost_usd": 4.0},
        {"run_id": "primary-retry", "retry_of": "primary", "ok": True, "cost_usd": 8.0},
    ])
    summary = attempts._escalation_summary(_cascade(), [result])
    assert summary["cascade_usd"] == 3.0
    assert summary["escalated_usd"] == 12.0
    assert summary["cheap_ok"] == 0
    assert summary["rate"] == 1.0


def test_successful_cheap_retry_counts_as_cheap_success():
    result = LaneResult(name="build", ok=True, attempts=[
        {"run_id": "cheap", "ok": False, "cost_usd": 1.0},
        {"run_id": "cheap-retry", "retry_of": "cheap", "ok": True, "cost_usd": 2.0},
    ])
    summary = attempts._escalation_summary(_cascade(), [result])
    assert summary["cheap_ok"] == 1
    assert summary["cascade_usd"] == 3.0
    assert summary["escalated_usd"] == 0.0
    assert summary["escalated"] == 0


@pytest.mark.parametrize("unknown_phase", ["cheap", "primary"])
def test_cascade_does_not_report_an_unpriced_phase_as_free(unknown_phase):
    result = LaneResult(name="build", ok=False, escalated=True, attempts=[
        {"run_id": "cheap", "cost_usd": None if unknown_phase == "cheap" else 1.0},
        {"run_id": "primary", "cost_usd": None if unknown_phase == "primary" else 2.0},
    ])
    summary = attempts._escalation_summary(_cascade(), [result])
    assert summary["cascade_usd"] == (None if unknown_phase == "cheap" else 1.0)
    assert summary["escalated_usd"] == (None if unknown_phase == "primary" else 2.0)


@pytest.mark.parametrize("flags,blocked", [
    ({}, True), ({"timed_out": True}, False), ({"interrupted": True}, False),
    ({"cancelled": True}, False), ({"spawned": False}, False),
])
def test_live_resume_and_recovery_agree_when_price_is_missing(home, flags, blocked):
    raw = {"fleet": "cursor", "spawned": True, "interrupted": False,
           "cancelled": False, "timed_out": False, "usage": None, **flags}
    run_dir = home / "runs" / "r"
    run_dir.mkdir(parents=True)
    receipt = run_dir / "result.json"
    receipt.write_text(json.dumps(raw))
    ledger = Ledger(5)
    ledger.add(SimpleNamespace(**raw))
    recovered = attempts._attempt_from_run_receipt("r", raw)
    previous = {"a": LaneResult(name="a", ok=False, attempts=[recovered])}
    assert ledger.unpriced == int(blocked)
    assert _run_receipt_spend(home, previous, None) == (0.0, int(blocked))
    # The recovered summary must preserve uncertainty if the original
    # receipt later becomes unavailable; this operates only on test data.
    receipt.rename(run_dir / "saved-result.json")
    assert _run_receipt_spend(home, previous, None) == (0.0, int(blocked))


def test_recorded_digest_requires_readable_bytes(tmp_path, monkeypatch):
    mission_dir = tmp_path / "mission"
    answer = mission_dir / "answers/a.txt"
    answer.parent.mkdir(parents=True)
    answer.write_text("the answer")
    lane = Lane(name="a", attempts=[Attempt(fleet="claude")])
    mission = Mission(name="m", cwd=str(tmp_path), lanes=[lane])
    result = LaneResult(name="a", ok=True, attempts=[{"spawned": True}],
                        answer_path=str(answer))
    attempts._record_artifact_digests(result)
    assert attempts._trusted_lane(mission, mission_dir, lane, result)
    # A transient read failure after the path/existence check is neither a
    # digest match nor evidence that this is a pre-digest receipt.
    monkeypatch.setattr(attest, "file_sha256", lambda path: None)
    assert not attempts._trusted_lane(mission, mission_dir, lane, result)


def test_recorded_digest_cannot_be_bypassed_by_a_null_artifact_path(tmp_path):
    lane = Lane(name="a", attempts=[Attempt(fleet="claude")])
    mission = Mission(name="m", cwd=str(tmp_path), lanes=[lane])
    result = LaneResult(name="a", ok=True, attempts=[{"spawned": True}],
                        answer_path=None, artifact_sha256={"answer": "a" * 64})
    assert not attempts._trusted_lane(mission, tmp_path, lane, result)


def test_missing_repository_is_not_a_transient_git_spawn_failure(tmp_path):
    lane = Lane(name="a", attempts=[Attempt(fleet="claude")])
    mission = Mission(name="m", cwd=str(tmp_path), lanes=[lane])
    result = LaneResult(name="a", ok=True, attempts=[{"spawned": True}],
                        cwd=str(tmp_path / "missing"), base_sha="a" * 40, tip_sha="b" * 40)
    notes = []
    assert not attempts._trusted_lane(mission, tmp_path, lane, result, notes=notes)
    assert any("repository is missing" in note for note in notes)


@pytest.mark.parametrize("receipt_cost,keep_receipt,unknown", [
    (None, True, 1), (None, False, 1), (2.0, True, 0),
])
def test_resume_preserves_cancelled_unknown_cost_until_a_price_is_available(
    home, repo, receipt_cost, keep_receipt, unknown
):
    raw = {"lanes": [{"fleet": "script", "command": "true"}], "max_cost_usd": 5}
    mission = mission_from_dict(raw, base_dir=repo)
    mission_dir = home / "missions" / "m"
    mission_dir.mkdir(parents=True)
    (mission_dir / "mission.json").write_text(json.dumps(raw))
    # A prior auxiliary dispatch remains part of lifetime accounting even
    # when this resumed plan no longer needs that auxiliary stage.
    (mission_dir / "result.json").write_text(json.dumps({"collate": {
        "run_id": "old", "spawned": True, "cancelled": True, "cost_unknown": True,
    }}))
    if keep_receipt:
        run_dir = home / "runs" / "old"
        run_dir.mkdir(parents=True)
        (run_dir / "result.json").write_text(json.dumps({
            "spawned": True, "cancelled": True, "usage": {"cost_usd": receipt_cost},
        }))
    result = run_mission(mission, home=home, resume_dir=mission_dir, dry_run=True)
    assert result.budget["unknown_cost_dispatches"] == unknown
    assert result.budget["unpriced_dispatches"] == 0
    assert result.budget["spent_usd"] == (receipt_cost or 0.0)
