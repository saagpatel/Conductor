"""What the CLI does with ordinary bad input.

A cold read of `cli.py` (2026-09-08) found the same shape in nine places: a
value argparse accepted without a bound, or a file read outside the try that
was meant to turn a bad path into a clean refusal. Each one reached a
traceback or a silently wrong answer where an error message was intended.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor.cli import main

# --- numeric bounds ---------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        # `entries[:-1]` is a valid end-relative slice: the listing became
        # "every entry except the oldest" under a heading that says recent N.
        ["missions", "--limit", "-1"],
        ["missions", "--limit", "0"],
        ["runs", "--limit", "-1"],
        # `timedelta(hours=nan)` raises ValueError, `inf` OverflowError, and
        # gc's own `< 0` check passes both because NaN fails every comparison.
        ["gc", "--older-than", "nan"],
        ["gc", "--older-than", "inf"],
        ["gc", "--older-than", "-1"],
        # A negative timeout reaches subprocess's wait.
        ["dispatch", "--fleet", "claude", "--timeout", "-5", "hello"],
    ],
)
def test_a_number_outside_its_bound_is_refused_at_the_parser(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_a_usable_limit_still_parses(home: Path, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))

    assert main(["missions", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == []


# --- mission ids are one path segment ---------------------------------------


@pytest.mark.parametrize("command", ["attest", "salvage", "land", "export"])
def test_a_path_shaped_mission_id_is_refused(command, capsys):
    """Every one of these joins the id straight onto `<home>/missions/`, and
    nothing refused `../other`."""
    argv = [command, "../elsewhere"]
    if command in {"salvage", "land"}:
        argv += ["--lane", "build"]

    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 2
    assert "is not a mission id" in capsys.readouterr().err


def test_golden_record_refuses_a_path_shaped_mission_id(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["golden", "record", "../elsewhere", "--out", "/tmp/x"])

    assert excinfo.value.code == 2
    assert "is not a mission id" in capsys.readouterr().err


# --- the prompt file --------------------------------------------------------


def test_a_missing_prompt_file_is_an_error_not_a_traceback(capsys, tmp_path: Path):
    """`--verdict-file` and `--agent-file` beside it already turn an
    unreadable path into a refusal; `--prompt-file` read it bare."""
    code = main(
        ["dispatch", "--fleet", "claude", "--prompt-file", str(tmp_path / "nope.md")]
    )

    assert code == 2
    assert "prompt file unreadable" in capsys.readouterr().err


def test_a_prompt_argument_and_a_prompt_file_together_are_refused(capsys, tmp_path: Path):
    """The file silently replaced the argument, so the dispatch could run a
    different prompt than the one on the command line."""
    prompt_file = tmp_path / "p.md"
    prompt_file.write_text("from the file")

    code = main(["dispatch", "--fleet", "claude", "on the command line",
                 "--prompt-file", str(prompt_file)])

    assert code == 2
    assert "not both" in capsys.readouterr().err


# --- a receipt that cannot be read ------------------------------------------


def _truncated(directory: Path) -> None:
    directory.mkdir(parents=True)
    (directory / "result.json").write_text('{"run_id": "x", ')


def test_a_truncated_mission_receipt_is_a_row_not_a_traceback(
    home: Path, monkeypatch, capsys
):
    """A crashed run leaves a half-written receipt. `spend` and `report`
    already skip one; `missions` and `runs` indexed it straight."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    _truncated(home / "missions" / "20260101T000000Z-broken")

    assert main(["missions"]) == 0

    rows = json.loads(capsys.readouterr().out)
    assert rows == [{"mission_id": "20260101T000000Z-broken", "status": "unreadable"}]


def test_a_truncated_run_receipt_is_a_row_not_a_traceback(home: Path, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    _truncated(home / "runs" / "20260101T000000Z-claude-x")

    assert main(["runs"]) == 0

    rows = json.loads(capsys.readouterr().out)
    assert rows == [{"run_id": "20260101T000000Z-claude-x", "status": "unreadable"}]


# --- shape a --out directory and bad paths -----------------------------------


def test_shape_a_creates_parent_directory_for_out(
    repo: Path, home: Path, monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = tmp_path / "spec.md"
    spec.write_text("# widget\n\n1. one\n")
    out = tmp_path / "nested" / "dir" / "mission.json"
    assert not out.parent.exists()

    code = main([
        "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
        "--items", "1", "--modules", "1", "--out", str(out), "--inline",
    ])
    assert code == 0
    assert out.is_file()
    assert (out.parent / "dispositions.schema.json").is_file()
    assert (out.parent / "evidence.schema.json").is_file()


def test_shape_a_bad_out_path_is_clean_error_not_traceback(
    repo: Path, home: Path, monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = tmp_path / "spec.md"
    spec.write_text("# widget\n\n1. one\n")
    blocker = tmp_path / "file_blocker"
    blocker.write_text("not a directory")
    bad_out = blocker / "child" / "mission.json"

    code = main([
        "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
        "--items", "1", "--modules", "1", "--out", str(bad_out),
    ])
    assert code == 3
    err = capsys.readouterr().err
    parsed = json.loads(err)
    assert "invalid" in parsed


def test_shape_a_out_is_directory_is_clean_error_not_traceback(
    repo: Path, home: Path, monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    spec = tmp_path / "spec.md"
    spec.write_text("# widget\n\n1. one\n")
    out_dir = tmp_path / "already_a_dir"
    out_dir.mkdir()

    code = main([
        "shape", "a", "--spec", str(spec), "--repo", str(repo), "--test", "true",
        "--items", "1", "--modules", "1", "--out", str(out_dir), "--force",
    ])
    assert code == 3
    err = capsys.readouterr().err
    parsed = json.loads(err)
    assert "invalid" in parsed


# --- lane names are one path segment ----------------------------------------


@pytest.mark.parametrize("command", ["salvage", "land"])
def test_a_path_shaped_lane_name_is_refused(command, capsys):
    """`cmd_land` interpolated `--lane` into a receipt path before `land()`
    checked `is_lane_name`; salvage already checked inside `_gather`."""
    with pytest.raises(SystemExit) as excinfo:
        main([command, "a-mission", "--lane", "../elsewhere"])

    assert excinfo.value.code == 2
    assert "is not a lane name" in capsys.readouterr().err


# --- money, seconds, counts, and resume ids --------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["dispatch", "--fleet", "claude", "--cap-usd", "nan", "hello"],
        ["dispatch", "--fleet", "claude", "--cap-usd", "inf", "hello"],
        ["dispatch", "--fleet", "claude", "--cap-usd", "-1", "hello"],
        ["dispatch", "--fleet", "claude", "--cap-grace-usd", "nan", "hello"],
        ["dispatch", "--fleet", "claude", "--stall-timeout", "-1", "hello"],
        ["dispatch", "--fleet", "claude", "--loop-limit", "-1", "hello"],
        ["dispatch", "--fleet", "claude", "--max-tool-calls", "-1", "hello"],
        ["dispatch", "--fleet", "claude", "--tool-idle-timeout", "-1", "hello"],
        ["dispatch", "--fleet", "claude", "--ports", "-1", "hello"],
        ["salvage", "m", "--lane", "build", "--items", "0"],
        ["salvage", "m", "--lane", "build", "--modules", "-1"],
        ["golden", "record", "m", "--out", "/tmp/x", "--max-bytes", "-1"],
        ["mission", "--resume", "../elsewhere"],
    ],
)
def test_a_money_wait_or_count_outside_its_bound_is_refused_at_the_parser(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_a_zero_that_disables_a_breaker_still_parses():
    """stall/loop/tool-idle/ports treat 0 as disable (or claim none)."""
    from conductor.cli import build_parser

    args = build_parser().parse_args(
        [
            "dispatch",
            "hello",
            "--fleet",
            "claude",
            "--stall-timeout",
            "0",
            "--loop-limit",
            "0",
            "--max-tool-calls",
            "0",
            "--tool-idle-timeout",
            "0",
            "--ports",
            "0",
        ]
    )
    assert args.stall_timeout == 0
    assert args.loop_limit == 0
    assert args.max_tool_calls == 0
    assert args.tool_idle_timeout == 0
    assert args.ports == 0


# --- path arguments -----------------------------------------------------------


def test_a_missing_cwd_is_an_error_not_a_traceback(capsys, tmp_path: Path):
    missing = tmp_path / "no-such-repo"
    code = main(["dispatch", "--fleet", "claude", "--cwd", str(missing), "hello"])

    assert code == 2
    assert "not a directory" in capsys.readouterr().err


def test_verify_missing_cwd_is_an_error_not_a_traceback(capsys, tmp_path: Path):
    code = main(["verify", "--cwd", str(tmp_path / "nope")])

    assert code == 2
    assert "not a directory" in capsys.readouterr().err


def test_a_missing_verdict_file_is_an_error_not_a_traceback(capsys, tmp_path: Path):
    code = main(
        [
            "dispatch",
            "--fleet",
            "claude",
            "--verdict-file",
            str(tmp_path / "nope.json"),
            "hello",
        ]
    )

    assert code == 3
    parsed = json.loads(capsys.readouterr().err)
    assert "verdict file unreadable" in parsed["refused"]


def test_a_binary_verdict_file_is_an_error_not_a_traceback(capsys, tmp_path: Path):
    path = tmp_path / "verdict.bin"
    path.write_bytes(b"\xff\xfe not utf-8")
    code = main(
        ["dispatch", "--fleet", "claude", "--verdict-file", str(path), "hello"]
    )

    assert code == 3
    parsed = json.loads(capsys.readouterr().err)
    assert "verdict file unreadable" in parsed["refused"]


def test_a_binary_agent_file_is_an_error_not_a_traceback(capsys, tmp_path: Path):
    path = tmp_path / "agent.bin"
    path.write_bytes(b"\xff\xfe not utf-8")
    code = main(
        ["dispatch", "--fleet", "claude", "--agent-file", str(path), "hello"]
    )

    assert code == 3
    parsed = json.loads(capsys.readouterr().err)
    assert "agent file unreadable" in parsed["refused"]
