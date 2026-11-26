# Verdict-Bench: Finish Plan — OmniCLI + Harness² Combined

## The Core Insight: They Fail Differently

From the World 1 results, OmniCLI and Harness² stock each win on different tasks:

| Family | Task | OmniCLI (A) | Harness² (B) | Winner |
|--------|------|-------------|--------------|--------|
| contract-compliance | C1 | ✗ 0/4 | ✓ 4/4 | **Harness²** |
| contract-compliance | K3 | ✓ 3/3 | ✗ 0/3 | **OmniCLI** |
| contract-compliance | K4 | ✓ 4/4 | ✗ 0/4 | **OmniCLI** |
| ops-reporting | D3 | ✗ 0/3 | ✓ 3/3 | **Harness²** |
| ops-reporting | D4 | ✓ 4/4 | ✗ 0/4 | **OmniCLI** |
| log-forensics | L2 | ✗ 0/3 | ✓ 3/3 | **Harness²** |
| log-forensics | L3 | ✗ 0/3 | ✓ 3/3 | **Harness²** |
| reconciliation | R2 | ✓ 4/4 | ✗ 0/4 | **OmniCLI** |

**They are statistically indistinguishable overall (72% vs 68%), but they fail on different tasks.** Combining them would cover both their strengths.

## Phase 1: Finish Verdict-Bench (Unknown Worlds) — ~14 hrs

### Step 1: World Generator with Unknown Task Families (~2 hrs)
Generate worlds where the model has NO prior knowledge of:
- Available tools
- Task rules
- Output format
- Failure modes

Each task has hidden rules computed in code (gold withheld).

### Step 2: Discovery Layer (~3 hrs)
Before any hypothesis, probe the environment:
- Available tools (which, PATH, env vars)
- Filesystem layout (ls /, find)
- Output expectations (READMEs, schemas, examples)
- Failure modes (try bad commands, observe errors)

### Step 3: Hypothesis + Self-Correction Layer (~4 hrs)
The model forms hypotheses, tests them, and recovers when wrong:
- Hypothesis: "the task wants a JSON file with key 'result'"
- Test: write candidate, check against environment
- On mismatch: parse error, adjust hypothesis, retry
- Log every cycle

### Step 4: Self-Verification Layer (~2 hrs)
AF2's completion gate checks deliverable exists + parses. Unknown worlds need more:
- Re-run deliverable in isolation (does it actually work?)
- Compare output against any schema/example
- Verify without re-reading task description
- Run twice for reproducibility

### Step 5: Run World 1 Regression + World 2 Unknown (~1 hr)
- Confirm AF2 still gets 100% on World 1 (known tasks)
- Run World 2 (unknown tasks) — target ≥70%

### Step 6: Report + Merge (~1 hr)
Document what worked, what didn't, merge learnings back to OmniCLI.

## Phase 2: Combine OmniCLI + Harness² Strengths — ~6 hrs

### What Each Harness Does Well

| OmniCLI Strength | Tasks Won |
|-----------------|-----------|
| Contract compliance (structured output) | K3, K4 |
| Ops reporting (log analysis) | D4, L1 |
| Reconciliation (cross-reference) | R2 |

| Harness² Strength | Tasks Won |
|------------------|-----------|
| Contract compliance (C1) | C1 |
| Ops reporting (D3) | D3 |
| Log forensics (L2, L3) | L2, L3 |

### Combined Harness Design

```
Task arrives
  → Run OmniCLI approach (structured, contract-focused)
  → Run Harness² approach (exploratory, log-focused)
  → If they agree: confirmed (high confidence)
  → If they disagree: run AF2 gates to resolve
    - completion gate: both must produce deliverable
    - output validation: both must parse + schema
    - script-first: consolidate into one probe script
  → Final verdict from AF2 gates
```

This covers both harnesses' blind spots.

### Expected Result

| Arm | Pass@1 (est) | Criteria |
|-----|-------------|----------|
| OmniCLI | 72% | 63/87 |
| Harness² | 68% | 57/87 |
| OmniCLI + AF2 | 100% | 87/87 |
| OmniCLI + Harness² combined | ≥100% target | 87/87 + unknown world generalization |

## Phase 3: Upgrade OmniCLI with Learnings — ~4 hrs

### What to Port Back to OmniCLI

From verdict-bench:
1. Discovery layer — probe environment before hypothesizing
2. Hypothesis + recovery — form hypotheses, test, recover when wrong
3. Self-verification — re-run deliverable, check reproducibility
4. Harness²'s Propose-Probe-Compose — editor watches agent, writes improvements

### OmniCLI Changes

```
oracle/omnicli/
  discovery.py         # NEW - environment probing
  hypothesis_engine.py # NEW - hypothesis + recovery cycle
  self_verify.py       # NEW - independent verification
  harness2_adapter.py  # NEW - borrow Harness²'s exploration style
  existing AF2 gates   # KEEP - completion, output validation, script-first
```

### Testing
- Run verdict-bench World 1 (known) — maintain 100%
- Run verdict-bench World 2 (unknown) — target ≥70%
- Run oracle test suite — 1805 tests, 0 failures

## Acceptance Criteria

| Criterion | Threshold |
|-----------|-----------|
| verdict-bench World 1 Pass@1 | 100% (AF2 baseline maintained) |
| verdict-bench World 2 Pass@1 | ≥70% (unknown world generalization) |
| Hypothesis recovery rate | ≥80% corrected within 3 attempts |
| Self-verification accuracy | ≥90% |
| Human intervention during unknown world run | Zero |
| OmniCLI test suite after upgrade | 1805 passed, 0 failed |

## Files to Create/Modify

### New Files (DONE)
```
verdict-bench/world/build_world.py       # unknown-world generator
verdict-bench/world/grader.py            # code-graded criteria
verdict-bench/runner/discovery.py        # environment discovery
verdict-bench/runner/hypothesis.py       # hypothesis + recovery cycle
verdict-bench/runner/verify.py           # self-verification
verdict-bench/runner/run_world.py        # main runner
verdict-bench/COMBINED_PLAN.md           # this plan
```

### To Build
```
verdict-bench/world/world_manifest.json  # generated by build_world.py
verdict-bench/world/gold_world2.json     # generated (withheld from agent)
verdict-bench/world/FREEZE_world2.json   # generated (integrity hash)
verdict-bench/results/                   # per-arm results

oracle/omnicli/discovery.py              # port from verdict-bench
oracle/omnicli/hypothesis_engine.py      # port from verdict-bench
oracle/omnicli/self_verify.py            # port from verdict-bench
oracle/omnicli/harness2_adapter.py       # borrow Harness² exploration
```

## Bottom Line

You're right: OmniCLI and Harness² fail differently. Combining them covers both blind spots. AF2 gates already get 100% on known worlds. Adding discovery + hypothesis + self-verification extends that to unknown worlds — Sorrell's criterion. Once verdict-bench is finished, the learnings flow back into OmniCLI, making it stronger on both known and unknown tasks.

**The path: finish verdict-bench → combine both harnesses → upgrade OmniCLI → run live bug bounty hunt.**
