"""D1: conflict-aware collate. Two sink lanes editing the same file usually
show up long before a human notices -- in the diffs conductor already holds.
`touched_files` and `overlap` read those diffs; `merge_conflicts` asks git
itself whether two lanes' committed tips would actually clash.

Evidence (docs/ROADMAP-2026-09.md item D1): a 27.7% conflict rate across
107k simulated agentic merges (AgenticFlict); Cursor's own agent swarm
accumulating 70k conflicts, 7,771 of them on one file.
"""

from __future__ import annotations

import itertools
import subprocess

_DIFF_GIT_RE_PREFIX = "diff --git a/"


def touched_files(patch: str) -> list[str]:
    """The repo-relative paths one unified diff touches, read from its
    `diff --git a/<p> b/<p>` headers. A rename's old and new paths both
    count; an add or a delete names the same path on both sides."""
    paths: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith(_DIFF_GIT_RE_PREFIX):
            continue
        rest = line[len(_DIFF_GIT_RE_PREFIX) :]
        marker = rest.rfind(" b/")
        if marker == -1:
            continue
        a_path = rest[:marker]
        b_path = rest[marker + len(" b/") :]
        if a_path:
            paths.add(a_path)
        if b_path:
            paths.add(b_path)
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
        conflicts.append(line)
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
            else:
                entry["conflicts"] = conflicts
        else:
            entry["error"] = proc.stderr.strip() or f"git merge-tree exited {proc.returncode}"
        pairs.append(entry)
        for path in entry.get("conflicts") or []:
            files.setdefault(path, []).append([a, b])
    return {"pairs": pairs, "files": files}
