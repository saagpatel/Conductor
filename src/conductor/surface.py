"""Pin the files that decide whether a gate passes.

Patterns are Git pathspec globs. They are passed as ``:(glob)pattern`` so
``**`` has Git's recursive-glob meaning instead of the shell's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .verify import git_run

DEFAULT_TEST_SURFACE = (
    "tests/**",
    "test/**",
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*.test.*",
    "**/*.spec.*",
    "**/conftest.py",
    "**/pytest.ini",
    "**/tox.ini",
    "**/setup.cfg",
    "**/pyproject.toml",
    "**/Makefile",
    "**/noxfile.py",
    ".github/workflows/**",
    ".gitlab-ci.yml",
    "**/package.json",
    "**/jest.config.*",
    "**/vitest.config.*",
    "**/.pre-commit-config.yaml",
)

# Byte-code and tool caches under a test directory are written by the gate
# itself, so counting them would make every pytest run "touch" the surface
# and pay the clean re-run for nothing. Nothing a gate reads lives in them.
CACHE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".hypothesis"}
)
CACHE_SUFFIXES = (".pyc", ".pyo")


def _is_cache_artifact(name: str) -> bool:
    parts = name.split("/")
    return any(part in CACHE_DIRS for part in parts[:-1]) or name.endswith(CACHE_SUFFIXES)


@dataclass(frozen=True)
class Surface:
    patterns: list[str]
    files: dict[str, str]
    digest: str

    def diff(self, other: Surface) -> list[str]:
        """Paths that appeared, vanished, or no longer have the same bytes."""
        return sorted(
            path
            for path in self.files.keys() | other.files.keys()
            if self.files.get(path) != other.files.get(path)
        )


def missing_surface(surface: Surface) -> Surface:
    """The observable surface after the fleet removed its working tree."""
    files = {name: "missing" for name in surface.files}
    return Surface(patterns=list(surface.patterns), files=files, digest=_digest(files))


def _digest(files: dict[str, str]) -> str:
    digest = sha256()
    for name, content_hash in sorted(files.items()):
        digest.update(name.encode(errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(content_hash.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def test_surface(cwd: str | Path, patterns: list[str] | tuple[str, ...] | None = None) -> Surface:
    """Hash the tracked and untracked files that can change a gate's verdict."""
    selected = list(DEFAULT_TEST_SURFACE if patterns is None else patterns)
    top = git_run(cwd, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise ValueError("test surface requires a git repository")
    root = Path(top.stdout.strip())
    if not selected:
        return Surface(patterns=[], files={}, digest=_digest({}))
    pathspecs = [f":(glob){pattern}" for pattern in selected]

    tracked = git_run(root, "ls-files", "-z", "--", *pathspecs)
    if tracked.returncode != 0:
        raise ValueError(tracked.stderr.strip() or "git ls-files failed for the test surface")
    names = {name for name in tracked.stdout.split("\0") if name}

    # Porcelain proves the working tree can be inspected before the second
    # Git query applies the pathspecs. `ls-files --others` deliberately has no
    # exclude-standard filter: an ignored conftest still changes pytest, and
    # hiding it in .gitignore must not hide it from the pinned surface.
    status = git_run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if status.returncode != 0:
        raise ValueError(status.stderr.strip() or "git status failed for the test surface")
    matching_untracked = git_run(
        root,
        "ls-files",
        "-z",
        "--others",
        "--",
        *pathspecs,
    )
    if matching_untracked.returncode != 0:
        raise ValueError(
            matching_untracked.stderr.strip() or "git ls-files failed for untracked test files"
        )
    names.update(name for name in matching_untracked.stdout.split("\0") if name)

    files: dict[str, str] = {}
    for name in sorted(names):
        if _is_cache_artifact(name):
            continue
        try:
            path = root / name
            if path.is_symlink():
                content_hash = sha256(os.readlink(path).encode(errors="surrogateescape"))
            else:
                content_hash = sha256()
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        content_hash.update(chunk)
            files[name] = content_hash.hexdigest()
        except OSError:
            files[name] = "missing"
    return Surface(patterns=selected, files=files, digest=_digest(files))
