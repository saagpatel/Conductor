"""E22: vendor CLI versions on the receipt.

`fleets.cli_version` shells out to a binary's own `--version`; these tests
fake that binary with a shell script on `PATH` rather than depending on any
real vendor CLI being installed.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from conductor import attest, fleets
from conductor import runner as runner_mod
from conductor.cli import main
from conductor.fleets import Spec
from conductor.runner import Result, dispatch

GOLDEN_DIR = Path(__file__).parent / "golden"


def _write_script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def _prepend_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")


@pytest.fixture(autouse=True)
def _clean_version_cache():
    fleets.clear_version_cache()
    yield
    fleets.clear_version_cache()


# --- fleets.cli_version --------------------------------------------------


def test_cli_version_reads_the_first_nonempty_stdout_line(tmp_path, monkeypatch):
    _write_script(tmp_path, "claude", 'echo "claude-cli 1.2.3"\necho "second line"')
    _prepend_path(monkeypatch, tmp_path)
    assert fleets.cli_version("claude") == "claude-cli 1.2.3"


def test_cli_version_falls_back_to_stderr_when_stdout_is_empty(tmp_path, monkeypatch):
    _write_script(tmp_path, "claude", 'echo "on stderr" 1>&2')
    _prepend_path(monkeypatch, tmp_path)
    assert fleets.cli_version("claude") == "on stderr"


def test_cli_version_is_none_on_a_nonzero_exit(tmp_path, monkeypatch):
    _write_script(tmp_path, "claude", 'echo "should not count"\nexit 1')
    _prepend_path(monkeypatch, tmp_path)
    assert fleets.cli_version("claude") is None


def test_cli_version_is_none_when_it_hangs_past_the_timeout(tmp_path, monkeypatch):
    _write_script(tmp_path, "claude", "sleep 2")
    _prepend_path(monkeypatch, tmp_path)
    assert fleets.cli_version("claude", timeout=0.2) is None


def test_cli_version_is_none_for_a_missing_binary(monkeypatch):
    # shutil.which is patched directly, not PATH itself: this machine has
    # real fleet CLIs installed, and clobbering PATH would also break the
    # git resolution dispatch relies on elsewhere in these tests.
    monkeypatch.setattr(fleets.shutil, "which", lambda binary: None)
    assert fleets.cli_version("claude") is None


def test_cli_version_is_none_for_an_unknown_fleet():
    assert fleets.cli_version("nope") is None


def test_cli_version_is_cached_per_process_by_binary_path(tmp_path, monkeypatch):
    counter = tmp_path / "calls.txt"
    _write_script(tmp_path, "claude", f'echo called >> {counter}\necho "v1"')
    _prepend_path(monkeypatch, tmp_path)
    assert fleets.cli_version("claude") == "v1"
    assert fleets.cli_version("claude") == "v1"
    assert counter.read_text().count("called\n") == 1


def test_clear_version_cache_forces_a_fresh_probe(tmp_path, monkeypatch):
    _write_script(tmp_path, "claude", 'echo "v1"')
    _prepend_path(monkeypatch, tmp_path)
    assert fleets.cli_version("claude") == "v1"
    _write_script(tmp_path, "claude", 'echo "v2"')
    fleets.clear_version_cache()
    assert fleets.cli_version("claude") == "v2"


# --- Result.from_dict ------------------------------------------------------


def _base_result(**overrides) -> Result:
    fields = dict(
        run_id="r1",
        fleet="claude",
        model="claude-sonnet-5",
        effort="standard",
        mode="read",
        cwd="/tmp/repo",
        timeout=600,
        exit_code=0,
        timed_out=False,
        duration_s=1.5,
        run_dir="/tmp/runs/r1",
        stdout_path="/tmp/runs/r1/stdout.log",
        stderr_path="/tmp/runs/r1/stderr.log",
        tail="ok",
        spawned=True,
    )
    fields.update(overrides)
    return Result(**fields)


def test_result_from_dict_defaults_a_missing_fleet_version():
    d = _base_result().to_dict()
    del d["fleet_version"]
    r = Result.from_dict(d)
    assert r.fleet_version is None


def test_result_from_dict_round_trips_a_fleet_version():
    r = _base_result(fleet_version="claude-cli 1.2.3")
    assert Result.from_dict(r.to_dict()) == r


# --- one dispatch, one receipt, one signed statement -----------------------


def test_dispatch_receipt_and_attestation_carry_the_fleet_version(
    repo, home, monkeypatch, fake_fleet, tmp_path
):
    _write_script(tmp_path, "cursor-agent", 'echo "cursor-agent 9.9.9"')
    _prepend_path(monkeypatch, tmp_path)
    fake_fleet(["sh", "-c", "echo looked around"])

    result = dispatch(Spec(fleet="cursor", prompt="look around", cwd=str(repo)), home=home)
    assert result.spawned is True
    assert result.fleet_version == "cursor-agent 9.9.9"

    result_data = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert result_data["fleet_version"] == "cursor-agent 9.9.9"

    envelope = json.loads(Path(result.attestation_path).read_text())
    key = attest.receipt_key(home)
    statement, reason = attest.verify(envelope, key)
    assert reason is None
    assert statement["fleet_version"] == "cursor-agent 9.9.9"


def test_dispatch_notes_an_unavailable_fleet_version(repo, home, monkeypatch, fake_fleet):
    # A version capture failure is simulated directly (not via PATH, which
    # this machine has real fleet CLIs on): it must land as a note, never
    # as a dispatch failure.
    monkeypatch.setattr(runner_mod, "cli_version", lambda fleet, **kw: None)
    fake_fleet(["sh", "-c", "echo looked around"])

    result = dispatch(Spec(fleet="cursor", prompt="look around", cwd=str(repo)), home=home)
    assert result.fleet_version is None
    assert "fleet version unavailable" in result.git_verdict["notes"]


# --- conductor fleets --------------------------------------------------


def test_cli_fleets_json_shows_the_version(monkeypatch, capsys):
    # Patched at the name `cli.py` looked up, not via PATH: this machine has
    # real fleet CLIs installed, and `conductor fleets` probes every one of
    # them, which a test must not depend on spawning for real.
    from conductor import cli as cli_mod

    monkeypatch.setattr(
        cli_mod, "cli_version", lambda name: "cursor-agent 9.9.9" if name == "cursor" else None
    )
    assert main(["fleets", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    cursor_row = next(r for r in rows if r["fleet"] == "cursor")
    assert cursor_row["version"] == "cursor-agent 9.9.9"


# --- golden.check version drift --------------------------------------------


def test_golden_check_reports_version_drift_and_stays_exit_0(tmp_path, monkeypatch, capsys):
    fixture_src = GOLDEN_DIR / "c5-build-cascade-capped"
    fixture = tmp_path / "c5-build-cascade-capped"
    shutil.copytree(fixture_src, fixture)
    manifest_path = fixture / "golden.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fleet_versions"] = {"claude": "claude-cli 4.0.0"}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    scripts_dir = tmp_path / "bin"
    scripts_dir.mkdir()
    _write_script(scripts_dir, "claude", 'echo "claude-cli 5.0.0"')
    _prepend_path(monkeypatch, scripts_dir)

    assert main(["golden", "check", str(fixture)]) == 0
    out = capsys.readouterr().out
    assert (
        f"{fixture.name}: claude recorded claude-cli 4.0.0, installed claude-cli 5.0.0" in out
    )


def test_golden_check_notes_unknown_recorded_version_on_shipped_fixtures(capsys):
    fixture = GOLDEN_DIR / "c5-build-cascade-capped"
    assert main(["golden", "check", str(fixture)]) == 0
    out = capsys.readouterr().out
    assert f"{fixture.name}: recorded version unknown" in out
