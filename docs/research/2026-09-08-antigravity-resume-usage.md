# Antigravity resumed-run usage accounting — 2026-09-08

Completed Antigravity streams now use their own per-step usage when usable
step counters are present. The terminal envelope still supplies the answer,
status, error, and session identity. Envelope-only output retains its existing
fallback behavior; interrupted streams already used the observed step totals.

A two-turn live helper comparison exposed the mismatch. The second invocation's
terminal counters were cumulative over the conversation, while its new step
updates described only the second invocation:

| Counter | First invocation | Resumed terminal | Resumed steps |
|---|---:|---:|---:|
| Uncached input | 224,725 | 267,812 | 43,087 |
| Output, including thinking | 106,537 | 110,002 | 3,465 |
| Thinking, included in output above | 41,485 | 42,169 | 684 |
| Cache reads | 1,357,382 | 2,315,514 | 958,132 |

For each counter, resumed terminal minus first invocation equals resumed steps.
These are host-reported counters from captured streams, not provider invoices
or subscription debits. Committed tests contain counter-only reductions rather
than prompts, model reasoning, or native session identifiers.

Previously, the budget watcher used the new steps but completed-run receipts
used the cumulative terminal envelope. Pricing those receipts counted the
first invocation again. The parser now shares `agy_step_usage` with the watcher
and incomplete-stream path. The latest update per valid numeric step index is
counted once. Malformed indices are ignored so a malformed step cannot crash
completed-stream parsing. Explicit zero counts are valid; unusable counters do
not create a free run or erase usable legacy envelope usage. Cumulative envelope
dollar figures are discarded when step counters supply the run's usage.

The legacy fallback cannot establish incremental usage from a cumulative
terminal envelope alone. This fix relies on the per-step stream that Conductor
requests from Antigravity; it does not infer a missing prior-turn baseline.
Historical receipts were not rewritten.

Verification:

- A dispatch regression failed on the original source with 267,812 input tokens
  where 43,087 were expected. It now passes through real local subprocesses,
  persisted run receipts, token pricing, and combined spend aggregation. Those
  subprocesses replay fixture output; they do not call a vendor.
- Replaying both complete captured host streams yields their exact step sums.
- Ruff passed; the full parallel gate passed 2,221 tests on Python 3.14.7.
- The affected parser, budget, and dispatch tests passed 92 tests on Python
  3.12.13 with parallel workers.
- The first full run used a cache under the user home and failed one unrelated
  export test because its fixture contained that home path. Running temporary
  fixtures outside the user home resolved it; no export code or test changed.

Composer 2.5 implemented the parser change and initial unit tests. The lead
added dispatch/spend integration coverage, invalid-counter cases, and malformed
step-index handling. Grok 4.6 independently identified the malformed step-index issue after
reading an earlier version of the function. The guard above resolves that
finding; all six invalid-index regression cases are included in the final
passing gates. Git diff was blocked in its Ask-mode session, so its review
used source and test reads; the lead inspected the actual diff.
