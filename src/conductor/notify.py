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
import tempfile

from .verify import killpg

NOTIFY_EVENTS = ("pause", "end", "breaker")
_ERROR_MAX_CHARS = 500
# Enough bytes to hold `_ERROR_MAX_CHARS` characters of any UTF-8 text, so
# the tail read is bounded whatever the command wrote.
_ERROR_TAIL_BYTES = _ERROR_MAX_CHARS * 4


def _output_tail(out) -> str:
    """The last `_ERROR_MAX_CHARS` characters the command wrote.

    Seeks to the end and reads back a bounded window rather than the whole
    file: a notify command that prints megabytes must not be pulled into
    memory to produce a 500-character reason. A window that starts mid
    character is why the decode replaces rather than raises.
    """
    out.seek(0, os.SEEK_END)
    size = out.tell()
    out.seek(max(0, size - _ERROR_TAIL_BYTES))
    return out.read().decode("utf-8", errors="replace")[-_ERROR_MAX_CHARS:].strip()


def emit(config: dict, event: dict, *, cwd: str) -> dict:
    """Run `config["command"]` through the shell with `event` as JSON on
    stdin, under `config["timeout"]` seconds. `event` must already carry
    `"event"` (the event name) and `"mission_id"`; both also reach the
    command as the environment variables `CONDUCTOR_EVENT` and
    `CONDUCTOR_MISSION`.

    W10: the command gets its own process group, exactly like the gate in
    `verify.run_tests`, and the group is killed whole on timeout --
    `subprocess.run`'s timeout kills only the shell, so a child the command
    spawned outlived the mission that fired it. Output goes to a temporary
    file rather than a pipe and is read back only as a bounded tail, so a
    chatty command neither blocks on a full pipe nor is buffered into memory
    for the 500 characters a failure reason actually uses.
    """
    name = event.get("event")
    env = dict(os.environ)
    env["CONDUCTOR_EVENT"] = str(name)
    env["CONDUCTOR_MISSION"] = str(event.get("mission_id"))
    timeout = config["timeout"]
    # A trailing newline so a command that appends stdin to a file (the
    # README's own example) produces one JSON line per event, never two
    # events concatenated on one line. Written to a file rather than a pipe
    # so `emit` never blocks writing it to a command that does not read.
    with tempfile.TemporaryFile() as stdin_file, tempfile.TemporaryFile() as out:
        stdin_file.write((json.dumps(event) + "\n").encode("utf-8"))
        stdin_file.seek(0)
        try:
            proc = subprocess.Popen(
                config["command"],
                shell=True,
                cwd=cwd,
                stdin=stdin_file,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            return {
                "event": name,
                "ok": False,
                "exit_code": None,
                "timed_out": False,
                "error": str(exc)[:_ERROR_MAX_CHARS],
            }
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            killpg(proc.pid)
            proc.wait()
            return {
                "event": name,
                "ok": False,
                "exit_code": None,
                "timed_out": True,
                "error": f"timed out after {timeout}s",
            }
        if proc.returncode != 0:
            tail = _output_tail(out)
            reason = f"exit {proc.returncode}" + (f": {tail}" if tail else "")
            return {
                "event": name,
                "ok": False,
                "exit_code": proc.returncode,
                "timed_out": False,
                "error": reason,
            }
    return {"event": name, "ok": True, "exit_code": 0, "timed_out": False, "error": None}
