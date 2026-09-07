"""Tests for scripts/release.py, the lead-only release helper.

Every fixture is built in tmp_path; the real repository files are never read.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"


def _load_release():
    spec = importlib.util.spec_from_file_location("conductor_release_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release()


PYPROJECT = """\
[project]
name = "conductor"
version = "{v}"
requires-python = ">=3.12"

[tool.ruff]
version = "9.9.9"
line-length = 100
"""

INIT = '''\
"""fixture package."""

__version__ = "{v}"

__all__ = ["__version__"]
'''

LOCK = """\
version = 1
requires-python = ">=3.12"

[[package]]
name = "conductor"
version = "{v}"
source = {{ editable = "." }}

[[package]]
name = "pytest"
version = "8.0.0"
source = {{ registry = "https://pypi.org/simple" }}
"""

RECEIPTS = """\
# Reset log

Some prose before the receipts.

## Receipt 2026-09-07: E10 shipped as v0.47.0 ($13.60, Shape A via the launcher)

The newest receipt.

## Receipt 2026-09-06: E19 shipped as v0.45.0 ($10.45, Shape A via the launcher)

An older receipt.
"""

ROADMAP = """\
# Roadmap

## Shipped since this was written

| item | version | shape | cost |
|---|---|---|---|
| E11 ledger report | 0.27.0 | Shape A | $7.69 |
| E1 deliverable verdicts | 0.28.0 | Shape A | $11.94 |

## Dropped

Prose after the table.
"""

ROADMAP_NO_TABLE = """\
# Roadmap

| item | change | cap arithmetic |
|---|---|---|
| Sonnet 5 | none | rule 2 stands |
"""


def make_repo(tmp_path: Path, *, version: str = "0.47.0", init_version: str | None = None,
              roadmap: str = ROADMAP) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "conductor").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "pyproject.toml").write_text(PYPROJECT.format(v=version), encoding="utf-8")
    (root / "src" / "conductor" / "__init__.py").write_text(
        INIT.format(v=init_version or version), encoding="utf-8"
    )
    (root / "uv.lock").write_text(LOCK.format(v=version), encoding="utf-8")
    (root / "docs" / "RESET-2026-09.md").write_text(RECEIPTS, encoding="utf-8")
    (root / "docs" / "ROADMAP-2026-10.md").write_text(roadmap, encoding="utf-8")
    return root


def run(root: Path, *args: str) -> int:
    return release.main([*args, "--root", str(root)])


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def ok_args(version: str = "0.48.0") -> list[str]:
    return [version, "--item", "E10 planner lanes", "--cost", "13.60", "--date", "2026-09-08"]


def test_bump_succeeds_and_all_three_versions_agree(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run(root, *ok_args()) == 0

    assert 'version = "0.48.0"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "0.48.0"' in (
        root / "src" / "conductor" / "__init__.py"
    ).read_text(encoding="utf-8")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "conductor"\nversion = "0.48.0"' in lock
    # only the conductor block moved
    assert 'name = "pytest"\nversion = "8.0.0"' in lock
    assert lock.splitlines()[0] == "version = 1"
    # the unrelated [tool.ruff] version is untouched
    assert 'version = "9.9.9"' in (root / "pyproject.toml").read_text(encoding="utf-8")


def test_non_increasing_version_is_refused(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    before = snapshot(root)
    assert run(root, *ok_args("0.47.0")) == 2
    assert run(root, *ok_args("0.46.9")) == 2
    assert snapshot(root) == before


def test_disagreeing_versions_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = make_repo(tmp_path, version="0.47.0", init_version="0.46.0")
    before = snapshot(root)
    assert run(root, *ok_args()) == 2
    assert "disagree" in capsys.readouterr().err
    assert snapshot(root) == before


def test_receipt_stub_lands_before_the_first_receipt(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run(root, *ok_args()) == 0
    lines = (root / "docs" / "RESET-2026-09.md").read_text(encoding="utf-8").splitlines()
    headings = [i for i, line in enumerate(lines) if line.startswith("## Receipt ")]
    assert lines[headings[0]] == (
        "## Receipt 2026-09-08: E10 planner lanes shipped as v0.48.0 "
        "($13.60, Shape A via the launcher)"
    )
    assert lines[headings[0] + 1] == ""
    assert lines[headings[0] + 2] == "TODO: write the receipt."
    assert lines[headings[1]].startswith("## Receipt 2026-09-07:")


def test_table_row_appended_after_the_last_row(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run(root, *ok_args()) == 0
    lines = (root / "docs" / "ROADMAP-2026-10.md").read_text(encoding="utf-8").splitlines()
    last_row = lines.index("| E1 deliverable verdicts | 0.28.0 | Shape A | $11.94 |")
    assert lines[last_row + 1] == (
        "| E10 planner lanes | 0.48.0 | Shape A via the launcher | $13.60 |"
    )
    assert lines[last_row + 2] == ""
    assert "## Dropped" in lines


def test_missing_table_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    root = make_repo(tmp_path, roadmap=ROADMAP_NO_TABLE)
    before = snapshot(root)
    assert run(root, *ok_args()) == 2
    assert "| item | version |" in capsys.readouterr().err
    assert snapshot(root) == before


def test_dry_run_writes_nothing_and_prints_five_diffs(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = make_repo(tmp_path)
    before = snapshot(root)
    assert run(root, *ok_args(), "--dry-run") == 0
    out = capsys.readouterr().out
    assert snapshot(root) == before
    for rel in ("pyproject.toml", "src/conductor/__init__.py", "uv.lock",
                "docs/RESET-2026-09.md", "docs/ROADMAP-2026-10.md"):
        assert f"+++ b/{rel}" in out
    assert "nothing written" in out


def test_no_receipt_touches_only_the_version_files(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run(root, "0.48.0", "--no-receipt") == 0
    assert (root / "docs" / "RESET-2026-09.md").read_text(encoding="utf-8") == RECEIPTS
    assert (root / "docs" / "ROADMAP-2026-10.md").read_text(encoding="utf-8") == ROADMAP


def test_item_and_cost_are_required_without_no_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    root = make_repo(tmp_path)
    assert run(root, "0.48.0") == 2
    assert "--item and --cost" in capsys.readouterr().err


def test_default_roadmap_is_the_newest_by_name(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "docs" / "ROADMAP-2026-09.md").write_text(ROADMAP, encoding="utf-8")
    assert run(root, *ok_args()) == 0
    newest = (root / "docs" / "ROADMAP-2026-10.md").read_text(encoding="utf-8")
    older = (root / "docs" / "ROADMAP-2026-09.md").read_text(encoding="utf-8")
    assert "E10 planner lanes" in newest
    assert older == ROADMAP


def test_explicit_roadmap_is_used(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "docs" / "ROADMAP-2026-11.md").write_text(ROADMAP, encoding="utf-8")
    assert run(root, *ok_args(), "--roadmap", "docs/ROADMAP-2026-10.md") == 0
    assert "E10 planner lanes" in (
        root / "docs" / "ROADMAP-2026-10.md"
    ).read_text(encoding="utf-8")
    assert (root / "docs" / "ROADMAP-2026-11.md").read_text(encoding="utf-8") == ROADMAP


def test_bad_version_string_is_refused(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    before = snapshot(root)
    assert run(root, "0.48", "--no-receipt") == 2
    assert snapshot(root) == before


def test_script_runs_as_a_subprocess(tmp_path: Path) -> None:
    import subprocess

    root = make_repo(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *ok_args(), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "git add pyproject.toml src/conductor/__init__.py uv.lock" in proc.stdout
    assert "docs/RESET-2026-09.md docs/ROADMAP-2026-10.md" in proc.stdout
