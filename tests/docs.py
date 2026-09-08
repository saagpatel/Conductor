"""Resolve a documentation section by heading across README and the reference tree.

Tests pin claims in the docs against the code. Those assertions used to
`.split` a heading out of `README.md`; after the reference split the same
heading may live in `docs/reference/` instead. A miss or a duplicate must
raise -- an empty body would turn every one of those tests into a test that
asserts nothing.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def doc_section(heading: str, *, root: Path | None = None) -> str:
    """The body of the named section, wherever it now lives."""
    base = _REPO_ROOT if root is None else root
    needle = _strip_hashes(heading)
    if not needle:
        raise AssertionError(f"heading {heading!r} is empty after stripping '#'")
    files = _doc_files(base)
    searched = [str(path.relative_to(base)) for path in files]
    matches: list[tuple[Path, str, str]] = []
    for path in files:
        text = path.read_text()
        for start, level, title in _headings(text):
            if not _title_matches(title, needle):
                continue
            body = _body_until(text, start, level)
            matches.append((path, title, body))
    if not matches:
        raise AssertionError(
            f"heading {heading!r} not found in {searched}"
        )
    exact = [item for item in matches if item[1] == needle]
    chosen = exact or matches
    if len(chosen) != 1:
        locations = [
            f"{path.relative_to(base)} ({title!r})" for path, title, _body in chosen
        ]
        raise AssertionError(
            f"heading {heading!r} appears {len(chosen)} times in {searched}: "
            + ", ".join(locations)
        )
    return chosen[0][2]


def _doc_files(root: Path) -> list[Path]:
    files = [root / "README.md"]
    reference = root / "docs" / "reference"
    if reference.is_dir():
        files.extend(sorted(path for path in reference.glob("*.md") if path.is_file()))
    return [path for path in files if path.is_file()]


def _strip_hashes(heading: str) -> str:
    text = heading.strip()
    while text.startswith("#"):
        text = text[1:]
    return text.strip()


def _title_matches(title: str, needle: str) -> bool:
    if title == needle:
        return True
    if not title.startswith(needle):
        return False
    if len(title) == len(needle):
        return True
    return title[len(needle)] in " :("


def _headings(text: str) -> list[tuple[int, int, str]]:
    """(start offset, level, title) for ATX headings outside fenced code."""
    found: list[tuple[int, int, str]] = []
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            hashes, title = _atx(line)
            if hashes is not None and title is not None:
                found.append((offset, hashes, title))
        offset += len(line)
    return found


def _atx(line: str) -> tuple[int | None, str | None]:
    if not line.startswith("#"):
        return None, None
    i = 0
    while i < len(line) and line[i] == "#":
        i += 1
    if i > 6 or i >= len(line) or line[i] not in " \t":
        return None, None
    title = line[i:].strip()
    if title.endswith("#"):
        title = title.rstrip("#").rstrip()
    if not title:
        return None, None
    return i, title


def _body_until(text: str, start: int, level: int) -> str:
    newline = text.find("\n", start)
    body_start = len(text) if newline < 0 else newline + 1
    body_end = len(text)
    for later, later_level, _title in _headings(text):
        if later <= start:
            continue
        if later_level <= level:
            body_end = later
            break
    return text[body_start:body_end]
