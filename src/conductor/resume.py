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


def cancelled_choices(
    needs: dict[str, tuple[str, ...]], kept: set[str], rerun: set[str],
    cancelled_by: dict[str, str],
) -> tuple[set[str], set[str], list[str]]:
    """One cancellation pass, also used by the legacy mission adapter."""
    kept, rerun = set(kept), set(rerun)
    notes: list[str] = []
    for name, winner in cancelled_by.items():
        if name in rerun and winner in kept and not any(n in rerun for n in needs[name]):
            kept.add(name)
            rerun.remove(name)
            notes.append(f"lane '{name}' stays cancelled: '{winner}' is kept")
        elif name in kept and winner in rerun:
            kept.remove(name)
            rerun.add(name)
            notes.append(f"lane '{name}' runs after all: '{winner}' is being rerun")
    return kept, rerun, notes


def settle_choices(
    needs: dict[str, tuple[str, ...]],
    kept: set[str],
    rerun: set[str],
    cancelled_by: dict[str, str],
) -> tuple[set[str], set[str], list[str]]:
    """Settle reuse decisions without reading files or mutating receipts.

    A cancelled competitor is reusable only while its winner and inputs
    remain reusable. Evaluate cancellation and downstream invalidation in
    the same fixed point so neither can leave the other stale.
    """
    kept, rerun = set(kept), set(rerun)
    notes: list[str] = []
    changed = True
    while changed:
        before = (set(kept), set(rerun))
        kept, rerun, cancellation_notes = cancelled_choices(needs, kept, rerun, cancelled_by)
        notes.extend(cancellation_notes)
        changed = before != (kept, rerun)
        for name, upstream in needs.items():
            if name in kept and any(n in rerun for n in upstream):
                kept.remove(name)
                rerun.add(name)
                changed = True
    return kept, rerun, notes
