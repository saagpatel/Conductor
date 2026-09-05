"""Spend reports preserve price gaps while grouping durable run receipts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor.cli import main


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
