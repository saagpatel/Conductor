"""Spend reports preserve price gaps while grouping durable run receipts."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from conductor import export, mission
from conductor import report as report_mod
from conductor.cli import main
from conductor.spend import Effect, effects, mission_run_ids


def _run(
    home: Path,
    run_id: str,
    *,
    fleet: str,
    model: str,
    cost: float | None,
    basis: str | None,
    tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    tool_calls: int = 0,
    ok: bool = True,
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
                "usage": {
                    "cost_usd": cost,
                    "cost_basis": basis,
                    "total_tokens": tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_write_tokens": cache_write_tokens,
                },
                "duration_s": 1.0,
                "breaker": {"tool_calls": tool_calls, "tripped": None},
                "ok": ok,
                "interrupted": False,
                "dry_run": dry_run,
            }
        )
    )


def _json_output(capsys) -> list[dict[str, object]]:
    return json.loads(capsys.readouterr().out)


def _sample(home: Path) -> tuple[str, str, str]:
    first = "20260101T000000Z-codex-first"
    second = "20260102T120000Z-claude-second"
    third = "20260103T000000Z-codex-third"
    _run(home, first, fleet="codex", model="sol", cost=1.25, basis="estimated", tokens=100)
    _run(
        home,
        second,
        fleet="claude",
        model="opus",
        cost=2.5,
        basis="reported",
        tokens=200,
        ok=False,
    )
    _run(home, third, fleet="codex", model="luna", cost=None, basis=None, tokens=300)
    return first, second, third


@pytest.mark.parametrize(
    ("grouping", "expected"),
    [
        ("day", {"2026-01-01", "2026-01-02", "2026-01-03"}),
        ("fleet", {"codex", "claude"}),
        ("model", {"sol", "opus", "luna"}),
        (
            "run",
            {
                "20260101T000000Z-codex-first",
                "20260102T120000Z-claude-second",
                "20260103T000000Z-codex-third",
            },
        ),
    ],
)
def test_spend_groups_every_supported_run_dimension(
    home: Path, monkeypatch, capsys, grouping: str, expected: set[str]
):
    _sample(home)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--by", grouping, "--json"]) == 0
    rows = _json_output(capsys)
    assert {row["group"] for row in rows[:-1]} == expected
    assert rows[-1] == {
        "group": "total",
        "runs": 3,
        "ok": 2,
        "cost_usd": 3.75,
        "estimated_runs": 1,
        "unpriced_runs": 1,
        "tokens": 600,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_calls": 0,
        "skipped": 0,
        "dry_runs": 0,
    }


def test_spend_associates_attempts_with_their_mission(home: Path, monkeypatch, capsys):
    first, _, _ = _sample(home)
    mission = home / "missions" / "20260104T000000Z-mission"
    mission.mkdir(parents=True)
    (mission / "result.json").write_text(
        json.dumps(
            {
                "name": "parser-refactor",
                "lanes": [{"name": "build", "attempts": [{"run_id": first}]}],
            }
        )
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--by", "mission", "--json"]) == 0
    rows = _json_output(capsys)
    assert {row["group"]: row["runs"] for row in rows[:-1]} == {
        "(standalone)": 2,
        "parser-refactor": 1,
    }


def test_spend_associates_a_ranking_collates_two_order_runs_with_their_mission(
    home: Path, monkeypatch, capsys
):
    """A ranking collate prices two dispatches (one per lane order) onto
    collate.orders[].run_id rather than collate.run_id; both must count
    toward the mission, not fall through to '(standalone)'."""
    first, second, third = _sample(home)
    mission = home / "missions" / "20260104T000000Z-mission"
    mission.mkdir(parents=True)
    (mission / "result.json").write_text(
        json.dumps(
            {
                "name": "rank-mission",
                "lanes": [{"name": "build", "attempts": [{"run_id": first}]}],
                "collate": {
                    "rank": True,
                    "orders": [{"run_id": second}, {"run_id": third}],
                },
            }
        )
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--by", "mission", "--json"]) == 0
    rows = _json_output(capsys)
    assert {row["group"]: row["runs"] for row in rows[:-1]} == {
        "rank-mission": 3,
    }


def test_spend_window_is_since_inclusive_and_until_exclusive(home: Path, monkeypatch, capsys):
    _sample(home)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert (
        main(
            [
                "spend",
                "--since",
                "2026-01-02T12:00:00Z",
                "--until",
                "2026-01-03",
                "--json",
            ]
        )
        == 0
    )
    rows = _json_output(capsys)
    assert rows[0]["group"] == "claude"
    assert rows[-1]["runs"] == 1
    assert rows[-1]["cost_usd"] == 2.5


def test_spend_counts_malformed_receipts_on_only_the_total_row(home: Path, monkeypatch, capsys):
    _sample(home)
    malformed = home / "runs" / "20260104T000000Z-broken"
    malformed.mkdir(parents=True)
    (malformed / "result.json").write_text("{broken")
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert all("skipped" not in row for row in rows[:-1])
    assert rows[-1]["skipped"] == 1


def _write_receipt(home: Path, run_id: str, payload: dict) -> None:
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps(payload))


@pytest.mark.parametrize(
    ("suffix", "cost_usd"),
    [
        ("boolcost", True),
        ("nancost", float("nan")),
        ("negcost", -1),
    ],
)
def test_spend_skips_a_well_formed_receipt_whose_cost_usd_fails_number_validation(
    home: Path, monkeypatch, capsys, suffix: str, cost_usd: object
):
    """`_number` raises on a bool, a non-finite value, and a negative
    number, so `_read_run` returns None and the whole receipt is skipped --
    it must not be counted as a run priced at 1/0, folded in as NaN, or
    subtracted from the total. Unlike the malformed-JSON case above, this
    JSON parses fine; only the value inside it is bad."""
    first, _, _ = _sample(home)
    run_id = f"20260105T000000Z-claude-{suffix}"
    _write_receipt(
        home,
        run_id,
        {
            "run_id": run_id,
            "fleet": "claude",
            "model": "sonnet",
            "usage": {"cost_usd": cost_usd, "cost_basis": "reported", "total_tokens": 5},
            "ok": True,
        },
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert rows[-1]["runs"] == 3
    assert rows[-1]["skipped"] == 1
    assert rows[-1]["cost_usd"] == 3.75
    assert first  # sanity: the sample fixture still ran


def test_spend_skips_a_receipt_whose_ok_is_not_a_bool(home: Path, monkeypatch, capsys):
    """`_read_run`'s `if not isinstance(ok, bool): return None` -- JSON's
    wire format has no bool/int distinction of its own, so an integer `1`
    here must not be read as `True` and folded into the ok count."""
    _sample(home)
    run_id = "20260105T000000Z-claude-okint"
    _write_receipt(
        home,
        run_id,
        {
            "run_id": run_id,
            "fleet": "claude",
            "model": "sonnet",
            "usage": {"cost_usd": 1.0, "cost_basis": "reported", "total_tokens": 5},
            "ok": 1,
        },
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert rows[-1]["runs"] == 3
    assert rows[-1]["skipped"] == 1
    assert rows[-1]["cost_usd"] == 3.75


def _priced_receipt(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "fleet": "claude",
        "model": "sonnet",
        "usage": {
            "cost_usd": 4.0,
            "cost_basis": "reported",
            "total_tokens": 10,
            "cache_read_tokens": 3,
            "cache_write_tokens": 2,
        },
        "breaker": {"tool_calls": 7, "tripped": None},
        "ok": True,
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"usage": {"cache_read_tokens": None}},
        {"usage": {"cache_write_tokens": None}},
        {"breaker": {"tool_calls": None, "tripped": None}},
    ],
)
def test_spend_keeps_a_priced_run_whose_optional_counter_is_json_null(
    home: Path, monkeypatch, capsys, patch: dict
):
    """`dict.get` returns a stored null, and the three optional counters used
    to treat that as a malformed receipt -- dropping the whole run, cost
    included. A missing key already counted as 0; an explicit null is the
    same missing count, not a skip."""
    first, _, _ = _sample(home)
    run_id = "20260105T000000Z-claude-null-counter"
    payload = _priced_receipt(run_id)
    if "usage" in patch:
        payload["usage"] = {**payload["usage"], **patch["usage"]}
    if "breaker" in patch:
        payload["breaker"] = patch["breaker"]
    _write_receipt(home, run_id, payload)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert rows[-1]["runs"] == 4
    assert rows[-1]["skipped"] == 0
    assert rows[-1]["cost_usd"] == 7.75
    assert first  # sanity: the sample fixture still ran


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cache_read_tokens", True),
        ("cache_read_tokens", "8"),
        ("cache_read_tokens", -1),
        ("cache_write_tokens", True),
        ("tool_calls", True),
        ("total_tokens", True),
    ],
)
def test_spend_still_skips_a_receipt_whose_optional_counter_is_the_wrong_type(
    home: Path, monkeypatch, capsys, field: str, value: object
):
    """A null is a missing count; a bool, a string, or a negative is still a
    refusal of the whole receipt. `True` is the load-bearing case: it is an
    `int` subclass and would otherwise read as 1."""
    _sample(home)
    run_id = "20260105T000000Z-claude-bad-counter"
    payload = _priced_receipt(run_id)
    if field == "tool_calls":
        payload["breaker"] = {"tool_calls": value, "tripped": None}
    else:
        payload["usage"][field] = value
    _write_receipt(home, run_id, payload)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert rows[-1]["runs"] == 3
    assert rows[-1]["skipped"] == 1
    assert rows[-1]["cost_usd"] == 3.75


def test_spend_reads_a_receipt_with_and_without_a_price_block(home: Path, monkeypatch, capsys):
    """W6: `usage.price` is new; a receipt written before it existed, and one
    that never estimated (a reported figure), both carry none of it -- and
    `conductor spend` must still load both, same as any other receipt."""
    without_price = "20260101T000000Z-codex-no-price"
    _run(home, without_price, fleet="codex", model="sol", cost=1.0, basis="estimated", tokens=10)

    with_price = home / "runs" / "20260101T000100Z-claude-with-price"
    with_price.mkdir(parents=True)
    (with_price / "result.json").write_text(
        json.dumps(
            {
                "run_id": with_price.name,
                "fleet": "claude",
                "model": "sonnet",
                "usage": {
                    "cost_usd": 2.0,
                    "cost_basis": "estimated",
                    "total_tokens": 20,
                    "price": {
                        "key": "claude-sonnet-5",
                        "source": "default",
                        "as_of": "2026-09-03",
                        "note": "",
                    },
                },
                "duration_s": 1.0,
                "breaker": {"tool_calls": 0, "tripped": None},
                "ok": True,
                "interrupted": False,
                "dry_run": False,
            }
        )
    )

    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert rows[-1]["runs"] == 2
    assert rows[-1]["skipped"] == 0
    assert rows[-1]["cost_usd"] == 3.0


def test_spend_text_calls_out_unpriced_runs(home: Path, monkeypatch, capsys):
    _sample(home)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith("total")
    assert "3.7500" in lines[-1]
    assert "1 unpriced" in lines[-1]


def test_spend_sums_cache_reads_and_exposes_them_in_text_and_json(
    home: Path, monkeypatch, capsys
):
    _run(
        home,
        "20260101T000000Z-codex-cached",
        fleet="codex",
        model="sol",
        cost=0.1,
        basis="estimated",
        tokens=125,
        cache_read_tokens=75,
    )
    # Old receipts have no cache field and must contribute zero, not disappear.
    legacy = home / "runs" / "20260102T000000Z-claude-legacy"
    legacy.mkdir(parents=True)
    (legacy / "result.json").write_text(
        json.dumps(
            {
                "run_id": legacy.name,
                "fleet": "claude",
                "model": "opus",
                "ok": True,
                "usage": {"cost_usd": 0.2, "cost_basis": "reported", "total_tokens": 10},
            }
        )
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    rows = _json_output(capsys)
    assert {row["group"]: row["cache_read_tokens"] for row in rows[:-1]} == {
        "claude": 0,
        "codex": 75,
    }
    assert rows[-1]["cache_read_tokens"] == 75
    assert main(["spend"]) == 0
    text = capsys.readouterr().out
    assert "cache_read_tokens" in text and text.splitlines()[-1].split()[-3] == "75"
    assert "cache_write_tokens" in text and text.splitlines()[-1].split()[-2] == "0"
    assert "tool_calls" in text and text.splitlines()[-1].split()[-1] == "0"


def test_spend_rejects_a_bad_since_with_one_line_and_exit_two(home: Path, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--since", "last-tuesday"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: invalid --since: last-tuesday\n"


def test_spend_excludes_dry_runs_instead_of_calling_them_unpriced(home: Path, monkeypatch, capsys):
    """Seen live 2026-09-03: 25 'unpriced' runs, most of them rehearsals."""
    _sample(home)
    _run(
        home,
        "20260903T010000Z-codex-x",
        fleet="codex",
        model="m",
        cost=None,
        basis=None,
        tokens=0,
        dry_run=True,
    )
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["spend", "--json"]) == 0
    total = _json_output(capsys)[-1]
    assert total["unpriced_runs"] == 1 and total["dry_runs"] == 1
    assert main(["spend"]) == 0
    assert "1 dry run(s) excluded" in capsys.readouterr().out.splitlines()[-1]


# --- F20: one effect inventory, read by resume, report, spend, and export ---


def _every_generation_snapshot() -> dict:
    """A snapshot carrying one entry of every generation of paid-dispatch
    key `spend.effects` has to know about: a bare-string lane `final`, a
    lane with `previous_attempts` and `attempts`, a collate with `orders`
    and a judge sitting's `judges[].orders` (E4), a `previous_collates`
    entry (E4), a `resolve` (D13), and two `previous_resolves` (D13)."""
    return {
        "lanes": [
            {"name": "build", "stage": "build", "final": "run-final"},
            {
                "name": "fix",
                "stage": "fix",
                "previous_attempts": [{"run_id": "run-prev-attempt"}],
                "attempts": [{"run_id": "run-attempt"}],
            },
        ],
        "previous_collates": [{"run_id": "run-prev-collate"}],
        "collate": {
            "run_id": "run-collate",
            "orders": [{"run_id": "run-order-1"}, {"run_id": "run-order-2"}],
            "judges": [
                {"orders": [{"run_id": "run-judge-order-1"}, {"run_id": "run-judge-order-2"}]}
            ],
        },
        "resolve": {"run_id": "run-resolve"},
        "previous_resolves": [
            {"run_id": "run-prev-resolve-1"},
            {"run_id": "run-prev-resolve-2"},
        ],
    }


_EVERY_GENERATION_ORDER: list[tuple[str, str, str | None, str | None, bool]] = [
    ("run-final", "attempt", "build", "build", False),
    # Live attempts before the superseded ones, matching `collate` /
    # `previous_collates` and `resolve` / `previous_resolves` below.
    ("run-attempt", "attempt", "fix", "fix", False),
    ("run-prev-attempt", "attempt", "fix", "fix", True),
    # The live collate before the superseded ones, matching `resolve` and
    # `previous_resolves` below (2026-09-08 review).
    ("run-collate", "collate", None, None, False),
    ("run-order-1", "order", None, None, False),
    ("run-order-2", "order", None, None, False),
    ("run-judge-order-1", "order", None, None, False),
    ("run-judge-order-2", "order", None, None, False),
    ("run-prev-collate", "collate", None, None, True),
    ("run-resolve", "resolve", None, None, False),
    ("run-prev-resolve-1", "resolve", None, None, True),
    ("run-prev-resolve-2", "resolve", None, None, True),
]


def test_effects_walks_every_generation_of_key_in_encounter_order():
    found = effects(_every_generation_snapshot())
    assert [
        (e.run_id, e.kind, e.lane, e.stage, e.superseded) for e in found
    ] == _EVERY_GENERATION_ORDER
    assert all(isinstance(e, Effect) for e in found)
    expected_ids = {row[0] for row in _EVERY_GENERATION_ORDER}
    assert mission_run_ids(_every_generation_snapshot()) == expected_ids


def test_a_collate_kept_across_a_resume_is_not_read_as_superseded():
    """`previous_collates` was walked before `collate`, and first occurrence
    wins, so a collate run appearing in both -- a collate kept across a
    resume -- was permanently marked superseded. `resolve` and
    `previous_resolves` already had the opposite, correct order."""
    snapshot = {
        "collate": {"run_id": "run-c"},
        "previous_collates": [{"run_id": "run-c"}, {"run_id": "run-older"}],
        "resolve": {"run_id": "run-r"},
        "previous_resolves": [{"run_id": "run-r"}],
    }

    by_id = {e.run_id: e for e in effects(snapshot)}

    assert by_id["run-c"].superseded is False
    assert by_id["run-older"].superseded is True
    # The shape that was already right, asserted beside it.
    assert by_id["run-r"].superseded is False


def test_an_attempt_in_both_lists_is_not_read_as_superseded():
    """`walk_lane` visited `previous_attempts` before `attempts`, so a run_id
    in both lists was registered as superseded -- the same first-occurrence
    bug `walk_collate` had. `final` is walked before both, so this uses an
    id that is not the lane's final: the overlap `final` does not already
    cover."""
    snapshot = {
        "lanes": [
            {
                "name": "build",
                "stage": "build",
                "final": "run-final",
                "attempts": [{"run_id": "run-live"}],
                "previous_attempts": [{"run_id": "run-live"}, {"run_id": "run-old"}],
            }
        ]
    }
    by_id = {e.run_id: e for e in effects(snapshot)}
    assert by_id["run-live"].superseded is False
    assert by_id["run-old"].superseded is True
    assert by_id["run-final"].superseded is False

    via_lanes = effects(
        lanes=[
            {
                "name": "fix",
                "stage": "fix",
                "attempts": [{"run_id": "run-live"}],
                "previous_attempts": [{"run_id": "run-live"}, {"run_id": "run-old"}],
            }
        ]
    )
    by_lane = {e.run_id: e for e in via_lanes}
    assert by_lane["run-live"].superseded is False
    assert by_lane["run-old"].superseded is True


def test_effects_docstring_names_current_before_superseded():
    """The snapshot-shape sentence used to list `previous_collates` before
    `collate` -- the traversal that caused the 2026-09-08 bug -- and
    `previous_attempts` before `attempts`. The code walks the live side
    first; the prose has to name that order."""
    doc = effects.__doc__
    assert doc is not None
    start = doc.index("`snapshot` is a mission")
    shape = doc[start : doc.index("iterable of lane-receipt")]
    assert shape.index("`attempts`") < shape.index("`previous_attempts`")
    assert shape.index("`collate`") < shape.index("`previous_collates`")
    assert shape.index("`resolve`") < shape.index("`previous_resolves`")


def test_report_scan_missions_join_names_every_effect_from_one_snapshot(home: Path):
    """D13 omission path: before F20, `_scan_missions` walked `attempts` and
    `previous_attempts` itself and only reached `collate`/`resolve` runs
    through a second hand-written walk -- a fifth kind of key (a future
    generation) would again need a matching edit here. Reading the join
    from `spend.effects` instead means this test would fail on old code the
    same way D13's own omission once did, if the inventory it now shares
    ever falls behind again."""
    mission_id = "20260101T000000Z-full-mission"
    mission_dir = home / "missions" / mission_id
    mission_dir.mkdir(parents=True)
    (mission_dir / "result.json").write_text(json.dumps(_every_generation_snapshot()))

    join, _meta = report_mod._scan_missions(home)

    for run_id, _kind, lane, stage, _superseded in _EVERY_GENERATION_ORDER:
        assert join[run_id] == (mission_id, lane, stage)


def test_mission_run_receipt_spend_prices_a_run_named_only_under_previous_resolves(
    home: Path,
):
    """D13 omission path: a resolver a rerun superseded is a paid dispatch
    that appears nowhere but `previous_resolves`. Before F20 this was its
    own hand-written branch in `_run_receipt_spend`; if the shared
    `spend.effects` inventory ever drops `previous_resolves` again, this
    run's cost silently falls out of resume's spend accounting the same way
    it once did pre-D13, and this test catches it."""
    run_id = "20260101T000000Z-claude-superseded-resolver"
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "fleet": "claude",
                "model": "opus",
                "ok": True,
                "dry_run": False,
                "usage": {"cost_usd": 3.5, "cost_basis": "reported", "total_tokens": 10},
            }
        )
    )
    prior_result = {"previous_resolves": [{"run_id": run_id}]}

    spent, unpriced = mission._run_receipt_spend(home, {}, prior_result)

    assert spent == pytest.approx(3.5)
    assert unpriced == 0


def test_mission_run_receipt_spend_prefers_the_freshly_loaded_lane_over_the_prior_snapshot(
    home: Path,
):
    """Opus review finding 1: `_run_receipt_spend` calls `spend.effects`
    with both `prior_result` (whose own `lanes` list is walked first) and
    `lanes=[...]` (the freshly reloaded lane receipts, walked last); with
    `effects`'s first-occurrence-wins rule that makes the stale snapshot's
    record win over the fresh one whenever the same run id appears in both.
    A lane whose `previous_result.json` recorded only a bare `final` string
    for a run (`record={}`, per `spend.py`) collides with that same run
    appearing in the freshly loaded lane's own `attempts` -- and the run's
    own receipt directory being gone (a resume long after `runs/` was
    pruned) means pricing falls back to the record, which the stale entry
    left empty. The spec (item 3) requires the opposite priority: "a lane
    attempt's record is the one kept when a run id appears in both"."""
    run_id = "20260101T000000Z-claude-shared-run"
    prior_result = {
        "lanes": [{"name": "build", "stage": "build", "final": run_id}],
    }
    previous = {
        "build": mission.LaneResult(
            name="build",
            ok=True,
            stage="build",
            attempts=[{"run_id": run_id, "cost_usd": 2.0}],
        )
    }

    spent, unpriced = mission._run_receipt_spend(home, previous, prior_result)

    assert spent == pytest.approx(2.0)
    assert unpriced == 0


def test_export_lane_run_ids_returns_a_run_named_only_under_previous_attempts(home: Path):
    mission_dir = home / "missions" / "20260101T000000Z-mission"
    lanes_dir = mission_dir / "lanes"
    lanes_dir.mkdir(parents=True)
    (lanes_dir / "build.json").write_text(
        json.dumps(
            {
                "name": "build",
                "stage": "build",
                "previous_attempts": [{"run_id": "run-superseded-attempt"}],
                "attempts": [],
            }
        )
    )

    assert export._lane_run_ids(mission_dir) == {"run-superseded-attempt"}


def test_no_hand_written_walk_of_collate_or_resolve_keys_remains():
    """The review that asked for F20 (D13, simplification 1) named the risk
    directly: a fifth hand-written walk of `previous_collates`,
    `previous_resolves`, or a collate's `orders` could always creep back in
    beside the shared `spend.effects` inventory. This fails on the
    pre-F20 code, where all three literal strings appear in each of these
    three functions."""
    for source in (
        inspect.getsource(report_mod._scan_missions),
        inspect.getsource(mission._run_receipt_spend),
        inspect.getsource(export._lane_run_ids),
    ):
        for banned in ("previous_collates", "previous_resolves", "orders"):
            assert banned not in source
