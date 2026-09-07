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
from pathlib import Path

from .fleets import CAP_GRACE_CEILING_USD

SHAPE_VERSION = "a-2026-09-06"

# Rule 2 and rule 10, as constants with the rule number beside each.
USD_PER_SPEC_ITEM = 1.0  # rule 2: about a dollar per spec item on Sonnet at hard
USD_SCHEDULER_TAX = 2.0  # rule 2: scheduler, runner wait loop, or resume
USD_PER_EXTRA_MODULE = 1.0  # rule 2: every module past the second
USD_CLAUDE_SUMMARY = 1.0  # rule 10: Claude's final summary costs, on every cap
USD_FIX_BASE = 2.0  # a fix lane is a one- or two-item spec on the resumed thread
USD_ADVERSARIAL = 3.0  # E16: one test that fails on the current tree, plus room to look
USD_GEMINI_READ = 1.0  # rule 7: Gemini reads only on this shape
USD_GROK_READ = 1.5  # rule 7: Grok reading only
USD_GROK_SUITE = 2.0  # rule 7: Grok when it runs the suite
USD_MISSION_SLACK = 1.5  # room for a retry's partial spend before the mission budget sinks
USD_CLAUDE_GRACE = 0.25  # E24: default grace band on the build and fix (claude) lanes

REVIEW_TAIL = (
    "Report anything that could cause incorrect behavior, a test failure, or a misleading "
    "result, including a spec item that is missing or only partly implemented. Omit pure "
    "style and naming. Number each item. For each item: file and line, what goes wrong, one "
    "sentence of consequence, your confidence 1-10. End the reply with exactly one final "
    "line: NO_FINDINGS if there is nothing to report, or FINDINGS: N where N is the number "
    "of items you numbered above. Either answer is complete. Put the entire review in this "
    "reply; the reply is the only thing the next agent receives."
)

GEMINI_REVIEW_PROMPT = (
    "Review the change below against the spec. The change is applied in the current "
    "working directory; read whatever you need. Do not run the test suite; the lead runs "
    "it. Read the diff and the code it touches. Change nothing.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    "Based on the information above: " + REVIEW_TAIL
)

GROK_REVIEW_PROMPT = (
    "Review the change below against the spec. The change is applied in the current "
    "working directory; read whatever you need and, if you are able to, run the gate named "
    "in the spec. If you run the suite, pass --basetemp pointing to a directory under "
    "$TMPDIR so nothing is written inside this working tree. CHANGE NOTHING.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    + REVIEW_TAIL.replace("Put the entire review", "Put the ENTIRE review")
)

GROK_READ_ONLY_PROMPT = GROK_REVIEW_PROMPT.replace(
    "read whatever you need and, if you are able to, run the gate named in the spec. If "
    "you run the suite, pass --basetemp pointing to a directory under $TMPDIR so nothing "
    "is written inside this working tree. CHANGE NOTHING.",
    "read whatever you need. Do not run the test suite; the lead runs it. Read the diff "
    "and the code it touches. CHANGE NOTHING.",
)

ADVERSARIAL_PROMPT = (
    "The change below is applied in the current working directory, on its own branch. Read "
    "whatever you need.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n<change>\n{{lanes.build.diff}}\n</change>\n\n"
    "If you find a defect in the change against the spec, write one test that fails on the "
    "current tree because of it -- in a new or existing test file -- and say what it proves. "
    "Change nothing else: no source, no fix, just the test. Run the gate named in the spec "
    "before finishing, with --basetemp under $TMPDIR, and confirm your test is what fails. "
    "If nothing meets that bar, change nothing and reply exactly: NO_FINDINGS. Either answer "
    "is complete. Do not commit; the harness commits."
)

FIX_PROMPT = (
    "The spec below is already implemented on this branch, by you earlier in this thread. "
    "Two reviewers from other vendors read the change; their reports follow, each with its "
    "items numbered.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n"
    "<review_gemini>\n{{lanes.review-gemini.answer}}\n</review_gemini>\n\n"
    "<review_grok>\n{{lanes.review-grok.answer}}\n</review_grok>\n\n"
    "For each reported item, first write a test that fails on the current tree because of "
    "it; then fix only what that test proves. This lane runs under conductor's reproduce "
    "gate: a fix with no test change is refused, and a test that already passes on the "
    "current tree is refused. If both reviews say NO_FINDINGS or nothing reproduces, change "
    "nothing and reply NO_CHANGES. For every item either reviewer numbered, add one line: "
    "DISPOSITION: <review-gemini or review-grok> <item number> "
    "<fixed, refused, already, or wording>: <reason>. Use fixed for an item you changed code "
    "for; refused, with the reason it is wrong, for one you rejected; already for one that "
    "was already true before this fix; wording for one that only asked for a comment or "
    "message change. Keep existing call signatures working. Run the gate named in the spec "
    "before finishing, with --basetemp under $TMPDIR. Do not commit; the harness commits."
)

# E16: appended to FIX_PROMPT, right after the two review blocks, only when
# `shape_a(adversarial=True)` -- the adversarial lane's own report and diff,
# so a fix built on it reads the failing test the same way it reads a review.
FIX_PROMPT_ADVERSARIAL_BLOCK = (
    "<adversarial>\n{{lanes.adversarial.answer}}\n\n{{lanes.adversarial.diff}}\n</adversarial>\n\n"
)


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

    @property
    def build_terms(self) -> list[tuple[str, float]]:
        terms = [(f"{self.spec_items} spec items", self.spec_items * USD_PER_SPEC_ITEM)]
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
    def mission_budget(self) -> float:
        lanes = self.build_cap + self.gemini_cap + self.grok_cap + self.fix_cap
        if self.adversarial:
            lanes += self.adversarial_cap
        return round(lanes + USD_MISSION_SLACK, 2)

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
        if self.adversarial:
            lines.append(line("adversarial cap", self.adversarial_terms, self.adversarial_cap))
        lines.append(line("fix cap", self.fix_terms, self.fix_cap))
        lines.append(
            f"grace: ${self.cap_grace_usd:.2f} per claude lane and the grok read lane "
            "(E24/F5, on top of its own cap; does not change the caps above)"
            if self.cap_grace_usd
            else "grace: disabled (E24/F5)"
        )
        lines.append(
            f"mission budget: lanes ${self.mission_budget - USD_MISSION_SLACK:.2f} "
            f"+ ${USD_MISSION_SLACK:.2f} slack = ${self.mission_budget:.2f}"
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
) -> CapArithmetic:
    if spec_items < 1:
        raise ShapeInvalid("--items must be at least 1")
    if modules < 1:
        raise ShapeInvalid("--modules must be at least 1")
    if cap_grace_usd < 0 or cap_grace_usd > CAP_GRACE_CEILING_USD:
        raise ShapeInvalid(f"--cap-grace-usd must be between 0 and ${CAP_GRACE_CEILING_USD:.2f}")
    return CapArithmetic(
        spec_items=spec_items,
        modules=modules,
        scheduler=scheduler,
        grok_runs_suite=grok_runs_suite,
        cap_grace_usd=cap_grace_usd,
        adversarial=adversarial,
    )


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
        "prompt": "{{mission.prompt}}",
    }
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
            "timeout": 1800,
            "cap_usd": caps.adversarial_cap,
            "test_policy": "allow",
            "commit": f"test({scope}): adversarial check for {mission_name}",
            "prompt": ADVERSARIAL_PROMPT,
        }
        if caps.cap_grace_usd:
            adversarial_lane["cap_grace_usd"] = caps.cap_grace_usd
    fix_prompt = FIX_PROMPT
    if adversarial:
        fix_prompt = fix_prompt.replace(
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
        "prompt": GROK_REVIEW_PROMPT if caps.grok_runs_suite else GROK_READ_ONLY_PROMPT,
    }
    if caps.cap_grace_usd:
        review_grok["cap_grace_usd"] = caps.cap_grace_usd
    fix: dict = {
        "name": "fix",
        "stage": "fix",
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
        "needs": (
            ["build", "review-gemini", "review-grok", "adversarial"]
            if adversarial
            else ["review-gemini", "review-grok"]
        ),
        "resume": "build",
        "no_op_ok": True,
        "timeout": 1800,
        "cap_usd": caps.fix_cap,
        "test_policy": test_policy,
        "branch": branch,
        "commit": fix_commit,
        "cascade": False,
        "prompt": fix_prompt,
    }
    if caps.cap_grace_usd:
        fix["cap_grace_usd"] = caps.cap_grace_usd
    policy = {
        "build": {"vendors": ["anthropic"]},
        "review": {"vendors": ["google", "xai"]},
        "fix": {"vendors": ["anthropic"]},
    }
    if adversarial:
        policy["adversarial"] = {"vendors": ["anthropic"]}
    return {
        "name": mission_name,
        "cwd": rel(repo),
        "prompt_file": rel(spec),
        "concurrency": 2,
        "require": "all",
        "max_cost_usd": caps.mission_budget,
        "test": test,
        "template_max_chars": 160000,
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
                "prompt": GEMINI_REVIEW_PROMPT,
            },
            review_grok,
            *([adversarial_lane] if adversarial_lane is not None else []),
            fix,
        ],
    }


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
    live to that.
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
        f"Review or fix the change as it stands; the fix lane lands on branch '{branch}'."
    )
    mission_prompt = f"{spec_prompt.rstrip()}\n\n{salvage_note}" if spec_prompt else salvage_note
    change_block = (
        f"The change is commit {salvage_sha}, HEAD of this working directory"
        + (f" (its parent is {base_sha})" if base_sha else "")
        + f". Read it with `git show {salvage_sha}`."
    )
    gemini_prompt = GEMINI_REVIEW_PROMPT.replace("{{lanes.build.diff}}", change_block)
    grok_prompt = GROK_READ_ONLY_PROMPT.replace("{{lanes.build.diff}}", change_block)
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
    fix: dict = {
        "name": "fix",
        "stage": "fix",
        "fleet": "claude",
        "model": "sonnet",
        "effort": "standard",
        "mode": "write",
        "needs": ["review-gemini", "review-grok"],
        "no_op_ok": True,
        "timeout": 1800,
        "cap_usd": caps.fix_cap,
        "test_policy": "allow",
        "branch": branch,
        "commit": fix_commit,
        "cascade": False,
        "prompt": FIX_PROMPT,
    }
    if caps.cap_grace_usd:
        fix["cap_grace_usd"] = caps.cap_grace_usd

    return {
        "name": mission_name,
        "cwd": str(worktree),
        "prompt": mission_prompt,
        "concurrency": 2,
        "require": "all",
        "max_cost_usd": round(
            caps.gemini_cap + caps.grok_cap + caps.fix_cap + USD_MISSION_SLACK, 2
        ),
        "test": test,
        "template_max_chars": 160000,
        "policy": {
            "review": {"vendors": ["google", "xai"]},
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
            fix,
        ],
    }
