# Verdict-Bench: Finishing Plan

## The Criterion (from Alexander Sorrell, Nov 20 2026)

> "That's the problem I'm most interested in solving. If your harness can generalize under those conditions — truly unknown worlds, no prior knowledge of tools/rules/failure modes, can it discover how the environment works, form hypotheses, recognize when it's wrong, recover, and verify itself without a human stepping in."

## Current State (World 1 Results)

| Arm | Pass@1 | Criteria |
|-----|--------|----------|
| OmniCLI as shipped | 72% (18/25) | 63/87 |
| Harness² stock | 68% (17/25) | 57/87 |
| OmniCLI + AF2 (gates) | **100% (25/25)** | **87/87** |

AF2 gates that got 100%:
- **Completion gate**: run can't end until deliverable exists
- **Output validation gate**: deliverable must parse + carry schema keys
- **Script-first method**: after 4 consecutive single-file reads, switch to one script
- **600s model-call timeout**
- **Read-streak gate**: after 4 consecutive single-file reads, force script mode

## The Gap: AF2 Only Works on Known Task Types

AF2 was tuned on a *dev world* (different seed). The README admits:
> "It does not yet show generalisation to new task types (that needs a fresh world and the APEX test split)."

**This is exactly Sorrell's criterion.** AF2 fails on truly unknown worlds because:
- It assumes task families exist (contract-compliance, ops-reporting, etc.)
- It assumes criteria names are known ahead of time
- It assumes the environment responds predictably
- No discovery mechanism when the model's hypothesis is wrong

## Plan: Three Gates for Unknown Worlds

### Gate 1: Environment Discovery (wasn't in AF2)

Before any hypothesis, the harness must map the unknown:

| Task | Mechanism |
|------|-----------|
| Discover available tools | Probe `which`, `help`, `PATH`, environment variables |
| Discover filesystem layout | `ls /`, `find / -maxdepth 3`, identify writable dirs |
| Discover output expectations | Look for READMEs, task descriptions, schema files, example outputs |
| Discover failure modes | Try intentionally bad commands, observe error messages |

**Deliverable**: `discovery.md` — map of environment, tools, paths, expected output shapes

### Gate 2: Hypothesis + Self-Correction (extends AF2)

AF2 validates output but doesn't hypothesize about *why* it failed:

| Failure mode | Detection | Recovery |
|--------------|-----------|----------|
| Command returns error | stderr non-empty | Parse error message, try alternative command |
| Output wrong shape | Schema validation fails | Read schema/examples, reformulate |
| Task stalls > N steps | Step count exceeded | Reset context, retry with different approach |
| File doesn't exist | FileNotFoundError | Re-discover paths, try alternatives |
| Output empty/truncated | File size 0 or partial | Re-run with different flags |

**Deliverable**: `hypothesis_log.md` — each hypothesis, test, result, correction

### Gate 3: Independent Verification (extends AF2 completion gate)

AF2 checks deliverable exists + parses. Unknown worlds need more:

| Check | Method |
|-------|--------|
| Self-verification | Re-run the deliverable against the environment, confirm it works |
| Cross-check | Compare output against any available schema/example |
| Independence | Verify without re-reading the task description |
| Reproducibility | Run twice, same result |

**Deliverable**: `verification_report.md` — pass/fail with evidence

## Combined Approach: OmniCLI AF2 + Harness² Lessons

### What Harness² Did (from README)

- Editor model watches agent run, writes improvement proposals
- Improver blind to rubrics/scores (can't game the metric)
- Propose-Probe-Compose loop
- Harness² stock got 68% — lower than OmniCLI's 72%

### What We Can Borrow

| Harness² idea | How we adapt |
|--------------|--------------|
| Editor watches agent | Discovery log reviewed by a separate "reviewer" agent |
| Improver blind to scores | Reviewer writes gate improvements without seeing task criteria |
| Propose-Probe-Compose | Each iteration: propose → probe environment → compose fix |

### OmniCLI AF2 Gates We Keep

| Gate | Why we keep it |
|------|---------------|
| Completion gate | Unknown world or not, no deliverable = no credit |
| Output validation | Schema keys may be unknown, but structure can be inferred |
| Script-first method | Prevents context waste on exploration |
| Read-streak gate | Forces action over endless reading |

## Verdict-Bench Folder Structure

```
verdict-bench/
  world/           # World generator + gold + grader
  runner/          # Sandbox, per-arm launcher, shared relay
  harness2_adapter/ # Harness² adapter (run_domain loop)
  apex/            # APEX-Agents wrapper
  results/         # Per-attempt results
```

## What Needs to Be Built

### 1. Unknown-World World Generator (`world/build_world.py`)

- Generate a world with **unknown task types** (not from the 5 known families)
- Each task has hidden rules the model must discover
- Gold answers computed in code, hidden from agent
- Independent solver verifies gold without LLM judge

### 2. Discovery Layer (`runner/discovery.py`)

- Probe environment on first contact
- Build `discovery.md` before any task-solving
- Cache discovery across tasks in same world
- Re-discover if environment changes

### 3. Hypothesis Layer (`runner/hypothesis.py`)

- Model forms hypothesis about task goal
- Tests hypothesis against environment
- On mismatch: parse error, adjust hypothesis, retry
- Log each hypothesis/test/correction cycle

### 4. Self-Verification Layer (`runner/verify.py`)

- Re-run deliverable in isolation
- Confirm output matches expected shape
- Run twice for reproducibility
- Mark as verified or failed

## Acceptance Criteria for "Finished"

| Criterion | Threshold |
|-----------|-----------|
| Pass@1 on known World 1 | Maintain 100% (AF2 baseline) |
| Pass@1 on unknown World 2 | Target: ≥70% (up from AF2's unknown world failure) |
| Hypothesis recovery rate | ≥80% of wrong hypotheses corrected within 3 attempts |
| Self-verification accuracy | ≥90% of verified results actually correct |
| Human intervention | Zero — no human steps in during unknown world run |

## Timeline

| Phase | Task | Effort |
|-------|------|--------|
| 1 | Create verdict-bench folder structure (done) | 5 min |
| 2 | Build unknown-world world generator | 2 hrs |
| 3 | Implement discovery layer | 3 hrs |
| 4 | Implement hypothesis + self-correction | 4 hrs |
| 5 | Implement self-verification | 2 hrs |
| 6 | Run World 1 (regression test AF2 = 100%) | 1 hr |
| 7 | Run World 2 (unknown world) | 1 hr |
| 8 | Report results + merge learnings | 1 hr |

**Total: ~14 hours**

## Files to Create

```
/verdict-bench/world/build_world.py       # unknown-world generator
/verdict-bench/world/grader.py            # code-graded criteria
/verdict-bench/world/independent_solver.py # no-LLM gold verification
/verdict-bench/runner/discovery.py         # environment discovery
/verdict-bench/runner/hypothesis.py        # hypothesis + recovery
/verdict-bench/runner/verify.py            # self-verification
/verdict-bench/runner/relay.py             # shared model relay (reuse from existing)
/verdict-bench/results/                    # per-attempt results
```

## How This Beats Harness²

| Harness² | Our Approach |
|----------|-------------|
| Propose-Probe-Compose | Same loop, but WITH discovery + hypothesis gates |
| Editor watches agent | Discovery log + hypothesis log make failures visible |
| Improver blind to scores | Gates developed without seeing task criteria |
| Stock: 68% | Our AF2 baseline: 100% on known, target ≥70% on unknown |

## Bottom Line

AF2 proved deterministic gates take OmniCLI from 72% to 100% on **known** worlds. Sorrell's criterion is **unknown** worlds. The finish plan adds discovery + hypothesis + self-verification on top of AF2 gates. That's what makes the harness worth exploring together.
