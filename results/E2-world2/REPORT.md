# verdict-bench — APEX Evaluation Report

> **Verdict:** `REJECT` | **Rule Version:** `1` | **Target Pass@1 (95% Wilson LB):** `70%` | **Attempts:** `50` (`0` voided)

Generated deterministically by `runner/report.py` from committed artifacts:
- **rows sha256:** `9decd0feb5b21f83`
- **gold sha256:** `90dc21c75f8a326a`
- **split sha256:** `dfe0eb42f1122c62`
- **model route(s):** `nvidia/nemotron-3-ultra-550b-a55b` via `http://127.0.0.1:8787/v1`

## 1. Executive Leaderboard

| Rank | Arm | Overall Pass@1 | Wilson 95% LB | Dev Pass@1 | Held-Out Test Pass@1 | Criteria | Valid JSON | Avg Steps | Avg Calls | Avg Wall | Gate Blocks / Repairs | Verdict |
|------|-----|----------------|---------------|------------|----------------------|----------|------------|-----------|-----------|----------|-----------------------|---------|
| #1 | **GATED** | 18/25 (72%) | 52% | 11/15 (73%) | 7/10 (70%) | 18/25 | 80% | — | — | — | 25 / 0 | **INCONCLUSIVE** |
| #2 | **MINIMAL** | 17/25 (68%) | 48% | 11/15 (73%) | 6/10 (60%) | 17/25 | 80% | — | — | — | 0 / 0 | **REJECT** |

## 2. Per-Domain Capability Matrix

| Domain | Tasks (Dev/Test) | GATED | MINIMAL |
|--------|------------------|------|------|
| command-output-transformation | 5 (3/2) | 0/5 (0%) | 0/5 (0%) |
| config-parsing | 5 (3/2) | 5/5 (100%) | 5/5 (100%) |
| environment-variable-resolution | 5 (3/2) | 3/5 (60%) | 2/5 (40%) |
| file-hash-verification | 5 (3/2) | 5/5 (100%) | 5/5 (100%) |
| log-pattern-detection | 5 (3/2) | 5/5 (100%) | 5/5 (100%) |

## 3. Pairwise Exact McNemar (GATED vs MINIMAL)

Paired strictly on tasks both arms completed live (excluding voids).

| Pair | Paired | First Only | Second Only | Both Pass | Both Fail | Discordant | Exact p-value | Sig (α=0.05) |
|------|--------|------------|-------------|-----------|-----------|------------|---------------|--------------|
| GATED vs MINIMAL | 25 | 1 | 0 | 17 | 7 | 1 | 1.0000 | no |

### Discordant Tasks: GATED vs MINIMAL (1 of 25 paired, p = 1.0000)
- W2-004 [TEST] (environment-variable-resolution): **GATED** won over MINIMAL (1/1 vs 0/1, loser scored zero: True)

## 4. Full Task Outcome Matrix

| Task | Split | Domain | GATED | MINIMAL |
|------|-------|--------|------|------|
| W2-000 | [DEV] | config-parsing | PASS | PASS |
| W2-001 | [TEST] | log-pattern-detection | PASS | PASS |
| W2-002 | [DEV] | file-hash-verification | PASS | PASS |
| W2-003 | [DEV] | command-output-transformation | FAIL (unparseable_reply) | FAIL (unparseable_reply) |
| W2-004 | [TEST] | environment-variable-resolution | PASS | FAIL (answered) |
| W2-005 | [DEV] | config-parsing | PASS | PASS |
| W2-006 | [DEV] | log-pattern-detection | PASS | PASS |
| W2-007 | [DEV] | file-hash-verification | PASS | PASS |
| W2-008 | [TEST] | command-output-transformation | FAIL (unparseable_reply) | FAIL (unparseable_reply) |
| W2-009 | [DEV] | environment-variable-resolution | FAIL (stopped_by_gate) | FAIL (answered) |
| W2-010 | [DEV] | config-parsing | PASS | PASS |
| W2-011 | [DEV] | log-pattern-detection | PASS | PASS |
| W2-012 | [DEV] | file-hash-verification | PASS | PASS |
| W2-013 | [DEV] | command-output-transformation | FAIL (unparseable_reply) | FAIL (unparseable_reply) |
| W2-014 | [TEST] | environment-variable-resolution | FAIL (stopped_by_gate) | FAIL (answered) |
| W2-015 | [TEST] | config-parsing | PASS | PASS |
| W2-016 | [TEST] | log-pattern-detection | PASS | PASS |
| W2-017 | [TEST] | file-hash-verification | PASS | PASS |
| W2-018 | [TEST] | command-output-transformation | FAIL (unparseable_reply) | FAIL (unparseable_reply) |
| W2-019 | [DEV] | environment-variable-resolution | PASS | PASS |
| W2-020 | [TEST] | config-parsing | PASS | PASS |
| W2-021 | [DEV] | log-pattern-detection | PASS | PASS |
| W2-022 | [TEST] | file-hash-verification | PASS | PASS |
| W2-023 | [DEV] | command-output-transformation | FAIL (unparseable_reply) | FAIL (unparseable_reply) |
| W2-024 | [DEV] | environment-variable-resolution | PASS | PASS |

## 5. Failure-Mode Telemetry & Gate Activity

| Arm | Live | Failed | Empty Answers | Invalid JSON | Overflows | Stopped Reasons (Failures) | Gate Blocks | Gate Repairs |
|-----|------|--------|---------------|--------------|-----------|----------------------------|-------------|--------------|
| GATED | 25 | 7 | 5 | 5 | 0 | stopped_by_gate: 2, unparseable_reply: 5 | completion: 25 | none |
| MINIMAL | 25 | 8 | 5 | 5 | 0 | answered: 3, unparseable_reply: 5 | none | none |

## 6. Void-and-Rerun Policy Audit

0 of 50 recorded attempts were voided (wall-clock timeout rc=124 or transport-dead empty attempt, never graded on their merits).

## 7. Recovery, Measured

No contradiction was injected in this run.

## 8. Budget Parity & Cryptographic Custody

- **Model parity:** OK (matched: `nvidia/nemotron-3-ultra-550b-a55b`)
- **Budget overflows:** 0
- **Receipt chain:** OK (`{'ok': True, 'count': 51, 'broken_seq': None, 'salt_ok': True}`)

| Arm | Models | Total Steps | Total Model Calls | Total Wall (s) | Overflows | Voided |
|-----|--------|-------------|-------------------|----------------|-----------|--------|
| GATED | nvidia/nemotron-3-ultra-550b-a55b | — | — | — | 0 | 0 |
| MINIMAL | nvidia/nemotron-3-ultra-550b-a55b | — | — | — | 0 | 0 |

### Independent Verdict (rule version 1)

- **VERDICT:** `REJECT`

## 9. Disclosures & Gate Blind Spots

- Voided attempts are excluded from every live score above; the recorded (pre-void) outcomes are published in Section 6.
- World 1 cannot be re-run: no task prompts, no world files, no gold hashes, and the launcher that produced its results is gone. What the recorded attempts support is offline re-verification, nothing more.
- The gates cannot check:
  - that the tasks are actually hard (a trivially guessable world passes every gate)
  - that the answer formulas are absent from the MODEL's context window at runtime
  - that an arm cannot reach gold through the model, a tool, or the OS
  - that the gold is CORRECT - a verifiable wrong answer is still wrong
  - that the run was fair (budget, load, retries) - that is bench.py's job

Every figure above regenerates byte-identically from the committed artifacts via `python -m runner.report`; no number was typed by hand.