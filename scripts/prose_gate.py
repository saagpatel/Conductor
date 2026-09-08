#!/usr/bin/env python3
"""A gate for a prose document: the checks an anti-slop pass must not break.

Exit 1 with one line per problem, exit 0 when the document passes. Used as a
conductor mission's `test` on a documentation lane (F10, the anti-slop
consumer), where the ordinary code gate says nothing about the deliverable.

Checks: the file exists and is not empty; no em or en dash (the house style
forbids them); none of the filler words below; every markdown table row has
the same number of cells as its header. Fenced code blocks are skipped: a
quoted tool result or log line is a receipt, and rewriting it to satisfy a
style rule falsifies it (the F22 live consumer, 2026-09-07).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FILLER = (
    "delve",
    "leverage",
    "seamless",
    "robust",
    "crucial",
    "in order to",
    "it is worth noting",
    "it should be noted",
    "utilize",
    "very ",
    "actually",
    "basically",
    "essentially",
    "simply",
    "just ",
    "note that",
    "importantly",
)
DASHES = ("—", "–")


def problems(text: str) -> list[str]:
    out: list[str] = []
    lines = text.splitlines()
    fenced = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        lowered = line.lower()
        for dash in DASHES:
            if dash in line:
                out.append(f"line {number}: dash U+{ord(dash):04X}")
        if line.startswith("|"):
            continue
        for word in FILLER:
            if re.search(r"\b" + re.escape(word.strip()) + r"\b", lowered):
                out.append(f"line {number}: filler {word.strip()!r}")
    header_cells: int | None = None
    fenced = False
    for number, line in enumerate(lines, start=1):
        # The same fence tracking the loop above does. Without it a fenced
        # block whose lines start with `|` (a shell pipeline, an ASCII
        # diagram, a quoted table from a tool's own output) is read as a
        # markdown table and its rows counted, which the module docstring
        # already promises not to do.
        if line.lstrip().startswith("```"):
            fenced = not fenced
            header_cells = None
            continue
        if fenced:
            continue
        if not line.startswith("|"):
            header_cells = None
            continue
        cells = len(line.strip().strip("|").split("|"))
        if header_cells is None:
            header_cells = cells
        elif cells != header_cells:
            out.append(f"line {number}: table row has {cells} cells, header has {header_cells}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: prose_gate.py DOCUMENT", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"{path}: missing")
        return 1
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        print(f"{path}: empty")
        return 1
    found = problems(text)
    for item in found:
        print(f"{path}: {item}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
