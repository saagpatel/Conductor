"""Signed receipts: Dead Simple Signing Envelope (DSSE) over HMAC-SHA256.

HMAC rather than a public-key scheme, for two reasons. The runtime is
standard library only, so there is no vetted asymmetric primitive to reach
for without vendoring one. And the property actually wanted is narrower than
"anyone can verify, only conductor can sign": it is that a receipt cannot be
edited after the fact by anything that does not hold the conductor-owned
key, which a shared secret gives just as well as a keypair would. The trade
is explicit rather than hidden: anyone who can read the key can forge a
receipt with it, so the key file is mode 600, its directory mode 700, and
the key itself is never copied into a receipt or logged.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

PAYLOAD_TYPE = "application/vnd.conductor.receipt+json"

_KEY_BYTES = 32
_KEY_WAIT_S = 2.0
_KEY_POLL_S = 0.001


def pae(payload_type: str, payload: bytes) -> bytes:
    """The DSSE pre-authentication encoding: what is actually signed."""
    type_bytes = payload_type.encode()
    return (
        b"DSSEv1 "
        + str(len(type_bytes)).encode()
        + b" "
        + type_bytes
        + b" "
        + str(len(payload)).encode()
        + b" "
        + payload
    )


def _key_path(home: Path) -> Path:
    return Path(home) / "keys" / "receipt.key"


def read_receipt_key(home: Path) -> bytes | None:
    """The key at `<home>/keys/receipt.key`, or None when it does not exist.

    Never creates it: a read-only verifier (`conductor attest`) must not
    conjure a key into existence just by looking for one.
    """
    try:
        return _key_path(home).read_bytes()
    except OSError:
        return None


def receipt_key(home: Path) -> bytes:
    """The 32-byte signing key at `<home>/keys/receipt.key`, created on
    first use.

    The directory is mode 700 and the file is created with
    `O_CREAT | O_EXCL | O_WRONLY` at mode 600, so two concurrent first uses
    cannot both write it. The loser of that race reads back the file the
    winner wrote, waiting briefly for the winner to finish its own write.
    """
    existing = read_receipt_key(home)
    if existing is not None and len(existing) == _KEY_BYTES:
        return existing
    path = _key_path(home)
    if existing is not None:
        # The file exists but is short: a concurrent first use has created
        # it with O_EXCL and not yet written it. Wait for that write rather
        # than returning the empty bytes as the key.
        return _await_key(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _await_key(path)
    try:
        os.write(fd, secrets.token_bytes(_KEY_BYTES))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return path.read_bytes()


def _await_key(path: Path) -> bytes:
    deadline = time.monotonic() + _KEY_WAIT_S
    while time.monotonic() < deadline:
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        if len(data) == _KEY_BYTES:
            return data
        time.sleep(_KEY_POLL_S)
    raise RuntimeError(f"receipt key at {path} never reached {_KEY_BYTES} bytes")


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def sign(statement: dict, key: bytes) -> dict:
    """A DSSE envelope over `statement`, HMAC-SHA256 under `key`."""
    payload = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    mac = hmac.new(key, pae(PAYLOAD_TYPE, payload), hashlib.sha256).digest()
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [
            {"keyid": key_id(key), "sig": base64.b64encode(mac).decode("ascii")}
        ],
    }


def verify(envelope: dict, key: bytes) -> tuple[dict | None, str | None]:
    """The decoded statement and None, or None and one failure reason:
    `malformed envelope`, `unexpected payload type`, `keyid mismatch: signed
    by <keyid>`, or `signature does not verify`."""
    try:
        payload_type = envelope["payloadType"]
        payload_b64 = envelope["payload"]
        signatures = envelope["signatures"]
        if not isinstance(payload_type, str) or not isinstance(payload_b64, str):
            return None, "malformed envelope"
        if not isinstance(signatures, list) or not signatures:
            return None, "malformed envelope"
        sig_entry = signatures[0]
        keyid = sig_entry["keyid"]
        sig_b64 = sig_entry["sig"]
        if not isinstance(keyid, str) or not isinstance(sig_b64, str):
            return None, "malformed envelope"
        payload = base64.b64decode(payload_b64, validate=True)
        sig = base64.b64decode(sig_b64, validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error):
        return None, "malformed envelope"

    if payload_type != PAYLOAD_TYPE:
        return None, "unexpected payload type"

    expected_keyid = key_id(key)
    if keyid != expected_keyid:
        return None, f"keyid mismatch: signed by {keyid}"

    expected_sig = hmac.new(key, pae(payload_type, payload), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected_sig):
        return None, "signature does not verify"

    try:
        statement = json.loads(payload)
    except json.JSONDecodeError:
        return None, "malformed envelope"
    if not isinstance(statement, dict):
        return None, "malformed envelope"
    return statement, None


def file_sha256(path: str | Path) -> str | None:
    """A file's content hash, or None when it does not exist."""
    p = Path(path)
    if not p.is_file():
        return None
    digest = hashlib.sha256()
    with p.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_run_attestation(
    home: Path, run_id: str, link_statement: dict, key: bytes
) -> tuple[list[str], dict | None]:
    """Whether one mission link's run still checks out: its attestation.json
    is unmoved and verifies, and it agrees with the run's own result.json
    and diff.patch on the few things the mission link claims about it.

    `link_statement` supplies `attestation_sha256` when the caller has one to
    check against (a mission link); an empty dict skips just that one check,
    so the same function verifies a run's attestation standalone (E13's
    export, for a run id no link names).

    Returns the problems found and the run's own attestation `taint` field
    (D2), so a caller can show it whether or not the run verifies."""
    problems: list[str] = []
    run_dir = home / "runs" / run_id
    attestation_file = run_dir / "attestation.json"
    expected_sha = link_statement.get("attestation_sha256")
    actual_sha = file_sha256(attestation_file)
    if actual_sha is None:
        problems.append(f"run '{run_id}': attestation.json is missing")
        return problems, None
    if expected_sha is not None and actual_sha != expected_sha:
        problems.append(f"run '{run_id}': attestation.json sha256 disagrees with the mission link")
    try:
        envelope = json.loads(attestation_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"run '{run_id}': attestation.json unreadable: {exc}")
        return problems, None
    statement, reason = verify(envelope, key)
    if statement is None:
        problems.append(f"run '{run_id}': attestation signature: {reason}")
        return problems, None
    taint = statement.get("taint")
    try:
        result_data = json.loads((run_dir / "result.json").read_text())
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"run '{run_id}': result.json unreadable: {exc}")
        return problems, taint
    if statement.get("ok") != result_data.get("ok"):
        problems.append(f"run '{run_id}': attestation ok disagrees with result.json")
    # Compare against the receipt's own `base_commit`/`tip_commit`, not a
    # reconstruction from `isolation`/`commit`: those are only set for an
    # isolated or landed dispatch, so a non-isolated read lane's real HEAD
    # would otherwise read back as a mismatch that never happened.
    if statement.get("base_commit") != result_data.get("base_commit"):
        problems.append(f"run '{run_id}': attestation base_commit disagrees with result.json")
    if statement.get("tip_commit") != result_data.get("tip_commit"):
        problems.append(f"run '{run_id}': attestation tip_commit disagrees with result.json")
    expected_digest = file_sha256(run_dir / "diff.patch")
    if statement.get("source_diff_sha256") != expected_digest:
        problems.append(f"run '{run_id}': attestation source_diff_sha256 disagrees with diff.patch")
    return problems, taint


class AttestInvalid(ValueError):
    """A mission whose receipt chain cannot be verified as asked."""


def attest_mission(home: Path, mission_id: str) -> dict:
    """Whatever `conductor attest` reports, as data: every link's signature,
    its place in the hash chain, and, for a link with a run, that the run's
    own attestation still matches its result.json and diff -- the same
    verification `cmd_attest` prints, factored out so `land.py` can run it
    as one of its own steps without shelling out to itself."""
    home = Path(home)
    mission_dir = home / "missions" / mission_id
    if not mission_dir.is_dir():
        raise AttestInvalid(f"mission '{mission_id}' does not exist")
    chain_path = mission_dir / "receipts" / "chain.json"
    if not chain_path.is_file():
        raise AttestInvalid(f"mission '{mission_id}' has no receipt chain")
    key = read_receipt_key(home)
    if key is None:
        raise AttestInvalid("receipt key is missing")
    try:
        chain = json.loads(chain_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AttestInvalid(f"chain.json is invalid: {exc}") from exc
    if not isinstance(chain, dict) or not isinstance(chain.get("links"), list):
        raise AttestInvalid("chain.json is malformed")

    rows = verify_chain_links(chain, home=home, key=key)
    results = [{k: v for k, v in row.items() if k != "_statement"} for row in rows]
    return {
        "mission_id": chain.get("mission_id", mission_id),
        "key_id": key_id(key),
        "links": results,
        "verified": all(row["verified"] for row in results),
    }


def verify_chain_links(chain: dict, *, home: Path, key: bytes) -> list[dict]:
    """The per-link verification loop `cmd_attest` runs: each link's
    signature, its place in the hash chain, and, for a link with a run, that
    the run's own attestation still matches its result.json and diff.patch.

    Each row carries the fields `cmd_attest` prints (`index`, `lane`,
    `run_id`, `taint`, `verified`, `problems`) plus `_statement`, the link's
    own decoded statement (or None) -- never printed by `cmd_attest`, but
    read by `export.export` so it does not have to decode every link a
    second time to learn a run's expected `attestation_sha256`."""
    results: list[dict] = []
    previous_sha: str | None = None
    links = chain.get("links") if isinstance(chain, dict) else None
    for entry in links or []:
        index = entry.get("index") if isinstance(entry, dict) else None
        lane = entry.get("lane") if isinstance(entry, dict) else None
        recorded_sha = entry.get("sha256") if isinstance(entry, dict) else None
        path_str = entry.get("path") if isinstance(entry, dict) else None
        problems: list[str] = []
        run_id: str | None = None
        run_taint: dict | None = None
        actual_sha: str | None = None
        statement: dict | None = None
        link_path = Path(path_str) if isinstance(path_str, str) else None
        if link_path is None or not link_path.is_file():
            problems.append("link file missing")
        else:
            actual_sha = file_sha256(link_path)
            if actual_sha != recorded_sha:
                problems.append("link file sha256 does not match chain.json")
            try:
                envelope = json.loads(link_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                envelope = None
                problems.append(f"link file is not valid JSON: {exc}")
            if envelope is not None:
                statement, reason = verify(envelope, key)
                if statement is None:
                    problems.append(f"link signature: {reason}")
                else:
                    if statement.get("previous") != previous_sha:
                        problems.append("previous does not match the prior link")
                    run_id = statement.get("run_id")
                    if isinstance(run_id, str):
                        run_problems, run_taint = verify_run_attestation(
                            home, run_id, statement, key
                        )
                        problems.extend(run_problems)
        results.append(
            {
                "index": index,
                "lane": lane,
                "run_id": run_id,
                "taint": run_taint,
                "verified": not problems,
                "problems": problems,
                "_statement": statement,
            }
        )
        previous_sha = actual_sha
    return results
