# Peer review notes: conductor at 0.60.0

Reviewer: GPT-6 Astra (Codex app, operator-run, 2026-09-07).
Brief: `docs/review/2026-09-07-peer-review-brief.md`. Contract: fill each section below; an
empty section says "nothing met the bar" rather than being deleted. Every defect and weakness
cites a file and line or a receipt path. No quotas apply anywhere in this file.

## 1. What conductor is, in the reviewer's words

<!-- Two or three paragraphs. The lead uses this to check that the review rests on a correct
model of the project before reading the rest. -->

## 2. First-principles assessment

<!-- Is this the right shape for the problem? Mission/lane model, trust boundary, where the
judgment sits, what the architecture makes easy and what it makes hard. Where would you have
drawn the lines differently and why. -->

## 3. Defects

<!-- One entry per item:
- **file:line** what goes wrong; one sentence of consequence; confidence 1-10; proposed fix in
  one or two sentences. -->

## 4. Weaknesses and risks

<!-- Not defects: trust and taint model, isolation, cost accounting, resume and receipt
integrity, the gate, fail-closed checks that have never been drilled, operational traps. Same
entry shape as section 3. -->

## 5. Simplifications

<!-- What to delete, merge, or flatten. Name the module or feature, what it costs to keep, what
breaks if it goes. -->

## 6. Test suite and golden fixtures

<!-- Coverage that matters versus coverage that exists; load-sensitive tests; what the fixtures
prove and what they cannot. -->

## 7. Prompts, docs, and the model notes

<!-- The prompts conductor sends (shape.py, prompts.py, mission.py), AGENTS.md's model notes and
prompt rules, the README as a map. What is wrong, stale, or missing. -->

## 8. The lead's eight ideas

<!-- Keep / change / drop for each, one to three sentences of reason. -->

| # | idea | verdict | reason |
|---|---|---|---|
| 1 | first-run drills for fail-closed checks | | |
| 2 | Shape C as a launcher policy option | | |
| 3 | spec-fidelity stage before review | | |
| 4 | Shape D for prose deliverables | | |
| 5 | live dispositions and calibration, then rule 10 on data | | |
| 6 | wall-clock receipts into the cost model | | |
| 7 | leaner Sonnet build prompt naming the gate flags | | |
| 8 | reproduce gate for non-code fix lanes | | |

## 9. Recommended roadmap items

<!-- Ranked. For each: name, why (cite the receipt or code), modules touched, whether it touches
the scheduler, the wait loop, or resume, and a rough spec-item count. Include fixes from
sections 3 and 4 that deserve a mission of their own. -->

## 10. Disagreements with standing decisions

<!-- One paragraph each, reasoning included, or "none". The operator decides whether to
reopen. -->

## 11. Questions for the operator

## 12. What was checked and what was not

<!-- Commands run and their outcome (gate, golden check, report), files read in full versus
skimmed, receipts opened. The lead reads this section first. -->
