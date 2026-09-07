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
    ("run-prev-attempt", "attempt", "fix", "fix", True),
    ("run-attempt", "attempt", "fix", "fix", False),
    ("run-prev-collate", "collate", None, None, True),
    ("run-collate", "collate", None, None, False),
    ("run-order-1", "order", None, None, False),
    ("run-order-2", "order", None, None, False),
    ("run-judge-order-1", "order", None, None, False),
    ("run-judge-order-2", "order", None, None, False),
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
