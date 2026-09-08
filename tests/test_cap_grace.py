"""E24: cap grace, a per-lane, opt-in band on top of cap_usd so Claude Code's
own terminal message can finish instead of being cut off mid-summary.

F5 extends the band to a cursor read lane, whose cap is post-hoc: there is no
terminal message to finish, but the band still widens the after-the-fact
verdict, so a complete review a few cents over cap_usd settles ok instead of
failing the lane and skipping the fix stage behind it (rule 7's trap).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor import runner as runner_mod
from conductor import shape
from conductor.budget import Budget
from conductor.cli import main
from conductor.fleets import CAP_GRACE_CEILING_USD, DispatchRefused, Spec, build_argv
from conductor.mission import (
    Ledger,
    MissionInvalid,
    _fit_cap_to_remaining,
    mission_from_dict,
    run_mission,
)
from conductor.runner import Result, dispatch


def envelope(answer: str, cost: float | None = None) -> str:
    """A claude-shaped envelope: the one fleet that reports dollars."""
    payload: dict = {"result": answer, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if cost is not None:
        payload["total_cost_usd"] = cost
    return json.dumps(payload)


def say(answer: str, cost: float | None = None) -> list[str]:
    return ["sh", "-c", f"echo '{envelope(answer, cost)}'"]


def fake_fleets(monkeypatch, by_fleet: dict[str, list[str]]) -> None:
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: by_fleet[spec.fleet])


# --- Spec.validate refusals (item 1) ----------------------------------------


def test_grace_without_a_cap_is_refused():
    with pytest.raises(DispatchRefused, match="needs a cap_usd"):
        Spec(fleet="claude", prompt="x", cwd="/tmp", cap_grace_usd=0.25).validate()


def test_grace_without_a_cap_is_refused_on_cursor_too():
    with pytest.raises(DispatchRefused, match="needs a cap_usd"):
        Spec(
            fleet="cursor", model="composer-2.5", prompt="x", cwd="/tmp", mode="read",
            cap_grace_usd=0.25,
        ).validate()


def test_grace_is_refused_on_any_fleet_but_claude_and_cursor_read():
    # F5: codex's cap is a watcher kill -- still no terminal message or
    # post-hoc verdict for the band to help, and the refusal now names the
    # fleet and its cap mode instead of a bare "claude fleet only".
    with pytest.raises(DispatchRefused, match="codex's cap mode is 'watcher'"):
        Spec(
            fleet="codex", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=0.25
        ).validate()


def test_grace_is_still_refused_on_antigravity_naming_fleet_and_mode():
    with pytest.raises(DispatchRefused, match="antigravity's cap mode is 'watcher'"):
        Spec(
            fleet="antigravity", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=0.25
        ).validate()


def test_grace_is_allowed_on_a_cursor_read_lane():
    Spec(
        fleet="cursor",
        model="composer-2.5",
        prompt="x",
        cwd="/tmp",
        mode="read",
        cap_usd=1.0,
        cap_grace_usd=0.25,
    ).validate()


def test_grace_is_refused_on_a_cursor_write_lane():
    with pytest.raises(DispatchRefused, match="cursor write lane"):
        Spec(
            fleet="cursor",
            model="composer-2.5",
            prompt="x",
            cwd="/tmp",
            mode="write",
            cap_usd=1.0,
            cap_grace_usd=0.25,
        ).validate()


@pytest.mark.parametrize("bad", [0, -0.1, float("inf"), float("nan")])
def test_grace_must_be_a_positive_finite_number(bad):
    with pytest.raises(DispatchRefused, match="positive finite"):
        Spec(
            fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=bad
        ).validate()


@pytest.mark.parametrize("bad", [0, -0.1, float("inf"), float("nan")])
def test_grace_on_cursor_must_be_a_positive_finite_number(bad):
    with pytest.raises(DispatchRefused, match="positive finite"):
        Spec(
            fleet="cursor", model="composer-2.5", prompt="x", cwd="/tmp", mode="read",
            cap_usd=1.0, cap_grace_usd=bad,
        ).validate()


def test_grace_above_the_ceiling_is_refused():
    with pytest.raises(DispatchRefused, match="ceiling"):
        Spec(
            fleet="claude",
            prompt="x",
            cwd="/tmp",
            cap_usd=1.0,
            cap_grace_usd=CAP_GRACE_CEILING_USD + 0.01,
        ).validate()
    # Right at the ceiling is fine.
    Spec(
        fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=CAP_GRACE_CEILING_USD
    ).validate()


def test_grace_above_the_ceiling_is_refused_on_cursor_too():
    with pytest.raises(DispatchRefused, match="ceiling"):
        Spec(
            fleet="cursor",
            model="composer-2.5",
            prompt="x",
            cwd="/tmp",
            mode="read",
            cap_usd=1.0,
            cap_grace_usd=CAP_GRACE_CEILING_USD + 0.01,
        ).validate()
    # Right at the ceiling is fine.
    Spec(
        fleet="cursor",
        model="composer-2.5",
        prompt="x",
        cwd="/tmp",
        mode="read",
        cap_usd=1.0,
        cap_grace_usd=CAP_GRACE_CEILING_USD,
    ).validate()


# --- the argv figure ---------------------------------------------------------


def test_the_claude_budget_flag_folds_in_the_grace():
    argv = build_argv(
        Spec(fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0, cap_grace_usd=0.25)
    )
    assert argv[argv.index("--max-budget-usd") + 1] == "1.25"
    # No grace: unchanged from before this item.
    argv = build_argv(Spec(fleet="claude", prompt="x", cwd="/tmp", cap_usd=1.0))
    assert argv[argv.index("--max-budget-usd") + 1] == "1"


# --- Budget.settle in each of the four states --------------------------------


def test_settle_within_cap_has_no_grace_used():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(0.90, killed=False, fleet_status="success")
    assert b.exceeded is False
    assert b.grace_used == 0.0


def test_settle_inside_the_band_is_not_over_budget():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(1.10, killed=False, fleet_status="success")
    assert b.exceeded is False
    assert b.grace_used == pytest.approx(0.10)


def test_settle_above_the_band_is_over_budget_and_grace_used_is_capped():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(2.0, killed=False, fleet_status="success")
    assert b.exceeded is True
    # The band can only ever absorb its own size, however far over it the run is.
    assert b.grace_used == pytest.approx(0.25)


def test_settle_killed_is_over_budget_whatever_the_grace():
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(1.10, killed=True, fleet_status=None)
    assert b.exceeded is True


def test_settle_with_no_grace_or_unknown_cost_leaves_grace_used_none():
    b = Budget(cap_usd=1.0, enforcement="native")
    b.settle(1.10, killed=False, fleet_status="success")
    assert b.grace_used is None
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(None, killed=False, fleet_status="success", interrupted=True)
    assert b.grace_used is None


def test_to_dict_omits_grace_fields_when_no_grace_was_set():
    b = Budget(cap_usd=1.0, enforcement="native")
    b.settle(0.5, killed=False, fleet_status="success")
    assert "grace_usd" not in b.to_dict()
    assert "grace_used" not in b.to_dict()
    b2 = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b2.settle(1.1, killed=False, fleet_status="success")
    assert b2.to_dict()["grace_used"] == pytest.approx(0.10)


def test_capped_uses_settle_ceiling_so_a_breaker_inside_the_band_is_not_cap():
    """No test in this file or test_errors.py covered `capped()` with a
    grace band: `exceeded` is true for any kill, and `observed > cap_usd`
    then named the run `cap` while `grace_used` recorded the band absorbing
    the same dollars."""
    from conductor.errors import capped, error_kind
    from conductor.runner import Result

    result = Result(
        run_id="r1",
        fleet="claude",
        model="m",
        effort="standard",
        mode="read",
        cwd="/tmp/repo",
        timeout=60,
        exit_code=0,
        timed_out=False,
        duration_s=1.0,
        run_dir="/tmp/r1",
        stdout_path="/tmp/r1/stdout.log",
        stderr_path="/tmp/r1/stderr.log",
        tail="",
        spawned=True,
        answer_path="/tmp/r1/answer.txt",
        git_verdict={"checked": True, "no_op": True},
        breaker={"tripped": "looping: x repeated 6 times"},
        budget={
            "exceeded": True,
            "cap_usd": 1.0,
            "grace_usd": 0.25,
            "observed_usd": 1.10,
            "grace_used": 0.10,
        },
        error="looping: x; killed",
    )
    assert capped(result) is False
    assert error_kind(result) == "breaker"


def test_settle_never_clears_an_exceeded_verdict_on_a_weaker_second_call():
    """A native stop or breaker kill settles exceeded=True; the crash
    receipt then called settle again with killed=False and no fleet_status,
    which flipped the verdict while the commit was already undone."""
    b = Budget(cap_usd=1.0, enforcement="native", grace_usd=0.25)
    b.settle(0.90, killed=True, fleet_status=None)
    assert b.exceeded is True
    b.settle(0.90, killed=False, fleet_status=None)
    assert b.exceeded is True
    assert b.observed_usd == 0.90
    assert b.unpriced is False


def test_settle_never_discards_a_priced_figure_for_unpriced():
    b = Budget(cap_usd=1.0, enforcement="native")
    b.settle(0.50, killed=False, fleet_status="success")
    b.settle(None, killed=False, fleet_status=None)
    assert b.unpriced is False
    assert b.observed_usd == 0.50


def test_fit_cap_keeps_grace_when_remaining_covers_the_ceiling():
    assert _fit_cap_to_remaining(1.0, 0.25, 10.0) == (1.0, 0.25)
    assert _fit_cap_to_remaining(1.0, 0.25, None) == (1.0, 0.25)


def test_fit_cap_drops_grace_when_remaining_is_the_last_dollars():
    assert _fit_cap_to_remaining(1.0, 0.25, 0.25) == (0.25, None)
    assert _fit_cap_to_remaining(1.0, 0.25, 1.0) == (1.0, None)


def test_fit_cap_shrinks_grace_when_remaining_covers_cap_but_not_the_band():
    cap, grace = _fit_cap_to_remaining(1.0, 0.25, 1.10)
    assert cap == 1.0
    assert grace == pytest.approx(0.10)


def _ok_result(repo: Path, spec: Spec) -> Result:
    run_dir = Path(spec.cwd).parent / "fake-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "answer.txt").write_text("done\n")
    return Result(
        run_id="fake-run",
        fleet=spec.fleet,
        model=spec.model or "claude-sonnet-5",
        effort=spec.effort,
        mode=spec.mode,
        cwd=str(repo),
        timeout=600,
        exit_code=0,
        timed_out=False,
        duration_s=0.1,
        run_dir=str(run_dir),
        stdout_path="",
        stderr_path="",
        answer_path=str(run_dir / "answer.txt"),
        tail="ok",
        spawned=True,
        git_verdict={"checked": False, "no_op": False},
        usage={"cost_usd": 0.01, "cost_basis": "reported"},
    )


def test_last_dollars_tightening_does_not_keep_full_grace_on_top(
    repo, home, tmp_path, monkeypatch
):
    """A cap squeezed to remaining $0.25 must not still carry a $0.25 grace
    band: the ceiling has to fit inside remaining, and the ledger records
    that ceiling, not the un-graced cap."""
    seen: dict = {}
    captured: list[float | None] = []
    original_start = Ledger.start

    def spy(self: Ledger, cap_usd: float | None) -> None:
        captured.append(cap_usd)
        original_start(self, cap_usd)

    monkeypatch.setattr(Ledger, "start", spy)

    def dispatcher(spec, **_kwargs):
        seen["cap_usd"] = spec.cap_usd
        seen["cap_grace_usd"] = spec.cap_grace_usd
        return _ok_result(repo, spec)

    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "max_cost_usd": 0.25,
        "lanes": [
            {
                "fleet": "claude",
                "mode": "read",
                "isolate": False,
                "cap_usd": 1.0,
                "cap_grace_usd": 0.25,
            }
        ],
    }
    result = run_mission(
        mission_from_dict(raw, base_dir=tmp_path), home=home, dispatcher=dispatcher
    )
    assert result.lanes[0]["ok"] is True, result.lanes[0]
    assert seen["cap_usd"] == 0.25
    assert seen["cap_grace_usd"] is None
    assert captured == [0.25]


def test_ordinary_remaining_keeps_grace_and_the_ledger_records_the_ceiling(
    repo, home, tmp_path, monkeypatch
):
    """When remaining comfortably covers cap+grace, the band stays, and
    outstanding_cap_usd is the ceiling the lane can actually reach."""
    seen: dict = {}
    captured: list[float | None] = []
    original_start = Ledger.start

    def spy(self: Ledger, cap_usd: float | None) -> None:
        captured.append(cap_usd)
        original_start(self, cap_usd)

    monkeypatch.setattr(Ledger, "start", spy)

    def dispatcher(spec, **_kwargs):
        seen["cap_usd"] = spec.cap_usd
        seen["cap_grace_usd"] = spec.cap_grace_usd
        lock = next((home / "missions").glob("*/running.json"))
        seen["outstanding"] = json.loads(lock.read_text())["budget"]["outstanding_cap_usd"]
        return _ok_result(repo, spec)

    raw = {
        "prompt": "x",
        "cwd": str(repo),
        "max_cost_usd": 10.0,
        "lanes": [
            {
                "fleet": "claude",
                "mode": "read",
                "isolate": False,
                "cap_usd": 1.0,
                "cap_grace_usd": 0.25,
            }
        ],
    }
    result = run_mission(
        mission_from_dict(raw, base_dir=tmp_path), home=home, dispatcher=dispatcher
    )
    assert result.lanes[0]["ok"] is True, result.lanes[0]
    assert seen["cap_usd"] == 1.0
    assert seen["cap_grace_usd"] == 0.25
    assert captured == [1.25]
    assert seen["outstanding"] == 1.25


# --- a cursor read run through dispatch: the post-hoc verdict widens too ---


def cursor_envelope(answer: str, cost: float) -> str:
    """A cursor-shaped envelope: usage arrives once, in the final result."""
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": answer,
        "usage": {"inputTokens": 10, "outputTokens": 5},
        "total_cost_usd": cost,
    }
    return json.dumps(payload)


def _cursor_spec(repo: Path, *, cap_grace_usd: float) -> Spec:
    return Spec(
        fleet="cursor",
        model="composer-2.5",
        prompt="review this",
        cwd=str(repo),
        mode="read",
        cap_usd=1.0,
        cap_grace_usd=cap_grace_usd,
    )


def test_a_cursor_read_run_inside_the_band_settles_ok(repo, home, fake_fleet):
    # cap $1.00 + $0.20 over, band $0.25: a complete answer settles ok.
    fake_fleet(["sh", "-c", f"echo '{cursor_envelope('NO_FINDINGS', 1.20)}'"])
    result = dispatch(_cursor_spec(repo, cap_grace_usd=0.25), home=home)
    assert result.ok is True
    assert result.budget["enforcement"] == "post-hoc"
    assert result.budget["exceeded"] is False
    assert result.budget["grace_used"] == pytest.approx(0.20)


def test_a_cursor_read_run_past_the_band_is_over_cap(repo, home, fake_fleet):
    # Same $0.20 overrun, but a $0.10 band cannot cover it: over cap, as
    # today, rather than a silent pass.
    fake_fleet(["sh", "-c", f"echo '{cursor_envelope('NO_FINDINGS', 1.20)}'"])
    result = dispatch(_cursor_spec(repo, cap_grace_usd=0.10), home=home)
    assert result.ok is False
    assert result.budget["enforcement"] == "post-hoc"
    assert result.budget["exceeded"] is True
    assert result.budget["grace_used"] == pytest.approx(0.10)  # capped at the band's own size


# --- mission-level and cascade-level refusals; no cascade to fallbacks ------


def test_mission_level_grace_is_refused(tmp_path: Path):
    raw = {
        "prompt": "x",
        "cap_grace_usd": 0.25,
        "lanes": [{"fleet": "claude", "cap_usd": 1.0}],
    }
    with pytest.raises(MissionInvalid, match="stated per lane"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_cascade_level_grace_is_refused(tmp_path: Path):
    raw = {
        "prompt": "x",
        "cascade": {"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.25},
        "lanes": [{"fleet": "claude", "cap_usd": 1.0, "mode": "write"}],
    }
    with pytest.raises(MissionInvalid, match="stated per lane"):
        mission_from_dict(raw, base_dir=tmp_path)


def test_a_lanes_grace_never_cascades_to_its_fallback(tmp_path: Path):
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "fleet": "claude",
                "cap_usd": 1.0,
                "cap_grace_usd": 0.25,
                "fallback": [{"fleet": "claude", "cap_usd": 1.0}],
            }
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = m.lanes[0].attempts
    assert primary.cap_grace_usd == 0.25
    assert fallback.cap_grace_usd is None


def test_a_fallback_may_set_its_own_grace(tmp_path: Path):
    raw = {
        "prompt": "x",
        "lanes": [
            {
                "fleet": "claude",
                "cap_usd": 1.0,
                "fallback": [{"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.1}],
            }
        ],
    }
    m = mission_from_dict(raw, base_dir=tmp_path)
    primary, fallback = m.lanes[0].attempts
    assert primary.cap_grace_usd is None
    assert fallback.cap_grace_usd == pytest.approx(0.1)


# --- through the fake fleet: a mission-level view of the band ---------------


def test_a_claude_lane_finishing_inside_the_band_is_ok_and_reports_grace(
    repo, home, monkeypatch, tmp_path
):
    fake_fleets(monkeypatch, {"claude": say("done", cost=1.10)})
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "lanes": [{"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.25}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.lanes[0]["ok"] is True
    attempt = result.lanes[0]["attempts"][0]
    assert attempt["over_cap"] is False
    assert attempt["grace_used"] == pytest.approx(0.10)
    assert "grace used $0.10" in Path(result.report_path).read_text()


def test_a_claude_lane_above_the_band_is_over_cap_as_today(repo, home, monkeypatch, tmp_path):
    fake_fleets(monkeypatch, {"claude": say("done", cost=1.30)})
    mission = mission_from_dict(
        {
            "prompt": "x",
            "cwd": str(repo),
            "lanes": [{"fleet": "claude", "cap_usd": 1.0, "cap_grace_usd": 0.25}],
        },
        base_dir=tmp_path,
    )
    result = run_mission(mission, home=home)
    assert result.lanes[0]["ok"] is False
    attempt = result.lanes[0]["attempts"][0]
    assert attempt["over_cap"] is True
    assert "over budget" in attempt["failure"]


# --- the Shape A launcher: default grace, and --cap-grace-usd 0 ------------


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n")
    return spec


def test_cap_arithmetic_defaults_to_a_quarter_and_render_prints_it():
    caps = shape.cap_arithmetic(1, 1)
    assert caps.cap_grace_usd == 0.25
    assert "grace: $0.25" in caps.render()
    disabled = shape.cap_arithmetic(1, 1, cap_grace_usd=0)
    assert "grace: disabled" in disabled.render()


def test_cap_arithmetic_refuses_grace_above_the_ceiling():
    with pytest.raises(shape.ShapeInvalid, match="cap-grace-usd"):
        shape.cap_arithmetic(1, 1, cap_grace_usd=CAP_GRACE_CEILING_USD + 0.01)


def test_shape_a_cli_sets_the_default_grace_on_build_fix_and_grok(
    repo, home, monkeypatch, tmp_path, capsys
):
    # F5: the band now also lands on review-grok, the cursor read lane.
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
    build, _gemini, grok, fix = raw["lanes"]
    assert build["cap_grace_usd"] == 0.25
    assert grok["cap_grace_usd"] == 0.25
    assert fix["cap_grace_usd"] == 0.25
    out_text = capsys.readouterr().out
    assert "per claude lane and the grok read lane" in out_text


def test_shape_a_cli_cap_grace_usd_zero_disables_it(repo, home, monkeypatch, tmp_path):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = _spec(tmp_path)
    out = tmp_path / "m" / "mission.json"
    out.parent.mkdir()
    code = main(
        [
            "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
            "--items", "1", "--modules", "1", "--out", str(out), "--cap-grace-usd", "0",
        ]
    )
    assert code == 0
    raw = json.loads(out.read_text())
    build, _gemini, grok, fix = raw["lanes"]
    assert "cap_grace_usd" not in build
    assert "cap_grace_usd" not in grok
    assert "cap_grace_usd" not in fix
