"""Section resolver: headings move; the lookup must not silently go empty."""

from __future__ import annotations

from pathlib import Path

import pytest

from docs import doc_section


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_doc_section_finds_a_heading_in_readme(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "# Project\n\nintro\n\n## Current support\n\nmatrix lives here\n\n## Why\n\nwhy body\n",
    )
    body = doc_section("Current support", root=tmp_path)
    assert "matrix lives here" in body
    assert "why body" not in body


def test_doc_section_finds_a_heading_in_a_reference_page(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Project\n\nfront page\n")
    _write(
        tmp_path,
        "docs/reference/salvage.md",
        "# Salvage\n\nA kept worktree.\n\n## Rule 3\n\ngate the tip\n",
    )
    body = doc_section("### Salvage", root=tmp_path)
    assert "A kept worktree." in body
    assert "gate the tip" in body


def test_doc_section_refuses_a_duplicate_heading(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Project\n\n## Taint\n\nfront\n")
    _write(tmp_path, "docs/reference/taint.md", "# Taint\n\nreference\n")
    with pytest.raises(AssertionError, match="appears 2 times") as error:
        doc_section("Taint", root=tmp_path)
    message = str(error.value)
    assert "Taint" in message
    assert "README.md" in message
    assert "docs/reference/taint.md" in message


def test_doc_section_refuses_a_missing_heading(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Project\n\n## Current support\n\nhere\n")
    _write(tmp_path, "docs/reference/salvage.md", "# Salvage\n\nkept\n")
    with pytest.raises(AssertionError, match="not found") as error:
        doc_section("No such heading", root=tmp_path)
    message = str(error.value)
    assert "No such heading" in message
    assert "README.md" in message
    assert "docs/reference/salvage.md" in message


def test_doc_section_stops_at_a_higher_level_heading(tmp_path: Path) -> None:
    """`.split('\\n## ', 1)` misses a following `#` and would swallow Other."""
    _write(
        tmp_path,
        "README.md",
        (
            "# Project\n\n"
            "## Child\n\n"
            "child-only\n\n"
            "# Other\n\n"
            "must-not-appear\n"
        ),
    )
    body = doc_section("## Child", root=tmp_path)
    assert "child-only" in body
    assert "must-not-appear" not in body
