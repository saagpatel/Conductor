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

SHAPE_VERSION = "a-2026-09-06"

# Rule 2 and rule 10, as constants with the rule number beside each.
USD_PER_SPEC_ITEM = 1.0  # rule 2: about a dollar per spec item on Sonnet at hard
USD_SCHEDULER_TAX = 2.0  # rule 2: scheduler, runner wait loop, or resume
USD_PER_EXTRA_MODULE = 1.0  # rule 2: every module past the second
USD_CLAUDE_SUMMARY = 1.0  # rule 10: Claude's final summary costs, on every cap
USD_FIX_BASE = 2.0  # a fix lane is a one- or two-item spec on the resumed thread
USD_GEMINI_READ = 1.0  # rule 7: Gemini reads only on this shape
USD_GROK_READ = 1.5  # rule 7: Grok reading only
USD_GROK_SUITE = 2.0  # rule 7: Grok when it runs the suite
USD_MISSION_SLACK = 1.5  # room for a retry's partial spend before the mission budget sinks

REVIEW_TAIL = (
    "Report anything that could cause incorrect behavior, a test failure, or a misleading "
    "result, including a spec item that is missing or only partly implemented. Omit pure "
    "style and naming. For each item: file and line, what goes wrong, one sentence of "
    "consequence, your confidence 1-10. If nothing meets that bar, reply exactly: "
    "NO_FINDINGS. Either answer is complete. Put the entire review in this reply; the "
    "reply is the only thing the next agent receives."
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

FIX_PROMPT = (
    "The spec below is already implemented on this branch, by you earlier in this thread. "
    "Two reviewers from other vendors read the change; their reports follow.\n\n"
    "<spec>\n{{mission.prompt}}\n</spec>\n\n"
    "<review_gemini>\n{{lanes.review-gemini.answer}}\n</review_gemini>\n\n"
    "<review_grok>\n{{lanes.review-grok.answer}}\n</review_grok>\n\n"
    "For each reported item, first write a test that fails on the current tree because of "
    "it; then fix only what that test proves, and say which items you rejected and why. "
    "This lane runs under conductor's reproduce gate: a fix with no test change is refused, "
    "and a test that already passes on the current tree is refused. If both reviews say "
    "NO_FINDINGS or nothing reproduces, change nothing and reply NO_CHANGES. Keep existing "
    "call signatures working. Run the gate named in the spec before finishing, with "
    "--basetemp under $TMPDIR. Do not commit; the harness commits."
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
    def mission_budget(self) -> float:
        lanes = self.build_cap + self.gemini_cap + self.grok_cap + self.fix_cap
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
            line("fix cap", self.fix_terms, self.fix_cap),
            f"mission budget: lanes ${self.mission_budget - USD_MISSION_SLACK:.2f} "
            f"+ ${USD_MISSION_SLACK:.2f} slack = ${self.mission_budget:.2f}",
        ]
        return "\n".join(lines)


def cap_arithmetic(
    spec_items: int,
    modules: int,
    *,
    scheduler: bool = False,
    grok_runs_suite: bool = False,
) -> CapArithmetic:
    if spec_items < 1:
        raise ShapeInvalid("--items must be at least 1")
    if modules < 1:
        raise ShapeInvalid("--modules must be at least 1")
    return CapArithmetic(
        spec_items=spec_items,
        modules=modules,
        scheduler=scheduler,
        grok_runs_suite=grok_runs_suite,
    )


def _prefix(repo: Path, about: str | None) -> str:
    what = about or f"the repository '{repo.name}'"
    return (
        f"You are one lane of a conductor mission on {what}. Every claim is judged on "
        "bytes: the diff, the gate, the receipts. Read before you write; cite file and line "
        "for anything you report."
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
    return {
        "name": mission_name,
        "cwd": rel(repo),
        "prompt_file": rel(spec),
        "concurrency": 2,
        "require": "all",
        "max_cost_usd": caps.mission_budget,
        "test": test,
        "template_max_chars": 160000,
        "policy": {
            "build": {"vendors": ["anthropic"]},
            "review": {"vendors": ["google", "xai"]},
            "fix": {"vendors": ["anthropic"]},
        },
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
            {
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
            },
            {
                "name": "fix",
                "stage": "fix",
                "fleet": "claude",
                "model": "sonnet",
                "effort": "standard",
                "mode": "write",
                "base": "build",
                "needs": ["review-gemini", "review-grok"],
                "resume": "build",
                "no_op_ok": True,
                "timeout": 1800,
                "cap_usd": caps.fix_cap,
                "test_policy": test_policy,
                "branch": branch,
                "commit": fix_commit,
                "cascade": False,
                "prompt": FIX_PROMPT,
            },
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
) -> dict:
    """E23: the review-and-fix mission for a salvage the lead has already
    committed by hand (AGENTS.md rule 6). Identical in shape to `shape_a`
    except there is no build lane: the kept worktree, already sitting at the
    lead's own commit, is the mission's `cwd` directly, so no lane needs a
    `base` to build on and the fix lane has nothing to `resume`. The two
    review prompts are `GEMINI_REVIEW_PROMPT` and `GROK_READ_ONLY_PROMPT`
    (never the suite-running one -- there is no build lane's session for
    Grok to fall back to reproducing by hand), with `{{lanes.build.diff}}`
    resolved eagerly to `diff` since there is no build lane to render it from;
    `{{mission.prompt}}` still renders live, to the paragraph below.

    `mission_dir` is accepted for the same reason `shape_a`'s is (the caller
    may want mission-relative bookkeeping); every path this function itself
    writes is already absolute, so it goes unused here.
    """
    del mission_dir
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

    mission_prompt = (
        f"This change is already committed at {salvage_sha} on branch '{branch}' in the "
        "worktree named as this mission's cwd: conductor's own clean gate rejected the "
        "original build lane's run, and the lead read the diff below, gated it by hand, "
        "and committed it. Review or fix the change as it stands.\n\n"
        f"<diff>\n{diff}\n</diff>"
    )
    gemini_prompt = GEMINI_REVIEW_PROMPT.replace("{{lanes.build.diff}}", diff)
    grok_prompt = GROK_READ_ONLY_PROMPT.replace("{{lanes.build.diff}}", diff)

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
            {
                "name": "review-grok",
                "stage": "review",
                "fleet": "cursor",
                "model": "grok-4.6",
                "effort": "standard",
                "mode": "read",
                "timeout": 1200,
                "cap_usd": caps.grok_cap,
                "prompt": grok_prompt,
            },
            {
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
                "branch": branch,
                "commit": fix_commit,
                "cascade": False,
                "prompt": FIX_PROMPT,
            },
        ],
    }
