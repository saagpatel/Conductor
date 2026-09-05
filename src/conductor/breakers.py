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


def _claude_signatures(event: dict) -> list[tuple[str, object | None]]:
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    signatures: list[tuple[str, object | None]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str):
            signatures.append((f"{name}:{_hash(block.get('input'))}", block.get("id")))
    return signatures


def _tool_and_args(container: dict) -> tuple[str | None, object]:
    """Read the direct and nested tool shapes used by agy and Cursor."""
    raw_tool = container.get("tool") or container.get("tool_name")
    args = _args(container)
    if isinstance(raw_tool, dict):
        args = _args(raw_tool, args)
        raw_tool = raw_tool.get("name") or raw_tool.get("tool_name")
    if isinstance(raw_tool, str):
        tool_info = container.get("tool_info")
        if args is None and isinstance(tool_info, dict):
            args = _args(tool_info)
        return raw_tool, args

    tool_info = container.get("tool_info")
    if isinstance(tool_info, dict):
        name = tool_info.get("name") or tool_info.get("tool_name")
        if isinstance(name, str):
            return name, _args(tool_info, args)

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


def _identity(fleet: str, event: dict, nested: object | None = None) -> str | None:
    """A lifecycle-stable call id, so started and completed count once."""
    value: object | None = nested
    if fleet == "codex":
        item = event.get("item")
        value = item.get("id") if isinstance(item, dict) else None
    elif fleet == "antigravity":
        step = event.get("step_update")
        value = step.get("step_index") if isinstance(step, dict) else event.get("step_index")
    elif fleet == "cursor":
        call = event.get("tool_call")
        if isinstance(call, dict):
            value = call.get("toolCallId") or call.get("tool_call_id")
        value = value or event.get("call_id")
    if isinstance(value, str | int) and not isinstance(value, bool):
        return f"{fleet}:{value}"
    return None


def _entries(fleet: str, text: str) -> list[tuple[str, str | None]]:
    entries: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        event = json_line(line)
        if event is None:
            continue
        if fleet == "codex":
            signature = _codex_signature(event)
            if signature is not None:
                entries.append((signature, _identity(fleet, event)))
        elif fleet == "claude":
            entries.extend(
                (signature, _identity(fleet, event, identity))
                for signature, identity in _claude_signatures(event)
            )
        elif fleet == "antigravity":
            signature = _antigravity_signature(event)
            if signature is not None:
                entries.append((signature, _identity(fleet, event)))
        elif fleet == "cursor":
            signature = _cursor_signature(event)
            if signature is not None:
                entries.append((signature, _identity(fleet, event)))
    return entries


def _new_signatures(fleet: str, text: str, seen: set[str]) -> list[str]:
    signatures: list[str] = []
    for signature, identity in _entries(fleet, text):
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
        signatures.append(signature)
    return signatures


def tool_events(fleet: str, text: str) -> list[str]:
    """The tool-call signatures present in complete JSON objects in ``text``."""
    return _new_signatures(fleet, text, set())


class Breaker:
    """Incrementally watch one fleet stdout file for runaway shapes."""

    def __init__(
        self,
        fleet: str,
        stdout_path: Path,
        *,
        stall_s: int | None,
        loop_limit: int | None,
        max_tool_calls: int | None,
        idle_s: int | None = None,
    ) -> None:
        self.fleet = fleet
        self.stdout_path = stdout_path
        self.stall_s = stall_s or None
        self.loop_limit = loop_limit or None
        self.max_tool_calls = max_tool_calls or None
        self.idle_s = idle_s or None
        self.signatures: list[str] = []
        self.tripped: str | None = None
        self._offset = 0
        self._partial = b""
        self._seen_calls: set[str] = set()
        self._last_size = self._size()
        self._last_change = time.monotonic()
        self._last_tool = time.monotonic()

    def _size(self) -> int:
        try:
            return self.stdout_path.stat().st_size
        except OSError:
            return 0

    def _read(self, now: float, *, final: bool = False) -> None:
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
        parts: list[bytes] = []
        if chunk:
            self._offset += len(chunk)
            parts = (self._partial + chunk).split(b"\n")
            self._partial = parts.pop()
        if final and self._partial:
            # Once the process exited, EOF terminates its final event even if
            # the fleet omitted a newline. Leaving it buffered lets a last
            # tool call evade the ceiling by finishing between polls.
            parts.append(self._partial)
            self._partial = b""
        complete = "\n".join(part.decode(errors="replace") for part in parts)
        if complete:
            new_sigs = _new_signatures(self.fleet, complete, self._seen_calls)
            if new_sigs:
                self.signatures.extend(new_sigs)
                self._last_tool = now

    def check(self, *, final: bool = False) -> str | None:
        """Return and remember the first breaker reason, or None."""
        if self.tripped is not None:
            return self.tripped
        now = time.monotonic()
        self._read(now, final=final)
        if self.stall_s is not None and now - self._last_change >= self.stall_s:
            self.tripped = f"stalled: no output for {self.stall_s}s"
        elif self.idle_s is not None and now - self._last_tool >= self.idle_s:
            self.tripped = f"idle: no tool call for {self.idle_s}s"
        elif (
            self.loop_limit is not None
            and len(self.signatures) >= self.loop_limit
            and len(set(self.signatures[-self.loop_limit :])) == 1
            # Codex's file_change event names the path, never the content, so
            # six successive edits to one large module look identical. They
            # are progress, not a loop: the breaker killed its own successor's
            # build that way (2026-09-04). Edits count as tool calls only.
            and not self.signatures[-1].startswith("edit:")
        ):
            signature = self.signatures[-1]
            self.tripped = f"looping: {signature} repeated {self.loop_limit} times"
        elif self.max_tool_calls is not None and len(self.signatures) > self.max_tool_calls:
            self.tripped = f"tool budget hit: {len(self.signatures)} tool calls"
        return self.tripped

    def to_dict(self) -> dict[str, int | float | str | None]:
        now = time.monotonic()
        return {
            "tool_calls": len(self.signatures),
            "last_output_age_s": max(0.0, now - self._last_change),
            "last_tool_call_age_s": max(0.0, now - self._last_tool),
            "tripped": self.tripped,
        }
