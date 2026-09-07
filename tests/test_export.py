"""E13: the receipt export bundle.

`export.export` copies a finished mission and every run it dispatched into a
scrubbed, manifest-checked bundle; `export.check` verifies that bundle
against its own manifest with nothing but the bundle itself. The tests here
follow the same on-bytes discipline as `test_attest.py`: build a real
mission through the fake fleet, then check the bundle and the manifest
against the mission's own files on disk, not against a fleet's prose.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from conductor import attest, export
from conductor import runner as runner_mod
from conductor.cli import main
from conductor.golden import scrub_guard
from conductor.mission import mission_from_dict, run_mission


def _two_lane_mission(repo, home, monkeypatch, tmp_path):
    """Mirrors `test_attest.py`'s fixture: a write lane that commits a file,
    then a read lane based on it -- two settled lanes, two chained receipt
    links, two run directories with a full set of artifacts."""
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: (
            ["sh", "-c", "printf 'v1\\n' > built.txt"]
            if spec.prompt.split()[0] == "BUILD"
            else ["sh", "-c", "echo reviewed"]
        ),
    )
    raw = {
        "cwd": str(repo),
        "lanes": [
            {
                "name": "build",
                "fleet": "claude",
                "mode": "write",
                "prompt": "BUILD it",
                "commit": "build",
            },
            {"name": "review", "fleet": "claude", "base": "build", "prompt": "REVIEW it"},
        ],
    }
    return run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)


# --- refusals --------------------------------------------------------------


def test_export_refuses_a_mission_id_that_is_not_a_directory_name(home, tmp_path):
    with pytest.raises(export.ExportError, match="directory name"):
        export.export(home, "../elsewhere", tmp_path / "out")


def test_export_refuses_a_mission_that_does_not_exist(home, tmp_path):
    with pytest.raises(export.ExportError, match="does not exist"):
        export.export(home, "no-such-mission", tmp_path / "out")


def test_export_refuses_an_out_dir_that_already_exists(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    out = tmp_path / "bundle"
    out.mkdir()
    with pytest.raises(export.ExportError, match="already exists"):
        export.export(home, result.mission_id, out)


def test_export_refuses_a_missing_receipt_key(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    (home / "keys" / "receipt.key").unlink()
    with pytest.raises(export.ExportError, match="key is missing"):
        export.export(home, result.mission_id, tmp_path / "out")


def test_export_does_not_claim_a_corrupt_chain_verified(repo, home, monkeypatch, tmp_path):
    """A chain.json `cmd_attest` would refuse as invalid must not be reported
    as `chain.verified_at_export: true` just because it produced zero rows to
    check -- that is the same vacuous-truth gap `cmd_attest` never has, since
    it refuses outright on the same input."""
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    chain_path = home / "missions" / result.mission_id / "receipts" / "chain.json"
    chain_path.write_text("not json")
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert export_result.chain_verified_at_export is False
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["chain"]["verified_at_export"] is False


# --- D8: the chain's state, not a bare boolean -----------------------------


def test_export_reports_a_missing_chain_as_missing_not_verified(
    repo, home, monkeypatch, tmp_path
):
    """D8: `chain_invalid` started False and only ever flipped for a chain
    file that existed and was bad, so a mission with no chain.json at all
    exported as `verified_at_export: true` with nothing verified."""
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    (home / "missions" / result.mission_id / "receipts" / "chain.json").unlink()
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert export_result.chain_state_at_export == "missing"
    assert export_result.chain_verified_at_export is False
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["chain"]["state"] == "missing"
    assert manifest["chain"]["verified_at_export"] is False
    assert manifest["chain"]["problems"] == ["chain.json is missing"]


def test_export_reports_a_malformed_chain_as_malformed(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    chain_path = home / "missions" / result.mission_id / "receipts" / "chain.json"
    chain_path.write_text("not json")
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert export_result.chain_state_at_export == "malformed"
    assert export_result.chain_verified_at_export is False
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["chain"]["state"] == "malformed"


def test_export_reports_an_empty_chain_as_empty(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    chain_path = home / "missions" / result.mission_id / "receipts" / "chain.json"
    chain = json.loads(chain_path.read_text())
    chain["links"] = []
    chain_path.write_text(json.dumps(chain))
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert export_result.chain_state_at_export == "empty"
    assert export_result.chain_verified_at_export is False
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["chain"]["state"] == "empty"
    assert manifest["chain"]["links"] == []


def test_export_reports_an_intact_chain_as_verified(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert export_result.chain_state_at_export == "verified"
    assert export_result.chain_verified_at_export is True
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["chain"]["state"] == "verified"
    assert manifest["chain"]["problems"] == []
    assert len(manifest["chain"]["links"]) == 2


def test_export_reports_a_truncated_chain_as_partial(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    chain_path = home / "missions" / result.mission_id / "receipts" / "chain.json"
    chain = json.loads(chain_path.read_text())
    chain["links"] = chain["links"][:1]
    chain_path.write_text(json.dumps(chain))
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert export_result.chain_state_at_export == "partial"
    assert export_result.chain_verified_at_export is False


# --- what a bundle holds -----------------------------------------------


def test_bundle_holds_mission_and_run_files_without_logs_by_default(
    repo, home, monkeypatch, tmp_path
):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    build_run_id = result.lanes[0]["attempts"][-1]["run_id"]
    out = tmp_path / "bundle"

    export_result = export.export(home, result.mission_id, out)

    assert (out / "mission.json").is_file()
    assert (out / "result.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "receipts" / "chain.json").is_file()
    assert (out / "lanes" / "build.json").is_file()
    assert (out / "lanes" / "review.json").is_file()

    run_dir = out / "runs" / build_run_id
    assert (run_dir / "result.json").is_file()
    assert (run_dir / "attestation.json").is_file()
    assert (run_dir / "prompt.txt").is_file()
    assert (run_dir / "argv.json").is_file()
    assert not (run_dir / "stdout.log").exists()
    assert not (run_dir / "stderr.log").exists()
    assert not (out / "runs" / build_run_id / "liveness.json").exists()

    assert export_result.chain_verified_at_export is True
    verified, total = export_result.attestations_verified_at_export
    assert total >= 1
    assert verified == total
    assert export_result.leaks == []


def test_bundle_holds_logs_when_asked(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    build_run_id = result.lanes[0]["attempts"][-1]["run_id"]
    out = tmp_path / "bundle"

    export.export(home, result.mission_id, out, logs=True)

    run_dir = out / "runs" / build_run_id
    assert (run_dir / "stdout.log").is_file()


# --- scrubbing ---------------------------------------------------------


def test_no_real_path_survives_anywhere_in_the_bundle_including_dsse_payloads(
    repo, home, monkeypatch, tmp_path
):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    build_run_id = result.lanes[0]["attempts"][-1]["run_id"]
    out = tmp_path / "bundle"

    export.export(home, result.mission_id, out)

    assert scrub_guard(out) == []

    envelope = json.loads((out / "runs" / build_run_id / "attestation.json").read_text())
    statement = json.loads(base64.b64decode(envelope["payload"]))
    blob = json.dumps(statement)
    assert str(home) not in blob
    assert str(repo) not in blob
    assert str(Path.home()) not in blob


# --- the manifest --------------------------------------------------------


def test_manifest_lists_every_file_with_both_digests(repo, home, monkeypatch, tmp_path):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    out = tmp_path / "bundle"

    export.export(home, result.mission_id, out)
    manifest = json.loads((out / "manifest.json").read_text())

    assert manifest["_type"] == "conductor/export/v1"
    assert manifest["mission_id"] == result.mission_id
    assert manifest["logs"] is False
    assert manifest["chain"]["verified_at_export"] is True
    assert len(manifest["chain"]["links"]) == 2
    assert manifest["not_verifiable_here"] == ["signatures"]
    assert "manifest.json" not in manifest["files"]

    for relpath, meta in manifest["files"].items():
        path = out / relpath
        assert path.is_file(), relpath
        assert meta["bytes"] == path.stat().st_size
        import hashlib

        assert meta["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert isinstance(meta["sha256_original"], str)

    for run_id, entry in manifest["attestations"].items():
        assert entry["verified_at_export"] is True, (run_id, entry["problems"])


# --- conductor export --check -----------------------------------------


def _export_bundle(repo, home, monkeypatch, tmp_path, name="bundle"):
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    out = tmp_path / name
    export.export(home, result.mission_id, out)
    return out


def test_check_passes_on_a_fresh_bundle(repo, home, monkeypatch, tmp_path):
    out = _export_bundle(repo, home, monkeypatch, tmp_path)
    result = export.check(out)
    assert result.ok is True
    assert result.problems == []
    assert result.links_checked == 2
    assert result.files_checked > 0


def test_check_fails_naming_an_edited_file(repo, home, monkeypatch, tmp_path):
    out = _export_bundle(repo, home, monkeypatch, tmp_path)
    report = out / "report.md"
    report.write_text(report.read_text() + "\ntampered\n")

    result = export.check(out)
    assert result.ok is False
    assert any("report.md" in p for p in result.problems)


def test_check_fails_when_a_file_is_added(repo, home, monkeypatch, tmp_path):
    out = _export_bundle(repo, home, monkeypatch, tmp_path)
    (out / "surprise.txt").write_text("not in the manifest\n")

    result = export.check(out)
    assert result.ok is False
    assert any("surprise.txt" in p and "not listed" in p for p in result.problems)


def test_check_fails_when_a_links_previous_is_broken(repo, home, monkeypatch, tmp_path):
    out = _export_bundle(repo, home, monkeypatch, tmp_path)
    chain = json.loads((out / "receipts" / "chain.json").read_text())
    second_link_path = out / chain["links"][1]["path"]
    envelope = json.loads(second_link_path.read_text())
    statement = json.loads(base64.b64decode(envelope["payload"]))
    statement["previous"] = "0" * 64
    envelope["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()
    second_link_path.write_text(json.dumps(envelope))

    result = export.check(out)
    assert result.ok is False
    assert any("previous does not match" in p for p in result.problems)


def test_check_never_reads_a_file_outside_the_bundle_via_a_manifest_path(
    repo, home, monkeypatch, tmp_path
):
    """`check` promises to read nothing but the bundle. A manifest entry
    whose path escapes `bundle_dir` with `..` must be reported as a problem,
    never followed and silently "verified" against whatever it points at
    outside the bundle -- even when that outside file happens to match the
    digest and size the manifest records for it."""
    import hashlib

    out = _export_bundle(repo, home, monkeypatch, tmp_path)
    secret = tmp_path / "outside-the-bundle-secret.txt"
    secret.write_bytes(b"not part of any bundle\n")

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    escape_key = "../outside-the-bundle-secret.txt"
    manifest["files"][escape_key] = {
        "sha256": hashlib.sha256(secret.read_bytes()).hexdigest(),
        "sha256_original": hashlib.sha256(secret.read_bytes()).hexdigest(),
        "bytes": secret.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    result = export.check(out)

    assert result.ok is False
    assert any(escape_key in p for p in result.problems)


def test_check_fails_when_an_attestations_original_digest_disagrees(
    repo, home, monkeypatch, tmp_path
):
    out = _export_bundle(repo, home, monkeypatch, tmp_path)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    run_id = next(iter(manifest["attestations"]))
    manifest["files"][f"runs/{run_id}/attestation.json"]["sha256_original"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2))

    result = export.check(out)
    assert result.ok is False
    assert any("original sha256 disagrees" in p for p in result.problems)


# --- conductor attest is unchanged by the refactor --------------------


def test_cli_attest_still_verifies_an_untouched_mission(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    assert main(["attest", result.mission_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is True
    assert len(out["links"]) == 2
    assert out["key_id"] == attest.key_id(attest.receipt_key(home))


# --- CLI -----------------------------------------------------------------


def test_cli_export_writes_a_bundle_and_prints_json(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    out = tmp_path / "cli-bundle"

    assert main(["export", result.mission_id, "--out", str(out), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["bundle_dir"] == str(out)
    assert printed["chain_verified_at_export"] is True
    assert (out / "manifest.json").is_file()


def test_cli_export_check_exits_0_on_a_clean_bundle_and_1_on_a_tampered_one(
    repo, home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    out = _export_bundle(repo, home, monkeypatch, tmp_path)

    assert main(["export", "--check", str(out), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True

    (out / "report.md").write_text("tampered\n")
    assert main(["export", "--check", str(out), "--json"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is False


def test_cli_export_exits_3_on_an_unknown_mission(home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["export", "no-such-mission", "--out", str(tmp_path / "out")]) == 3
    assert "does not exist" in capsys.readouterr().err


def test_cli_export_exits_1_on_a_leak(repo, home, monkeypatch, tmp_path, capsys):
    """A literal real path in a text file is caught by the placeholder
    replace before it ever reaches disk, so it is not a useful leak to
    simulate. The gap `scrub_guard` actually exists for is a real path
    hiding inside a base64 run in a file the scrubber does not decode (only
    a recognized DSSE envelope's payload gets decoded and scrubbed) --
    exactly the shape C7's `scrub_guard` was built to catch."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    build_run_id = result.lanes[0]["attempts"][-1]["run_id"]
    leaked = base64.b64encode(f"see {home} for details, hidden in base64".encode()).decode()
    (home / "runs" / build_run_id / "answer.txt").write_text(leaked + "\n")

    out = tmp_path / "leaky-bundle"
    rc = main(["export", result.mission_id, "--out", str(out)])
    assert rc == 1
    assert not out.exists()
