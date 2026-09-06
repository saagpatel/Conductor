"""E12: notifications.

An opt-in shell hook a mission fires at three settle boundaries (pause, end,
breaker -- see the mission-level `notify` key and its three call sites in
`mission.py`). A notification is a note, never a verdict: it never changes
`ok`, an exit code, or a pause, so `emit` never raises -- every failure mode
(missing command, non-zero exit, timeout, OSError) comes back as `ok: false`
with the reason, for the caller to record and move on.
"""

from __future__ import annotations

import json
import os
import subprocess

NOTIFY_EVENTS = ("pause", "end", "breaker")
_ERROR_MAX_CHARS = 500


def emit(config: dict, event: dict, *, cwd: str) -> dict:
    """Run `config["command"]` through the shell with `event` as JSON on
    stdin, under `config["timeout"]` seconds. `event` must already carry
    `"event"` (the event name) and `"mission_id"`; both also reach the
    command as the environment variables `CONDUCTOR_EVENT` and
    `CONDUCTOR_MISSION`."""
    name = event.get("event")
    env = dict(os.environ)
    env["CONDUCTOR_EVENT"] = str(name)
    env["CONDUCTOR_MISSION"] = str(event.get("mission_id"))
    try:
        proc = subprocess.run(
            config["command"],
            shell=True,
            cwd=cwd,
            input=json.dumps(event),
            capture_output=True,
            text=True,
            timeout=config["timeout"],
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "event": name,
            "ok": False,
            "exit_code": None,
            "timed_out": True,
            "error": f"timed out after {config['timeout']}s",
        }
    except OSError as exc:
        return {
            "event": name,
            "ok": False,
            "exit_code": None,
            "timed_out": False,
            "error": str(exc)[:_ERROR_MAX_CHARS],
        }
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()
        reason = f"exit {proc.returncode}" + (f": {tail}" if tail else "")
        return {
            "event": name,
            "ok": False,
            "exit_code": proc.returncode,
            "timed_out": False,
            "error": reason[:_ERROR_MAX_CHARS],
        }
    return {"event": name, "ok": True, "exit_code": 0, "timed_out": False, "error": None}
