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
    if existing is not None:
        return existing
    path = _key_path(home)
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
