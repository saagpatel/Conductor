"""Stop a dispatch whose output shows that work is no longer progressing.

The fleet process writes stdout while conductor waits.  Re-reading that whole
file every two seconds makes the detector itself progressively more expensive,
so Breaker owns an append-only byte offset and holds an incomplete final line
until the fleet finishes writing it.
"""

from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path

from .outputs import json_line


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True).encode()
    return sha256(encoded).hexdigest()[:12]


def _codex_signature(event: dict) -> str | None:
    if event.get("type") not in {"item.started", "item.completed"}:
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    if kind == "command_execution":
        command = item.get("command")
        return f"cmd:{command}" if isinstance(command, str) else None
    if kind == "file_change":
        raw = item.get("changes")
        if not isinstance(raw, list):
            raw = [item] if isinstance(item.get("path"), str) else []
        paths = sorted(
            str(change.get("path")) if isinstance(change, dict) else str(change)
            for change in raw
            if (isinstance(change, str) and change)
            or (isinstance(change, dict) and change.get("path"))
        )
        return f"edit:{','.join(paths)}" if paths else None
    if kind == "mcp_tool_call":
        server = item.get("server") or item.get("server_name")
        tool = item.get("tool") or item.get("tool_name")
        if isinstance(server, str) and isinstance(tool, str):
            return f"mcp:{server}.{tool}"
    return None


def _claude_signatures(event: dict) -> list[str]:
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    signatures: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str):
            signatures.append(f"{name}:{_hash(block.get('input'))}")
    return signatures


def _tool_and_args(container: dict) -> tuple[str | None, object]:
    """Read the direct and nested tool shapes used by agy and Cursor."""
    raw_tool = container.get("tool") or container.get("tool_name")
    args = _args(container)
    if isinstance(raw_tool, dict):
        args = _args(raw_tool, args)
        raw_tool = raw_tool.get("name") or raw_tool.get("tool_name")
    if isinstance(raw_tool, str):
        return raw_tool, args

    metadata = container.get("metadata")
    if isinstance(metadata, dict):
        name, nested_args = _tool_and_args(metadata)
        if name is not None:
            return name, nested_args

    # Cursor wraps calls as e.g. createPlanToolCall: {args: {...}}.
    for key, value in container.items():
        if not key.endswith("ToolCall") or not isinstance(value, dict):
            continue
        name = key.removesuffix("ToolCall")
        nested_args = _args(value)
        return name, nested_args
    return None, args


def _args(container: dict, default: object = None) -> object:
    for key in ("args", "arguments", "input", "tool_args", "tool_input", "parameters"):
        if key in container:
            return container[key]
    return default


def _antigravity_signature(event: dict) -> str | None:
    if event.get("event") != "step_update" and event.get("type") != "step_update":
        return None
    step = event.get("step_update")
    if not isinstance(step, dict):
        step = event
    tool, args = _tool_and_args(step)
    if tool is None:
        fallback = step.get("step_type") or step.get("name")
        tool = fallback if isinstance(fallback, str) else None
    return f"{tool}:{_hash(args)}" if tool is not None else None


def _cursor_signature(event: dict) -> str | None:
    if event.get("type") != "tool_call":
        return None
    call = event.get("tool_call")
    container = call if isinstance(call, dict) else event
    tool, args = _tool_and_args(container)
    return f"{tool}:{_hash(args)}" if tool is not None else None


def tool_events(fleet: str, text: str) -> list[str]:
    """The tool-call signatures present in complete JSON objects in ``text``."""
    signatures: list[str] = []
    for line in text.splitlines():
        event = json_line(line)
        if event is None:
            continue
        if fleet == "codex":
            signature = _codex_signature(event)
            if signature is not None:
                signatures.append(signature)
        elif fleet == "claude":
            signatures.extend(_claude_signatures(event))
        elif fleet == "antigravity":
            signature = _antigravity_signature(event)
            if signature is not None:
                signatures.append(signature)
        elif fleet == "cursor":
            signature = _cursor_signature(event)
            if signature is not None:
                signatures.append(signature)
    return signatures


class Breaker:
    """Incrementally watch one fleet stdout file for three runaway shapes."""

    def __init__(
        self,
        fleet: str,
        stdout_path: Path,
        *,
        stall_s: int | None,
        loop_limit: int | None,
        max_tool_calls: int | None,
    ) -> None:
        self.fleet = fleet
        self.stdout_path = stdout_path
        self.stall_s = stall_s or None
        self.loop_limit = loop_limit or None
        self.max_tool_calls = max_tool_calls or None
        self.signatures: list[str] = []
        self.tripped: str | None = None
        self._offset = 0
        self._partial = b""
        self._last_size = self._size()
        self._last_change = time.monotonic()

    def _size(self) -> int:
        try:
            return self.stdout_path.stat().st_size
        except OSError:
            return 0

    def _read(self, now: float) -> None:
        size = self._size()
        if size != self._last_size:
            self._last_size = size
            self._last_change = now
        if size < self._offset:
            # A replacement or truncation must not leave the reader seeking
            # forever beyond EOF and silently disabling every tool breaker.
            self._offset = 0
            self._partial = b""
        try:
            with self.stdout_path.open("rb") as source:
                source.seek(self._offset)
                chunk = source.read()
        except OSError:
            return
        if not chunk:
            return
        self._offset += len(chunk)
        parts = (self._partial + chunk).split(b"\n")
        self._partial = parts.pop()
        complete = "\n".join(part.decode(errors="replace") for part in parts)
        if complete:
            self.signatures.extend(tool_events(self.fleet, complete))

    def check(self) -> str | None:
        """Return and remember the first breaker reason, or None."""
        if self.tripped is not None:
            return self.tripped
        now = time.monotonic()
        self._read(now)
        if self.stall_s is not None and now - self._last_change >= self.stall_s:
            self.tripped = f"stalled: no output for {self.stall_s}s"
        elif (
            self.loop_limit is not None
            and len(self.signatures) >= self.loop_limit
            and len(set(self.signatures[-self.loop_limit :])) == 1
        ):
            signature = self.signatures[-1]
            self.tripped = f"looping: {signature} repeated {self.loop_limit} times"
        elif self.max_tool_calls is not None and len(self.signatures) > self.max_tool_calls:
            self.tripped = f"tool budget hit: {len(self.signatures)} tool calls"
        return self.tripped

    def to_dict(self) -> dict[str, int | float | str | None]:
        return {
            "tool_calls": len(self.signatures),
            "last_output_age_s": max(0.0, time.monotonic() - self._last_change),
            "tripped": self.tripped,
        }
