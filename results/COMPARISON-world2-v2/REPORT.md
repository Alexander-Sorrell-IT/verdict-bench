# verdict-bench — APEX Evaluation Report

> **Verdict:** `PASS` | **Rule Version:** `1` | **Target Pass@1 (95% Wilson LB):** `70%` | **Attempts:** `75` (`0` voided)

Generated deterministically by `runner/report.py` from committed artifacts:
- **rows sha256:** `0395e52647d4d90d`
- **gold sha256:** `90dc21c75f8a326a`
- **split sha256:** `dfe0eb42f1122c62`
- **model route(s):** `nvidia/nemotron-3-ultra-550b-a55b` via `http://127.0.0.1:8787/v1`

## 1. Executive Leaderboard

| Rank | Arm | Overall Pass@1 | Wilson 95% LB | Dev Pass@1 | Held-Out Test Pass@1 | Criteria | Valid JSON | Avg Steps | Avg Calls | Avg Wall | Gate Blocks / Repairs | Verdict |
|------|-----|----------------|---------------|------------|----------------------|----------|------------|-----------|-----------|----------|-----------------------|---------|
| #1 | **GATED** | 25/25 (100%) | 87% | 15/15 (100%) | 10/10 (100%) | 25/25 | 100% | 3.5 | 3.5 | 46.1s | 3 / 0 | **PASS** |
| #2 | **MINIMAL** | 25/25 (100%) | 87% | 15/15 (100%) | 10/10 (100%) | 25/25 | 100% | 3.7 | 3.7 | 37.9s | 0 / 0 | **PASS** |
| #3 | **C** | 24/25 (96%) | 80% | 14/15 (93%) | 10/10 (100%) | 24/25 | 96% | 4.0 | 4.0 | 36.5s | 0 / 0 | **PASS** |

## 2. Per-Domain Capability Matrix

| Domain | Tasks (Dev/Test) | GATED | MINIMAL | C |
|--------|------------------|------|------|------|
| command-output-transformation | 5 (3/2) | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |
| config-parsing | 5 (3/2) | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |
| environment-variable-resolution | 5 (3/2) | 5/5 (100%) | 5/5 (100%) | 4/5 (80%) |
| file-hash-verification | 5 (3/2) | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |
| log-pattern-detection | 5 (3/2) | 5/5 (100%) | 5/5 (100%) | 5/5 (100%) |

## 3. Pairwise Exact McNemar (C vs GATED vs MINIMAL)

Paired strictly on tasks both arms completed live (excluding voids).

| Pair | Paired | First Only | Second Only | Both Pass | Both Fail | Discordant | Exact p-value | Sig (α=0.05) |
|------|--------|------------|-------------|-----------|-----------|------------|---------------|--------------|
| C vs GATED | 25 | 0 | 1 | 24 | 0 | 1 | 1.0000 | no |
| C vs MINIMAL | 25 | 0 | 1 | 24 | 0 | 1 | 1.0000 | no |
| GATED vs MINIMAL | 25 | 0 | 0 | 25 | 0 | 0 | 1.0000 | no |

### Discordant Tasks: C vs GATED (1 of 25 paired, p = 1.0000)
- W2-019 [DEV] (environment-variable-resolution): **GATED** won over C (1/1 vs 0/1, loser scored zero: True)

### Discordant Tasks: C vs MINIMAL (1 of 25 paired, p = 1.0000)
- W2-019 [DEV] (environment-variable-resolution): **MINIMAL** won over C (1/1 vs 0/1, loser scored zero: True)

### Discordant Tasks: GATED vs MINIMAL (0 of 25 paired, p = 1.0000)
- Perfect agreement on all paired tasks.

## 4. Full Task Outcome Matrix

| Task | Split | Domain | GATED | MINIMAL | C |
|------|-------|--------|------|------|------|
| W2-000 | [DEV] | config-parsing | PASS (2s/2c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-001 | [TEST] | log-pattern-detection | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-002 | [DEV] | file-hash-verification | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-003 | [DEV] | command-output-transformation | PASS (5s/5c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-004 | [TEST] | environment-variable-resolution | PASS (3s/3c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-005 | [DEV] | config-parsing | PASS (2s/2c) | PASS (2s/2c) | PASS (4s/4c) |
| W2-006 | [DEV] | log-pattern-detection | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-007 | [DEV] | file-hash-verification | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-008 | [TEST] | command-output-transformation | PASS (5s/5c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-009 | [DEV] | environment-variable-resolution | PASS (3s/3c) | PASS (5s/5c) | PASS (4s/4c) |
| W2-010 | [DEV] | config-parsing | PASS (2s/2c) | PASS (2s/2c) | PASS (4s/4c) |
| W2-011 | [DEV] | log-pattern-detection | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-012 | [DEV] | file-hash-verification | PASS (2s/2c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-013 | [DEV] | command-output-transformation | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-014 | [TEST] | environment-variable-resolution | PASS (3s/3c) | PASS (3s/3c) | PASS (4s/4c) |
| W2-015 | [TEST] | config-parsing | PASS (2s/2c) | PASS (2s/2c) | PASS (4s/4c) |
| W2-016 | [TEST] | log-pattern-detection | PASS (5s/5c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-017 | [TEST] | file-hash-verification | PASS (2s/2c) | PASS (5s/5c) | PASS (4s/4c) |
| W2-018 | [TEST] | command-output-transformation | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-019 | [DEV] | environment-variable-resolution | PASS (4s/4c) | PASS (6s/6c) | FAIL (unparseable_compose) |
| W2-020 | [TEST] | config-parsing | PASS (2s/2c) | PASS (2s/2c) | PASS (4s/4c) |
| W2-021 | [DEV] | log-pattern-detection | PASS (4s/4c) | PASS (4s/4c) | PASS (4s/4c) |
| W2-022 | [TEST] | file-hash-verification | PASS (2s/2c) | PASS (3s/3c) | PASS (4s/4c) |
| W2-023 | [DEV] | command-output-transformation | PASS (4s/4c) | PASS (3s/3c) | PASS (4s/4c) |
| W2-024 | [DEV] | environment-variable-resolution | PASS (8s/8c) | PASS (4s/4c) | PASS (4s/4c) |

## 5. Failure-Mode Telemetry & Gate Activity

| Arm | Live | Failed | Empty Answers | Invalid JSON | Overflows | Stopped Reasons (Failures) | Gate Blocks | Gate Repairs |
|-----|------|--------|---------------|--------------|-----------|----------------------------|-------------|--------------|
| GATED | 25 | 0 | 0 | 0 | 0 | none | evidence_grounding: 2, sandbox: 1 | none |
| MINIMAL | 25 | 0 | 0 | 0 | 0 | none | none | none |
| C | 25 | 1 | 1 | 1 | 0 | unparseable_compose: 1 | none | none |

## 6. Void-and-Rerun Policy Audit

0 of 75 recorded attempts were voided (wall-clock timeout rc=124 or transport-dead empty attempt, never graded on their merits).

## 7. Recovery, Measured

No contradiction was injected in this run.

## 8. Budget Parity & Cryptographic Custody

- **Model parity:** OK (matched: `nvidia/nemotron-3-ultra-550b-a55b`)
- **Budget overflows:** 0
- **Receipt chain:** OK (`{'ok': True, 'count': 76, 'broken_seq': None, 'salt_ok': True}`)

| Arm | Models | Total Steps | Total Model Calls | Total Wall (s) | Overflows | Voided |
|-----|--------|-------------|-------------------|----------------|-----------|--------|
| C | nvidia/nemotron-3-ultra-550b-a55b | 100 | 100 | 912.3s | 0 | 0 |
| GATED | nvidia/nemotron-3-ultra-550b-a55b | 88 | 88 | 1151.7s | 0 | 0 |
| MINIMAL | nvidia/nemotron-3-ultra-550b-a55b | 93 | 93 | 948.1s | 0 | 0 |

### Independent Verdict (rule version 1)

- **VERDICT:** `PASS`

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