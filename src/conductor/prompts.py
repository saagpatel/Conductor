"""E17: a version id for every prompt conductor authors itself.

Conductor writes its own prompt text in five places: the collate and resolve
defaults and the rank contract in `mission.py`, the verdict checklist
contract in `verdicts.py`, and the Shape A texts in `shape.py` -- the build
wrapper, six review/fix prompts (Gemini, Grok, Grok read-only, Opus,
adversarial, and fix), and the shared prefix. None of that text carried a version:
an edit to any of it changed what every future mission sends with no trace
on a receipt. `prompt_versions()` fingerprints each one -- the first 12 hex
characters of the sha256 of its text, rendered with a fixed sample input for
the two that take one -- so an edit moves the id with no hand bump.

Fragments in `shape.py` (REVIEW_TAIL, GATE_BLOCK, the `*_GATE_RUN` sentences,
FIX_PROMPT_OPUS_BLOCK, FIX_PROMPT_ADVERSARIAL_BLOCK, FOLLOWON_FIX_OPENING,
EVIDENCE_* and DELIVERABLE_* notes) are not catalogued on their own: they
are folded into a fingerprinted constant, except FOLLOWON_FIX_OPENING,
which rewrites the already-fingerprinted FIX_PROMPT opening for salvage.
An edit to that opening does not move `shape_fix`. No other complete
lane prompt in `shape.py` is missing from the map below.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import mission as mission_mod
from . import shape as shape_mod
from . import verdicts as verdicts_mod

# Fixed sample inputs, used only to fingerprint the *code* that renders a
# contract or a prefix, never a real mission's own lane names or repo.
_SAMPLE_CRITERIA = [
    verdicts_mod.Criterion("correct", "Does the change implement every requirement?"),
    verdicts_mod.Criterion("tested", "Do focused tests pin the changed behavior?"),
]
_SAMPLE_LANE_NAMES = ["lane-a", "lane-b"]
_SAMPLE_REPO = Path("sample-repo")


def _id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def prompt_versions() -> dict[str, str]:
    """Name -> stable version id, one per conductor-authored prompt text."""
    return {
        "collate_default": _id(mission_mod.DEFAULT_COLLATE_INSTRUCTIONS),
        "resolve_default": _id(mission_mod.DEFAULT_RESOLVE_INSTRUCTIONS),
        "rank_contract": _id(mission_mod._rank_contract(_SAMPLE_LANE_NAMES)),
        "checklist_contract": _id(verdicts_mod.checklist_contract(_SAMPLE_CRITERIA)),
        "shape_gemini_review": _id(shape_mod.GEMINI_REVIEW_PROMPT),
        "shape_grok_review": _id(shape_mod.GROK_REVIEW_PROMPT),
        "shape_grok_read_only": _id(shape_mod.GROK_READ_ONLY_PROMPT),
        "shape_opus_review": _id(shape_mod.OPUS_REVIEW_PROMPT),
        "shape_adversarial": _id(shape_mod.ADVERSARIAL_PROMPT),
        "shape_fix": _id(shape_mod.FIX_PROMPT),
        "shape_build": _id(shape_mod.BUILD_PROMPT),
        "shape_prefix": _id(shape_mod._prefix(_SAMPLE_REPO, None)),
    }
