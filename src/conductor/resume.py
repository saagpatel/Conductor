"""A retryable verification pause preserves the last completed mission receipt."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .graph import MissionInvalid

VERIFICATION_FILE = "resume-verification.json"


class VerificationUnavailable(MissionInvalid):
    """No evidence either for reusing a lane or for paying to rerun it."""

    def __init__(self, lane: str, repo: str | Path, command: tuple[str, ...], reason: str):
        self.check = {
            "kind": "verification",
            "lane": lane,
            "repository": str(repo),
            "command": ["git", *command],
            "reason": reason or "git did not complete",
            "attempts": 2,
        }
        super().__init__(
            f"resume paused: git could not verify '{lane}' in {repo} after two attempts "
            f"({' '.join(command)}): {self.check['reason']}; retry resume when Git is available"
        )


def verification_block(mission_dir: Path) -> dict | None:
    path = mission_dir / VERIFICATION_FILE
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"kind": "verification", "reason": "verification pause record is unreadable"}
    if not isinstance(raw, dict):
        return {"kind": "verification", "reason": "verification pause record is invalid"}
    return raw


def record_verification_block(mission_dir: Path, exc: VerificationUnavailable) -> None:
    """Publish diagnostics separately; result.json and paid run receipts stay intact."""
    raw = {**exc.check, "checked_at": datetime.now(UTC).isoformat()}
    fd, name = tempfile.mkstemp(prefix=".resume-verification-", dir=mission_dir)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(raw, stream, indent=2)
            stream.write("\n")
        os.replace(name, mission_dir / VERIFICATION_FILE)
    finally:
        Path(name).unlink(missing_ok=True)


def clear_verification_block(mission_dir: Path) -> None:
    (mission_dir / VERIFICATION_FILE).unlink(missing_ok=True)
