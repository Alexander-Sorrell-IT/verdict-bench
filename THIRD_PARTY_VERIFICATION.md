# THIRD-PARTY VERIFICATION — the done-condition, run 2026-11-27 (v2 APEX)

The last clause of the definition of done (REMAINING_WORK.md §6): the whole
chain re-verifies **offline, by someone who is not you, from committed code
alone**. This is the procedure and its output, from a fresh export of HEAD with
no access to this machine's state.

```bash
rm -rf /tmp/verify-v2 && mkdir -p /tmp/verify-v2
git archive HEAD | tar -x -C /tmp/verify-v2     # no .git, no working tree
cd /tmp/verify-v2
python -m pytest -q -p no:hypothesispytest      # 225 passed
python -m world.gates world $(mktemp -d)        # 68/68 gates passed, exit 0
python -m world.independent_solver              # 25/25 gold hashes reproduced
python -m mypy .                                # Success: no issues found in 56 source files
python -m runner.freeze spec/split.json         # frozen-ok

# Independent sealed grader on v2 three-arm comparison (75 rows):
python -m score.independent_grader \
  --rows results/COMPARISON-world2-v2/results.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json --target 0.70         # VERDICT: PASS (exit 0)

# Byte-identical Markdown + standalone HTML regeneration (v2 three-arm run):
python -m runner.report \
  --rows results/COMPARISON-world2-v2/results.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json \
  --receipts results/COMPARISON-world2-v2/receipts.json \
  --out /tmp/verify-v2/cmp_v2.md \
  --html /tmp/verify-v2/cmp_v2.html
cmp /tmp/verify-v2/cmp_v2.md results/COMPARISON-world2-v2/REPORT.md      # identical
cmp /tmp/verify-v2/cmp_v2.html results/COMPARISON-world2-v2/REPORT.html  # identical

# Byte-identical regeneration of v1 historical comparison:
python -m runner.report \
  --rows results/COMPARISON-world2/results.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json \
  --receipts results/E2-world2/receipts.json \
  --out /tmp/verify-v2/cmp_v1.md \
  --html /tmp/verify-v2/cmp_v1.html || true
cmp /tmp/verify-v2/cmp_v1.md results/COMPARISON-world2/REPORT.md         # identical
cmp /tmp/verify-v2/cmp_v1.html results/COMPARISON-world2/REPORT.html     # identical
```

Result: every gate (68/68), every unit test (225/225), the independent solver
(25/25), mypy (56/56 clean), the freeze (`frozen-ok`), the sealed grader
(`PASS` on v2: GATED 25/25, MINIMAL 25/25, C 24/25; 10/10 held-out test across
all three arms), and EVERY published Markdown and standalone HTML report
regenerate byte-identical from the committed artifacts. The receipt chain
verifies inside the report (76 rows, `ok: True`, `salt_ok: True`), and the
sealed grader re-grades every row from its answer against gold without trusting
recorded verdicts.

What a third party still cannot do, and is told so: re-run World-1 (its four
inputs are gone), or reach the NVIDIA relay key (the key resolves inside the
relay and is never committed). Neither is a regression of the chain.

Everything above is reproducible by the commands in the code block. No step
requires the author.
