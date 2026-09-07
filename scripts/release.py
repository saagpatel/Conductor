#!/usr/bin/env python3
"""Release bookkeeping for conductor: version bump, receipt stub, roadmap row.

Lead-only helper. It never runs git; it prints the ``git add`` line to run once the
edits look right. Standard library only, like the rest of the repository.

    python3 scripts/release.py 0.48.0 --item "E10 planner lanes" --cost 13.60
    python3 scripts/release.py 0.48.0 --item "E10 planner lanes" --cost 13.60 --dry-run

Exit codes: 0 ok, 2 bad arguments or a refusal, 1 an unexpected error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SHAPE = "Shape A via the launcher"
RECEIPT_BODY = "TODO: write the receipt."

PYPROJECT = "pyproject.toml"
INIT = "src/conductor/__init__.py"
LOCK = "uv.lock"
RECEIPT_DOC = "docs/RESET-2026-09.md"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_RECEIPT_HEADING_RE = re.compile(r"^## Receipt ")
_TABLE_HEADER_RE = re.compile(r"^\|\s*item\s*\|\s*version\s*\|")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Refusal(Exception):
    """A refusal the lead can act on: bad input or a file that does not match."""


@dataclass
class Edit:
    """One file rewritten in full."""

    path: Path
    rel: str
    old: str
    new: str


def parse_version(text: str, what: str) -> tuple[int, int, int]:
    if not _VERSION_RE.match(text):
        raise Refusal(f"{what} is not an X.Y.Z version: {text!r}")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


# --- version sites -----------------------------------------------------------


def _pyproject_version_span(text: str) -> tuple[int, int, str]:
    """Return (start, end, version) for the ``version = "..."`` line in [project]."""
    section: str | None = None
    for match in re.finditer(r"^(?:\[(?P<section>[^\]]+)\]|version\s*=\s*\"(?P<v>[^\"]*)\")\s*$",
                             text, re.MULTILINE):
        if match.group("section") is not None:
            section = match.group("section")
            continue
        if section == "project":
            return match.start("v"), match.end("v"), match.group("v")
    raise Refusal(f"{PYPROJECT}: no version under [project]")


def _init_version_span(text: str) -> tuple[int, int, str]:
    matches = list(re.finditer(r"^__version__\s*=\s*\"(?P<v>[^\"]*)\"\s*$", text, re.MULTILINE))
    if not matches:
        raise Refusal(f"{INIT}: no __version__ assignment")
    if len(matches) > 1:
        raise Refusal(f"{INIT}: {len(matches)} __version__ assignments, expected one")
    match = matches[0]
    return match.start("v"), match.end("v"), match.group("v")


def _lock_version_span(text: str) -> tuple[int, int, str]:
    """Locate the ``version`` line of the block whose ``name = "conductor"``."""
    starts = [m for m in re.finditer(r"^name\s*=\s*\"conductor\"\s*$", text, re.MULTILINE)]
    if not starts:
        raise Refusal(f'{LOCK}: no block with name = "conductor"')
    if len(starts) > 1:
        raise Refusal(f'{LOCK}: {len(starts)} blocks named "conductor", expected one')
    block = text[starts[0].end():]
    end = block.find("\n\n")
    if end != -1:
        block = block[:end]
    match = re.search(r"^version\s*=\s*\"(?P<v>[^\"]*)\"\s*$", block, re.MULTILINE)
    if match is None:
        raise Refusal(f'{LOCK}: the "conductor" block has no version line')
    offset = starts[0].end()
    return offset + match.start("v"), offset + match.end("v"), match.group("v")


_SITES = (
    (PYPROJECT, _pyproject_version_span),
    (INIT, _init_version_span),
    (LOCK, _lock_version_span),
)


def bump_versions(root: Path, new_version: str) -> tuple[list[Edit], str]:
    """Rewrite the three version sites. Returns (edits, current version)."""
    found: list[tuple[str, Path, str, int, int, str]] = []
    for rel, locate in _SITES:
        path = root / rel
        text = read_text(path, rel)
        start, end, current = locate(text)
        found.append((rel, path, text, start, end, current))

    currents = {rel: current for rel, _, _, _, _, current in found}
    distinct = set(currents.values())
    if len(distinct) > 1:
        detail = ", ".join(f"{rel} = {value}" for rel, value in currents.items())
        raise Refusal(f"the three files disagree on the current version: {detail}")

    current = distinct.pop()
    current_parts = parse_version(current, "the current version")
    new_parts = parse_version(new_version, "the new version")
    if new_parts <= current_parts:
        raise Refusal(f"new version {new_version} is not greater than current version {current}")

    edits = [
        Edit(path=path, rel=rel, old=text, new=text[:start] + new_version + text[end:])
        for rel, path, text, start, end, _ in found
    ]
    return edits, current


# --- receipt stub ------------------------------------------------------------


def receipt_edit(root: Path, new_version: str, item: str, cost: str, shape: str,
                 date: str) -> Edit:
    rel = RECEIPT_DOC
    path = root / rel
    text = read_text(path, rel)
    lines = text.splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if _RECEIPT_HEADING_RE.match(line)), None)
    if index is None:
        raise Refusal(f"{rel}: no line starting with '## Receipt ' to insert before")
    stub = (
        f"## Receipt {date}: {item} shipped as v{new_version} (${cost}, {shape})\n"
        "\n"
        f"{RECEIPT_BODY}\n"
        "\n"
    )
    new_lines = lines[:index] + [stub] + lines[index:]
    return Edit(path=path, rel=rel, old=text, new="".join(new_lines))


# --- roadmap row -------------------------------------------------------------


def default_roadmap(root: Path) -> str:
    docs = root / "docs"
    candidates = sorted(p.name for p in docs.glob("ROADMAP-*.md")) if docs.is_dir() else []
    if not candidates:
        raise Refusal("no docs/ROADMAP-*.md found; pass --roadmap")
    return f"docs/{candidates[-1]}"


def roadmap_edit(root: Path, rel: str, new_version: str, item: str, cost: str,
                 shape: str) -> Edit:
    path = root / rel
    text = read_text(path, rel)
    lines = text.splitlines(keepends=True)
    header = next((i for i, line in enumerate(lines) if _TABLE_HEADER_RE.match(line)), None)
    if header is None:
        raise Refusal(f"{rel}: no shipped table whose header row starts with '| item | version |'")
    last = header
    for i in range(header + 1, len(lines)):
        if lines[i].lstrip().startswith("|"):
            last = i
            continue
        break
    if last == header:
        raise Refusal(f"{rel}: the shipped table has no rows under its header")
    row = f"| {item} | {new_version} | {shape} | ${cost} |\n"
    new_lines = lines[:last + 1] + [row] + lines[last + 1:]
    return Edit(path=path, rel=rel, old=text, new="".join(new_lines))


# --- io ----------------------------------------------------------------------


def read_text(path: Path, rel: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Refusal(f"{rel}: file not found at {path}") from exc


def write_atomic(path: Path, text: str) -> None:
    directory = path.parent
    handle, tmp_name = tempfile.mkstemp(dir=directory, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def diff_of(edit: Edit) -> str:
    return "".join(
        difflib.unified_diff(
            edit.old.splitlines(keepends=True),
            edit.new.splitlines(keepends=True),
            fromfile=f"a/{edit.rel}",
            tofile=f"b/{edit.rel}",
        )
    )


# --- cli ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release.py",
        description="Bump the version, stub a receipt, and append the roadmap row.",
    )
    parser.add_argument("version", help="the new version, X.Y.Z")
    parser.add_argument("--item", help='the roadmap item, e.g. "E10 planner lanes"')
    parser.add_argument("--cost", help="fleet spend for the item, e.g. 13.60")
    parser.add_argument("--shape", default=DEFAULT_SHAPE,
                        help=f"shape note (default: {DEFAULT_SHAPE!r})")
    parser.add_argument("--roadmap", help="roadmap file (default: the newest docs/ROADMAP-*.md)")
    parser.add_argument("--date", help="receipt date, YYYY-MM-DD (default: today)")
    parser.add_argument("--no-receipt", action="store_true",
                        help="skip the receipt stub and the roadmap row")
    parser.add_argument("--root", default=None, help="repository root (default: the script's repo)")
    parser.add_argument("--dry-run", action="store_true", help="print diffs and write nothing")
    return parser


def normalize_cost(raw: str) -> str:
    cost = raw.strip().lstrip("$").replace(",", "")
    if not re.match(r"^\d+(\.\d+)?$", cost):
        raise Refusal(f"--cost is not a number: {raw!r}")
    return cost


def plan(args: argparse.Namespace) -> tuple[list[Edit], str]:
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    new_version = args.version.strip()
    parse_version(new_version, "the new version")

    edits, current = bump_versions(root, new_version)

    if not args.no_receipt:
        if not args.item or not args.cost:
            raise Refusal("--item and --cost are required unless --no-receipt is given")
        item = args.item.strip()
        if not item:
            raise Refusal("--item is empty")
        cost = normalize_cost(args.cost)
        if args.date is not None and not _DATE_RE.match(args.date):
            raise Refusal(f"--date is not YYYY-MM-DD: {args.date!r}")
        date = args.date or _dt.date.today().isoformat()
        roadmap = args.roadmap or default_roadmap(root)
        edits.append(receipt_edit(root, new_version, item, cost, args.shape, date))
        edits.append(roadmap_edit(root, roadmap, new_version, item, cost, args.shape))

    return edits, current


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        edits, current = plan(args)
    except Refusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for edit in edits:
            sys.stdout.write(diff_of(edit))
        print(f"dry run: {current} -> {args.version}, {len(edits)} files, nothing written")
        return 0

    for edit in edits:
        write_atomic(edit.path, edit.new)
    print(f"released {current} -> {args.version}, {len(edits)} files written")
    print("git add " + " ".join(edit.rel for edit in edits))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # unexpected: report it, never swallow it
        print(f"error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
