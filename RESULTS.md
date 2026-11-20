# Results: OmniCLI vs Google Harness² on World 1

_Generated 2026-11-20 07:04 CST from the per-attempt results in `results/`._

**Setup:** World 1 (Halvorsen Freight ops & audit desk), 25 tasks, 87 binary criteria graded in code. Same model for every arm (`nvidia/nemotron-3-ultra-550b-a55b`), same prompt, same 8-minute wall per attempt, same sandbox image family, all model traffic through one shared relay. Pass@1 = one attempt per task (k=1). 95% intervals are bootstrap over tasks.

| Arm | Tasks scored | Pass@1 (95% CI) | Criteria passed | Wrong answers* |
|---|---|---|---|---|
| OmniCLI (Alex's harness), as shipped | 25/25 | **72%** (52%–88%) | 63/87 | 0 |
| Harness² stock (Google) | 25/25 | **68%** (48%–84%) | 57/87 | 0 |
| OmniCLI + deterministic gates (AF2) | 25/25 | **100%** (100%–100%) | 87/87 | 0 |

\*Wrong answer = the arm wrote a valid answer that failed a criterion. Every other failure is a run that ended without delivering an answer.

## Head-to-head: OmniCLI (as shipped) vs Harness² stock

On the 25 tasks both completed: OmniCLI 18, Harness² 17. Paired difference +4% (95% CI -20% to +28%). **Statistically indistinguishable at this sample size.**

- OmniCLI-only wins: D4, K3, K4, L1, R2
- Harness²-only wins: C1, D3, L2, L3

## Per-task matrix (✓ pass, ✗ fail, · not yet run)

| Task | Family | A | B | AF2 |
|---|---|---|---|---|
| C1 | contract-compliance | ✗ 0/4 | ✓ 4/4 | ✓ 4/4 |
| C2 | contract-compliance | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| C3 | contract-compliance | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| C4 | contract-compliance | ✗ 0/4 | ✗ 0/4 | ✓ 4/4 |
| C5 | contract-compliance | ✓ 4/4 | ✓ 4/4 | ✓ 4/4 |
| D1 | ops-reporting | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| D2 | ops-reporting | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| D3 | ops-reporting | ✗ 0/3 | ✓ 3/3 | ✓ 3/3 |
| D4 | ops-reporting | ✓ 4/4 | ✗ 0/4 | ✓ 4/4 |
| D5 | ops-reporting | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| K1 | config-audit | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| K2 | config-audit | ✗ 0/3 | ✗ 0/3 | ✓ 3/3 |
| K3 | config-audit | ✓ 3/3 | ✗ 0/3 | ✓ 3/3 |
| K4 | config-audit | ✓ 4/4 | ✗ 0/4 | ✓ 4/4 |
| K5 | config-audit | ✗ 0/4 | ✗ 0/4 | ✓ 4/4 |
| L1 | log-forensics | ✓ 4/4 | ✗ 0/4 | ✓ 4/4 |
| L2 | log-forensics | ✗ 0/3 | ✓ 3/3 | ✓ 3/3 |
| L3 | log-forensics | ✗ 0/3 | ✓ 3/3 | ✓ 3/3 |
| L4 | log-forensics | ✓ 4/4 | ✓ 4/4 | ✓ 4/4 |
| L5 | log-forensics | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |
| R1 | reconciliation | ✓ 4/4 | ✓ 4/4 | ✓ 4/4 |
| R2 | reconciliation | ✓ 4/4 | ✗ 0/4 | ✓ 4/4 |
| R3 | reconciliation | ✓ 4/4 | ✓ 4/4 | ✓ 4/4 |
| R4 | reconciliation | ✓ 4/4 | ✓ 4/4 | ✓ 4/4 |
| R5 | reconciliation | ✓ 3/3 | ✓ 3/3 | ✓ 3/3 |

## Per family (passed / scored)

| Family | A | B | AF2 |
|---|---|---|---|
| reconciliation | 5/5 | 4/5 | 5/5 |
| log-forensics | 3/5 | 4/5 | 5/5 |
| contract-compliance | 3/5 | 4/5 | 5/5 |
| config-audit | 3/5 | 1/5 | 5/5 |
| ops-reporting | 4/5 | 4/5 | 5/5 |

## Disclosures

- **Free-tier model endpoint.** NVIDIA's per-model quota (HTTP 429) throttled every arm equally through the shared relay.
- **Void rule (all arms):** attempts ended by infrastructure (launcher error, Docker I/O failure after the host disk filled, model-endpoint failure) were voided and re-run once under identical settings. One OmniCLI attempt that graded pass but ended in a Docker error was voided too.
- **OmniCLI variants:** AF2 adds a completion gate, an output-validation gate and a script-first method, developed only on a separate dev world (different seed); this world (the test world) was frozen by hash before any arm ran and never used for tuning.
- **Harness² arms:** B is Harness²'s own stock base harness on OpenCode 1.14.18 (its pinned version); C runs Harness²'s own `run_domain` Propose-Probe-Compose loop with the same model as executor and editor (its default).
- **What AF2's result does and does not show.** AF2 was tuned only on the dev world (different seed and data, same 25 task types), then frozen (image id in the change log) before touching this world. So it shows the gates generalise to unseen data for known task types; it does not yet show generalisation to new task types (that needs a fresh world and the APEX test split).
- **Provenance of each OmniCLI change.** The first change (AF: completion gate + 900 s shell default) was made after observing as-shipped OmniCLI fail on this world. Every later change (output-validation gate, script-first method, 600 s model-call timeout, read-streak gate) was developed on the dev world only.
- **Load matching.** AF2's test run used the same concurrency as A/B (Harness² loop stream 2 paused); relay median latency was 15.1 s during A/B and 14.8 s just before AF2.
- **Still running when generated:** any arm with fewer than 25 tasks scored. This file is regenerated as results land.

