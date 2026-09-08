"""Six fail-closed gaps in the export bundle and its scrubber.

Every one of these was found by a cold read on 2026-09-08 and verified on
bytes before it was fixed. The first is the most consequential: the guard
fired on the scrubber's own output, so a mission whose transcript merely
mentioned an Authorization header could not be exported at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conductor import export
from conductor.golden import _pattern_hits, _redact_secrets, scrub_guard


def test_the_guard_does_not_fire_on_its_own_bearer_redaction():
    scrubbed = _redact_secrets("Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz012345")

    assert scrubbed == "Authorization: Bearer <redacted>"
    assert _pattern_hits(scrubbed, []) == [], "the scrubber's own output is not a leak"
    assert _redact_secrets(scrubbed) == scrubbed, "scrubbing stays idempotent"


def test_an_unredacted_bearer_token_is_still_a_leak():
    assert _pattern_hits("Authorization: Bearer abc123def456ghi789", []) == ["bearer token"]


@pytest.mark.parametrize(
    "secret",
    [
        "sk_live_51abcdefghijklmnopqrstuvwx",
        "sk_test_51abcdefghijklmnopqrstuvwx",
        "xoxb-1234567890-abcdefghijklmnop",
        "xoxp-1234567890-abcdefghijklmnop",
        "ASIAIOSFODNN7EXAMPLEXYZ",
        "npm_abcdefghijklmnopqrstuvwxyz01",
    ],
)
def test_vendor_token_shapes_are_redacted_by_value_not_by_key_name(secret: str):
    assert _redact_secrets(f"the value is {secret} here") == "the value is <redacted> here"


def test_the_guard_finds_every_encoding_of_the_receipt_key(tmp_path: Path):
    import base64

    key = bytes(range(32))
    forms = {
        "raw.bin": key,
        "lower.txt": key.hex().encode(),
        "upper.txt": key.hex().upper().encode(),
        "b64.txt": base64.b64encode(key),
        "urlsafe.txt": base64.urlsafe_b64encode(key).rstrip(b"="),
    }
    for name, content in forms.items():
        (tmp_path / name).write_bytes(content)
        assert export._find_key_material(tmp_path, key) is not None, f"{name} hid the key"
        (tmp_path / name).unlink()

    (tmp_path / "clean.txt").write_text("nothing to see")
    assert export._find_key_material(tmp_path, key) is None


def test_a_symlink_in_a_mission_directory_is_not_inlined_into_the_bundle(tmp_path: Path):
    root = tmp_path / "mission"
    (root / "lanes").mkdir(parents=True)
    outside = tmp_path / "outside-the-mission.txt"
    outside.write_text("a private file the bundle must never carry\n")
    (root / "lanes" / "link.json").symlink_to(outside)
    (root / "lanes" / "real.json").write_text("{}")

    kept = [
        path
        for path in (root / "lanes").rglob("*")
        if path.is_file() and not export._is_symlinked(path, root)
    ]

    assert [path.name for path in kept] == ["real.json"]


def test_check_reports_a_malformed_files_entry_instead_of_raising(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "_type": export.FORMAT,
                "mission_id": "m1",
                "files": {"report.md": "deadbeef"},
                "chain": {"state": "missing", "verified_at_export": False, "links": []},
            }
        )
    )

    result = export.check(bundle)

    assert result.ok is False
    assert any("manifest entry is not an object" in problem for problem in result.problems)


def test_check_refuses_a_manifest_that_claims_a_chain_the_bundle_does_not_carry(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "_type": export.FORMAT,
                "mission_id": "m1",
                "files": {},
                "chain": {
                    "state": "verified",
                    "verified_at_export": True,
                    "links": [{"index": 0, "lane": "build", "run_id": "r1", "verified": True}],
                },
            }
        )
    )

    result = export.check(bundle)

    assert result.ok is False
    assert any("records 1 chain link(s)" in problem for problem in result.problems)
    assert any("carries no chain links" in problem for problem in result.problems)


def test_scrub_guard_is_told_about_every_repository_a_mission_named(tmp_path: Path):
    """The export-path regression from the same review, at the guard's level:
    a path the guard is not given is a path it cannot find."""
    work = tmp_path / "work"
    work.mkdir()
    other = "/Volumes/elsewhere/another-project"
    (work / "answer.txt").write_text(f"I read {other}/src/main.py\n")

    assert scrub_guard(work) == [], "the guard has no way to know about an unnamed path"
    assert scrub_guard(work, extra=[(other, "mission cwd 2")]) != []


def test_find_key_material_refuses_an_unreadable_file(tmp_path: Path):
    """A file the scan could not read is a file it could not clear."""
    key = bytes(range(32))
    locked = tmp_path / "locked.bin"
    locked.write_bytes(b"x")
    locked.chmod(0)
    try:
        with pytest.raises(export.ExportError, match="cannot read locked.bin"):
            export._find_key_material(tmp_path, key)
    finally:
        locked.chmod(0o644)


def test_scrub_guard_checks_the_home_the_caller_passed(tmp_path: Path, monkeypatch):
    """The guard used to always needle `conductor_home()` from the
    environment. `export(home=other)` placeholders that other tree; the
    leak backstop has to check the same one. Callers that pass nothing
    still get the environment default."""
    env_home = tmp_path / "env-conductor"
    env_home.mkdir()
    monkeypatch.setenv("CONDUCTOR_HOME", str(env_home))
    used_home = tmp_path / "used-conductor"
    used_home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / "note.txt").write_text(f"see {used_home} for receipts\n")

    assert scrub_guard(work) == [], "the environment home is not in the bundle"
    findings = scrub_guard(work, home=used_home)
    assert findings != []
    assert any("conductor home" in finding for finding in findings)
