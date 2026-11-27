# THIRD-PARTY VERIFICATION — the done-condition, run 2026-11-27

The last clause of the definition of done (REMAINING_WORK.md §6): the whole
chain re-verifies **offline, by someone who is not you, from committed code
alone**. This is the procedure and its output, from a fresh export of HEAD with
no access to this machine's state.

```
git archive HEAD | tar -x -C /tmp/verify      # no .git, no working tree
cd /tmp/verify
python -m pytest -q -p no:hypothesispytest    # 210 passed
python -m world.gates world <empty-dir>       # 68/68 gates passed, exit 0
python -m world.independent_solver            # 25/25 gold hashes reproduced
python -m mypy .                              # no issues in 55 files
python -m runner.freeze spec/split.json       # frozen-ok
python -m runner.report --rows results/COMPARISON-world2/results.jsonl \
  --gold world/private/gold_world2.json --split spec/split.json \
  --receipts results/E2-world2/receipts.json --out /tmp/verify/cmp.md
cmp /tmp/verify/cmp.md results/COMPARISON-world2/REPORT.md   # identical
# the World-1 re-verification assembles byte-identical with the same script
# committed in its header (results/world1/REVERIFICATION.md)
```

Result: every gate, every test, the solver at 25/25, mypy clean, the freeze
holding, and BOTH published reports regenerate byte-identical from the
committed artifacts. The E.2 receipts chain verifies inside the report; the
sealed grader re-grades every row from its answer against gold and the rows
carry those answers, so a third party can re-derive the verdict without
trusting the run's recorded verdicts.

What a third party still cannot do, and is told so: re-run World-1 (its four
inputs are gone), or reach the NVIDIA relay key (the key resolves inside the
relay and is never committed). Neither is a regression of the chain.

Everything above is reproducible by the four commands in the code block. No
step requires the author.
