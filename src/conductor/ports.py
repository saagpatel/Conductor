"""Free TCP ports for one lane, claimed so two dispatches can never collide.

A worktree isolates files; it does not isolate the port a dev server or an
integration suite binds to. The kernel will happily hand the same "free" port
to two processes that ask for one at nearly the same moment, so the bind
itself is not the lock: a port is drawn by binding to 127.0.0.1:0 and reading
back whatever the kernel assigned, then claimed by creating a file under
`$CONDUCTOR_HOME/ports/<port>` with `O_CREAT | O_EXCL`. The file's existence,
not the bind, is what a second dispatch racing the same port loses on.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

MAX_DRAWS = 50


def _draw() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def claim(n: int, home: Path, run_id: str) -> tuple[list[int], str | None]:
    """Claim `n` distinct free ports for `run_id`.

    Returns the claimed ports and `None` on success. On failure (50 draws
    without finding `n` free ports; two conductor homes drawing from the same
    small kernel range is the case this guards), whatever was claimed is
    released and the second element is the error a dispatch should report --
    this runs before the fleet spawns, so nothing has been spent yet.
    """
    if n <= 0:
        return [], None
    directory = home / "ports"
    directory.mkdir(parents=True, exist_ok=True)
    claimed: list[int] = []
    draws = 0
    while len(claimed) < n and draws < MAX_DRAWS:
        draws += 1
        port = _draw()
        claim_path = directory / str(port)
        try:
            fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w") as f:
            f.write(run_id)
        claimed.append(port)
    if len(claimed) < n:
        release(home, claimed)
        return [], f"ports: could not allocate {n} free ports"
    return claimed, None


def release(home: Path, ports: list[int]) -> None:
    """Remove this run's claim files. Safe to call with an empty list."""
    directory = home / "ports"
    for port in ports:
        try:
            (directory / str(port)).unlink()
        except OSError:
            pass
