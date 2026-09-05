"""A5: signed lane receipts.

`attest.py` is a DSSE envelope over HMAC-SHA256, standard library only. A
receipt cannot be edited after the fact by anything that does not hold the
conductor-owned key; the tests here check that on bytes, the same way the
rest of conductor checks a dispatch on bytes rather than a fleet's prose.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import threading
from pathlib import Path

from conductor import attest
from conductor import runner as runner_mod
from conductor.cli import main
from conductor.fleets import Spec
from conductor.mission import Mission, mission_from_dict, run_mission
from conductor.runner import dispatch

# --- attest.py primitives ----------------------------------------------


def test_pae_matches_the_dsse_specs_own_example():
    """https://github.com/secure-systems-lab/dsse: PAE("http://example.com/
    HelloWorld", "hello world") == "DSSEv1 30 http://example.com/HelloWorld
    11 hello world"."""
    assert attest.pae(
        "http://example.com/HelloWorld", b"hello world"
    ) == b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"


def test_sign_then_verify_round_trips():
    key = os.urandom(32)
    statement = {"b": 2, "a": 1, "nested": {"x": [1, 2, 3]}}
    envelope = attest.sign(statement, key)
    assert envelope["payloadType"] == attest.PAYLOAD_TYPE
    decoded, reason = attest.verify(envelope, key)
    assert reason is None
    assert decoded == statement


def test_verify_rejects_a_malformed_envelope():
    key = os.urandom(32)
    decoded, reason = attest.verify({"payloadType": "x"}, key)
    assert decoded is None and reason == "malformed envelope"

    decoded, reason = attest.verify(
        {
            "payloadType": attest.PAYLOAD_TYPE,
            "payload": "not valid base64!!",
            "signatures": [{"keyid": "a", "sig": "b"}],
        },
        key,
    )
    assert decoded is None and reason == "malformed envelope"


def test_verify_rejects_an_unexpected_payload_type():
    key = os.urandom(32)
    envelope = attest.sign({"x": 1}, key)
    envelope["payloadType"] = "application/vnd.something-else+json"
    decoded, reason = attest.verify(envelope, key)
    assert decoded is None and reason == "unexpected payload type"


def test_verify_rejects_a_keyid_mismatch():
    key_a, key_b = os.urandom(32), os.urandom(32)
    envelope = attest.sign({"x": 1}, key_a)
    decoded, reason = attest.verify(envelope, key_b)
    assert decoded is None
    assert reason == f"keyid mismatch: signed by {attest.key_id(key_a)}"


def test_verify_rejects_a_tampered_signature():
    key = os.urandom(32)
    envelope = attest.sign({"x": 1}, key)
    tampered_payload = json.dumps({"x": 2}, sort_keys=True, separators=(",", ":")).encode()
    envelope["payload"] = base64.b64encode(tampered_payload).decode("ascii")
    decoded, reason = attest.verify(envelope, key)
    assert decoded is None and reason == "signature does not verify"


def test_receipt_key_is_created_once_at_mode_600_and_stable(home):
    key1 = attest.receipt_key(home)
    key_path = home / "keys" / "receipt.key"
    assert key_path.is_file() and len(key1) == 32
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((home / "keys").stat().st_mode) == 0o700
    key2 = attest.receipt_key(home)
    assert key1 == key2 == key_path.read_bytes()
    assert attest.read_receipt_key(home) == key1


def test_read_receipt_key_never_creates_one(home):
    assert attest.read_receipt_key(home) is None
    assert not (home / "keys").exists()


def test_two_concurrent_first_uses_agree_on_one_key(home):
    results: list[bytes] = []
    barrier = threading.Barrier(4)

    def go() -> None:
        barrier.wait()
        results.append(attest.receipt_key(home))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4
    assert len(set(results)) == 1


def test_file_sha256_is_none_for_a_missing_file(tmp_path):
    assert attest.file_sha256(tmp_path / "nope.txt") is None
    path = tmp_path / "x.txt"
    path.write_bytes(b"hello")
    assert attest.file_sha256(path) == hashlib.sha256(b"hello").hexdigest()


# --- one dispatch, one signed envelope ----------------------------------


def spec_for(repo: Path, **kw) -> Spec:
    base = dict(fleet="claude", prompt="test attest", cwd=str(repo))
    base.update(kw)
    return Spec(**base)


def test_a_spawned_dispatch_writes_a_verifiable_attestation(repo, home, fake_fleet):
    fake_fleet(
        [
            "sh",
            "-c",
            "echo work > w.txt && git add -A && git commit -qm 'fleet work'",
        ]
    )
    result = dispatch(
        spec_for(repo, mode="write"), home=home, isolate=True, test_command="true"
    )
    assert result.ok is True
    assert result.attestation_path is not None
    envelope = json.loads(Path(result.attestation_path).read_text())
    key = attest.receipt_key(home)
    statement, reason = attest.verify(envelope, key)
    assert reason is None

    result_data = json.loads((Path(result.run_dir) / "result.json").read_text())
    assert statement["run_id"] == result.run_id
    assert statement["fleet"] == "claude"
    assert statement["mode"] == "write"
    assert statement["stage"] is None
    assert statement["ok"] == result_data["ok"] is True
    assert statement["error"] is None
    assert statement["base_commit"] == result_data["isolation"]["base_sha"]
    assert statement["tip_commit"] == result_data["commit"]["sha"]
    assert statement["gate"] == {
        "command": "true",
        "counted": "own",
        "exit_code": 0,
        "passed": True,
    }
    assert statement["test_surface"]["touched"] is False
    assert statement["test_surface"]["digest_before"] == statement["test_surface"]["digest_after"]
    diff_bytes = Path(result.diff_path).read_bytes()
    assert statement["source_diff_sha256"] == hashlib.sha256(diff_bytes).hexdigest()

    other_key = os.urandom(32)
    _, bad_reason = attest.verify(envelope, other_key)
    assert bad_reason is not None


def test_dry_run_writes_no_attestation(repo, home):
    result = dispatch(spec_for(repo), dry_run=True, home=home)
    assert result.attestation_path is None
    assert not (Path(result.run_dir) / "attestation.json").exists()


def test_a_refused_dispatch_writes_no_attestation(repo, home, fake_fleet):
    (repo / "seed.txt").write_text("operator edit\n")
    fake_fleet(["sh", "-c", "echo spawned > spawned.txt"])
    result = dispatch(
        spec_for(repo, mode="write"), home=home, commit_message="feat: unsafe sweep"
    )
    assert result.spawned is False
    assert result.attestation_path is None
    assert not (Path(result.run_dir) / "attestation.json").exists()


# --- the mission-wide hash chain -----------------------------------------


def test_pipeline_mission_chains_one_link_per_lane_including_a_skipped_lane(
    repo, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        runner_mod,
        "build_argv",
        lambda spec: ["sh", "-c", "exit 1"]
        if spec.prompt.split()[0] == "A"
        else ["sh", "-c", "echo x"],
    )
    raw = {
        "cwd": str(repo),
        "concurrency": 1,
        "lanes": [
            {"name": "c", "fleet": "cursor", "prompt": "C", "needs": ["b"]},
            {"name": "b", "fleet": "codex", "prompt": "B", "needs": ["a"]},
            {"name": "a", "fleet": "claude", "prompt": "A"},
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    mission_dir = Path(result.mission_dir)
    chain = json.loads((mission_dir / "receipts" / "chain.json").read_text())
    assert [link["lane"] for link in chain["links"]] == ["a", "b", "c"]
    assert result.chain == {
        "path": str(mission_dir / "receipts" / "chain.json"),
        "links": 3,
        "head": chain["links"][-1]["sha256"],
    }

    key = attest.receipt_key(home)
    previous: str | None = None
    for link in chain["links"]:
        envelope = json.loads(Path(link["path"]).read_text())
        statement, reason = attest.verify(envelope, key)
        assert reason is None
        assert statement["previous"] == previous
        previous = link["sha256"]
        assert previous == attest.file_sha256(link["path"])

    b_envelope = json.loads(Path(chain["links"][1]["path"]).read_text())
    b_statement, _ = attest.verify(b_envelope, key)
    assert b_statement["lane"] == "b"
    assert b_statement["run_id"] is None
    assert b_statement["skipped"] == "needs a, which was not ok"
    assert b_statement["ok"] is False

    report = Path(result.report_path).read_text()
    assert f"Receipt chain: 3 links, head {chain['links'][-1]['sha256'][:12]}" in report


def test_resume_reruns_one_lane_and_appends_a_link_whose_previous_is_on_disk(
    repo, home, monkeypatch, tmp_path
):
    calls = {"BUILD": 0, "REVIEW": 0, "FIX": 0}

    def fake_build(spec: Spec) -> list[str]:
        key = spec.prompt.split()[0]
        calls[key] += 1
        if key == "BUILD":
            return ["sh", "-c", "printf 'v1\\n' > built.txt"]
        if key == "REVIEW":
            return ["sh", "-c", "echo reviewed"]
        if calls[key] == 1:
            return ["sh", "-c", "exit 7"]
        return ["sh", "-c", "printf 'v2\\n' >> built.txt"]

    monkeypatch.setattr(runner_mod, "build_argv", fake_build)
    raw = {
        "cwd": str(repo),
        "concurrency": 1,
        "lanes": [
            {
                "name": "build",
                "fleet": "claude",
                "mode": "write",
                "prompt": "BUILD it",
                "commit": "build",
            },
            {"name": "review", "fleet": "claude", "base": "build", "prompt": "REVIEW it"},
            {
                "name": "fix",
                "fleet": "claude",
                "mode": "write",
                "base": "build",
                "needs": ["review"],
                "prompt": "FIX it",
                "commit": "fix",
            },
        ],
    }
    mission = mission_from_dict(raw, base_dir=tmp_path)
    first = run_mission(mission, home=home)
    assert first.ok is False
    mission_dir = Path(first.mission_dir)
    first_chain = json.loads((mission_dir / "receipts" / "chain.json").read_text())
    assert [link["lane"] for link in first_chain["links"]] == ["build", "review", "fix"]
    last_link_sha_before = first_chain["links"][-1]["sha256"]

    snapshot = json.loads((mission_dir / "mission.json").read_text())
    resumed = run_mission(Mission.from_snapshot(snapshot), home=home, resume_dir=mission_dir)
    assert resumed.ok is True

    resumed_chain = json.loads((mission_dir / "receipts" / "chain.json").read_text())
    assert [link["lane"] for link in resumed_chain["links"]] == [
        "build",
        "review",
        "fix",
        "fix",
    ]
    new_link_envelope = json.loads(Path(resumed_chain["links"][-1]["path"]).read_text())
    key = attest.receipt_key(home)
    statement, reason = attest.verify(new_link_envelope, key)
    assert reason is None
    assert statement["previous"] == last_link_sha_before
    assert resumed.chain == {
        "path": str(mission_dir / "receipts" / "chain.json"),
        "links": 4,
        "head": resumed_chain["links"][-1]["sha256"],
    }


def test_dry_run_mission_writes_no_chain(repo, home, tmp_path):
    raw = {"cwd": str(repo), "lanes": [{"name": "a", "fleet": "claude", "prompt": "A"}]}
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home, dry_run=True)
    assert result.chain is None
    assert not (Path(result.mission_dir) / "receipts").exists()


# --- conductor attest -----------------------------------------------------


def _two_lane_mission(repo, home, monkeypatch, tmp_path):
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


def _three_lane_mission(repo, home, monkeypatch, tmp_path):
    table = {
        "BUILD": ["sh", "-c", "printf 'v1\\n' > built.txt"],
        "REVIEW": ["sh", "-c", "echo reviewed"],
        "FIX": ["sh", "-c", "printf 'v2\\n' >> built.txt"],
    }
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: table[spec.prompt.split()[0]])
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
            {
                "name": "fix",
                "fleet": "claude",
                "mode": "write",
                "base": "build",
                "needs": ["review"],
                "prompt": "FIX it",
                "commit": "fix",
            },
        ],
    }
    return run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)


def test_cli_attest_verifies_a_non_isolated_read_lane(repo, home, monkeypatch, tmp_path, capsys):
    """A read lane may opt out of isolation (`isolate: false`); its
    attestation's `base_commit`/`tip_commit` are then the shared checkout's
    own HEAD, not an isolation worktree's `base_sha`/`tip_sha` or a landed
    commit's sha. `conductor attest` must not read that as tampering."""
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    monkeypatch.setattr(runner_mod, "build_argv", lambda spec: ["sh", "-c", "echo looked around"])
    raw = {
        "cwd": str(repo),
        "lanes": [
            {"name": "look", "fleet": "claude", "mode": "read", "isolate": False, "prompt": "LOOK"}
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    assert result.lanes[0]["ok"] is True

    assert main(["attest", result.mission_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is True
    assert out["links"][0]["problems"] == []


def test_cli_attest_verifies_an_untouched_mission(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    assert main(["attest", result.mission_id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is True
    assert len(out["links"]) == 2
    assert all(link["verified"] and not link["problems"] for link in out["links"])
    assert out["key_id"] == attest.key_id(attest.receipt_key(home))


def test_cli_attest_flags_a_flipped_result_ok(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    build_run_id = result.lanes[0]["attempts"][-1]["run_id"]
    result_path = home / "runs" / build_run_id / "result.json"
    data = json.loads(result_path.read_text())
    data["ok"] = not data["ok"]
    result_path.write_text(json.dumps(data))

    assert main(["attest", result.mission_id]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is False
    build_row = next(row for row in out["links"] if row["lane"] == "build")
    assert build_row["verified"] is False
    assert any("ok disagrees with result.json" in p for p in build_row["problems"])


def test_cli_attest_flags_an_edited_diff_patch(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    build_run_id = result.lanes[0]["attempts"][-1]["run_id"]
    diff_path = home / "runs" / build_run_id / "diff.patch"
    diff_path.write_text(diff_path.read_text() + "\ntampered\n")

    assert main(["attest", result.mission_id]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is False
    build_row = next(row for row in out["links"] if row["lane"] == "build")
    assert any("source_diff_sha256 disagrees" in p for p in build_row["problems"])


def test_cli_attest_flags_an_edited_link_file(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    mission_dir = Path(result.mission_dir)
    chain = json.loads((mission_dir / "receipts" / "chain.json").read_text())
    link_path = Path(chain["links"][0]["path"])
    envelope = json.loads(link_path.read_text())
    tampered_statement = {"_type": "conductor/mission-link/v1", "index": 0}
    envelope["payload"] = base64.b64encode(json.dumps(tampered_statement).encode()).decode()
    link_path.write_text(json.dumps(envelope))

    assert main(["attest", result.mission_id]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is False
    build_row = next(row for row in out["links"] if row["lane"] == "build")
    assert build_row["verified"] is False
    assert build_row["problems"]


def test_cli_attest_flags_a_link_deleted_from_the_middle(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _three_lane_mission(repo, home, monkeypatch, tmp_path)
    mission_dir = Path(result.mission_dir)
    chain_path = mission_dir / "receipts" / "chain.json"
    chain = json.loads(chain_path.read_text())
    assert [link["lane"] for link in chain["links"]] == ["build", "review", "fix"]
    del chain["links"][1]
    chain_path.write_text(json.dumps(chain))

    assert main(["attest", result.mission_id]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is False
    fix_row = next(row for row in out["links"] if row["lane"] == "fix")
    assert fix_row["verified"] is False
    assert any("previous does not match" in p for p in fix_row["problems"])


def test_cli_attest_exits_3_on_an_unknown_mission(home, monkeypatch, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    assert main(["attest", "no-such-mission"]) == 3
    assert "does not exist" in capsys.readouterr().err


def test_cli_attest_exits_3_when_the_key_is_missing(repo, home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CONDUCTOR_HOME", str(home))
    result = _two_lane_mission(repo, home, monkeypatch, tmp_path)
    (home / "keys" / "receipt.key").unlink()

    assert main(["attest", result.mission_id]) == 3
    assert "key is missing" in capsys.readouterr().err


# --- README --------------------------------------------------------------


def test_readme_documents_signed_lane_receipts():
    readme = Path(__file__).parents[1] / "README.md"
    section = readme.read_text().split("### Signed lane receipts", 1)[1].split("\n## ", 1)[0]
    assert "attestation.json" in section
    assert "base_commit" in section and "tip_commit" in section
    assert "conductor attest MISSION_ID" in section
    assert "$CONDUCTOR_HOME/keys/receipt.key" in section
    assert "mode 700" in section and "mode 600" in section
    assert "docs/ROADMAP-2026-09.md" in section and "item A5" in section
    assert "draft-marques-asqav-compliance-receipts" in section
