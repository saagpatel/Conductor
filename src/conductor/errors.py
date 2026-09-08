"""One label for why a dispatch was not ok.

`runner.Result.failure()` already reduces a dispatch to one line of prose;
this module reduces it further, to one of a fixed set of labels, so a
mission's fallback list can select on *why* an attempt failed instead of
escalating every failure down the same list. A rate limit and a gate failure
both currently read as "not ok" to `mission._run_attempts`; nothing on the
receipt says which, without reading the prose.

`error_kind` never raises: an unrecognized shape reads as `unknown` rather
than crashing an unattended run over a text field a vendor changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .outputs import INCOMPLETE
from .verify import NO_OP_COMMIT_REASONS

if TYPE_CHECKING:
    from .runner import Result

# Checked in this order; first match wins. A cap kill that also timed out is
# `cap`, not `timeout`; a fleet error that also mentions a rate limit is
# `rate_limit`, not `fleet_error`.
# D9: the prefix `runner.dispatch` writes on a receipt for a run that was
# paid for and then failed while its output was being read -- a malformed
# envelope, an unparseable deliverable, a settlement that raised. Matched by
# prefix rather than by a field of its own so an older receipt rehydrates
# into the same classification.
PARSE_FAILURE_PREFIX = "parse failed: "

KINDS: tuple[str, ...] = (
    "interrupted",
    "cancelled",
    "parse",
    # Conductor's own checks come before the cap (2026-09-08 review): an
    # unpriced run cannot be shown to have stayed under its cap, so `capped`
    # read every one of these as `cap` on a cursor lane. See
    # `_own_check_kind`.
    "setup",
    "taint",
    "settings",
    "refused",
    "agent",
    "adversarial",
    "plan",
    "denied",
    "reproduce",
    "cap",
    "breaker",
    "timeout",
    "rate_limit",
    "transport",
    "refusal",
    "fleet_error",
    "exit",
    "gate_test_surface",
    "gate",
    "deliverable",
    "no_op",
    "read_moved_bytes",
    "no_answer",
    "commit",
    "unknown",
)

# fleet ("*" = every fleet), kind, case-insensitive substring, where seen.
# rate_limit and transport are read from the fleet's own reported error text
# (`Result.fleet_error`); refusal also reads Claude's `subtype` (see
# `_is_refusal`). One tuple per pattern keeps each addition a one-line diff.
_PATTERN_TABLE: tuple[tuple[str, str, str, str], ...] = (
    ("*", "rate_limit", "rate limit", "generic prose wording used by every vendor"),
    ("*", "rate_limit", "rate_limit", "machine-readable variant of the same wording"),
    ("*", "rate_limit", "429", "raw HTTP status quoted in an error message"),
    ("*", "rate_limit", "overloaded", "Anthropic's 'Overloaded' error type"),
    ("*", "rate_limit", "529", "Anthropic's overloaded HTTP status"),
    ("*", "rate_limit", "quota", "OpenAI/Google quota-exceeded wording"),
    ("*", "rate_limit", "resource exhausted", "Google gRPC RESOURCE_EXHAUSTED"),
    ("*", "rate_limit", "too many requests", "generic HTTP 429 status text"),
    ("*", "transport", "ECONNRESET", "TCP connection reset"),
    ("*", "transport", "ECONNREFUSED", "TCP connection refused"),
    # The same failure in prose. Without it, a claude receipt reading
    # "Connection refused" under an `error_*` status matched `_is_refusal`'s
    # "refus" and was classified as a safety refusal, which no retry policy
    # covers, instead of the transport error a `retry: [transport]` lane
    # would have retried (2026-09-08 review).
    ("*", "transport", "connection refused", "a connection refused in prose"),
    ("*", "transport", "ETIMEDOUT", "TCP connection timed out"),
    ("*", "transport", "EPIPE", "broken pipe on a dropped connection"),
    ("*", "transport", "socket hang up", "a Node HTTP client (cursor-agent, agy) losing a socket"),
    ("*", "transport", "fetch failed", "Node undici fetch failure"),
    ("*", "transport", "network", "generic network-failure wording"),
    ("*", "transport", "502", "bad gateway"),
    ("*", "transport", "503", "service unavailable"),
    ("*", "transport", "504", "gateway timeout"),
    (
        "*",
        "transport",
        "stream ended without a result event",
        "outputs.py's own cut-short marker, set for every streaming fleet",
    ),
)

RATE_LIMIT_PATTERNS: tuple[str, ...] = tuple(
    pattern for _fleet, kind, pattern, _seen in _PATTERN_TABLE if kind == "rate_limit"
)
TRANSPORT_PATTERNS: tuple[str, ...] = tuple(
    pattern for _fleet, kind, pattern, _seen in _PATTERN_TABLE if kind == "transport"
)

# Claude's own budget and turn-limit stops are `error_*` subtypes but are not
# refusals; every other `error_*` subtype is a candidate, confirmed by the
# text itself.
_CLAUDE_NON_REFUSAL_SUBTYPES = frozenset({"error_max_budget_usd", "error_max_turns"})
_REFUSAL_WORDS = ("refus", "cannot help", "not able to")


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(pattern.lower() in low for pattern in patterns)


def _is_refusal(fleet: str, text: str, status: str | None) -> bool:
    low = text.lower()
    if "i can't help" in low or "i cannot help" in low:
        return True
    return bool(
        fleet == "claude"
        and isinstance(status, str)
        and status.startswith("error_")
        and status not in _CLAUDE_NON_REFUSAL_SUBTYPES
        and any(word in low for word in _REFUSAL_WORDS)
    )


def capped(result: Result) -> bool:
    """True only for a genuine cap verdict, not merely a shared `killed` flag.

    `budget.settle()` (budget.py) sets `exceeded=True` for *any* kill the
    watcher observed -- a breaker trip included (`killed=capped or
    breaker_reason is not None` in runner.py) -- so `budget["exceeded"]` alone
    cannot tell a real cap kill from a breaker kill that happened to run
    under a cap. A kill is only `cap` here when there is independent evidence
    of it: the native budget flag, an observed spend over the cap, or no
    breaker trip at all to blame it on instead.
    """
    budget = result.budget or {}
    if not budget:
        return False
    if budget.get("unpriced"):
        # An unpriced run cannot be shown to have stayed under its cap, so
        # `cap` is the safe reading -- unless conductor already watched
        # something else end the run. A timeout kill and a breaker trip are
        # both its own observations, and naming either one `cap` puts the
        # wrong cause on the receipt: a Grok read lane killed at its 600s
        # timeout came back kind `cap` purely because Cursor prices
        # post-hoc (2026-09-08). Same reasoning D9 applies to `parse`.
        return not (result.timed_out or (result.breaker or {}).get("tripped"))
    if not budget.get("exceeded"):
        return False
    if result.fleet_status == "error_max_budget_usd":
        return True
    cap_usd = budget.get("cap_usd")
    observed = budget.get("observed_usd")
    if cap_usd is not None and observed is not None and observed > cap_usd:
        return True
    return not (result.breaker or {}).get("tripped")


def _gate_failed(result: Result) -> bool:
    """Mirrors `Result.failure()`'s own gate check: whichever gate counted
    (the clean replacement, or the fleet's own) ran and did not pass."""
    clean = (result.test_surface or {}).get("clean_gate") or {}
    counted = clean if clean.get("ran") else result.tests
    return bool(counted and counted.get("ran") and not result.gate_passed)


def _gate_test_surface_failed(result: Result) -> bool:
    """A narrower `_gate_failed`: only the clean gate's plain "exited N"
    failure (not a timeout or an interruption, which keep their own kind),
    and only when the diff itself is what touched the test surface -- the
    trap AGENTS.md rule 3 exists for, distinct from an ordinary broken build.

    Excludes an `infra_error` outcome (`runner._git_failure`: worktree add,
    read-tree, or git apply failing before the gate command ever ran) --
    that is a transplant-machinery failure, not evidence the gate command
    itself was tripped up by the touched fixtures."""
    clean = (result.test_surface or {}).get("clean_gate") or {}
    if not clean.get("ran") or result.gate_passed:
        return False
    if clean.get("interrupted") or clean.get("timed_out") or clean.get("infra_error"):
        return False
    surface = result.test_surface or {}
    return bool(surface.get("touched") and surface.get("changed"))


def _no_op(result: Result) -> bool:
    verdict = result.git_verdict or {}
    moved_nothing = bool(verdict.get("checked") and verdict.get("no_op"))
    return result.mode == "write" and moved_nothing and not result.no_op_ok


def _read_moved_bytes(result: Result) -> bool:
    verdict = result.git_verdict or {}
    return (
        result.mode == "read"
        and bool(verdict.get("checked"))
        and not verdict.get("no_op")
        and not verdict.get("deliverable_only")
    )


def _deliverable_failed(result: Result) -> bool:
    deliverable = result.deliverable
    return bool(deliverable and deliverable.get("ok") is False)


def _no_answer(result: Result) -> bool:
    return result.mode == "read" and result.exit_code is not None and not result.answer_path


def _commit_failed(result: Result) -> bool:
    commit = result.commit
    if not commit or commit.get("committed"):
        return False
    nothing = commit.get("reason") in NO_OP_COMMIT_REASONS
    return not (result.no_op_ok and nothing)


def _own_check_kind(result: Result) -> str | None:
    """The kind for a failure conductor itself decided, from the `error`
    text it wrote: a setup command, a taint or settings verdict, a refusal
    before the paid turn ever spawned, a persona or adversarial or plan or
    permission check, or the reproduce gate. None when the receipt carries
    no such prefix.

    Split out and asked BEFORE `capped` (2026-09-08 review), for D9's own
    reason: an unpriced run -- every cursor lane -- cannot be shown to have
    stayed under its cap, so `capped` reads any of these as `cap`. A lane
    that failed its setup, or was refused before it spawned, has its cause
    on the receipt already, and naming it `cap` puts the wrong one there
    and sends a `fallback: [{on: ["cap"]}]` lane chasing a budget that was
    never the problem.
    """
    error_text = result.error or ""
    if error_text == "setup timed out" or error_text.startswith("setup failed:"):
        return "setup"
    # F13: the free `/hooks` preflight can now fail this closed before the
    # paid turn ever spawns (`spawned=False`), the same prefix the
    # after-the-run log-count check below has always used -- checked ahead
    # of the generic `refused` catch-all so a pre-spawn taint failure keeps
    # the same kind as a post-spawn one, like `setup` above.
    if error_text.startswith("taint hooks not enforced:"):
        return "taint"
    # F12: --restricted's own promise (no Bash, no WebFetch in the init
    # tool list), verified on bytes the same way E21 verifies agy's hooks --
    # a claude-fleet-only check, so it shares the `taint` kind rather than
    # inventing a second confinement label.
    if error_text.startswith("restricted mode not enforced:"):
        return "taint"
    # Settings digest (third drill pass, 2026-09-07): a claude write lane
    # that created, changed, or deleted one of its own
    # `.claude/settings*.json` files -- conductor's own digest comparison
    # across the run, never a fleet's word, and its own kind rather than
    # `taint` because it fires on an untainted lane too.
    if error_text.startswith("settings modified:"):
        return "settings"
    if not result.spawned and result.error:
        return "refused"
    # D3: the persona assertion writes its own `error` text directly on the
    # receipt (never through `fleet_error`, since no fleet reports this --
    # it is conductor's own check of the stream's init event).
    if error_text.startswith("agent '") and " not applied: " in error_text:
        return "agent"
    # E16: conductor's own check, from the git diff against the adversarial
    # lane's base -- never a fleet's word, like `agent` and `taint` above.
    if error_text.startswith("adversarial lane changed source:"):
        return "adversarial"
    # E10: a plan lane's child mission checks (load, depth, budget, ceiling,
    # dry run) and its launch outcome -- conductor's own checks and its own
    # recursive `run_mission` result, never a fleet's word.
    if error_text.startswith("plan:"):
        return "plan"
    # F12: a write lane where `--permission-prompts none` denied something --
    # conductor's own read of `result.permission_denials`, never a fleet's
    # word, like `agent`, `taint`, and `plan` above.
    if error_text.startswith("permission denied:"):
        return "denied"
    # F19: the reproduce-before-fix gate's own refusals (a fix or adversarial
    # lane without a reproducing check, or a check that already passes on the
    # base) -- conductor's own verdict on the transplanted test surface,
    # never a fleet's word. Read as `unknown` until the F18 fix lane hit it.
    if " without a reproducing check:" in error_text or error_text.startswith(
        "reproduce gate passed on the base:"
    ):
        return "reproduce"
    return None


def error_kind(result: Result) -> str | None:
    """One of `KINDS`, or `None` when `result.ok`."""
    if result.ok:
        return None
    # `_bail` refusals during setup or the reproduce gate route a stop
    # request through `error` text rather than the `interrupted` field
    # (they never reach the code that sets it); the prefix every such
    # message shares is the second half of this check.
    if result.interrupted or (result.error or "").startswith("interrupted:"):
        return "interrupted"
    if result.cancelled:
        return "cancelled"
    # D9: checked ahead of the cap because a run whose output could not be
    # read comes back with no priced usage, which `capped` reads as an
    # unenforced cap. What actually ended this run is the parse, and the
    # receipt exists only because conductor wrote it after the fact.
    if (result.error or "").startswith(PARSE_FAILURE_PREFIX):
        return "parse"
    own_check = _own_check_kind(result)
    if own_check is not None:
        return own_check
    if capped(result):
        return "cap"
    if (result.breaker or {}).get("tripped"):
        return "breaker"
    if result.timed_out:
        return "timeout"
    fleet_text = result.fleet_error
    if fleet_text:
        if _matches(fleet_text, RATE_LIMIT_PATTERNS):
            return "rate_limit"
        if _matches(fleet_text, TRANSPORT_PATTERNS):
            return "transport"
        if _is_refusal(result.fleet, fleet_text, result.fleet_status):
            return "refusal"
        return "fleet_error"
    # D15: a stream that stopped before its fleet's terminal event is the
    # same class of failure as the cut-short streams the transport table
    # above already matches on their `error` text; this one carries a status
    # instead, because no fleet reported it.
    if result.fleet_status == INCOMPLETE:
        return "transport"
    if result.exit_code not in (0, None):
        return "exit"
    if _gate_test_surface_failed(result):
        return "gate_test_surface"
    if _gate_failed(result):
        return "gate"
    # A run that never really finished (interrupted, capped, timed out,
    # gated, rate-limited) must classify as whatever ended it, not as
    # "deliverable" merely because the file also happens to be missing --
    # mirrors `Result.failure()`'s own order.
    if _deliverable_failed(result):
        return "deliverable"
    if _no_op(result):
        return "no_op"
    if _read_moved_bytes(result):
        return "read_moved_bytes"
    if _no_answer(result):
        return "no_answer"
    if _commit_failed(result):
        return "commit"
    return "unknown"
