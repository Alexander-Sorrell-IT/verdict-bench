# World 2, three arms, one frozen world — the comparison claim

Same world (frozen hash verified), same model (nvidia/nemotron-3-ultra-550b-a55b
via the relay), same evidence naming, same per-task call allowance in effect
(~5 model calls/task for all three arms).

| Arm | Shape | Passed | Pass@1 | Wilson 95% lower |
|---|---|---|---|---|
| MINIMAL | direct command/answer loop | 17/25 | 68% | 48% |
| GATED | the same loop + the AF2 gate set | 18/25 | 72% | 52% |
| C | the Harness² shape, ported in-repo | 3/25 | 12% | 4% |

Exact McNemar, paired on the tasks both arms completed live:
  C vs GATED:    17 discordant of 25, p = 0.0003
  C vs MINIMAL:  16 discordant of 25, p = 0.0005
  GATED vs MINIMAL: 1 discordant of 25, p = 1.0000

Per family:
| family | MINIMAL | GATED | C |
|---|---|---|---|
| config-parsing | 5/5 | 5/5 | 0/5 |
| log-pattern-detection | 5/5 | 5/5 | 2/5 |
| file-hash-verification | 5/5 | 5/5 | 0/5 |
| command-output-transformation | 0/5 | 0/5 | 1/5 |
| environment-variable-resolution | 2/5 | 3/5 | 0/5 |

## What the numbers say

1. The two bench arms are statistically indistinguishable (p = 1.0, one
   discordant task): on this world, with this model, the AF2 gate set changes
   nothing measurable. That is an honest null, not a disappointment.
2. The Harness²-shaped port is dramatically and significantly worse
   (p < 0.001 against either bench arm). The two shapes are COMPLEMENTARY, not
   ranked on one axis: the direct loop owns read-and-parse tasks; the port
   lands the single transform task both bench arms miss (their replies are
   tool-call-shaped there and the extractors reject them).
3. The transform family fails on the bench arms for a cause that is diagnosed
   and recorded (E.2 FINDINGS), not hidden.

## Caveats that travel with the numbers

- One 25-task world, one model, one rep per task. Nothing here generalizes
  beyond that.
- C is the Harness² SHAPE as ported to this repo's own plumbing, not Google's
  implementation (no docker, no OpenCode, no the external package).
- C runs under its own probe budget, not spec/arms.json's Budget object; the
  per-task allowance matched in effect, the governing object did not.
- The verdict remains REJECT by the pre-registered rule: no arm's 95% Wilson
  lower bound clears the 0.70 target at n=25, and the weakest arm decides.
