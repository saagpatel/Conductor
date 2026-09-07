"""F1: reviewer verdicts and fix dispositions on the ledger.

A review lane narrates before its verdict, so tallying its whole answer
against `NO_FINDINGS` counts narration as a finding. A fix lane's decision
on each reported item -- fixed, refused, already, wording -- is otherwise
prose with no receipt. `verdicts.review_verdict` and
`verdicts.fix_dispositions` parse each from the final marker line and the
`DISPOSITION:` lines respectively; `mission.py` records both on the lane
receipt, and `report.py` tallies them across missions.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from conductor import runner as runner_mod
from conductor.fleets import Spec
from conductor.mission import LaneResult, mission_from_dict, run_mission
from conductor.verdicts import dispositions_malformed, fix_dispositions, review_verdict

# --- verdicts.review_verdict -------------------------------------------------


def test_review_verdict_ignores_narration_before_no_findings():
    answer = "Looked at every hunk, nothing stood out.\n\nNO_FINDINGS"
    assert review_verdict(answer) == {"verdict": "no_findings", "findings": 0}


def test_review_verdict_exact_no_findings_with_no_narration():
    assert review_verdict("NO_FINDINGS") == {"verdict": "no_findings", "findings": 0}


def test_review_verdict_findings_zero_is_distinct_from_no_findings():
    answer = "1. file.py:12: numbered but withdrawn on reflection.\n\nFINDINGS: 0"
    assert review_verdict(answer) == {"verdict": "findings", "findings": 0}


def test_review_verdict_reads_the_findings_count():
    answer = "1. a.py:1: off by one\n2. b.py:9: wrong sign\n\nFINDINGS: 2"
    assert review_verdict(answer) == {"verdict": "findings", "findings": 2}


def test_review_verdict_with_no_final_marker_is_unparsed():
    answer = "file.py:12: off-by-one, no consequence stated"
    assert review_verdict(answer) == {"verdict": "unparsed", "findings": None}


def test_review_verdict_ignores_trailing_whitespace_and_code_fence():
    assert review_verdict("NO_FINDINGS\n\n  \n") == {"verdict": "no_findings", "findings": 0}
    assert review_verdict("```\nFINDINGS: 3\n```\n") == {"verdict": "findings", "findings": 3}


def test_review_verdict_findings_n_is_case_sensitive_and_exact():
    assert review_verdict("findings: 1") == {"verdict": "unparsed", "findings": None}
    assert review_verdict("FINDINGS: 1 (roughly)") == {"verdict": "unparsed", "findings": None}


# --- verdicts.fix_dispositions and dispositions_malformed --------------------


def test_fix_dispositions_parses_every_line_in_order():
    answer = (
        "Fixed the first, rejected the second.\n"
        "DISPOSITION: review-gemini 1 fixed: added the missing bounds check\n"
        "some other prose in between\n"
        "DISPOSITION: review-grok 2 refused: the report misread the guard clause\n"
    )
    assert fix_dispositions(answer) == [
        {
            "lane": "review-gemini",
            "index": 1,
            "disposition": "fixed",
            "reason": "added the missing bounds check",
        },
        {
            "lane": "review-grok",
            "index": 2,
            "disposition": "refused",
            "reason": "the report misread the guard clause",
        },
    ]
    assert dispositions_malformed(answer) == 0


def test_fix_dispositions_skips_and_counts_a_malformed_line():
    answer = (
        "DISPOSITION: review-gemini 1 fixed: ok\n"
        "DISPOSITION: review-grok not-a-number wording: missing the index\n"
        "DISPOSITION: review-grok 2 already: was already handled upstream\n"
    )
    parsed = fix_dispositions(answer)
    assert [item["disposition"] for item in parsed] == ["fixed", "already"]
    assert dispositions_malformed(answer) == 1


def test_fix_dispositions_empty_when_no_marker_present():
    assert fix_dispositions("NO_CHANGES") == []
    assert dispositions_malformed("NO_CHANGES") == 0


# --- LaneResult.from_dict: old receipts predate the new keys -----------------


def test_lane_result_from_dict_defaults_review_and_dispositions_to_none():
    raw = {"name": "review-gemini", "ok": True, "stage": "review"}
    lane = LaneResult.from_dict(raw)
    assert lane.review is None
    assert lane.dispositions is None
    assert lane.dispositions_malformed is None
    # And a receipt written by the new code round-trips through the same path.
    lane.review = {"verdict": "findings", "findings": 2}
    lane.dispositions = [
        {"lane": "review-gemini", "index": 1, "disposition": "fixed", "reason": "x"}
    ]
    lane.dispositions_malformed = 0
    assert LaneResult.from_dict(lane.to_dict()) == lane


# --- mission.py: settle() computes both from the lane's own answer ----------


def claude_envelope(answer: str) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": answer,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


def antigravity_envelope(answer: str) -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": answer,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    )


REVIEW_ANSWER = "1. a.py:3: off-by-one\n2. b.py:8: wrong sign\n\nFINDINGS: 2"
FIX_ANSWER = (
    "DISPOSITION: review 1 fixed: corrected the loop bound\n"
    "DISPOSITION: review 2 refused: b.py:8 was already correct\n"
    "DISPOSITION: review garbled wording: missing the index\n"
)


def _build_argv(spec: Spec) -> list[str]:
    if spec.prompt == "REVIEW":
        return ["sh", "-c", f"printf %s {shlex.quote(antigravity_envelope(REVIEW_ANSWER))}"]
    return ["sh", "-c", f"printf %s {shlex.quote(claude_envelope(FIX_ANSWER))}"]


def test_mission_settle_records_review_verdict_and_fix_dispositions(
    repo, home, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner_mod, "build_argv", _build_argv)
    raw = {
        "cwd": str(repo),
        "concurrency": 2,
        "lanes": [
            {
                "name": "review",
                "fleet": "antigravity",
                "stage": "review",
                "mode": "read",
                "prompt": "REVIEW",
            },
            {
                "name": "fix",
                "fleet": "claude",
                "stage": "fix",
                "mode": "write",
                "prompt": "FIX",
            },
        ],
    }
    result = run_mission(mission_from_dict(raw, base_dir=tmp_path), home=home)
    by_name = {lane["name"]: lane for lane in result.lanes}

    assert by_name["review"]["review"] == {"verdict": "findings", "findings": 2}
    assert by_name["fix"]["dispositions"] == [
        {
            "lane": "review",
            "index": 1,
            "disposition": "fixed",
            "reason": "corrected the loop bound",
        },
        {
            "lane": "review",
            "index": 2,
            "disposition": "refused",
            "reason": "b.py:8 was already correct",
        },
    ]
    assert by_name["fix"]["dispositions_malformed"] == 1

    receipt = json.loads(Path(result.mission_dir, "lanes", "review.json").read_text())
    assert receipt["review"] == {"verdict": "findings", "findings": 2}
    fix_receipt = json.loads(Path(result.mission_dir, "lanes", "fix.json").read_text())
    assert fix_receipt["dispositions_malformed"] == 1

    report = Path(result.report_path).read_text()
    assert "2 findings" in report
    assert "1 fixed, 1 refused" in report
