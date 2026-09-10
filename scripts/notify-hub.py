#!/usr/bin/env python3
"""Deliver a conductor `notify` event to the operator's notification-hub.

Conductor runs the mission's `notify.command` with one JSON event on stdin at
`pause`, `end`, and `breaker`. This script turns that event into a hub event
and hands it to the harness's own producer (`notification-hub-producer.py`,
which owns the producer token and a durable outbox), and appends the raw
event to `<CONDUCTOR_HOME>/notify-events.jsonl` so the morning read has a
local record even when the hub was down.

The producer's exit code is this script's exit code: conductor records a
failed notification as a note, never as a verdict (README, Notifications),
and a notification that silently claimed success would be worth nothing.

Environment:
  CONDUCTOR_HUB_PRODUCER  path to the producer script (default: the harness's
                          hooks/notification-hub-producer.py under $HOME/.claude)
  CONDUCTOR_HOME          where notify-events.jsonl is appended (default ~/.conductor)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PRODUCER = Path(
    os.environ.get("CONDUCTOR_HUB_PRODUCER")
    or Path.home() / ".claude" / "hooks" / "notification-hub-producer.py"
)
HOME = Path(os.environ.get("CONDUCTOR_HOME") or Path.home() / ".conductor")
LOG = HOME / "notify-events.jsonl"

LEVELS = {"pause": "attention", "breaker": "attention", "end": "normal"}


def describe(event: dict[str, object]) -> tuple[str, str, str]:
    """(title, body, level) for the three event kinds conductor sends."""
    kind = str(event.get("event") or "unknown")
    mission = str(event.get("mission_id") or "?")
    if kind == "pause":
        title = "Conductor mission paused"
        body = f"{mission}: {event.get('question') or event.get('kind')}"
    elif kind == "breaker":
        title = "Conductor lane tripped a breaker"
        body = (
            f"{mission}: lane {event.get('lane')} hit {event.get('breaker')} "
            f"at ${float(event.get('cost_usd') or 0):.2f}"
        )
    elif kind == "end":
        ok = event.get("ok")
        title = "Conductor mission " + ("finished green" if ok else "finished red")
        lanes = event.get("lanes") or []
        failed = [
            str(lane.get("name"))
            for lane in lanes
            if isinstance(lane, dict) and not lane.get("ok")
        ]
        body = f"{mission}: ${float(event.get('cost_usd') or 0):.2f}"
        if failed:
            body += "; not ok: " + ", ".join(failed)
        if not ok:
            level = "attention"
            return title, body[:2000], level
    else:
        title = "Conductor event"
        body = json.dumps(event)[:2000]
    return title, body[:2000], LEVELS.get(kind, "normal")


def main() -> int:
    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("event must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"notify-hub: bad event on stdin: {exc}", file=sys.stderr)
        return 2

    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps({"at": now, **event}) + "\n")
    except OSError as exc:
        print(f"notify-hub: could not append {LOG}: {exc}", file=sys.stderr)

    title, body, level = describe(event)
    mission = str(event.get("mission_id") or "")
    kind = str(event.get("event") or "")
    digest = hashlib.sha256(f"{mission}|{kind}|{body}".encode()).hexdigest()[:32]
    payload = {
        "event_id": f"cc:conductor-{digest}",
        "event_type": f"conductor.mission.{kind or 'event'}",
        "source_revision": mission,
        "source": "cc",
        "producer": "cc",
        "level": level,
        "title": title[:200],
        "body": body,
        "project": "conductor",
        "session_label": mission[:200] or "conductor",
    }
    if not PRODUCER.is_file():
        print(f"notify-hub: producer not found at {PRODUCER}", file=sys.stderr)
        return 3
    proc = subprocess.run(
        [sys.executable, str(PRODUCER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=8,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
