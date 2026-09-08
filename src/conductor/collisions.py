"""D1: conflict-aware collate. Two sink lanes editing the same file usually
show up long before a human notices -- in the diffs conductor already holds.
`touched_files` and `overlap` read those diffs; `merge_conflicts` asks git
itself whether two lanes' committed tips would actually clash.

Evidence (docs/archive/roadmaps-closed.md item D1): a 27.7% conflict rate across
107k simulated agentic merges (AgenticFlict); Cursor's own agent swarm
accumulating 70k conflicts, 7,771 of them on one file.
"""

from __future__ import annotations

import itertools
import subprocess

_DIFF_GIT_PREFIX = "diff --git "

# C-style escapes git writes inside a quoted path, beside the octal bytes
# handled separately below (`quote_c_style` in git's quote.c).
_C_ESCAPES = {
    '"': b'"',
    "\\": b"\\",
    "a": b"\a",
    "b": b"\b",
    "f": b"\f",
    "n": b"\n",
    "r": b"\r",
    "t": b"\t",
    "v": b"\v",
}


def _closing_quote(text: str) -> int:
    """The index of the quote that closes the one at `text[0]`, or -1."""
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index
        index += 1
    return -1


def _unquote(token: str) -> str:
    """A git-quoted path name (quotes included) as text.

    Git writes non-printable and non-ASCII bytes as C-style escapes -- `\\t`
    for a tab, `\\303\\251` for the two UTF-8 bytes of `e-acute` -- whenever
    `core.quotePath` is on, which is git's default. The bytes are rebuilt and
    decoded as UTF-8 with `surrogateescape`, so a path that is not valid UTF-8
    round-trips through the same lossless representation the rest of the
    stdlib uses for undecodable filenames rather than raising.
    """
    body = token[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out += char.encode("utf-8", "surrogateescape")
            index += 1
            continue
        index += 1
        if index >= len(body):  # a trailing backslash: keep it as written
            out += b"\\"
            break
        escape = body[index]
        if escape in "01234567":
            digits = ""
            while index < len(body) and body[index] in "01234567" and len(digits) < 3:
                digits += body[index]
                index += 1
            out.append(int(digits, 8) & 0xFF)
            continue
        out += _C_ESCAPES.get(escape, escape.encode("utf-8", "surrogateescape"))
        index += 1
    return out.decode("utf-8", "surrogateescape")


def _strip_side(path: str, side: str) -> str:
    prefix = f"{side}/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def _header_paths(rest: str) -> tuple[str, str] | None:
    """The `a` and `b` paths of one `diff --git` header, prefixes stripped.

    Either side may be quoted, independently of the other; the unquoted form
    is parsed exactly as before (the last ` b/` wins, so a path with spaces
    still splits where git put the second prefix)."""
    if rest.startswith('"'):
        end = _closing_quote(rest)
        if end == -1:
            return None
        a_path = _unquote(rest[: end + 1])
        remainder = rest[end + 1 :]
        if not remainder.startswith(" "):
            return None
        b_raw = remainder[1:]
    else:
        marker = rest.find(' "b/')
        if marker == -1 or not rest.endswith('"'):
            if not rest.startswith("a/"):
                return None
            plain = rest[len("a/") :]
            split = plain.rfind(" b/")
            if split == -1:
                return None
            return plain[:split], plain[split + len(" b/") :]
        a_path = rest[:marker]
        b_raw = rest[marker + 1 :]
    b_path = _unquote(b_raw) if b_raw.startswith('"') and b_raw.endswith('"') else b_raw
    return _strip_side(a_path, "a"), _strip_side(b_path, "b")


def touched_files(patch: str) -> list[str]:
    """The repo-relative paths one unified diff touches, read from its
    `diff --git a/<p> b/<p>` headers, quoted or not. A rename's old and new
    paths both count; an add or a delete names the same path on both sides."""
    paths: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith(_DIFF_GIT_PREFIX):
            continue
        sides = _header_paths(line[len(_DIFF_GIT_PREFIX) :])
        if sides is None:
            continue
        for path in sides:
            if path:
                paths.add(path)
    return sorted(paths)


def overlap(diffs: dict[str, str]) -> dict:
    """Which lanes touch which files, and the files two or more of them
    share -- a hotspot. `diffs` maps lane name to that lane's patch text,
    in the order the caller wants ties broken."""
    files: dict[str, list[str]] = {}
    for lane, patch in diffs.items():
        for path in touched_files(patch):
            files.setdefault(path, []).append(lane)
    hotspots = sorted(path for path, lanes in files.items() if len(lanes) >= 2)
    lane_counts = {lane: 0 for lane in diffs}
    for path in hotspots:
        for lane in files[path]:
            lane_counts[lane] += 1
    return {
        "files": {path: files[path] for path in sorted(files)},
        "hotspots": hotspots,
        "lanes": lane_counts,
    }


def _parse_conflicts(stdout: str) -> list[str] | None:
    """The conflicting paths from a `git merge-tree --write-tree --name-only`
    exit-1 run, or None when `stdout` is not the shape that command writes on
    a real conflict (an invalid ref writes its complaint to stderr instead
    and leaves stdout empty)."""
    lines = stdout.splitlines()
    if not lines or not lines[0].strip():
        return None
    conflicts: list[str] = []
    for line in lines[1:]:
        if line == "":
            break
        # git quotes a path with non-ASCII or special characters the same way
        # in `merge-tree --name-only` as in a `diff --git` header, and
        # `touched_files` already unquotes the latter. Leaving these quoted
        # meant a conflicting path and the hotspot naming the same file never
        # matched each other (2026-09-08 review).
        conflicts.append(
            _unquote(line) if line.startswith('"') and line.endswith('"') else line
        )
    return conflicts


def merge_conflicts(cwd: str, tips: dict[str, str], *, timeout: int = 60) -> dict:
    """Ask git itself whether each pair of lane tips would conflict, with
    `git merge-tree --write-tree --name-only`: exit 0 is a clean merge, exit
    1 gives the conflicting paths, and anything else -- a bad ref, a timeout,
    a missing git -- is recorded as that pair's error rather than raised, so
    one bad tip never takes the whole report down."""
    names = list(tips)
    pairs: list[dict] = []
    files: dict[str, list[list[str]]] = {}
    for a, b in itertools.combinations(names, 2):
        entry: dict = {"lanes": [a, b]}
        try:
            proc = subprocess.run(
                ["git", "merge-tree", "--write-tree", "--name-only", tips[a], tips[b]],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            entry["error"] = str(exc)
            pairs.append(entry)
            continue
        if proc.returncode == 0:
            entry["conflicts"] = []
        elif proc.returncode == 1:
            conflicts = _parse_conflicts(proc.stdout)
            if conflicts is None:
                entry["error"] = proc.stderr.strip() or "git merge-tree failed"
            elif not conflicts:
                # Exit 1 is git saying the merge conflicts. A run that says so
                # and then names no file is not a clean merge, and recording
                # it as `conflicts: []` made it read as exactly that
                # (2026-09-08 review).
                entry["error"] = (
                    proc.stderr.strip()
                    or "git merge-tree reported a conflict but named no files"
                )
            else:
                entry["conflicts"] = conflicts
        else:
            entry["error"] = proc.stderr.strip() or f"git merge-tree exited {proc.returncode}"
        pairs.append(entry)
        for path in entry.get("conflicts") or []:
            files.setdefault(path, []).append([a, b])
    return {"pairs": pairs, "files": files}
