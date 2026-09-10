# Consumer: an anti-slop pass over one document (F10), 2026-09-07

Release 0.59.0. The second of F10's consumers to run: one document edited by a model lane as an
E1 deliverable, marked untrusted output (E3), reviewed cold by two models that did not write the
text, gated by a prose check instead of the code suite. The document was
`docs/research/2026-09-07-f15-shape-c-fixes.md`, 103 lines of the lead's own prose from an hour
earlier. Mission `20260907T101347Z-f10-anti-slop-f15-doc`.

## Shape

| lane | model | mode | cap | what |
|---|---|---|---|---|
| edit | Sonnet 5, hard | write, `untrusted_output: true`, deliverable = the document | $4.00 | the rules below, on its own branch |
| review-gemini | Gemini 3.7 Flash | read, `base: edit`, tainted by the edit's diff | $1.00 | report any fact, claim, or meaning that changed, or any rule-named pattern left |
| review-opus | Opus 5, hard | read, `base: edit`, tainted the same way | $2.50 | same prompt |

Gate: `scripts/prose_gate.py DOC`, new with this consumer: the file exists and is not empty, no
em or en dash, none of a filler-word list, every markdown table row has the header's cell count.
Rules given to the editor: remove filler, hedges, restatement, and unmeasured adjectives; active
voice where the actor is already named; split sentences joined by semicolons or parentheses; no
dashes, no parenthetical over four words; keep every number, hash, path, identifier, table cell,
heading, and section order byte-identical; add no claim, drop no claim.

Two things the shape could not be. Grok could not review: the edit lane's `untrusted_output`
taints every lane that references its diff, and taint on Cursor is refused (no rule kind covers
its web tools), so the second reviewer is Opus 5 with `self_judging: allow`, the same call Shape
B needed. And there is no fix lane: `stage: fix` runs under the reproduce gate, which refuses a
fix without a test-surface change, and a prose document has no test surface. The lead applied
the reviews by hand. That also means this consumer produced no `dispositions.json` receipt; the
first of those still waits for the next Shape A on code.

## The run

| lane | outcome | cost |
|---|---|---|
| edit | green: 1168 words to 1182, 47 lines changed, gate 0, committed on `feat/anti-slop-f15-doc` | $0.56 |
| review-gemini, first attempt | failed on bytes: `taint hooks not enforced: agy loaded 1 named hook(s), expected 30`; its answer (NO_FINDINGS, after "I have launched the command") did not count | $0.10 |
| review-opus | 8 findings, confidence 4 to 7, every one cited to a line | $0.77 |
| review-gemini, rerun after the fix below | 1 finding, confidence 10, the same 23-word parenthetical as Opus's item 3 | $0.10 |

$1.53 on the mission receipt (an earlier draft of this table listed the Gemini lane's cumulative $0.20 as the rerun and summed to $1.63; the outside review caught it). Wall clock 10 minutes across the two processes, concurrency 3, `paused_s 0`.

The editor's own note named its least certain edit ("real" was on the ban list, so "Grok's
finding was real and test-only" became "held, and applied only to the test"); neither reviewer
flagged it. What Opus found was the kind of drift the rules could not gate: a parenthetical
unwrapped into an "A, B, and C" series so two unbuilt items read as three; a sizing derivation
flattened so "8 items, 6 defects with tests counted twice" no longer shows where 8 came from;
"so" dropped from the sentence that explained why the fix lane was stopped; a pronoun moved past
a JSON blob; a period moved inside a quotation of the roadmap; a 23-word parenthetical the rules
forbade left untouched; an actor and a mission attribution supplied where the source named none.
The lead fixed the first six by hand (a932a10) and left the two attributions, which were true.
Gemini's rerun found one of the eight, the mechanical one, at confidence 10; its first attempt,
which the taint check failed, had written NO_FINDINGS without reading.

## What the consumer found in conductor

The Gemini lane's first failure was conductor's, not Gemini's. E21's after-run check required
agy's `loaded N named hooks from M hooks.json file(s)` line to carry N equal to the number of
matchers conductor wrote (30). agy counts named hooks per file: the whole deny file is one named
hook, and the log read `loaded 1 named hooks from 1 hooks.json file(s)`. The 2026-09-06 probe had
written one matcher, so 1 = 1 held there and the assumption was never tested at scale; this was
the first tainted Antigravity lane to run live since E21, and it failed every time by
construction. The F13 preflight had already shown the file loaded with all 30 matchers under its
`actions` list, which is the per-tool evidence. Fix (d1e2ac8): the preflight requires every
matcher conductor wrote to be named back in that list and records `matchers_missing`; the log
check fails only on N of zero, the malformed-file signal the probe found. Three tests: zero
named hooks fails, one named hook for thirty matchers passes, a preflight short of one matcher
fails before the paid turn. The rerun above is the live receipt: `hooks_written 30, hooks_loaded
1, preflight ok, matchers_missing []`.

## Reading

An anti-slop pass on a receipt document is cheap and the edit is modest: the editor shortened
almost nothing (the word count rose) and mostly split sentences, which is what the rules asked.
The value was the review: two cold readers at $1 caught eight meaning drifts in 47 changed lines,
and the ones that mattered were exactly the kind a word-list gate cannot see. For this shape the
reviewer is the product; the editor is the thing being reviewed. And the consumer paid for
itself in the first minute by running a code path (tainted Antigravity) that conductor's own
missions never exercise.
