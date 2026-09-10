# Consumer: the evidence map and Shape C on one real mission (W7), 2026-09-07

Release 0.68.0. The first mission after Phase H items 4, 5, and 6 shipped, chosen so three new
things get a live receipt at once: the build lane's evidence map (item 6), `shape a
--opus-review` (Shape C, item 4), and the wall block's critical path and occupancy (item 5, W8).
The spec was W7 from the peer review: label the reviewer precision table's selection and
denominators, three items in `report.py` with tests. Mission
`20260907T142156Z-w7-precision-labels`, launched from the conductor checkout.

## Shape

`conductor shape a --items 3 --tests-items 1 --modules 1 --opus-review`, ceiling $20 per hour.
The launcher's $5.00 build cap sat under the forecast's $8.04 p80 for Anthropic builds, so the
lead raised it to $8.00 by hand before launch (rule 10); the mission budget became $23. The
first process was interrupted at ten cents to relaunch detached from the lead's tool session and
resumed; nothing was paid twice.

| lane | model | outcome | cost |
|---|---|---|---|
| build | Sonnet 5, hard | green, gate 49 s, `evidence.json` written, captured, removed from the worktree, worktree released clean | $1.54 |
| review-gemini | Gemini 3.7 Flash | NO_FINDINGS; its reply checked the evidence map against the touched files | $0.15 |
| review-grok | Grok 4.6, read only | 1 finding, confidence 7: the printed heading still called the figure precision | $0.38 |
| review-opus | Opus 5, hard | 4 findings, confidence 5 to 8 | $1.67 |
| fix | Sonnet 5 on the build's thread | 3 fixed, 1 refused, 1 wording; landed on `feat/w7-precision-labels` | $1.61 |

$5.35 on the mission receipt, 22 minutes wall clock, landed by `conductor land` with its own gate
and golden run, then the lead's gate: 1445 passed, golden exit 0.

## What each new piece did

**Evidence map.** The build wrote a three-entry map, each item `built`, with the files, the test
functions by name, and the gate command as the check. Opus's third finding is the map doing its
job: the map claimed the test for spec item 3(c) (an old receipt reads the new fields as zero)
was covered by a test that constructed the row directly and never read a receipt. The reviewer
read the map as a claim, checked it against the test, and reported the gap with the map's own
entry as the citation; the fix lane rewrote the coverage through the real replay path. Without
the map the claim existed only in the builder's head.

**Shape C.** Gemini passed the change, Grok found the heading, and Opus found the heading plus a
definitional defect the pair did not: `missions` counted a mission for every vendor with a review
lane on it, including a vendor whose findings never received a disposition, which inflated the
denominator the column exists to expose. The fix lane reproduced it (the existing unparsed-verdict
test now asserts the count is zero) and moved the bookkeeping into the disposition loop. Opus's
finding on a vacuous test assertion was refused by the fix lane under the reproduce gate, with a
reason the lead agrees with: a stricter assertion passes on the current tree, so there is nothing
to reproduce. Three of four Opus findings were real; $1.67 for the lane.

**Wall block.** First live reading of the W8 figures:

| figure | seconds |
|---|---|
| wall_s | 1327.6 |
| paused_s | 27.7 |
| occupied_s | 1258.2 |
| critical_path_s | 1206.1 |
| lead_s | 41.7 |
| lanes_s (sum, overlaps) | 1428.7 |

Stretch 1.10: the mission took ten percent longer than its critical path, and the lead's own
time on the clock was 42 seconds, the pause answered in 28. The three reviewers ran side by side
(concurrency 3) and `lanes_s` exceeds `wall_s` for that reason, which is what W8 said to stop
reading as utilisation.

## What the consumer found in conductor

Nothing that failed. Two observations for the record: the launcher's build cap from rule 2 was
under the forecast's p80 and the lead overrode it by hand, which the forecast warning exists
for; and the `undispositioned` count as built covers a mission with no dispositions at all, while
a vendor whose lane parsed on a mission whose dispositions named only another vendor is counted
in `findings` but in neither `missions` nor `undispositioned` (Opus's mirror case). The row is
still honest, since nothing is scored for that vendor; it is a definition to sharpen when the
column has more data behind it.
