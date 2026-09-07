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

if TYPE_CHECKING:
    from .runner import Result

# Checked in this order; first match wins. A cap kill that also timed out is
# `cap`, not `timeout`; a fleet error that also mentions a rate limit is
# `rate_limit`, not `fleet_error`. One exception to "checked in this order":
# `deliverable` sits beside `agent` here -- both are conductor's own checks,
# never a fleet's -- but is actually tested for later (see `error_kind`,
# after `gate`), mirroring `Result.failure()`'s own order: a run that never
# really finished (interrupted, capped, timed out, gated, rate-limited) must
# classify as whatever ended it, not as "deliverable" merely because the
# file also happens to be missing.
KINDS: tuple[str, ...] = (
    "interrupted",
    "cancelled",
    "cap",
    "breaker",
    "timeout",
    "setup",
    "refused",
    "agent",
    "deliverable",
    "taint",
    "adversarial",
    "plan",
    "rate_limit",
    "transport",
    "refusal",
    "fleet_error",
    "exit",
    "gate",
    "gate_test_surface",
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


def _capped(result: Result) -> bool:
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
        return True
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
    nothing = commit.get("reason") == "nothing to commit"
    return not (result.no_op_ok and nothing)


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
    if _capped(result):
        return "cap"
    if (result.breaker or {}).get("tripped"):
        return "breaker"
    if result.timed_out:
        return "timeout"
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
    fleet_text = result.fleet_error
    if fleet_text:
        if _matches(fleet_text, RATE_LIMIT_PATTERNS):
            return "rate_limit"
        if _matches(fleet_text, TRANSPORT_PATTERNS):
            return "transport"
        if _is_refusal(result.fleet, fleet_text, result.fleet_status):
            return "refusal"
        return "fleet_error"
    if result.exit_code not in (0, None):
        return "exit"
    if _gate_test_surface_failed(result):
        return "gate_test_surface"
    if _gate_failed(result):
        return "gate"
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
