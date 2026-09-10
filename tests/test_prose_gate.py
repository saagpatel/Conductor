"""scripts/prose_gate.py: the checks an anti-slop pass on a document must not break."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prose_gate.py"
_spec = importlib.util.spec_from_file_location("prose_gate", _SCRIPT)
assert _spec is not None and _spec.loader is not None
prose_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prose_gate)


def test_prose_dash_and_filler_are_reported() -> None:
    found = prose_gate.problems("one — two\nwe simply wait\n")
    assert found == ["line 1: dash U+2014", "line 2: filler 'simply'"]


def test_fenced_code_is_skipped() -> None:
    # A quoted tool result is a receipt; the gate must not demand it be rewritten.
    text = "prose\n```\ndenied — nobody can answer, just wait\n```\nafter\n"
    assert prose_gate.problems(text) == []


def test_dash_after_a_fence_closes_is_still_reported() -> None:
    text = "```\n— quoted\n```\nback — to prose\n"
    assert prose_gate.problems(text) == ["line 4: dash U+2014"]


def test_table_rows_must_match_the_header() -> None:
    text = "| a | b |\n|---|---|\n| 1 |\n"
    assert prose_gate.problems(text) == ["line 3: table row has 1 cells, header has 2"]


def test_a_fenced_block_of_pipes_is_not_read_as_a_table() -> None:
    # 2026-09-08 review: the table loop kept its own line walk and never
    # tracked fences, so a quoted shell pipeline or an ASCII table inside a
    # fence was counted as markdown table rows -- the exact rewriting of a
    # receipt the module docstring promises not to ask for.
    text = "prose\n```\n| a | b | c |\n| one |\n```\nafter\n"
    assert prose_gate.problems(text) == []


def test_a_real_table_after_a_fenced_one_is_still_checked() -> None:
    text = "```\n| x |\n```\n| a | b |\n|---|---|\n| 1 |\n"
    assert prose_gate.problems(text) == ["line 6: table row has 1 cells, header has 2"]
