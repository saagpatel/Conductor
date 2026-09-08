"""Shape A prompt and schema defects: adversarial dispositions, fingerprints,
the no-quota bar, ports on gate-running lanes, and build_prompt through with_gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conductor import shape
from conductor.prompts import prompt_versions


def _spec(tmp_path: Path) -> Path:
    spec = tmp_path / "specs" / "widget.md"
    spec.parent.mkdir()
    spec.write_text("# widget\n\n1. one\n2. two\n")
    return spec


def _lanes(raw: dict) -> dict[str, dict]:
    return {lane["name"]: lane for lane in raw["lanes"]}


# --- item 1: adversarial lane in the fix prompt and dispositions schema ------


def test_adversarial_mission_tells_the_fixer_about_that_lane(repo, tmp_path):
    """`--adversarial` used to append the report block and leave FIX_PROMPT
    saying both-reviews-NO_FINDINGS means change nothing, with no legal
    `lane` value for the test the mission just paid for."""
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(1, 1, adversarial=True)
    raw = shape.shape_a(
        spec=spec, repo=repo, test="true", caps=caps, adversarial=True
    )
    prompt = _lanes(raw)["fix"]["prompt"]
    assert '"lane": "review-gemini", "review-grok", or "adversarial"' in prompt
    assert '"lane": "review-gemini" or "review-grok"' not in prompt
    assert (
        "If both reviews say NO_FINDINGS and the adversarial lane says NO_FINDINGS"
        in prompt
    )
    assert "If both reviews say NO_FINDINGS or nothing reproduces" not in prompt
    assert "the adversarial lane said NO_FINDINGS" in prompt
    assert 'lane "adversarial" and index 1' in prompt
    assert "that lane writes at most one test" in prompt
    assert "plus the adversarial lane's test when it wrote one" in prompt

    written = shape.dispositions_schema(adversarial=True)
    description = written["properties"]["dispositions"]["description"]
    assert '"lane": "review-gemini", "review-grok", or "adversarial"' in description
    assert "review-opus" not in description
    assert "the adversarial lane said NO_FINDINGS" in description
    assert 'lane "adversarial" and index 1' in description
    on_disk = json.loads(
        shape.write_dispositions_schema(tmp_path / "adv", adversarial=True).read_text()
    )
    assert on_disk == written

    # Default two-reviewer schema is unchanged when the flag is off.
    two = shape.dispositions_schema()
    assert two == shape.DISPOSITIONS_SCHEMA
    assert "adversarial" not in two["properties"]["dispositions"]["description"]


def test_adversarial_and_opus_review_compose_on_prompt_and_schema(repo, tmp_path):
    """The two rewrites must not fight: both flags together name every lane
    the fixer may dispose, including an adversarial-only finding."""
    spec = _spec(tmp_path)
    caps = shape.cap_arithmetic(1, 1, adversarial=True, opus_review=True)
    raw = shape.shape_a(
        spec=spec,
        repo=repo,
        test="true",
        caps=caps,
        adversarial=True,
        opus_review=True,
    )
    prompt = _lanes(raw)["fix"]["prompt"]
    assert (
        '{"lane": "review-gemini", "review-grok", "review-opus", or "adversarial", "index": '
        in prompt
    )
    assert "Three reviewers" in prompt
    assert "Two reviewers" not in prompt
    assert (
        "If every review says NO_FINDINGS and the adversarial lane says NO_FINDINGS"
        in prompt
    )
    assert "If both reviews say NO_FINDINGS" not in prompt
    assert 'lane "adversarial" and index 1' in prompt

    description = shape.dispositions_schema(
        opus_review=True, adversarial=True
    )["properties"]["dispositions"]["description"]
    assert (
        '{"lane": "review-gemini", "review-grok", "review-opus", or "adversarial", "index": '
        in description
    )
    assert "every review said NO_FINDINGS and the adversarial lane said NO_FINDINGS" in (
        description
    )
    assert "both reviews" not in description
    # Each flag alone still does its own rewrite.
    opus_only = shape.dispositions_schema(opus_review=True)["properties"]["dispositions"][
        "description"
    ]
    assert "review-opus" in opus_only
    assert "adversarial" not in opus_only
    adv_only = shape.dispositions_schema(adversarial=True)["properties"]["dispositions"][
        "description"
    ]
    assert "adversarial" in adv_only
    assert "review-opus" not in adv_only


# --- item 2: every conductor-authored prompt is fingerprinted -----------------


def test_prompt_versions_fingerprints_the_adversarial_prompt_and_matches_the_inventory():
    """ADVERSARIAL_PROMPT was the one paid-lane prompt with no version id.
    The docstring's inventory is the function's keys: six review/fix texts,
    the build wrapper, and the prefix. Fragments (REVIEW_TAIL, FOLLOWON_FIX_OPENING,
    the insertion blocks) are not separate catalog entries."""
    versions = prompt_versions()
    expected_id = hashlib.sha256(shape.ADVERSARIAL_PROMPT.encode()).hexdigest()[:12]
    assert versions["shape_adversarial"] == expected_id
    assert set(k for k in versions if k.startswith("shape_")) == {
        "shape_gemini_review",
        "shape_grok_review",
        "shape_grok_read_only",
        "shape_opus_review",
        "shape_adversarial",
        "shape_fix",
        "shape_build",
        "shape_prefix",
    }
    assert versions["shape_build"] == hashlib.sha256(shape.BUILD_PROMPT.encode()).hexdigest()[
        :12
    ]


# --- item 3: adversarial prompt carries the same bar as the other reviews ---


def test_adversarial_prompt_states_the_consequence_threshold_and_asks_for_a_citation():
    """Rule 3 (consequence threshold, not adjectives) and rule 5 (cite or
    drop). `If you find` stays conditional; FINDING:/FINDINGS: N stay off
    this constant -- those markers belong to REVIEW_TAIL for F15."""
    text = shape.ADVERSARIAL_PROMPT
    assert "incorrect behavior, a test failure, or a misleading result" in text
    assert "including a spec item that is missing or only partly implemented" in text
    assert "Omit pure style and naming" in text
    assert "file and line" in text
    assert "If you find" in text
    assert "FINDING:" not in text
    assert "FINDINGS:" not in text
    assert "at least" not in text
    filled = shape.adversarial_prompt("true")
    assert "incorrect behavior, a test failure, or a misleading result" in filled
    assert "file and line" in filled
    # The other review constants still carry the same bar; this is reuse,
    # not a third phrasing.
    assert "incorrect behavior, a test failure, or a misleading result" in shape.REVIEW_TAIL
    assert "Omit pure style and naming" in shape.GROK_READ_ONLY_PROMPT


# --- item 4: every gate-running lane gets ports ------------------------------


def test_ports_reach_every_lane_that_runs_the_gate(repo, tmp_path):
    """`--ports N` is a count. dispatch claims N distinct free ports per
    spawn (bind + O_EXCL), so two gate-running lanes that overlap in time
    (adversarial and suite-running Grok, after build) get different numbers,
    never the same CONDUCTOR_PORT_1. Gemini, Opus, and read-only Grok do
    not run the gate and do not claim."""
    spec = _spec(tmp_path)
    default = _lanes(
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1),
            ports=2,
        )
    )
    assert default["build"]["ports"] == 2
    assert default["fix"]["ports"] == 2
    assert "ports" not in default["review-gemini"]
    assert "ports" not in default["review-grok"]

    overlapping = _lanes(
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1, grok_runs_suite=True, adversarial=True),
            ports=2,
            adversarial=True,
        )
    )
    assert overlapping["build"]["ports"] == 2
    assert overlapping["fix"]["ports"] == 2
    assert overlapping["adversarial"]["ports"] == 2
    assert overlapping["review-grok"]["ports"] == 2
    assert "ports" not in overlapping["review-gemini"]

    opus = _lanes(
        shape.shape_a(
            spec=spec,
            repo=repo,
            test="true",
            caps=shape.cap_arithmetic(1, 1, opus_review=True, grok_runs_suite=True),
            ports=1,
            opus_review=True,
        )
    )
    assert opus["build"]["ports"] == 1
    assert opus["fix"]["ports"] == 1
    assert opus["review-grok"]["ports"] == 1
    assert "ports" not in opus["review-opus"]
    assert "ports" not in opus["review-gemini"]


# --- item 5: build_prompt goes through with_gate -------------------------------


def test_build_prompt_with_no_gate_does_not_claim_there_is_one():
    """The other builders already strip an empty test. build_prompt used to
    emit an empty <gate> block and keep the instruction to run it."""
    assembled = shape.build_prompt("")
    assert "<gate>" not in assembled
    assert "{gate}" not in assembled
    assert "That is the gate the lead will run" not in assembled
    assert "Run it the same way" not in assembled
    assert "run the gate" not in assembled.lower()
    # A named gate still fills the block and keeps the run sentences.
    filled = shape.build_prompt("true")
    assert "<gate>\ntrue\n</gate>" in filled
    assert "That is the gate the lead will run" in filled
    # The other builders' empty-test and filled-gate behaviour is unchanged.
    assert "<gate>" not in shape.grok_review_prompt("")
    assert "<gate>\ntrue\n</gate>" in shape.grok_review_prompt("true")
    assert "<gate>" not in shape.fix_prompt("")
    assert "<gate>\ntrue\n</gate>" in shape.fix_prompt("true")
    assert "<gate>" not in shape.adversarial_prompt("")
    assert "<gate>\ntrue\n</gate>" in shape.adversarial_prompt("true")
