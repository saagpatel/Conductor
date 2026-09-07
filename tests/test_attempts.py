"""F21 slice 3: the attempt lifecycle extracted into attempts.py pins two
invariants -- every paid dispatch is counted once across a retry, and a
rehearsal is never trusted on resume."""

from __future__ import annotations

import ast
import json
import shlex
from dataclasses import replace
from pathlib import Path

from conductor import attempts as attempts_mod
from conductor import runner as runner_mod
from conductor.mission import LaneResult, Mission, _trusted_lane, mission_from_dict, run_mission

_TRANSPORT_ENVELOPE = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"result":"","error":"ECONNRESET while streaming"}'
)


def _ok_envelope(cost: float) -> str:
    return (
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        f'"usage":{{"inputTokens":10,"outputTokens":5}},"total_cost_usd":{cost}}}'
    )


def _flaky_script(counter: Path, fail_times: int, cost: float) -> str:
    """Fails with a transport-shaped error the first `fail_times` calls
    (tracked in a counter file so every dispatch of the same argv can tell
    which try it is), then succeeds -- the same shape test_errors.py's
    retry tests already use."""
    return f"""
n=$(cat {counter})
n=$((n + 1))
echo $n > {counter}
if [ "$n" -le {fail_times} ]; then
  printf '%s\\n' {shlex.quote(_TRANSPORT_ENVELOPE)}
  exit 1
else
  printf '%s\\n' {shlex.quote(_ok_envelope(cost))}
  exit 0
fi
"""


def test_retry_dispatches_are_counted_once_and_a_resume_never_repays(
    repo, home, monkeypatch, tmp_path
):
    """(a) Expected to hold on the code before this extraction too: a lane's
    two retries and its eventual success are three attempts of one lane, each
    priced from its own run receipt exactly once (never doubled by the retry
    loop, never doubled again by a resume). Resuming the already-finished
    mission trusts the lane's receipt outright (`_trusted_lane`'s ordinary
    path, not a rerun): its three attempts stay exactly where they were,
    `previous_attempts` empty, and the mission's `cost_usd` is unchanged --
    the lane was never asked to fold its history, because it was never asked
    to run again.
    """
    counter = tmp_path / "count"
    counter.write_text("0")
    script = _flaky_script(counter, fail_times=2, cost=0.05)
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", script])
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "retry": {"kinds": ["transport"], "attempts": 2, "backoff_s": 0},
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is True
    lane = first.lanes[0]
    assert len(lane["attempts"]) == 3
    assert lane["kinds"] == ["transport", "transport", None]

    total = 0.0
    for attempt in lane["attempts"]:
        receipt = json.loads((home / "runs" / attempt["run_id"] / "result.json").read_text())
        usage = receipt.get("usage") or {}
        total += usage.get("cost_usd") or 0.0
    assert total == first.cost_usd == lane["cost_usd"]

    snapshot = json.loads(Path(first.mission_dir, "mission.json").read_text())
    resumed = run_mission(
        Mission.from_snapshot(snapshot), home=home, resume_dir=Path(first.mission_dir)
    )
    assert resumed.ok is True
    assert resumed.resumed_from["kept"] == ["a"]
    assert resumed.resumed_from["rerun"] == []
    rlane = resumed.lanes[0]
    assert len(rlane["attempts"]) == 3
    assert rlane["previous_attempts"] == []
    assert resumed.cost_usd == first.cost_usd


def test_trusted_lane_refuses_an_unspawned_rehearsal_attempt(repo, home, monkeypatch, tmp_path):
    """(b) Expected to hold on the code before this extraction too: an ok
    receipt whose last attempt was never actually spawned (a dry-run
    rehearsal's shape) is never trusted -- trusting it would turn the next
    real resume into another rehearsal that dispatches nothing."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", f"printf '%s\\n' {shlex.quote(_ok_envelope(0.1))}"],
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True
    mission_dir = Path(result.mission_dir)
    lane = mission.lanes[0]
    receipt = LaneResult.from_dict(json.loads((mission_dir / "lanes" / "a.json").read_text()))
    assert _trusted_lane(mission, mission_dir, lane, receipt) is True

    rehearsed = replace(receipt)
    rehearsed.attempts = [{**receipt.attempts[-1], "spawned": False}]
    assert _trusted_lane(mission, mission_dir, lane, rehearsed) is False


def test_trusted_lane_refuses_a_receipt_whose_artifact_digest_no_longer_matches(
    repo, home, monkeypatch, tmp_path
):
    """(b) Expected to hold on the code before this extraction too: an ok
    receipt is never trusted once the bytes on disk no longer match the
    digest it recorded -- the file being in the right place is not enough,
    the bytes inside it must be what the receipt actually saw."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", f"printf '%s\\n' {shlex.quote(_ok_envelope(0.1))}"],
    )
    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "mode": "read",
        "lanes": [{"name": "a", "fleet": "claude"}],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    result = run_mission(mission, home=home)
    assert result.ok is True
    mission_dir = Path(result.mission_dir)
    lane = mission.lanes[0]
    receipt = LaneResult.from_dict(json.loads((mission_dir / "lanes" / "a.json").read_text()))
    assert _trusted_lane(mission, mission_dir, lane, receipt) is True

    (mission_dir / "answers" / "a.txt").write_text("tampered, not what the lane wrote\n")
    assert _trusted_lane(mission, mission_dir, lane, receipt) is False


def test_attempts_module_has_no_module_level_import_of_mission():
    """Structural (c): mission.py re-exports every name attempts.py defines,
    so a module-level import back from here would be a cycle -- every
    reach into mission.py's own state is a lazy `from . import mission as
    mission_mod` inside the function that needs it."""
    source = Path(attempts_mod.__file__).read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            offending = node.module in {"mission", "conductor.mission"} or (
                node.module is None and "mission" in names
            )
            assert not offending, f"module-level import of mission at line {node.lineno}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in {"mission", "conductor.mission"}, (
                    f"module-level import of mission at line {node.lineno}"
                )
