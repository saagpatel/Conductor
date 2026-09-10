"""Filesystem paths shared across conductor commands."""

from __future__ import annotations

import os
from pathlib import Path


def conductor_home() -> Path:
    return Path(os.environ.get("CONDUCTOR_HOME", Path.home() / ".conductor"))
