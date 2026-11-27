# verdict-bench

**The model proposes; deterministic executed checks own the verdict.**

A standalone, zero-external-agent harness-vs-harness benchmark comparing in-repo agent harnesses (`GATED` with deterministic AF2 enforcement gates, `MINIMAL` ungated control, and `C` ported from Google Harness²'s Propose → Probe → Compose + dev-lesson loop) under a frozen unknown world, a single shared budget, a single shared model relay, cryptographic hash-chained receipts, and a sealed code-only grader.

---

## Headline Result — World 2 (Frozen, Matched Model & Budget, Graded in Code)

Model: `nvidia/nemotron-3-ultra-550b-a55b` via shared local relay (`http://127.0.0.1:8787/v1`) · Shared budget: [`spec/arms.json`](spec/arms.json) (`400` steps / `200` model calls / `2700s`) · Pre-registered rule: `RULE_VERSION = 1` (target **70%** Pass@1 **and** 95% Wilson lower bound) · **Sealed Grader Verdict: `PASS`**

| Rank | Arm | Overall Pass@1 | Wilson 95% LB | Dev (15) | Held-Out Test (10) | Avg Steps / Calls | Gate Blocks | Verdict |
|---|---|---|---|---|---|---|---|---|
| **#1** | **`GATED`** (AF2 gates) | **25/25 (100%)** | **87%** | **15/15 (100%)** | **10/10 (100%)** | **3.5 / 3.5** | 3 (`evidence_grounding: 2`, `sandbox: 1`) | **PASS** |
| **#2** | **`MINIMAL`** (ungated control) | **25/25 (100%)** | **87%** | **15/15 (100%)** | **10/10 (100%)** | **3.7 / 3.7** | 0 | **PASS** |
| **#3** | **`C`** (Harness² APEX dev→test) | **24/25 (96%)** | **80%** | **14/15 (93%)** | **10/10 (100%)** | **4.0 / 4.0** | 0 | **PASS** |

- **74/75** total passes across all three arms, **30/30 (100%)** on the frozen held-out test split, **0 voids**, **0 budget overflows**, **0 reconciliation discrepancies**, **76-row cryptographic receipt chain verified (`ok: True`, `salt_ok: True`)**.
- **Full generated APEX report & standalone HTML scorecard:**
  - Markdown: [`results/COMPARISON-world2-v2/REPORT.md`](results/COMPARISON-world2-v2/REPORT.md)
  - Standalone HTML dashboard: [`results/COMPARISON-world2-v2/REPORT.html`](results/COMPARISON-world2-v2/REPORT.html)
  - Raw rows & sealed receipts: [`results/COMPARISON-world2-v2/results.jsonl`](results/COMPARISON-world2-v2/results.jsonl), [`results/COMPARISON-world2-v2/receipts.json`](results/COMPARISON-world2-v2/receipts.json)
- **Before vs. after v2 harness fixes on the exact same frozen World 2 & model:**
  - `v1` baseline ([`results/COMPARISON-world2/REPORT.md`](results/COMPARISON-world2/REPORT.md)): `GATED` **18/25 (72%)**, `MINIMAL` **17/25 (68%)**, `C` **3/25 (12%)** → Verdict **`REJECT`**.
  - `v2` upgraded harness ([`results/COMPARISON-world2-v2/REPORT.md`](results/COMPARISON-world2-v2/REPORT.md)): `GATED` **25/25 (100%)**, `MINIMAL` **25/25 (100%)**, `C` **24/25 (96%)** → Verdict **`PASS`**.
  - The frozen world (`spec/frozen_run.json`), withheld gold, and sealed grader rule (`RULE_VERSION = 1`) did not change between `v1` and `v2`.

### Honest World-1 Re-Verification & Audit Trail

Earlier World-1 runs published `AF2 25/25` (`100%–100%` bootstrap CI), `A 18/25`, and `B 17/25`. Offline re-verification in code ([`results/world1/REVERIFICATION.md`](results/world1/REVERIFICATION.md)) uncovered that wall-clock timeouts (`rc=124`) had been counted as passes (17 of AF2's 25 rows, 12 of B's 25, 3 of A's 25). Applying the void policy in `runner/stats.py` leaves **AF2 8/8** (Wilson 95% LB **68%**), **A 16/22**, and **B 13/13** (paired exact McNemar A vs. B: 3 discordant of 12 paired, `p = 0.2500`). World 1 cannot be re-run because its original task files, gold, and launcher are gone; its offline audit is preserved verbatim beside the retraction record ([`AUDIT_2026-11-25.md`](AUDIT_2026-11-25.md)).

---

## Integrity Guarantees (Enforced in Code, Not Prose)

1. **No-Paradox World Gates (`world/gates.py`) & Independent Solver (`world/independent_solver.py`)** — 68 structural/leakage checks run automatically before every benchmark run, and a zero-LLM code solver reproduces **25/25 (100%)** of World-2 gold SHA-256 hashes from public evidence files alone.
2. **Cryptographic Pre-Run Freeze (`runner/freeze.py`)** — hashes the manifest, withheld gold, agent-visible files, and dev/test split against [`spec/frozen_run.json`](spec/frozen_run.json) before any arm starts; any mutation aborts the run.
3. **Structural Parity & Per-Arm Sandbox Isolation (`runner/bench.py`, `runner/operator_mode.py`)** — all arms share one `Budget` (`spec/arms.json`) and one `ModelClient` (`harness/model.py`); per-arm spec overrides are refused; every arm runs in its own fresh sandbox copy so artifacts written by one arm (`deliverable.json`) can never leak to another.
4. **C.1-Isolated Sealed Grader (`score/independent_grader.py`)** — imports **zero** `harness/` modules (verified in a subprocess with a broken `harness/` package), re-grades every row's raw answer against withheld gold (`world/grader.py`), rejects any run whose recorded verdicts disagree with re-grading, computes dev/test splits, excludes voided attempts (`rc=124` timeouts and transport-dead empties) from empty-answer rates, and runs pairwise exact McNemar tests.
5. **Tamper-Evident Receipt Chains (`runner/receipts.py`)** — SHA-256 hash-chained manifest and per-task grade receipts; flipping a single byte breaks verification with the exact sequence number.
6. **Byte-Deterministic APEX Reports (`runner/report.py`)** — generates both Markdown and zero-dependency standalone HTML scorecards with zero timestamps; regenerating from committed artifacts produces byte-identical files.

---

## Quickstart: Offline Third-Party Verification (No API Key Required)

Anyone can verify every test, gate, solver hash, type check, freeze hash, sealed verdict, and published report offline from a clean export of `HEAD` (see [`THIRD_PARTY_VERIFICATION.md`](THIRD_PARTY_VERIFICATION.md)):

```bash
python -m pytest -q -p no:hypothesispytest          # 225 passed
python -m mypy .                                    # Success: no issues found in 56 source files
python -m world.gates world $(mktemp -d)            # 68/68 gates passed
python -m world.independent_solver                  # 25/25 gold hashes reproduced
python -m runner.freeze spec/split.json             # frozen-ok

# Re-grade the v2 3-arm run independently from raw answers + gold:
python -m score.independent_grader \
  --rows results/COMPARISON-world2-v2/results.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json --target 0.70             # VERDICT: PASS

# Regenerate Markdown + standalone HTML and verify byte-identical match:
python -m runner.report \
  --rows results/COMPARISON-world2-v2/results.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json \
  --receipts results/COMPARISON-world2-v2/receipts.json \
  --out /tmp/cmp_v2.md --html /tmp/cmp_v2.html
cmp /tmp/cmp_v2.md results/COMPARISON-world2-v2/REPORT.md
cmp /tmp/cmp_v2.html results/COMPARISON-world2-v2/REPORT.html
```

---

## Running Live With Any OpenAI-Compatible Provider / Model

`verdict-bench` is standalone and works with any OpenAI-compatible endpoint configured in [`spec/providers.json`](spec/providers.json) (API keys resolve from environment variables and are never written to configs, rows, or receipts):

```bash
# 1. Start the shared local relay (throttles + retries 429/5xx equally across all arms)
export NVIDIA_API_KEY="nvapi-..."
python -m runner.relay --upstream https://integrate.api.nvidia.com/v1 --port 8787 --rpm 35

# 2. Run MINIMAL + GATED under one shared budget, client, and receipt chain
python -m runner.operator_mode \
  --provider nvidia \
  --model nvidia/nemotron-3-ultra-550b-a55b \
  --base-url http://127.0.0.1:8787/v1 \
  --out results/my-e2-run

# 3. Run Arm C (Harness² APEX dev->test split: learns on dev, frozen lessons on held-out test)
python -m harness2_adapter.h2_world \
  --provider nvidia \
  --model nvidia/nemotron-3-ultra-550b-a55b \
  --base-url http://127.0.0.1:8787/v1 \
  --probes 3 \
  --out results/my-e3-run

# 4. Combine, independently grade, and generate APEX Markdown + HTML scorecard
cat results/my-e2-run/results.jsonl results/my-e3-run/results.jsonl > results/my-cmp.jsonl
python -m score.independent_grader \
  --rows results/my-cmp.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json \
  --receipts results/my-cmp-receipts.json
python -m runner.report \
  --rows results/my-cmp.jsonl \
  --gold world/private/gold_world2.json \
  --split spec/split.json \
  --receipts results/my-cmp-receipts.json \
  --out results/my-REPORT.md \
  --html results/my-REPORT.html
```

---

## Repository Layout

| Path | Role |
|---|---|
| `harness/` | In-repo arms (`minimal_arm.py`: `MINIMAL` & `GATED`), deterministic AF2 gates (`gates.py`), OpenAI-compatible client (`model.py`), shared `Budget` (`budget.py`), API key resolver (`keys.py`), sandboxed file tool (`tools.py`), contradiction injector (`adversary.py`). |
| `harness2_adapter/` | In-repo Harness²-shaped arm `C` (`h2_world.py`): stateful Propose → Probe → Compose with APEX dev→test lesson hygiene. |
| `runner/` | Matched-budget spine (`bench.py`), operator mode (`operator_mode.py`), shared throttle/retry relay (`relay.py`), cryptographic receipts (`receipts.py`), pre-run freeze (`freeze.py`), exact McNemar/Wilson stats & void policy (`stats.py`), APEX Markdown + HTML report generator (`report.py`), sandbox-scoped discovery (`discovery.py`). |
| `score/` | C.1-isolated sealed independent grader (`independent_grader.py`) — imports zero `harness/` code. |
| `world/` | Deterministic World-2 generator (`build_world.py`), 68 world gates (`gates.py`), code-only solver (`independent_solver.py`), SHA-256 hash grader (`grader.py`), public files (`world_files/`), withheld gold (`private/`). |
| `spec/` | Shared budget & arm entrypoints (`arms.json`), frozen dev/test split (`split.json`), committed freeze hashes (`frozen_run.json`), provider registry (`providers.json`). |
| `results/` | Committed v2 live comparison (`COMPARISON-world2-v2/`, `E2-world2-v2/`, `E3-harness2-v2/`), v1 baseline (`COMPARISON-world2/`, `E2-world2/`, `E3-harness2/`), and World-1 offline re-verification (`world1/`). |
| `tests/` | 225 offline unit and regression tests (no network required). |

---

## Disclosures

What the automated gates **cannot** check (carried verbatim in every generated report):
- that the tasks are actually hard (a trivially guessable world passes every gate)
- that the answer formulas are absent from the model's context window at runtime
- that an arm cannot reach gold through the model, a tool, or the OS
- that the gold is correct — a verifiable wrong answer is still wrong
- that the run was fair (budget, load, retries) — enforced by `runner/bench.py` and `runner/relay.py`

## Attribution

- `harness/tools.py` vendors `filelens` from `groundwork` (Copyright SignalCore Inc. and Bastion Fold LLC); the original license notice is preserved verbatim at the top of the file.
- `runner/receipts.py` absorbs the hash-chained receipt pattern from `acme-refund-agent`.
- `harness/gates.py` ports gate architecture concepts from `CLI-Enforcement`.
- `harness2_adapter/h2_world.py` ports the Propose → Probe → Compose loop shape inspired by Google Research's Harness² without executing external packages.
