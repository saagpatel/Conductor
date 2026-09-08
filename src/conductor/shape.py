"""E5: the Shape A mission template and the cap arithmetic behind it.

Shape A is the measured default in AGENTS.md: Sonnet 5 builds at `hard`, Gemini 3.7
Flash and Grok 4.6 review cold and in parallel with the no-quota template, Sonnet fixes
on the build's resumed thread, and the lead judges on bytes. Before this module every
mission file was hand-written and its caps hand-sized, and mis-sized caps cost real
money five times on record (A3, B5, B2, C5, D1). The template lives here, under version
control, so a second repository adopts the shape in one command.

The caps follow AGENTS.md rules 2 and 10 and nothing else. `spec_items` and `modules`
are hand-counted inputs: conductor has no notion of a spec item or a module touched, and
this module must not grow a forecaster (that is E14). The launcher prints every term of
the arithmetic, not just the sum, because B2's $2 shortfall was a module undercount and
one printed number would reproduce it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import tempfile
from pathlib import Path

from . import ceiling as ceiling_mod
from .fleets import CAP_GRACE_CEILING_USD
from .runner import GATE_TIMEOUT
from .verify import git_run, run_tests

SHAPE_VERSION = "a-2026-09-06"

# Rule 2 and rule 10, as constants with the rule number beside each.
USD_PER_SPEC_ITEM = 1.0  # rule 2: about a dollar per spec item on Sonnet at hard
USD_SCHEDULER_TAX = 2.0  # rule 2: scheduler, runner wait loop, or resume
USD_PER_EXTRA_MODULE = 1.0  # rule 2: every module past the second
USD_CLAUDE_SUMMARY = 1.0  # rule 10: Claude's final summary costs, on every cap
USD_FIX_BASE = 2.0  # a fix lane is a one- or two-item spec on the resumed thread
USD_PER_FINDING = 1.0  # F6: a dollar per expected review finding on the fix cap
DEFAULT_FINDINGS = 4  # F6: the median Grok finding count on this repository
USD_ADVERSARIAL = 3.0  # E16: one test that fails on the current tree, plus room to look
USD_GEMINI_READ = 1.0  # rule 7: Gemini reads only on this shape
USD_GROK_READ = 1.5  # rule 7: Grok reading only
USD_GROK_SUITE = 2.0  # rule 7: Grok when it runs the suite
USD_OPUS_READ = 3.0  # F9 Shape C: Opus 5 reads cold; $2.40 to $2.76 per mission on the receipt
USD_MISSION_SLACK = 1.5  # room for a retry's partial spend before the mission budget sinks
USD_CLAUDE_GRACE = 0.25  # E24: default grace band on the build and fix (claude) lanes

# F6: --ceiling none writes this explicit null-bounds dict. An attended launch
# is watched, so the E9 spend ceiling follows --unattended at run time, not
# the shape launcher (operator decision 2026-09-07): a mission carrying this
# never checks the ceiling regardless of how it is later run.
CEILING_NONE: dict = {"per_hour_usd": None, "per_day_usd": None}

GATE_PREFLIGHT_PREFIX = "conductor-gate-preflight-"

REVIEW_TAIL = (
    "Report anything that could cause incorrect behavior, a test failure, or a misleading "
    "result, including a spec item that is missing or only partly implemented. Omit pure "
    "style and naming. Number each item. For each item: file and line, what goes wrong, one "
    "sentence of consequence, your confidence 1-10. Right before the final marker line "
    "below, write one line per numbered item in the exact shape FINDING: <n> <file>:<line> "
    "confidence <1-10> -- a review with no items writes none of these lines. End the reply "
    "with exactly one final line: NO_FINDINGS if there is nothing to report, or FINDINGS: N "
    "where N is the number of items you numbered above. Either answer is complete. Put the "
    "entire review in this reply; the reply is the only thing the next agent receives."
)

GEMINI_REVIEW_PROMPT = (
    "Review the change below against the spec. The change is applied in the current "
    "working directory; read whatever you need. Do not run the test suite; the lead runs "
    "it. Read the diff and the code it touches. Change nothing.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    "Based on the information above: " + REVIEW_TAIL
)

# Shared `<gate>` block, filled from the mission's `test` the same way BUILD_PROMPT
# fills `{gate}`. The three prompts that used to say "the gate named in the spec"
# never received that command: `{{mission.prompt}}` is the operator's spec.
GATE_BLOCK = "<gate>\n{gate}\n</gate>\n\n"

GROK_GATE_RUN = (
    " and, if you are able to, run the gate in the <gate> block. If you run the suite, "
    "pass --basetemp pointing to a directory under $TMPDIR so nothing is written inside "
    "this working tree"
)

ADVERSARIAL_GATE_RUN = (
    " Run the gate in the <gate> block before finishing. When the gate is pytest, pass "
    "--basetemp under $TMPDIR, and confirm your test is what fails."
)

FIX_GATE_RUN = (
    " Run the gate in the <gate> block before finishing. When the gate is pytest, pass "
    "--basetemp under $TMPDIR."
)

GROK_REVIEW_PROMPT = (
    "Review the change below against the spec. The change is applied in the current "
    "working directory; read whatever you need"
    + GROK_GATE_RUN
    + ". CHANGE NOTHING.\n\n"
    + GATE_BLOCK
    + "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    + REVIEW_TAIL.replace("Put the entire review", "Put the ENTIRE review")
)

# Own constant, not a replace of GROK_REVIEW_PROMPT: this lane must not be told
# to run a gate, and xAI's non-negotiables are capitalized (CHANGE NOTHING,
# ENTIRE, and on this repo DO NOT RUN THE TEST SUITE -- a Grok reviewer that
# runs the suite in its worktree fails the lane on bytes).
GROK_READ_ONLY_PROMPT = (
    "Review the change below against the spec. The change is applied in the current "
    "working directory; read whatever you need. DO NOT RUN THE TEST SUITE; the lead runs "
    "it. Read the diff and the code it touches. CHANGE NOTHING.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    + REVIEW_TAIL.replace("Put the entire review", "Put the ENTIRE review")
)

# F9 Shape C, a launcher option since Phase H: Opus 5 as a third cold reviewer
# beside Gemini and Grok. On the receipt (docs/research/2026-09-07-f9-shape-b-c.md)
# it reported nothing false, found six defects the pair missed, and caught
# three spec items the pair had passed as built, at about three times the
# pair's cost. Same no-quota tail as the other two; reads only, like Gemini.
OPUS_REVIEW_PROMPT = (
    "Review the change below against the spec. The change is applied in the current "
    "working directory; read whatever you need, including code the diff does not touch. "
    "Do not run the test suite; the lead runs it. Change nothing.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    + REVIEW_TAIL
)

ADVERSARIAL_PROMPT = (
    "The change below is applied in the current working directory, on its own branch. Read "
    "whatever you need.\n\n"
    + GATE_BLOCK
    + "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    "If you find a defect in the change against the spec, write one test that fails on the "
    "current tree because of it -- in a new or existing test file -- and say what it proves. "
    "Change nothing else: no source, no fix, just the test."
    + ADVERSARIAL_GATE_RUN
    + " If nothing meets that bar, change nothing and reply exactly: NO_FINDINGS. Either "
    "answer is complete. Do not commit; the harness commits."
)

FIX_PROMPT = (
    "The spec below is already implemented on this branch, by you earlier in this thread. "
    "Two reviewers from other vendors read the change; their reports follow, each with its "
    "items numbered.\n\n"
    + GATE_BLOCK
    + "<spec>\n{{mission.prompt}}\n</spec>\n\n"
    "<review_gemini>\n{{lanes.review-gemini.answer}}\n</review_gemini>\n\n"
    "<review_grok>\n{{lanes.review-grok.answer}}\n</review_grok>\n\n"
    "For each reported item, first write a test that fails on the current tree because of "
    "it; then fix only what that test proves. This lane runs under conductor's reproduce "
    "gate: a fix with no test change is refused, and a test that already passes on the "
    "current tree is refused. If both reviews say NO_FINDINGS or nothing reproduces, change "
    "nothing and reply NO_CHANGES. For every item either reviewer numbered, write one entry "
    "to a file named dispositions.json at the repository root: a JSON object "
    '{"dispositions": [...]}, each entry {"lane": "review-gemini" or "review-grok", "index": '
    '<the item number>, "disposition": "fixed", "refused", "already", or "wording", '
    '"reason": <why>}. Use fixed for an item you changed code for; refused, with the reason '
    "it is wrong, for one you rejected; already for one that was already true before this "
    "fix; wording for one that only asked for a comment or message change. Write "
    "dispositions.json even when you reply NO_CHANGES -- one entry per item either reviewer "
    "numbered (already, refused, or wording); an empty dispositions array is only correct "
    "when both reviews said NO_FINDINGS. Keep existing call signatures working."
    + FIX_GATE_RUN
    + " Do not commit; the harness commits."
)

# F15 mission 2 item 2: the fix lane's dispositions.json deliverable schema.
# The description carries the entry shape since runner._schema_mismatch only
# checks top-level required/typed properties, never an array's items.
DISPOSITIONS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dispositions"],
    "properties": {
        "dispositions": {
            "type": "array",
            "description": (
                "One entry per item either reviewer numbered: "
                '{"lane": "review-gemini" or "review-grok", "index": <the item number>, '
                '"disposition": "fixed", "refused", "already", or "wording", '
                '"reason": <why>}. An empty array is only correct when both reviews said '
                "NO_FINDINGS."
            ),
        }
    },
}


def _three_reviewer_wording(text: str) -> str:
    """The same substitutions `fix_prompt_with_opus` applies to prompt
    prose, used also on the dispositions schema description so a
    `--opus-review` mission does not tell the fixer that Opus's items
    need no disposition."""
    return (
        text.replace(
            "Two reviewers from other vendors read the change; their reports follow, each "
            "with its items numbered.",
            "Three reviewers read the change, two from other vendors and Opus 5 from yours; "
            "their reports follow, each with its items numbered.",
        )
        .replace(
            '{"lane": "review-gemini" or "review-grok", "index": ',
            '{"lane": "review-gemini", "review-grok", or "review-opus", "index": ',
        )
        .replace("If both reviews say NO_FINDINGS", "If every review says NO_FINDINGS")
        .replace("either reviewer numbered", "any reviewer numbered")
        .replace("when both reviews said NO_FINDINGS", "when every review said NO_FINDINGS")
    )


def dispositions_schema(*, opus_review: bool = False) -> dict:
    """`DISPOSITIONS_SCHEMA`, rewritten for three reviewers when `opus_review`."""
    description = DISPOSITIONS_SCHEMA["properties"]["dispositions"]["description"]
    if opus_review:
        description = _three_reviewer_wording(description)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["dispositions"],
        "properties": {
            "dispositions": {
                "type": "array",
                "description": description,
            }
        },
    }


def write_dispositions_schema(base_dir: Path, *, opus_review: bool = False) -> Path:
    """`dispositions.json`'s schema is data, not a prompt: written beside
    the mission file (like `prompts/<lane>.md`) so `deliverable.schema` --
    always a path -- resolves to something real, whether the mission goes
    through `cmd_shape_a`'s prompt-writing dance (`--inline` skips that, not
    this) or a salvage follow-on's `emit()`. `opus_review` applies the same
    three-reviewer rewrite `fix_prompt_with_opus` does."""
    path = Path(base_dir) / "dispositions.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dispositions_schema(opus_review=opus_review), indent=2) + "\n")
    return path

# E16: appended to FIX_PROMPT, right after the two review blocks, only when
# `shape_a(adversarial=True)` -- the adversarial lane's own report and diff,
# so a fix built on it reads the failing test the same way it reads a review.
FIX_PROMPT_ADVERSARIAL_BLOCK = (
    "<adversarial>\n{{lanes.adversarial.answer}}\n\n{{lanes.adversarial.diff}}\n</adversarial>\n\n"
)

# F9 Shape C: the third review block, inserted right after the Grok block
# (and before any adversarial block) when `shape_a(opus_review=True)`.
FIX_PROMPT_OPUS_BLOCK = "<review_opus>\n{{lanes.review-opus.answer}}\n</review_opus>\n\n"

# E23: `FIX_PROMPT` opens by telling the fixer it wrote the change itself,
# earlier in the same thread, because in `shape_a` it resumes the build
# lane's session and that is true. A salvage follow-on has no build lane and
# nothing to resume: the fix lane is a new session looking at a commit the
# lead made by hand. Telling it otherwise invites it to go looking for
# context it never had, or to treat the salvage as its own prior work
# (2026-09-08 review).
FOLLOWON_FIX_OPENING = (
    "The spec below is already implemented on the commit this working directory sits at. "
    "You did not write it and there is no earlier turn in this thread to recall. "
)


def followon_fix_prompt(prompt: str) -> str:
    """`FIX_PROMPT` (or a variant) with its resumed-thread opening replaced
    by one that describes a fresh session over a commit."""
    opening = (
        "The spec below is already implemented on this branch, by you earlier in this thread. "
    )
    if opening not in prompt:  # pragma: no cover - the constant is pinned by a test
        raise ShapeInvalid("FIX_PROMPT no longer opens the way the follow-on rewrite expects")
    return prompt.replace(opening, FOLLOWON_FIX_OPENING)


def fix_prompt_with_opus(prompt: str) -> str:
    """`FIX_PROMPT` (or a variant of it) rewritten for three reviewers: the
    Opus block follows the Grok block, and every "two reviewers", "either
    reviewer", "both reviews" reads for three, so the dispositions contract
    names the third lane too."""
    grok_block = "<review_grok>\n{{lanes.review-grok.answer}}\n</review_grok>\n\n"
    return _three_reviewer_wording(prompt.replace(grok_block, grok_block + FIX_PROMPT_OPUS_BLOCK))


class ShapeInvalid(ValueError):
    """The launcher's inputs cannot produce a Shape A mission."""


@dataclasses.dataclass(frozen=True)
class CapArithmetic:
    """Every term of the build cap, so the lead can see which one is wrong."""

    spec_items: int
    modules: int
    scheduler: bool
    grok_runs_suite: bool
    # E24/F5: the grace band on the build and fix (claude) lanes and the
    # review-grok (cursor read) lane; 0 disables it.
    cap_grace_usd: float = USD_CLAUDE_GRACE
    # E16: whether this shape carries the adversarial lane; the term (and
    # the mission budget's share of it) only appears when it does.
    adversarial: bool = False
    # F6 rule 11: spec items that are tests, already counted once in
    # `spec_items`; this is the second dollar each one earns.
    tests_items: int = 0
    # F6: expected review findings, at a dollar each on the fix cap.
    findings: int = DEFAULT_FINDINGS
    # F9 Shape C: whether this shape carries the Opus review lane; its term
    # joins the mission budget only when it does.
    opus_review: bool = False

    @property
    def build_terms(self) -> list[tuple[str, float]]:
        terms = [(f"{self.spec_items} spec items", self.spec_items * USD_PER_SPEC_ITEM)]
        if self.tests_items:
            terms.append(
                (
                    f"tests counted twice ({self.tests_items} items)",
                    self.tests_items * USD_PER_SPEC_ITEM,
                )
            )
        if self.scheduler:
            terms.append(("scheduler tax", USD_SCHEDULER_TAX))
        extra = max(0, self.modules - 2)
        if extra:
            terms.append((f"{extra} modules past the second", extra * USD_PER_EXTRA_MODULE))
        terms.append(("Claude summary", USD_CLAUDE_SUMMARY))
        return terms

    @property
    def build_cap(self) -> float:
        return round(sum(v for _, v in self.build_terms), 2)

    @property
    def fix_terms(self) -> list[tuple[str, float]]:
        terms = [("fix base", USD_FIX_BASE)]
        if self.findings:
            terms.append((f"{self.findings} findings", self.findings * USD_PER_FINDING))
        if self.scheduler:
            terms.append(("scheduler tax", USD_SCHEDULER_TAX))
        terms.append(("Claude summary", USD_CLAUDE_SUMMARY))
        return terms

    @property
    def fix_cap(self) -> float:
        return round(sum(v for _, v in self.fix_terms), 2)

    @property
    def gemini_cap(self) -> float:
        return USD_GEMINI_READ

    @property
    def grok_cap(self) -> float:
        return USD_GROK_SUITE if self.grok_runs_suite else USD_GROK_READ

    @property
    def adversarial_terms(self) -> list[tuple[str, float]]:
        terms = [("adversarial test", USD_ADVERSARIAL)]
        terms.append(("Claude summary", USD_CLAUDE_SUMMARY))
        return terms

    @property
    def adversarial_cap(self) -> float:
        return round(sum(v for _, v in self.adversarial_terms), 2)

    @property
    def opus_terms(self) -> list[tuple[str, float]]:
        # Rule 10: every Claude cap, read lanes included, carries the summary
        # dollar; the F9 lanes finished at $2.40 to $2.76 under a $3.00 cap.
        return [("Opus cold read", USD_OPUS_READ), ("Claude summary", USD_CLAUDE_SUMMARY)]

    @property
    def opus_cap(self) -> float:
        return round(sum(v for _, v in self.opus_terms), 2)

    @property
    def review_caps(self) -> float:
        """Every review lane's cap, so the follow-on shape sums the same set."""
        total = self.gemini_cap + self.grok_cap
        if self.opus_review:
            total += self.opus_cap
        return total

    @property
    def graced_lanes(self) -> int:
        """How many lanes carry `cap_grace_usd`: build, review-grok and fix
        always, plus the adversarial and Opus lanes when this shape has
        them (`shape_a` writes the field onto exactly these)."""
        if not self.cap_grace_usd:
            return 0
        return 3 + int(self.adversarial) + int(self.opus_review)

    @property
    def mission_budget(self) -> float:
        """Every lane's cap, the grace those lanes may legally draw on top
        of it, and the slack.

        The grace band used to be left out entirely (2026-09-08 review). It
        is real spend against the same ledger -- Grok's is post-hoc, so the
        dollars are already gone when it is granted -- so five graced lanes
        at the $0.50 ceiling could legally spend $2.50 over the summed caps
        against $1.50 of slack, and the mission ran out of budget before the
        fix lane, after a green build and two clean reviews.
        """
        lanes = self.build_cap + self.review_caps + self.fix_cap
        if self.adversarial:
            lanes += self.adversarial_cap
        grace = self.graced_lanes * self.cap_grace_usd
        return round(lanes + grace + USD_MISSION_SLACK, 2)

    @property
    def followon_budget(self) -> float:
        """The mission budget without a build lane: same arithmetic as
        `mission_budget`, minus the build cap. A salvage follow-on still
        graces grok, fix, and opus, and those dollars are real spend."""
        return round(self.mission_budget - self.build_cap, 2)

    def render(self) -> str:
        def line(label: str, terms: list[tuple[str, float]], total: float) -> str:
            body = " + ".join(f"${v:.2f} {name}" for name, v in terms)
            return f"{label}: {body} = ${total:.2f}"

        lines = [
            line("build cap", self.build_terms, self.build_cap),
            f"review-gemini cap: ${self.gemini_cap:.2f} (reads only; rule 7)",
            f"review-grok cap: ${self.grok_cap:.2f} "
            + ("(runs the suite; rule 7)" if self.grok_runs_suite else "(reads only; rule 7)"),
        ]
        if self.opus_review:
            lines.append(line("review-opus cap", self.opus_terms, self.opus_cap))
        if self.adversarial:
            lines.append(line("adversarial cap", self.adversarial_terms, self.adversarial_cap))
        lines.append(line("fix cap", self.fix_terms, self.fix_cap))
        grace_total = self.graced_lanes * self.cap_grace_usd
        lines.append(
            f"grace: ${self.cap_grace_usd:.2f} per claude lane and the grok read lane "
            f"(E24/F5, on top of its own cap; {self.graced_lanes} lanes, "
            f"${grace_total:.2f} in the mission budget)"
            if self.cap_grace_usd
            else "grace: disabled (E24/F5)"
        )
        lines.append(
            f"mission budget: lanes "
            f"${self.mission_budget - USD_MISSION_SLACK - grace_total:.2f} "
            f"+ ${grace_total:.2f} grace + ${USD_MISSION_SLACK:.2f} slack "
            f"= ${self.mission_budget:.2f}"
        )
        return "\n".join(lines)


def cap_arithmetic(
    spec_items: int,
    modules: int,
    *,
    scheduler: bool = False,
    grok_runs_suite: bool = False,
    cap_grace_usd: float = USD_CLAUDE_GRACE,
    adversarial: bool = False,
    tests_items: int = 0,
    findings: int = DEFAULT_FINDINGS,
    opus_review: bool = False,
) -> CapArithmetic:
    if spec_items < 1:
        raise ShapeInvalid("--items must be at least 1")
    if modules < 1:
        raise ShapeInvalid("--modules must be at least 1")
    if cap_grace_usd < 0 or cap_grace_usd > CAP_GRACE_CEILING_USD:
        raise ShapeInvalid(f"--cap-grace-usd must be between 0 and ${CAP_GRACE_CEILING_USD:.2f}")
    if tests_items < 0:
        raise ShapeInvalid("--tests-items must be zero or more")
    if tests_items > spec_items:
        raise ShapeInvalid("--tests-items must not exceed --items")
    if findings < 0:
        raise ShapeInvalid("--findings must be zero or more")
    return CapArithmetic(
        spec_items=spec_items,
        modules=modules,
        scheduler=scheduler,
        grok_runs_suite=grok_runs_suite,
        cap_grace_usd=cap_grace_usd,
        adversarial=adversarial,
        tests_items=tests_items,
        findings=findings,
        opus_review=opus_review,
    )


def _refuse_unsized_lanes(caps: CapArithmetic, **emitted: bool) -> None:
    """The lanes this shape emits and the caps it sizes them against are two
    separate arguments, and `max_cost_usd` comes from the caps alone. When
    they disagree the mission loads and then runs out of budget partway
    through, because the extra lane's cap was never in the total
    (2026-09-08 review). The CLI always passes them together; a caller
    that does not is refused rather than shipped an under-budgeted
    mission.
    """
    for flag, value in emitted.items():
        sized = getattr(caps, flag)
        if value != sized:
            raise ShapeInvalid(
                f"{flag}={value} but the cap arithmetic was built with "
                f"{flag}={sized}: the mission budget would not carry that lane's cap"
            )


def parse_ceiling(value: str) -> dict:
    """`--ceiling none|default|H,D`. `none` (the default) writes explicit
    null bounds: an attended launch is watched, so the E9 spend ceiling
    follows `--unattended` at run time, not this launcher (operator decision
    2026-09-07). `default` reads `ceiling.py`'s own module constants live,
    never re-typing the numbers. `H,D` sets both bounds explicitly."""
    value = value.strip()
    if value == "none":
        return dict(CEILING_NONE)
    if value == "default":
        return {
            "per_hour_usd": ceiling_mod.USD_PER_HOUR,
            "per_day_usd": ceiling_mod.USD_PER_DAY,
        }
    parts = value.split(",")
    if len(parts) != 2:
        raise ShapeInvalid("--ceiling must be 'none', 'default', or 'H,D'")
    try:
        per_hour, per_day = float(parts[0]), float(parts[1])
    except ValueError:
        raise ShapeInvalid("--ceiling H,D must both be numbers") from None
    if per_hour <= 0 or per_day <= 0:
        raise ShapeInvalid("--ceiling H,D must both be positive")
    return {"per_hour_usd": per_hour, "per_day_usd": per_day}


def gate_preflight(repo: Path, test_command: str, *, timeout: int = GATE_TIMEOUT) -> None:
    """F6: prove the gate command actually runs before the launcher ever
    writes a mission file. F1 and F3 were launched with a gate that tested
    the main checkout's source from inside a worktree (`.venv/bin/pytest`
    with no `PYTHONPATH=src`); this reproduces exactly that failure mode by
    running the gate once, for real, in a throwaway worktree of `repo` at
    HEAD -- a temporary directory under `$TMPDIR`, torn down on every path,
    never under `repo` itself. A lead-side check only; nothing inside a
    mission ever calls this.
    """
    repo = Path(repo).expanduser().resolve()
    tmp_root = tempfile.mkdtemp(prefix=GATE_PREFLIGHT_PREFIX, dir=os.environ.get("TMPDIR"))
    worktree = Path(tmp_root) / "worktree"
    try:
        added = git_run(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
        if added.returncode != 0:
            raise ShapeInvalid(
                "gate preflight: could not create a throwaway worktree at HEAD: "
                f"{added.stderr.strip() or added.stdout.strip()}"
            )
        env = dict(os.environ)
        env["CONDUCTOR_WORKTREE"] = str(worktree)
        outcome = run_tests(str(worktree), test_command, timeout=timeout, env=env)
        if not outcome.passed:
            tail = "\n".join(outcome.tail.splitlines()[-10:])
            code = "none" if outcome.exit_code is None else str(outcome.exit_code)
            raise ShapeInvalid(
                f"gate preflight: the gate command exited {code} in a clean worktree at "
                "HEAD -- it must bring its own toolchain by absolute path and PYTHONPATH=src "
                f"when the package is imported from the tree.\n{tail}"
            )
    finally:
        git_run(repo, "worktree", "remove", "--force", str(worktree))
        git_run(repo, "worktree", "prune")
        shutil.rmtree(tmp_root, ignore_errors=True)


# Code-and-test sentences in BUILD_PROMPT. A document or data deliverable
# (F23) has neither call signatures nor tests to game, and DELIVERABLE_BUILD_PARAGRAPH
# already carries the commit and investigate lines, so `build_prompt` strips
# this block when `deliverable` is set.
BUILD_CODE_RULES = (
    " Keep every existing call signature working; add new parameters as keywords with "
    "defaults. Do not commit; the harness commits. Do not special-case a test to make it "
    "pass: a test that passes for the wrong reason is worse than a failing one, and the "
    "reviewers read every test edit. Investigate before answering: read the code a change "
    "touches before changing it.\n\n"
)

BUILD_PROMPT = (
    "Implement the spec below on this branch.\n\n"
    "<gate>\n{gate}\n</gate>\n\n"
    "That is the gate the lead will run on your work, exactly as written. Run it the same "
    "way, flags included, before you finish. When the gate is a test suite, the flags are "
    "what make it take under a minute on this machine, and a serial run of the same suite "
    "takes several times longer and proves nothing extra. When the gate is pytest, pass "
    "--basetemp pointing to a directory under $TMPDIR so nothing is written inside this "
    "working tree."
    + BUILD_CODE_RULES
    + "Before you finish, write an evidence map to a file named evidence.json at the "
    "repository root: a JSON object {\"items\": [...]}, one entry per spec item in the "
    "spec's own order, each {\"item\": <the item's number or its first words>, \"status\": "
    "\"built\", \"partial\", or \"not_built\", \"files\": [<paths you changed for it>], "
    "\"tests\": [<test functions or files that exercise it>], \"check\": <the command you ran "
    "that proves it, or \"none\">, \"note\": <one sentence, empty if nothing to say>}. "
    "An item you did not build is written as not_built with the reason in its note; the "
    "map is read by the reviewers beside your diff, so a claim it cannot support is a "
    "finding against the build. The harness keeps evidence.json out of the commit.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>"
)

# Evidence map (Phase H item 6, operator decision 2026-09-07): the build lane's
# own map from spec item to files, tests, and the check it ran, as an E1
# deliverable with `commit: false`, handed to the reviewers beside the diff.
# The outside review asked for this before any paid spec-fidelity stage: the
# three spec items Shape C caught as "passed as built, not built" (F15) had no
# artifact that named them at all.
EVIDENCE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "description": (
                "One entry per spec item, in the spec's order: "
                '{"item": <number or first words>, "status": "built", "partial", or '
                '"not_built", "files": [<paths changed>], "tests": [<tests that exercise '
                'it>], "check": <the command run, or "none">, "note": <one sentence>}.'
            ),
        }
    },
}

EVIDENCE_BLOCK = "<evidence>\n{{lanes.build.deliverable}}\n</evidence>\n\n"

EVIDENCE_REVIEW_NOTE = (
    "The evidence block is the builder's own map from each spec item to the files, tests, "
    "and check behind it. Treat it as a claim: an item whose files or tests are not in the "
    "change, or a check that was not run, is reportable the same as any other item, with "
    "the map's entry as the citation. A spec item the map does not name is reportable the "
    "same way: cite the spec item itself and the map as the thing that omits it. "
)


# F23: a document or data deliverable through Shape A. The build lane's
# deliverable is the file itself, with its validator (F22) in place of the
# evidence map (a one-file spec has nothing for a map to map), the reviewers
# read a note instead of the evidence block, and the review-applying lane
# runs as `stage: build`: a document fix has no test to reproduce, and the
# validator already passes on the build's tip, so a `stage: fix` lane would
# be refused every time with `validator passed on the base too` (drill 2 of
# `docs/research/2026-09-07-consumer-validator-data.md`).
DELIVERABLE_BUILD_PARAGRAPH = (
    "Your deliverable is the file `{path}`; the spec below is about that file, and the "
    "change stays inside it unless the spec names another. {validator_sentence}Do not "
    "commit; the harness commits. Investigate before answering: read the file in full "
    "before changing it.\n\n"
)

DELIVERABLE_VALIDATOR_SENTENCE = (
    "Conductor runs `{validator}` on the file before and after your change and refuses a "
    "file that fails it; run it yourself before you finish. "
)

DELIVERABLE_REVIEW_NOTE = (
    "The build's deliverable is the file `{path}`{validator_clause}. What a machine can "
    "check is checked; whether the file still says what the spec asked, and nothing the "
    "spec did not ask, is your question. "
)

DELIVERABLE_FIX_SENTENCES = (
    "For each reported item, edit `{path}` to address it and nothing else; the deliverable "
    "is a file, not code, so there is no test to write and this lane runs as a build "
    "lane, not under the reproduce gate. {validator_sentence}"
)


def write_evidence_schema(base_dir: Path) -> Path:
    """`evidence.json`'s schema, written beside the mission file like the
    dispositions schema, so the build lane's `deliverable.schema` path
    resolves wherever the mission is launched from."""
    path = Path(base_dir) / "evidence.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(EVIDENCE_SCHEMA, indent=2) + "\n")
    return path


def write_shape_schemas(base_dir: Path, *, opus_review: bool = False) -> tuple[Path, Path]:
    """Both schema files a Shape A mission's deliverables name, beside the
    mission file: `dispositions.schema.json` (fix lane) and
    `evidence.schema.json` (build lane)."""
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    return write_dispositions_schema(base_dir, opus_review=opus_review), write_evidence_schema(
        base_dir
    )


def review_prompt_with_evidence(prompt: str) -> str:
    """A Shape A review prompt with the build's evidence map after its
    `<change>` block and the note on how to read it ahead of the review
    tail. Only `shape_a` uses this: the salvage follow-on has no build lane
    and so no map."""
    change_end = "</change>\n\n"
    assert change_end in prompt
    return prompt.replace(change_end, change_end + EVIDENCE_BLOCK, 1).replace(
        "Report anything that could cause incorrect behavior",
        EVIDENCE_REVIEW_NOTE + "Report anything that could cause incorrect behavior",
        1,
    )


def validator_for_prompt(validator: str, path: str) -> str:
    """The copy of a validator command that goes into prompt text: `{path}`
    already replaced with the deliverable, so the model is not told to run
    `python3 check.py {path}` verbatim."""
    return validator.replace("{path}", path)


def review_prompt_with_deliverable(prompt: str, path: str, validator: str) -> str:
    """A Shape A review prompt for a deliverable mission (F23): the note on
    what the build's deliverable is ahead of the review tail, and no
    evidence block, since the build lane declared the file instead of a
    map."""
    shown = validator_for_prompt(validator, path) if validator else ""
    clause = f", and conductor's validator `{shown}` passed on it" if shown else ""
    note = DELIVERABLE_REVIEW_NOTE.format(path=path, validator_clause=clause)
    return prompt.replace(
        "Report anything that could cause incorrect behavior",
        note + "Report anything that could cause incorrect behavior",
        1,
    )


def fix_prompt_for_deliverable(prompt: str, path: str, validator: str) -> str:
    """`FIX_PROMPT` (or a variant) for a deliverable mission (F23): the
    reproduce-gate sentences give way to the file edit. There is no
    reproduce gate, so "nothing reproduces" does not apply, and the gate
    on this lane is a file validator, not pytest, so `--basetemp` does
    not apply either. The `<gate>` block (item 1) still names the
    mission's `test` command when there is one."""
    old = (
        "For each reported item, first write a test that fails on the current tree because "
        "of it; then fix only what that test proves. This lane runs under conductor's "
        "reproduce gate: a fix with no test change is refused, and a test that already "
        "passes on the current tree is refused. "
    )
    assert old in prompt
    shown = validator_for_prompt(validator, path) if validator else ""
    sentence = DELIVERABLE_VALIDATOR_SENTENCE.format(validator=shown) if shown else ""
    rewritten = prompt.replace(
        old, DELIVERABLE_FIX_SENTENCES.format(path=path, validator_sentence=sentence), 1
    )
    rewritten = rewritten.replace(
        "Use fixed for an item you changed code for",
        "Use fixed for an item you edited the file for",
        1,
    )
    # No reproduce gate on a deliverable (stage: build), and `--basetemp` is
    # a pytest flag the file validator does not take.
    rewritten = rewritten.replace(" or nothing reproduces", "")
    rewritten = rewritten.replace(
        FIX_GATE_RUN, " Run the gate in the <gate> block before finishing."
    )
    return rewritten


def with_gate(prompt: str, test: str = "") -> str:
    """Fill a prompt's `{gate}` from `test`. When there is no command to
    name, drop the `<gate>` block and every sentence that tells the model
    to run one, so the prompt does not claim a gate exists."""
    if test.strip():
        return prompt.replace("{gate}", test)
    stripped = (
        prompt.replace(GATE_BLOCK, "")
        .replace(GROK_GATE_RUN, "")
        .replace(ADVERSARIAL_GATE_RUN, "")
        .replace(FIX_GATE_RUN, "")
    )
    return stripped


def grok_review_prompt(test: str = "") -> str:
    """`GROK_REVIEW_PROMPT` with the mission's gate filled in, or with the
    gate claim removed when `test` is empty."""
    return with_gate(GROK_REVIEW_PROMPT, test)


def adversarial_prompt(test: str = "") -> str:
    """`ADVERSARIAL_PROMPT` with the mission's gate filled in, or with the
    gate claim removed when `test` is empty."""
    return with_gate(ADVERSARIAL_PROMPT, test)


def fix_prompt(test: str = "") -> str:
    """`FIX_PROMPT` with the mission's gate filled in, or with the gate
    claim removed when `test` is empty."""
    return with_gate(FIX_PROMPT, test)


def build_prompt(test: str, deliverable: str = "", validator: str = "") -> str:
    """The Shape A build lane's prompt: `BUILD_PROMPT` with the gate command filled in.

    Idea 7 of the 2026-09 review: the build lane used to receive the bare spec, so a builder
    that ran the suite ran it serially (four minutes) instead of with the xdist flags the
    lead's gate uses (under a minute), and spent its cap on that. Naming the gate verbatim
    costs one line and removes the guess. `{{mission.prompt}}` stays a template reference,
    rendered by the mission like every other lane prompt.

    `deliverable` (F23) names the file a document or data build produces: the evidence-map
    paragraph gives way to one naming the file and, when `validator` is set, the command
    conductor runs on it before and after, with `{path}` already substituted so the model
    can run the same command conductor will.
    """
    prompt = BUILD_PROMPT.replace("{gate}", test)
    if not deliverable:
        return prompt
    start = "Before you finish, write an evidence map"
    end = "The harness keeps evidence.json out of the commit.\n\n"
    assert start in prompt and end in prompt
    assert BUILD_CODE_RULES in prompt
    prompt = prompt.replace(BUILD_CODE_RULES, "\n\n", 1)
    head, _, rest = prompt.partition(start)
    _, _, tail = rest.partition(end)
    shown = validator_for_prompt(validator, deliverable) if validator else ""
    sentence = DELIVERABLE_VALIDATOR_SENTENCE.format(validator=shown) if shown else ""
    paragraph = DELIVERABLE_BUILD_PARAGRAPH.format(path=deliverable, validator_sentence=sentence)
    return head + paragraph + tail


def _prefix(repo: Path, about: str | None) -> str:
    what = about or f"the repository '{repo.name}'"
    return (
        f"You are one lane of a conductor mission on {what}. Every claim is judged on "
        "bytes: the diff, the gate, the receipts. Read before you write; cite file and line "
        "for anything you report. You are operating autonomously: nobody is watching in real "
        "time and nobody can answer a question mid-task, so a question blocks the work; make "
        "the routine call, state it in one line, and keep going. Keep changes and tests to "
        "what the task asks for: no unrequested fixes, no surplus test files. Before ending, "
        "check your last paragraph: if it is a plan or a promise, do that work now."
    )


def shape_a(
    *,
    spec: Path,
    repo: Path,
    test: str,
    caps: CapArithmetic,
    name: str = "",
    about: str | None = None,
    ports: int = 0,
    test_policy: str = "allow",
    mission_dir: Path | None = None,
    branch: str = "",
    build_commit: str = "",
    fix_commit: str = "",
    adversarial: bool = False,
    ceiling: dict | None = None,
    opus_review: bool = False,
    deliverable: str = "",
    deliverable_validator: str = "",
) -> dict:
    """The Shape A mission as a dict ready for `mission_from_dict` or `json.dump`.

    The fix lane lands on `branch` (default `feat/<name>`) with `fix_commit`; the build
    lane commits with `build_commit` on its own worktree and the fix lane resumes it, the
    way every Shape A mission on record ran. `spec` and `repo` are written as paths
    relative to `mission_dir` when the mission file will sit under the same tree,
    absolute otherwise, so the file loads from where it is written. `test_policy`
    defaults to `allow` on the build and fix lanes (rule 3): a spec that changes what
    existing missions may do fails the clean gate otherwise, and the lead reads every
    existing-test edit either way.

    `adversarial` (E16) adds a lane beside the two reviewers whose deliverable is a test
    that fails on the build's tip because of a defect in the change against the spec, not
    prose. The fix lane then builds on it instead of directly on `build` (still resuming
    `build`'s own thread) and inherits its check when the adversarial lane actually
    reproduced something, so the fix does not have to re-earn a test-surface change of its
    own for a defect that already has one.

    `ceiling` is the mission's E9 rolling-spend ceiling dict, `{"per_hour_usd", "per_day_usd"}`
    (see `parse_ceiling`); `None` (the default) writes `CEILING_NONE`, the same as `--ceiling
    none`.

    `opus_review` (F9 Shape C) adds `review-opus`, Opus 5 at `hard` reading cold beside the
    pair, at `caps.opus_cap`. The build is Sonnet, the same vendor, so the mission carries
    `self_judging: allow` and the review policy admits `anthropic`; the fix lane needs the third
    review and its prompt carries a `<review_opus>` block. Concurrency rises to three so the
    reviewers still run side by side (rule 11).

    `deliverable` (F23) is a repo-relative file a document or data spec produces, with
    `deliverable_validator` the F22 command conductor runs on it (`{path}` substituted).
    The build lane declares the file in place of the evidence map, the reviewers read a
    note in place of the evidence block, and the review-applying lane keeps its name,
    its `dispositions.json` receipt, and its place in `pause.before`, but runs as
    `stage: build`: a file has no test to reproduce and the validator already passes on
    the build's tip, so under `stage: fix` it would be refused every time. The mission
    policy then names `build` and `review` only. `adversarial` is refused with it.
    """
    spec = Path(spec).expanduser().resolve()
    repo = Path(repo).expanduser().resolve()
    if not spec.is_file():
        raise ShapeInvalid(f"spec is not a file: {spec}")
    if not (repo / ".git").exists():
        raise ShapeInvalid(f"repo is not a git repository: {repo}")
    if not test.strip():
        raise ShapeInvalid("--test must name the gate command; a Shape A build has one")
    if ports < 0:
        raise ShapeInvalid("--ports must be zero or more")
    _refuse_unsized_lanes(caps, adversarial=adversarial, opus_review=opus_review)
    if deliverable_validator and not deliverable:
        raise ShapeInvalid("--deliverable-validator needs --deliverable")
    if deliverable:
        if Path(deliverable).is_absolute() or ".." in Path(deliverable).parts:
            raise ShapeInvalid("--deliverable must be a repo-relative path without '..'")
        if adversarial:
            raise ShapeInvalid(
                "--adversarial with --deliverable: an adversarial lane writes a test that "
                "fails on the build, and a file deliverable has no test surface"
            )
    base = Path(mission_dir).expanduser().resolve() if mission_dir else spec.parent
    mission_name = name or spec.stem
    scope = repo.name
    branch = branch or f"feat/{mission_name}"
    if branch.startswith("conductor/") or not branch.strip():
        raise ShapeInvalid("--branch must be a name outside conductor/")
    build_commit = build_commit or f"feat({scope}): {mission_name}"
    fix_commit = fix_commit or f"fix({scope}): address cross-vendor review of {mission_name}"

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)

    build: dict = {
        "name": "build",
        "stage": "build",
        "fleet": "claude",
        "model": "sonnet",
        "effort": "hard",
        "mode": "write",
        "timeout": 3300,
        "cap_usd": caps.build_cap,
        "test_policy": test_policy,
        "commit": build_commit,
        "deliverable": {
            "path": "evidence.json",
            "schema": "evidence.schema.json",
            "commit": False,
        },
        "prompt": build_prompt(test, deliverable, deliverable_validator),
    }
    if deliverable:
        build["deliverable"] = {"path": deliverable}
        if deliverable_validator:
            build["deliverable"]["validator"] = deliverable_validator
    if ports:
        build["ports"] = ports
    if caps.cap_grace_usd:
        build["cap_grace_usd"] = caps.cap_grace_usd
    adversarial_lane: dict | None = None
    if adversarial:
        adversarial_lane = {
            "name": "adversarial",
            "stage": "adversarial",
            "fleet": "claude",
            "model": "sonnet",
            "effort": "standard",
            "mode": "write",
            "base": "build",
            "needs": ["build"],
            "no_op_ok": True,
            "timeout": 1800,
            "cap_usd": caps.adversarial_cap,
            "test_policy": "allow",
            "commit": f"test({scope}): adversarial check for {mission_name}",
            "prompt": adversarial_prompt(test),
        }
        if caps.cap_grace_usd:
            adversarial_lane["cap_grace_usd"] = caps.cap_grace_usd
    fix_text = fix_prompt(test)
    if adversarial:
        fix_text = fix_text.replace(
            "<review_grok>\n{{lanes.review-grok.answer}}\n</review_grok>\n\n",
            "<review_grok>\n{{lanes.review-grok.answer}}\n</review_grok>\n\n"
            + FIX_PROMPT_ADVERSARIAL_BLOCK,
        ).replace(
            "This lane runs under conductor's reproduce gate: a fix with no test change is "
            "refused, and a test that already passes on the current tree is refused.",
            "This lane runs under conductor's reproduce gate. When the adversarial block above "
            "carries a test that failed on the build (the lane found a defect), that test is "
            "already at this tree's base and is your reproducing check: fix the source so it "
            "passes, with no further test change required. Otherwise a fix with no test change "
            "is refused, and a test that already passes on the current tree is refused.",
        )
    if opus_review:
        # After the adversarial rewrite, so the Opus block lands between the
        # Grok block and the adversarial one: reviews first, then the test.
        fix_text = fix_prompt_with_opus(fix_text)
    if deliverable:
        fix_text = fix_prompt_for_deliverable(fix_text, deliverable, deliverable_validator)

    def review_prompt(prompt: str) -> str:
        if deliverable:
            return review_prompt_with_deliverable(prompt, deliverable, deliverable_validator)
        return review_prompt_with_evidence(prompt)
    review_grok: dict = {
        "name": "review-grok",
        "stage": "review",
        "fleet": "cursor",
        "model": "grok-4.6",
        "effort": "standard",
        "mode": "read",
        "base": "build",
        "timeout": 1200,
        "cap_usd": caps.grok_cap,
        "prompt": review_prompt(
            grok_review_prompt(test) if caps.grok_runs_suite else GROK_READ_ONLY_PROMPT
        ),
    }
    if caps.cap_grace_usd:
        review_grok["cap_grace_usd"] = caps.cap_grace_usd
    review_opus: dict | None = None
    if opus_review:
        review_opus = {
            "name": "review-opus",
            "stage": "review",
            "fleet": "claude",
            "model": "opus",
            "effort": "hard",
            "mode": "read",
            "base": "build",
            "timeout": 1800,
            "cap_usd": caps.opus_cap,
            "prompt": review_prompt(OPUS_REVIEW_PROMPT),
        }
        if caps.cap_grace_usd:
            review_opus["cap_grace_usd"] = caps.cap_grace_usd
    reviewers = ["review-gemini", "review-grok"] + (["review-opus"] if opus_review else [])
    fix: dict = {
        "name": "fix",
        "stage": "build" if deliverable else "fix",
        "fleet": "claude",
        "model": "sonnet",
        "effort": "standard",
        "mode": "write",
        "base": "adversarial" if adversarial else "build",
        # `resume` stays `build` even when `base` moves to `adversarial`
        # (the resumed thread is the build's, not the adversarial lane's own
        # session); mission.py's resume rule requires `resume` to be a need
        # or the base, so `build` must be named here explicitly once `base`
        # is no longer `build` itself.
        "needs": (["build", *reviewers, "adversarial"] if adversarial else reviewers),
        "resume": "build",
        "no_op_ok": True,
        "timeout": 1800,
        "cap_usd": caps.fix_cap,
        "test_policy": test_policy,
        "branch": branch,
        "commit": fix_commit,
        "cascade": False,
        "deliverable": {
            "path": "dispositions.json",
            "schema": "dispositions.schema.json",
            "commit": False,
        },
        "prompt": fix_text,
    }
    if caps.cap_grace_usd:
        fix["cap_grace_usd"] = caps.cap_grace_usd
    policy = {
        "build": {"vendors": ["anthropic"]},
        "review": {"vendors": ["google", "xai", "anthropic"] if opus_review else ["google", "xai"]},
        "fix": {"vendors": ["anthropic"]},
    }
    if adversarial:
        policy["adversarial"] = {"vendors": ["anthropic"]}
    if deliverable:
        # No lane declares `fix` now, and a policy stage no lane declares is
        # refused at load.
        del policy["fix"]
    mission: dict = {
        "name": mission_name,
        "cwd": rel(repo),
        "prompt_file": rel(spec),
        "concurrency": 3 if opus_review else 2,
        "require": "all",
        "max_cost_usd": caps.mission_budget,
        "test": test,
        "template_max_chars": 160000,
        "ceiling": dict(ceiling) if ceiling is not None else dict(CEILING_NONE),
        "policy": policy,
        "prefix": _prefix(repo, about),
        "pause": {"before": ["fix"]},
        "lanes": [
            build,
            {
                "name": "review-gemini",
                "stage": "review",
                "fleet": "antigravity",
                "model": "gemini-3.7-flash",
                "effort": "standard",
                "mode": "read",
                "base": "build",
                "timeout": 1200,
                "cap_usd": caps.gemini_cap,
                "prompt": review_prompt(GEMINI_REVIEW_PROMPT),
            },
            review_grok,
            *([review_opus] if review_opus is not None else []),
            *([adversarial_lane] if adversarial_lane is not None else []),
            fix,
        ],
    }
    if opus_review:
        # The build and the third reviewer share a vendor; mission load refuses
        # that pair unless the lift is declared (README, "Self-judging").
        mission["self_judging"] = "allow"
    return mission


def shape_a_followon(
    *,
    worktree: Path,
    salvage_sha: str,
    diff: str,
    test: str,
    caps: CapArithmetic,
    name: str,
    about: str | None = None,
    branch: str = "",
    fix_commit: str = "",
    mission_dir: Path | None = None,
    spec_prompt: str = "",
    base_sha: str = "",
    ceiling: dict | None = None,
    opus_review: bool = False,
) -> dict:
    """E23: the review-and-fix mission for a salvage the lead has already
    committed by hand (AGENTS.md rule 6). Identical in shape to `shape_a`
    except there is no build lane: the kept worktree, already sitting at the
    lead's own commit, is the mission's `cwd` directly, so no lane needs a
    `base` to build on and the fix lane has nothing to `resume`. The two
    review prompts are `GEMINI_REVIEW_PROMPT` and `GROK_READ_ONLY_PROMPT`
    (never the suite-running one -- there is no build lane's session for
    Grok to fall back to reproducing by hand), with the `<change>` block
    pointing at the commit itself (`git show <sha>` in the working directory)
    instead of a pasted `{{lanes.build.diff}}`: a diff pasted into a prompt
    is scanned as a template, and a change that touches conductor's own
    prompt constants carries template syntax in its context lines, so the
    mission would never load. `diff` is written beside the mission file as
    `<name>-diff.patch` for the lead (or nowhere, when `mission_dir` is None)
    and is never part of a prompt. `spec_prompt`, when given, is the original
    mission's prompt (the spec) and leads the mission prompt, so reviewers
    judge against the same text the build did; `{{mission.prompt}}` renders
    live to that. `ceiling` is the same E9 rolling-spend dict `shape_a` takes; `None`
    (the default) writes `CEILING_NONE`.
    """
    worktree = Path(worktree).expanduser().resolve()
    if not (worktree / ".git").exists():
        raise ShapeInvalid(f"worktree is not a git checkout: {worktree}")
    if not salvage_sha.strip():
        raise ShapeInvalid("salvage_sha must name a commit")
    if not test.strip():
        raise ShapeInvalid("--test must name the gate command; a Shape A build has one")
    if not name.strip():
        raise ShapeInvalid("name must not be empty")
    _refuse_unsized_lanes(caps, opus_review=opus_review)

    mission_name = name
    scope = worktree.name
    branch = branch or f"feat/{mission_name}"
    if branch.startswith("conductor/") or not branch.strip():
        raise ShapeInvalid("--branch must be a name outside conductor/")
    fix_commit = fix_commit or f"fix({scope}): address cross-vendor review of {mission_name}"

    salvage_note = (
        f"This change is already committed at {salvage_sha}, the HEAD of the worktree named "
        "as this mission's cwd: conductor's own clean gate rejected the original build "
        "lane's run, and the lead read the diff, gated it by hand, and committed it. "
        "The reviews judge the change as it stands; the fix lane, if it edits anything, "
        f"lands on branch '{branch}'."
    )
    mission_prompt = f"{spec_prompt.rstrip()}\n\n{salvage_note}" if spec_prompt else salvage_note
    change_block = (
        f"The change is commit {salvage_sha}, HEAD of this working directory"
        + (f" (its parent is {base_sha})" if base_sha else "")
        + f". Read it with `git show {salvage_sha}`."
    )
    gemini_prompt = GEMINI_REVIEW_PROMPT.replace("{{lanes.build.diff}}", change_block)
    grok_prompt = GROK_READ_ONLY_PROMPT.replace("{{lanes.build.diff}}", change_block)
    opus_prompt = OPUS_REVIEW_PROMPT.replace("{{lanes.build.diff}}", change_block)
    if mission_dir is not None:
        patch_path = Path(mission_dir).expanduser().resolve() / f"{mission_name}-diff.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff)

    review_grok: dict = {
        "name": "review-grok",
        "stage": "review",
        "fleet": "cursor",
        "model": "grok-4.6",
        "effort": "standard",
        "mode": "read",
        "timeout": 1200,
        "cap_usd": caps.grok_cap,
        "prompt": grok_prompt,
    }
    if caps.cap_grace_usd:
        review_grok["cap_grace_usd"] = caps.cap_grace_usd
    review_opus: dict | None = None
    if opus_review:
        review_opus = {
            "name": "review-opus",
            "stage": "review",
            "fleet": "claude",
            "model": "opus",
            "effort": "hard",
            "mode": "read",
            "timeout": 1800,
            "cap_usd": caps.opus_cap,
            "prompt": opus_prompt,
        }
        if caps.cap_grace_usd:
            review_opus["cap_grace_usd"] = caps.cap_grace_usd
    reviewers = ["review-gemini", "review-grok"] + (["review-opus"] if opus_review else [])
    fix: dict = {
        "name": "fix",
        "stage": "fix",
        "fleet": "claude",
        "model": "sonnet",
        "effort": "standard",
        "mode": "write",
        "needs": reviewers,
        "no_op_ok": True,
        "timeout": 1800,
        "cap_usd": caps.fix_cap,
        "test_policy": "allow",
        "branch": branch,
        "commit": fix_commit,
        "cascade": False,
        "deliverable": {
            "path": "dispositions.json",
            "schema": "dispositions.schema.json",
            "commit": False,
        },
        "prompt": followon_fix_prompt(
            fix_prompt_with_opus(fix_prompt(test)) if opus_review else fix_prompt(test)
        ),
    }
    if caps.cap_grace_usd:
        fix["cap_grace_usd"] = caps.cap_grace_usd

    mission: dict = {
        "name": mission_name,
        "cwd": str(worktree),
        "prompt": mission_prompt,
        "concurrency": 3 if opus_review else 2,
        "require": "all",
        "max_cost_usd": caps.followon_budget,
        "test": test,
        "template_max_chars": 160000,
        "ceiling": dict(ceiling) if ceiling is not None else dict(CEILING_NONE),
        "policy": {
            "review": {
                "vendors": ["google", "xai", "anthropic"] if opus_review else ["google", "xai"]
            },
            "fix": {"vendors": ["anthropic"]},
        },
        "prefix": _prefix(worktree, about),
        "pause": {"before": ["fix"]},
        "lanes": [
            {
                "name": "review-gemini",
                "stage": "review",
                "fleet": "antigravity",
                "model": "gemini-3.7-flash",
                "effort": "standard",
                "mode": "read",
                "timeout": 1200,
                "cap_usd": caps.gemini_cap,
                "prompt": gemini_prompt,
            },
            review_grok,
            *([review_opus] if review_opus is not None else []),
            fix,
        ],
    }
    # No build lane here, so no self-judging pair to lift: the third reviewer
    # and the fix lane share a vendor, but a fix is not a judge of a review.
    return mission
