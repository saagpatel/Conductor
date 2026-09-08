"""Reprice corrects parser-moved token counters and refuses the two traps."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from conductor import prices
from conductor.cli import main
from conductor.outputs import parse
from conductor.reprice import Summary, _validate_fleet_filter, reprice


def _cursor_stdout(*, input_tokens: int, output_tokens: int, cache_read: int) -> str:
    return json.dumps(
        {
            "type": "result",
            "result": "ok",
            "usage": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheReadTokens": cache_read,
            },
        }
    )


def _codex_stdout(*, input_tokens: int, output_tokens: int, cached: int = 0) -> str:
    events = [
        {"type": "thread.started", "thread_id": "t1"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "ok"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached,
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events)


def _claude_stdout(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cost_usd: float | None = None,
) -> str:
    payload: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
        },
    }
    if cost_usd is not None:
        payload["total_cost_usd"] = cost_usd
    return json.dumps(payload)


def _antigravity_stdout(*, input_tokens: int, output_tokens: int) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": "ok",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            },
        }
    )


def _write_run(
    home: Path,
    run_id: str,
    *,
    fleet: str,
    model: str,
    stdout: str,
    usage: dict | None,
    ok: bool = True,
    dry_run: bool = False,
    extra: dict | None = None,
    compact: bool = False,
) -> Path:
    directory = home / "runs" / run_id
    directory.mkdir(parents=True)
    receipt: dict[str, object] = {
        "run_id": run_id,
        "fleet": fleet,
        "model": model,
        "ok": ok,
        "dry_run": dry_run,
        "usage": usage,
    }
    if extra:
        receipt.update(extra)
    text = json.dumps(receipt) if compact else json.dumps(receipt, indent=2)
    (directory / "result.json").write_text(text)
    (directory / "stdout.log").write_text(stdout)
    return directory


def _json_output(capsys) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def _reprice(home: Path, monkeypatch, capsys, *flags: str) -> dict[str, object]:
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["reprice", *flags]) == 0
    return _json_output(capsys)


def test_reprice_rewrites_moved_counters_and_is_idempotent_on_a_second_pass(
    home: Path, monkeypatch, capsys
):
    """The 2026-09-08 shape: stored input was zeroed by subtracting cache."""
    stdout = _cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000)
    parsed = parse("cursor", stdout).usage
    assert parsed is not None
    assert parsed.input_tokens == 100
    stored_cost = 0.002525
    run_id = "20260903T000000Z-cursor-zeroed"
    _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=stdout,
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": stored_cost,
            "cost_basis": "estimated",
            "price": {"key": "composer-2.5", "source": "default", "as_of": "2026-09-01"},
        },
    )
    first = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert first["scanned"] == 1
    assert first["moved"] == 1
    assert first["apply"] is True
    rewritten = json.loads((home / "runs" / run_id / "result.json").read_text())
    usage = rewritten["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 10
    assert usage["cache_read_tokens"] == 5000
    assert usage["total_tokens"] == parsed.total_tokens
    expected_cost = prices.estimate(
        "composer-2.5",
        input_tokens=100,
        output_tokens=10,
        cache_read_tokens=5000,
        cache_write_tokens=0,
    )
    assert usage["cost_usd"] == expected_cost
    assert usage["cost_usd"] != stored_cost
    assert usage["cost_basis"] == "estimated"
    assert usage["price"]["key"] == "composer-2.5"
    second = _reprice(home, monkeypatch, capsys, "--json")
    assert second["moved"] == 0
    assert second["skipped"]["tokens_unchanged"] == 1


def test_reprice_keeps_a_reported_cost_when_tokens_move(home: Path, monkeypatch, capsys):
    stdout = _claude_stdout(
        input_tokens=200,
        output_tokens=20,
        cache_read=50,
        cost_usd=9.99,
    )
    parsed = parse("claude", stdout).usage
    assert parsed is not None
    assert parsed.cost_usd == 9.99
    run_id = "20260903T120000Z-claude-reported"
    _write_run(
        home,
        run_id,
        fleet="claude",
        model="claude-sonnet-5",
        stdout=stdout,
        usage={
            "input_tokens": 0,
            "output_tokens": 20,
            "cache_read_tokens": 50,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 70,
            "cost_usd": 1.25,
            "cost_basis": "reported",
            "price": None,
        },
    )
    result = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert result["moved"] == 1
    usage = json.loads((home / "runs" / run_id / "result.json").read_text())["usage"]
    assert usage["input_tokens"] == 200
    assert usage["total_tokens"] == parsed.total_tokens
    assert usage["cost_usd"] == 1.25
    assert usage["cost_basis"] == "reported"
    assert usage["price"] is None


def test_reprice_skips_unchanged_counters_even_when_the_price_table_moved(
    home: Path, monkeypatch, capsys
):
    """The by-hand trap: same tokens, a later as_of, today's rate is fiction."""
    stdout = _codex_stdout(input_tokens=1000, output_tokens=100)
    parsed = parse("codex", stdout).usage
    assert parsed is not None
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    old_cost = prices.estimate(
        "gpt-5.6-sol",
        input_tokens=parsed.input_tokens,
        output_tokens=parsed.output_tokens,
        cache_read_tokens=parsed.cache_read_tokens,
        cache_write_tokens=parsed.cache_write_tokens,
    )
    assert old_cost is not None
    run_id = "20260903T000000Z-codex-sol"
    usage = {
        "input_tokens": parsed.input_tokens,
        "output_tokens": parsed.output_tokens,
        "cache_read_tokens": parsed.cache_read_tokens,
        "cache_write_tokens": parsed.cache_write_tokens,
        "thinking_tokens": parsed.thinking_tokens,
        "total_tokens": parsed.total_tokens,
        "cost_usd": old_cost,
        "cost_basis": "estimated",
        "price": {"key": "gpt-5.6-sol", "source": "default", "as_of": "2026-09-02"},
    }
    directory = _write_run(
        home,
        run_id,
        fleet="codex",
        model="gpt-5.6-sol",
        stdout=stdout,
        usage=usage,
        compact=True,
    )
    before = (directory / "result.json").read_bytes()
    (home / "prices.json").write_text(json.dumps({"gpt-5.6-sol": {"input": 8.0, "output": 40.0}}))
    new_cost = prices.estimate(
        "gpt-5.6-sol",
        input_tokens=parsed.input_tokens,
        output_tokens=parsed.output_tokens,
        cache_read_tokens=parsed.cache_read_tokens,
        cache_write_tokens=parsed.cache_write_tokens,
    )
    assert new_cost is not None
    assert new_cost != old_cost
    result = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert result["moved"] == 0
    assert result["skipped"]["tokens_unchanged"] == 1
    assert result["archive"] is None
    assert (directory / "result.json").read_bytes() == before


def test_reprice_dry_run_is_the_default_and_writes_nothing(home: Path, monkeypatch, capsys):
    stdout = _cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000)
    run_id = "20260903T000000Z-cursor-dry"
    directory = _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=stdout,
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    before = (directory / "result.json").read_bytes()
    defaulted = _reprice(home, monkeypatch, capsys, "--json")
    assert defaulted["apply"] is False
    assert defaulted["moved"] == 1
    assert defaulted["archive"] is None
    assert (directory / "result.json").read_bytes() == before
    explicit = _reprice(home, monkeypatch, capsys, "--dry-run", "--json")
    assert explicit["apply"] is False
    assert explicit["moved"] == 1
    assert (directory / "result.json").read_bytes() == before
    assert list(home.glob("runs-receipts.backup-*.tgz")) == []


def test_reprice_apply_archives_every_receipt_before_the_first_write(
    home: Path, monkeypatch, capsys
):
    moving = "20260903T000000Z-cursor-moving"
    still = "20260903T010000Z-cursor-still"
    _write_run(
        home,
        moving,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        extra={"ok": True, "note": "pre-write-moving"},
    )
    _write_run(
        home,
        still,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=10, output_tokens=1, cache_read=0),
        usage={
            "input_tokens": 10,
            "output_tokens": 1,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 11,
            "cost_usd": 0.000008,
            "cost_basis": "estimated",
        },
        extra={"ok": True, "note": "pre-write-still"},
    )
    result = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert result["moved"] == 1
    archive = result["archive"]
    assert isinstance(archive, str)
    archive_path = Path(archive)
    assert archive_path.parent == home
    assert archive_path.name.startswith("runs-receipts.backup-")
    assert archive_path.name.endswith(".tgz")
    with tarfile.open(archive_path, "r:gz") as tar:
        names = set(tar.getnames())
        assert f"runs/{moving}/result.json" in names
        assert f"runs/{still}/result.json" in names
        moving_file = tar.extractfile(f"runs/{moving}/result.json")
        still_file = tar.extractfile(f"runs/{still}/result.json")
        assert moving_file is not None and still_file is not None
        archived_moving = json.loads(moving_file.read())
        archived_still = json.loads(still_file.read())
    assert archived_moving["usage"]["input_tokens"] == 0
    assert archived_moving["note"] == "pre-write-moving"
    assert archived_still["usage"]["input_tokens"] == 10
    on_disk_moving = json.loads((home / "runs" / moving / "result.json").read_text())
    on_disk_still = json.loads((home / "runs" / still / "result.json").read_text())
    assert on_disk_moving["usage"]["input_tokens"] == 100
    assert on_disk_still["usage"]["input_tokens"] == 10
    assert on_disk_still["note"] == "pre-write-still"


def test_reprice_skips_a_receipt_that_reparses_to_no_usage(home: Path, monkeypatch, capsys):
    run_id = "20260904T000000Z-codex-watcher"
    _write_run(
        home,
        run_id,
        fleet="codex",
        model="gpt-5.6-sol",
        stdout="no envelope, watcher-sourced figures only",
        usage={
            "input_tokens": 40,
            "output_tokens": 8,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 48,
            "cost_usd": 0.12,
            "cost_basis": "estimated",
        },
    )
    before = json.loads((home / "runs" / run_id / "result.json").read_text())
    result = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert result["scanned"] == 1
    assert result["moved"] == 0
    assert result["skipped"]["no_parsed_usage"] == 1
    after = json.loads((home / "runs" / run_id / "result.json").read_text())
    assert after["usage"] == before["usage"]
    assert after["usage"]["input_tokens"] == 40
    assert after["usage"]["cost_usd"] == 0.12


def test_reprice_leaves_verdicts_ok_and_commit_byte_identical(home: Path, monkeypatch, capsys):
    run_id = "20260903T000000Z-cursor-fields"
    verdict = {"ok": False, "criteria": [{"id": "a", "ok": True, "evidence": "file.py:1"}]}
    commit = {"committed": True, "sha": "abc123", "branch": "conductor/lane"}
    _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        extra={
            "ok": False,
            "verdict": verdict,
            "commit": commit,
            "error": "gate failed",
        },
    )
    _reprice(home, monkeypatch, capsys, "--apply", "--json")
    rewritten = json.loads((home / "runs" / run_id / "result.json").read_text())
    assert rewritten["ok"] is False
    assert rewritten["verdict"] == verdict
    assert rewritten["commit"] == commit
    assert json.dumps(rewritten["verdict"], sort_keys=True) == json.dumps(verdict, sort_keys=True)
    assert json.dumps(rewritten["commit"], sort_keys=True) == json.dumps(commit, sort_keys=True)
    assert rewritten["error"] == "gate failed"
    text = (home / "runs" / run_id / "result.json").read_text()
    assert not text.endswith("\n")


def test_reprice_skips_a_receipt_with_no_usage_object(home: Path, monkeypatch, capsys):
    run_id = "20260904T010000Z-no-usage"
    _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=10, output_tokens=1, cache_read=0),
        usage=None,
    )
    result = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert result["moved"] == 0
    assert result["skipped"]["no_usage"] == 1
    assert json.loads((home / "runs" / run_id / "result.json").read_text())["usage"] is None


def test_reprice_skips_a_dry_run_receipt(home: Path, monkeypatch, capsys):
    run_id = "20260904T020000Z-dry"
    _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        dry_run=True,
    )
    before = (home / "runs" / run_id / "result.json").read_bytes()
    result = _reprice(home, monkeypatch, capsys, "--apply", "--json")
    assert result["moved"] == 0
    assert result["skipped"]["dry_run"] == 1
    assert (home / "runs" / run_id / "result.json").read_bytes() == before


def test_reprice_nothing_found_is_exit_zero(home: Path, monkeypatch, capsys):
    home.mkdir(parents=True)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["reprice", "--json"]) == 0
    payload = _json_output(capsys)
    assert payload["scanned"] == 0
    assert payload["moved"] == 0
    assert "fleet" not in payload
    assert "fleet_mismatch" not in payload["skipped"]


def test_reprice_unfiltered_summary_has_no_fleet_filter_keys(home: Path, monkeypatch, capsys):
    stdout = _cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000)
    _write_run(
        home,
        "20260903T000000Z-cursor-compat",
        fleet="cursor",
        model="composer-2.5",
        stdout=stdout,
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    payload = _reprice(home, monkeypatch, capsys, "--json")
    assert "fleet" not in payload
    assert "fleet_mismatch" not in payload["skipped"]


def test_reprice_rejects_an_unknown_api_fleet_before_scanning(home: Path):
    home.mkdir(parents=True)
    with pytest.raises(ValueError, match="unknown fleet 'bogus'"):
        reprice(home, fleet="bogus")


def test_reprice_cli_rejects_an_unknown_fleet(home: Path, monkeypatch):
    home.mkdir(parents=True)
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    with pytest.raises(SystemExit):
        main(["reprice", "--fleet", "bogus"])


def test_reprice_fleet_filter_moves_only_the_selected_fleet(home: Path, monkeypatch, capsys):
    agy_id = "20260908T000000Z-antigravity-moving"
    cursor_id = "20260908T010000Z-cursor-moving"
    agy_stdout = _antigravity_stdout(input_tokens=200, output_tokens=20)
    cursor_stdout = _cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000)
    _write_run(
        home,
        agy_id,
        fleet="antigravity",
        model="gemini-3.7-flash",
        stdout=agy_stdout,
        usage={
            "input_tokens": 0,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 20,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    _write_run(
        home,
        cursor_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=cursor_stdout,
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    payload = _reprice(home, monkeypatch, capsys, "--fleet", "antigravity", "--json")
    assert payload["fleet"] == "antigravity"
    assert payload["scanned"] == 2
    assert payload["moved"] == 1
    assert payload["skipped"]["fleet_mismatch"] == 1
    agy_fleet = payload["fleets"]["antigravity"]
    assert agy_fleet["receipts"] == 1
    assert agy_fleet["input_tokens"] == 200
    assert agy_fleet["output_tokens"] == 0
    cursor_usage = json.loads((home / "runs" / cursor_id / "result.json").read_text())["usage"]
    assert cursor_usage["input_tokens"] == 0


def test_reprice_fleet_filter_skips_other_fleets_before_reading_stdout(
    home: Path, monkeypatch, capsys
):
    """Excluded stdout must not be parsed: invalid cursor bytes stay unread."""
    run_id = "20260908T020000Z-cursor-excluded"
    directory = _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout="this is not cursor json and would be no_parsed_usage if read",
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    before = (directory / "result.json").read_bytes()
    payload = _reprice(home, monkeypatch, capsys, "--fleet", "antigravity", "--json")
    assert payload["scanned"] == 1
    assert payload["moved"] == 0
    assert payload["skipped"]["fleet_mismatch"] == 1
    assert payload["skipped"]["no_parsed_usage"] == 0
    assert (directory / "result.json").read_bytes() == before


def test_reprice_fleet_filter_dry_run_preserves_matching_and_excluded_bytes(
    home: Path, monkeypatch, capsys
):
    agy_id = "20260908T030000Z-antigravity-dry"
    cursor_id = "20260908T040000Z-cursor-dry"
    agy_dir = _write_run(
        home,
        agy_id,
        fleet="antigravity",
        model="gemini-3.7-flash",
        stdout=_antigravity_stdout(input_tokens=50, output_tokens=5),
        usage={
            "input_tokens": 0,
            "output_tokens": 5,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    cursor_dir = _write_run(
        home,
        cursor_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    agy_before = (agy_dir / "result.json").read_bytes()
    cursor_before = (cursor_dir / "result.json").read_bytes()
    payload = _reprice(
        home, monkeypatch, capsys, "--fleet", "antigravity", "--dry-run", "--json"
    )
    assert payload["apply"] is False
    assert payload["moved"] == 1
    assert payload["archive"] is None
    assert (agy_dir / "result.json").read_bytes() == agy_before
    assert (cursor_dir / "result.json").read_bytes() == cursor_before


def test_reprice_fleet_filter_apply_rewrites_only_matching_receipts(
    home: Path, monkeypatch, capsys
):
    agy_id = "20260908T050000Z-antigravity-apply"
    cursor_id = "20260908T060000Z-cursor-apply"
    _write_run(
        home,
        agy_id,
        fleet="antigravity",
        model="gemini-3.7-flash",
        stdout=_antigravity_stdout(input_tokens=80, output_tokens=8),
        usage={
            "input_tokens": 0,
            "output_tokens": 8,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 8,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        extra={"note": "agy-pre-write"},
    )
    _write_run(
        home,
        cursor_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        extra={"note": "cursor-pre-write"},
    )
    payload = _reprice(home, monkeypatch, capsys, "--fleet", "antigravity", "--apply", "--json")
    assert payload["moved"] == 1
    archive = payload["archive"]
    assert isinstance(archive, str)
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
        assert f"runs/{agy_id}/result.json" in names
        assert f"runs/{cursor_id}/result.json" in names
        agy_file = tar.extractfile(f"runs/{agy_id}/result.json")
        cursor_file = tar.extractfile(f"runs/{cursor_id}/result.json")
        assert agy_file is not None and cursor_file is not None
        archived_agy = json.loads(agy_file.read())
        archived_cursor = json.loads(cursor_file.read())
    assert archived_agy["usage"]["input_tokens"] == 0
    assert archived_cursor["usage"]["input_tokens"] == 0
    on_disk_agy = json.loads((home / "runs" / agy_id / "result.json").read_text())
    on_disk_cursor = json.loads((home / "runs" / cursor_id / "result.json").read_text())
    assert on_disk_agy["usage"]["input_tokens"] == 80
    assert on_disk_agy["note"] == "agy-pre-write"
    assert on_disk_cursor["usage"]["input_tokens"] == 0
    assert on_disk_cursor["note"] == "cursor-pre-write"


def test_reprice_fleet_filter_empty_selection_is_exit_zero(home: Path, monkeypatch, capsys):
    _write_run(
        home,
        "20260908T070000Z-cursor-only",
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    payload = _reprice(home, monkeypatch, capsys, "--fleet", "antigravity", "--json")
    assert payload["fleet"] == "antigravity"
    assert payload["scanned"] == 1
    assert payload["moved"] == 0
    assert payload["skipped"]["fleet_mismatch"] == 1
    assert sum(payload["skipped"].values()) == 1


@pytest.mark.parametrize(
    "invalid",
    [
        True,
        False,
        [],
        {},
        "",
        "bogus",
    ],
)
def test_reprice_rejects_invalid_api_fleet_types_before_scanning(
    home: Path, invalid: object
):
    home.mkdir(parents=True)
    _write_run(
        home,
        "20260908T080000Z-cursor-never-scanned",
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
    )
    with pytest.raises(ValueError, match="unknown fleet"):
        reprice(home, fleet=invalid)  # type: ignore[arg-type]
    receipt = home / "runs" / "20260908T080000Z-cursor-never-scanned" / "result.json"
    assert json.loads(receipt.read_text())["usage"]["input_tokens"] == 0


def test_validate_fleet_filter_rejects_nonstring_before_membership():
    with pytest.raises(ValueError, match="unknown fleet"):
        _validate_fleet_filter([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown fleet"):
        _validate_fleet_filter({})  # type: ignore[arg-type]


def test_reprice_fleet_filter_counts_other_fleet_no_usage_as_mismatch(
    home: Path, monkeypatch, capsys
):
    run_id = "20260908T090000Z-cursor-no-usage-filtered"
    _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=10, output_tokens=1, cache_read=0),
        usage=None,
    )
    payload = _reprice(home, monkeypatch, capsys, "--fleet", "antigravity", "--json")
    assert payload["scanned"] == 1
    assert payload["skipped"]["fleet_mismatch"] == 1
    assert payload["skipped"]["no_usage"] == 0


def test_reprice_fleet_filter_counts_other_fleet_dry_run_as_mismatch(
    home: Path, monkeypatch, capsys
):
    run_id = "20260908T100000Z-cursor-dry-filtered"
    _write_run(
        home,
        run_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        dry_run=True,
    )
    payload = _reprice(home, monkeypatch, capsys, "--fleet", "antigravity", "--json")
    assert payload["scanned"] == 1
    assert payload["skipped"]["fleet_mismatch"] == 1
    assert payload["skipped"]["dry_run"] == 0


def test_reprice_unfiltered_keeps_selected_fleet_dry_run_and_no_usage_skips(
    home: Path, monkeypatch, capsys
):
    dry_id = "20260908T110000Z-cursor-dry-unfiltered"
    no_usage_id = "20260908T120000Z-cursor-no-usage-unfiltered"
    _write_run(
        home,
        dry_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=100, output_tokens=10, cache_read=5000),
        usage={
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_tokens": 5000,
            "cache_write_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 5010,
            "cost_usd": 0.01,
            "cost_basis": "estimated",
        },
        dry_run=True,
    )
    _write_run(
        home,
        no_usage_id,
        fleet="cursor",
        model="composer-2.5",
        stdout=_cursor_stdout(input_tokens=10, output_tokens=1, cache_read=0),
        usage=None,
    )
    payload = _reprice(home, monkeypatch, capsys, "--json")
    assert payload["scanned"] == 2
    assert payload["skipped"]["dry_run"] == 1
    assert payload["skipped"]["no_usage"] == 1
    assert payload["skipped"].get("fleet_mismatch", 0) == 0


def test_summary_positional_constructor_keeps_skipped_and_moves_slots():
    summary = Summary(2, False, None, {"unreadable": 2}, [])
    assert summary.scanned == 2
    assert summary.apply is False
    assert summary.archive is None
    assert summary.skipped == {"unreadable": 2}
    assert summary.moves == []
    assert summary.fleet is None
    payload = summary.to_dict()
    assert payload["scanned"] == 2
    assert payload["skipped"] == {"unreadable": 2}
    assert payload["moved"] == 0
    assert "fleet" not in payload
