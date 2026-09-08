"""Git's LF-delimited records must preserve Unicode separators inside paths."""
from __future__ import annotations

import pytest

from conductor.collisions import merge_conflicts, overlap, touched_files


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_real_git_unicode_separator_paths(repo, git_out, separator):
    git_out(repo, "config", "core.quotePath", "false")
    name = f"before{separator}after.txt"
    git_out(repo, "checkout", "-b", "left")
    (repo / name).write_text("left\n")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", "left")
    left = git_out(repo, "rev-parse", "HEAD")
    patch = git_out(repo, "diff", "main", "left")
    assert touched_files(patch) == [name]
    assert overlap({"left": patch, "right": patch})["hotspots"] == [name]

    git_out(repo, "checkout", "-b", "right", "main")
    (repo / name).write_text("right\n")
    git_out(repo, "add", "-A")
    git_out(repo, "commit", "-qm", "right")
    right = git_out(repo, "rev-parse", "HEAD")
    conflicts = merge_conflicts(str(repo), {"left": left, "right": right})
    assert conflicts["pairs"] == [
        {"lanes": ["left", "right"], "conflicts": [name]}
    ]
    assert conflicts["files"] == {name: [["left", "right"]]}
