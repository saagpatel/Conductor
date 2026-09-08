"""The failing paths of `conductor land`'s own checks.

A cold read of land.py and of this suite (2026-09-08) found the happy path
well covered and three refusals never fired: golden diffs on the merged
head, a chain that is not `verified`, and the already-merged shortcut over a
merge an earlier land could not undo. Each test here goes red when the check
it names is deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_land import _run_landable_lane  # type: ignore[import-not-found]

from conductor import land as land_mod
from conductor.land import LandInvalid, land


def _land_receipt(home: Path, mission_id: str, lane: str) -> dict:
    receipts = sorted((home / "missions" / mission_id / "land").glob(f"{lane}-*.json"))
    return json.loads(receipts[-1].read_text())


def test_golden_diffs_on_the_merged_head_fail_the_land_and_stop_before_attest(
    repo, home, fake_fleet, git_out, monkeypatch
):
    """`_run_checks` returns on a non-empty golden diff. Without that early
    return the land runs on to attest and reports a green landing over a
    fixture the merged tree no longer reproduces."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    pre_head = git_out(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(land_mod, "_golden_check", lambda worktree, **kw: ["m: replay differs"])

    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.ok is False
    assert result.failed_step == "golden"
    assert [step["name"] for step in result.steps] == ["merge", "gate", "golden"]
    assert "replay differs" in result.steps[-1]["detail"]
    # A failed land puts the checkout back where it found it.
    assert git_out(repo, "rev-parse", "HEAD") == pre_head
    assert result.reset is True


@pytest.mark.parametrize("state", ["partial", "empty", "unrecorded"])
def test_a_chain_that_is_not_verified_fails_the_land(
    repo, home, fake_fleet, git_out, monkeypatch, state
):
    """D7: only a complete chain may land. `partial` is a valid prefix of
    the mission's own chain, so an equality test against "verified" is the
    whole check -- a truthiness test on the attestation would pass here."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    pre_head = git_out(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        land_mod.attest, "attest_mission", lambda home, mission: {"state": state, "links": 1}
    )

    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.ok is False
    assert result.failed_step == "attest"
    assert state in result.steps[-1]["detail"]
    assert git_out(repo, "rev-parse", "HEAD") == pre_head


def test_the_gate_environment_carries_all_three_dispatch_variables(repo, home):
    """`_gate_env` stands in for the environment `runner.dispatch` gives a
    lane's own gate. Asserting only `CONDUCTOR_LANE` left the other two free
    to be dropped."""
    env = land_mod._gate_env("20260101T000000Z-m", "fix", "/tmp/wt")

    assert env["CONDUCTOR_RUN_ID"] == "land-20260101T000000Z-m-fix"
    assert env["CONDUCTOR_WORKTREE"] == "/tmp/wt"
    assert env["CONDUCTOR_LANE"] == "1"


def test_a_refusal_receipt_carries_the_refusal_message_itself(repo, home, fake_fleet):
    mission_id, _lane = _run_landable_lane(repo, home, fake_fleet)

    with pytest.raises(LandInvalid, match="does not exist"):
        land(mission_id, "nope", home=home, checkout=str(repo))

    receipt = _land_receipt(home, mission_id, "nope")
    assert receipt["refused"] == f"lane 'nope' does not exist in mission '{mission_id}'"


def test_a_land_over_an_ungated_merge_an_earlier_land_left_is_refused(
    repo, home, fake_fleet, git_out, monkeypatch
):
    """`_perform` resets only when HEAD is still exactly the merge commit and
    the tree is clean. When it cannot, the ungated merge stays in the branch
    -- and the already-merged shortcut used to answer `ok=True` over it,
    without gate, golden, or attest."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)

    # A red gate whose reset cannot run: the checkout is left holding the
    # merge, exactly as a dirty submodule or a failed `reset --hard` does.
    real_git_run = land_mod.git_run

    def no_reset(root, *args, **kwargs):
        if args[:2] == ("reset", "--hard"):
            return real_git_run(root, "rev-parse", "--verify", "--quiet", "nope-not-a-ref")
        return real_git_run(root, *args, **kwargs)

    monkeypatch.setattr(land_mod, "git_run", no_reset)
    failed = land(mission_id, lane, home=home, checkout=str(repo), gate_command="false")
    assert failed.ok is False
    assert failed.reset is False
    merge_sha = failed.merge_sha
    assert git_out(repo, "rev-parse", "HEAD") == merge_sha
    monkeypatch.setattr(land_mod, "git_run", real_git_run)

    with pytest.raises(LandInvalid, match="ungated merge") as caught:
        land(mission_id, lane, home=home, checkout=str(repo))

    assert merge_sha[:12] in str(caught.value)
    # The refusal is receipted like every other one.
    assert "ungated merge" in _land_receipt(home, mission_id, lane)["refused"]


def test_a_failed_merge_whose_abort_also_failed_says_so_on_the_receipt(
    repo, home, fake_fleet, monkeypatch
):
    """An abort that fails leaves MERGE_HEAD set, and every later `land`
    then refuses with "checkout is mid-merge" naming nothing about why."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    real_git_run = land_mod.git_run

    fail = "rev-parse", "--verify", "--quiet", "nope-not-a-ref"
    merged = []

    def broken(root, *args, **kwargs):
        # Both the merge and its abort fail, and MERGE_HEAD is still set
        # afterwards -- the shape a rejecting hook plus an index.lock makes.
        if args[:2] == ("merge", "--no-ff"):
            merged.append(True)
            return real_git_run(root, *fail)
        if args[:1] == ("merge",) and "--abort" in args:
            return real_git_run(root, *fail)
        if merged and args[:2] == ("rev-parse", "--verify") and "MERGE_HEAD" in args:
            return real_git_run(root, "rev-parse", "--verify", "--quiet", "HEAD")
        return real_git_run(root, *args, **kwargs)

    monkeypatch.setattr(land_mod, "git_run", broken)
    result = land(mission_id, lane, home=home, checkout=str(repo))

    assert result.ok is False
    assert result.failed_step == "merge"
    assert "still mid-merge" in result.steps[0]["detail"]


def test_the_shortcut_still_answers_for_a_lane_that_landed_cleanly(
    repo, home, fake_fleet, git_out
):
    """The guard reads failed land receipts only: a lane that landed green
    still takes the already-merged shortcut on a second call."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    assert land(mission_id, lane, home=home, checkout=str(repo)).ok is True

    again = land(mission_id, lane, home=home, checkout=str(repo))

    assert again.ok is True
    assert again.already_merged is True


def test_an_interrupt_during_the_gate_does_not_green_the_next_land(
    repo, home, fake_fleet, git_out, monkeypatch
):
    """`_perform` commits the merge and only then runs the gate; `land()`
    writes the receipt only after `_land` returns. A death between those
    two leaves the merge in HEAD with no receipt, and the already-merged
    shortcut used to answer `ok=True` over it -- a green land of work whose
    gate never finished."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    pre_head = git_out(repo, "rev-parse", "HEAD")

    def die(*_args, **_kwargs):
        raise RuntimeError("gate interrupted")

    with monkeypatch.context() as patched:
        patched.setattr(land_mod, "_run_checks", die)
        with pytest.raises(RuntimeError, match="gate interrupted"):
            land(mission_id, lane, home=home, checkout=str(repo))

    assert git_out(repo, "rev-parse", "HEAD") != pre_head
    land_dir = home / "missions" / mission_id / "land"
    assert not land_dir.is_dir() or not list(land_dir.glob("*.json"))

    with pytest.raises(LandInvalid, match="ungated merge") as caught:
        land(mission_id, lane, home=home, checkout=str(repo))
    assert git_out(repo, "rev-parse", "HEAD")[:12] in str(caught.value)


@pytest.mark.parametrize("lane", ["../escape", "a/b", "..", "", "-leading-dash"])
def test_a_lane_name_that_is_not_one_does_not_leave_the_land_directory(
    repo, home, fake_fleet, lane
):
    """`land()` catches the `LandInvalid` `_land` raises for a bad name and
    used to call `_write_receipt` with that name. `--lane` is an untyped CLI
    string, so `../escape` wrote outside `land/` and `a/b` raised
    `FileNotFoundError` instead of the refusal."""
    mission_id, _ok = _run_landable_lane(repo, home, fake_fleet)
    mission_dir = home / "missions" / mission_id
    before = {p for p in mission_dir.rglob("*") if p.is_file()}

    with pytest.raises(LandInvalid, match="is not a lane name"):
        land(mission_id, lane, home=home, checkout=str(repo))

    after = {p for p in mission_dir.rglob("*") if p.is_file()}
    assert after == before
    # `../escape` used to land in the mission directory, `../../x` one level
    # above that; neither is a file that existed before this call.
    assert not list((mission_dir).glob("escape-*.json"))
    assert not list((home / "missions").glob("escape-*.json"))


def test_a_prefix_lane_does_not_inherit_another_lanes_ungated_merge(
    repo, home, fake_fleet, git_out
):
    """Receipts are `{lane}-{stamp}.json`, so `land_dir.glob(f"{lane}-*.json")`
    for lane `fix` also matched every receipt of `fix-2`. The already-merged
    guard then refused `fix` for a merge `fix-2` left behind."""
    mission_id, lane = _run_landable_lane(repo, home, fake_fleet)
    landed = land(mission_id, lane, home=home, checkout=str(repo))
    assert landed.ok is True
    assert lane == "fix"

    land_dir = home / "missions" / mission_id / "land"
    planted = land_dir / "fix-2-20260101T000000000000Z.json"
    planted.write_text(
        json.dumps(
            {
                "ok": False,
                "lane": "fix-2",
                "merge_sha": git_out(repo, "rev-parse", "HEAD"),
                "reset": False,
            }
        )
    )

    again = land(mission_id, lane, home=home, checkout=str(repo))

    assert again.ok is True
    assert again.already_merged is True


def test_land_refuses_a_lane_environment_before_reading_the_mission(home, monkeypatch):
    """The `CONDUCTOR_LANE` refusal sat after `_land` had already read the
    mission directory, parsed lane and mission JSON, and run `git rev-parse`
    in the lane's repository. A missing mission used to raise 'does not
    exist' while the variable was set; the docstring says the refusal is
    outright, so it is the first thing `_land` does."""
    monkeypatch.setenv("CONDUCTOR_LANE", "1")

    with pytest.raises(LandInvalid, match="lane's environment"):
        land("no-such", "fix", home=home, checkout=str(home))
    assert not (home / "missions" / "no-such").exists()
