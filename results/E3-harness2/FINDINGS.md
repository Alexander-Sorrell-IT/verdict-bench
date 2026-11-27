# E.3 — arm C (the Harness² shape, ported in-repo): 3/25

Recorded: 3/25 passed (193 model calls, 696 s, receipts chain OK, 26 receipts).
Per family: config-parsing 0/5, log-pattern-detection 2/5,
file-hash-verification 0/5, command-output-transformation 1/5,
environment-variable-resolution 0/5. 21 of 25 empty (compose produced nothing
the extractor accepted).

The one correct answer shows the shape CAN land: W2-003
`{"transformed": "HIDDEN_DATA_8060"}` — the exact hash match the bench arms
failed on that task (both reply tool-call-shaped there and score 0). So the
two shapes fail on opposite families, which is itself the comparative signal.

## What this comparison does and does not say

Same world (frozen hash verified), same model (nemotron-3-ultra-550b-a55b via
the relay), same evidence naming, same per-task call allowance in effect (~5
model calls per task for all three arms).

Does NOT say "Google's Harness² scores 3/25": arm C is the Harness² SHAPE
(propose -> probe -> compose, lessons threaded per family) ported to this
repo's own plumbing — no docker, no OpenCode, no the external harness2
package. It is our port of the shape, not their implementation.

Budget disclosure: the bench arms run under spec/arms.json's Budget object
(200 model calls / 400 steps / 2700 s, which MAX_ATTEMPTS=5 caps in effect at
5 calls per task); arm C runs under its own probe budget (3 probes + 1
compose + 1 lesson), not the spec's Budget. The per-task allowance matched in
effect; the GOVERNING object did not. Wiring the spec Budget through arm C is
the parity fix for the next comparison run — until then the comparison is
same-world/same-model/same-per-task-allowance, not same-budget-object.
