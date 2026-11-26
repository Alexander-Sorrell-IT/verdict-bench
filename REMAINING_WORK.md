# REMAINING WORK — verdict-bench, from 2026-11-26

Everything still to do, in order, with the state each thing starts from. Written so a
fresh session (or another engineer) can pick this up cold.

**Working tree:** `/media/phantomcore/AI_DRIVE/take home test/verdict-bench`
**Committed:** 10 commits, `dca8cd1` → `656be90`. **Tests:** 80 passing.
**Source of truth for the plan:** `PLAN_2026-11-27.md` (v2) and `ABSORPTION_PLAN.md`.
**Retraction of the false result:** `AUDIT_2026-11-25.md` + corrective commit `dd6c1df`
in `code-hunter-extreme`.

---

## 1. What is done and verified

| Module | Lines | Tests | Note |
|---|---|---|---|
| `runner/bench.py` | 616 | self-test | matched budget, **hash grading**, leakage guard. Rejects the committed world with 8 findings |
| `runner/stats.py` | 536 | verified on real World-1 | McNemar exact, disagreement table, Wilson bounds (replaces RESULTS.md's impossible 100%-100%) |
| `runner/receipts.py` | 194 | 8 | hash-chained tamper-evident results. **Absorbed from `acme-refund-agent`** |
| `harness/gates.py` | 339 | 28 | 6 AF2 gates. **Ported from `CLI-Enforcement`'s architecture**; blocks the 25/25 placeholder as invalid JSON |
| `harness/minimal_arm.py` | 364 | 15 | the arm. **Live-verified against nemotron-3-ultra-550b** → `{"value": "value_2022"}`, 3 steps, 3 calls, 6.4 s |
| `harness/model.py` | 202 | — | one OpenAI-compatible client, relay-aware, key never logged |
| `harness/keys.py` | 193 | 8 | **OmniCLI's exact precedence order**; loads an OmniCLI config unmodified |
| `harness/budget.py` | — | — | one `Budget` definition shared by runner and arms; refuses missing fields |
| `harness/filelens/` | 550 | 21 | vendored file tool, PolyForm notices intact |
| `tests/` | — | **80 pass** | |

**Live-verified facts (do not re-litigate):**
- The key path resolves the real `NVIDIA_API_KEY` (70 chars, `nvapi…`).
- The arm reaches `nvidia/nemotron-3-ultra-550b-a55b` at `https://integrate.api.nvidia.com/v1` and answers correctly.
- The World-1 head-to-head numbers are verified against the recorded attempts: A 18/25, B 17/25, criteria 63/87 vs 57/87, McNemar p = 1.0000, 9 discordant tasks where **every loser scored zero**.
- The World-2 25/25 is retracted: real result 0/25 by the repo's own grader; all attempts 5/46 = 10.87%.

---

## 2. Still to do, in dependency order

### Item 5 — World rewrite + gate suite (~2 h)
- [ ] `world/build_world.py`: drop `families` from the agent-visible manifest; no answer formula anywhere the model sees; gold written **outside** the agent-visible tree
- [ ] `world/gates.py`: per-world verification gates, pattern from `alignerr-task-factory/verify/gates.py` (read-first)
- [ ] Adopt the hard rule: **a task that cannot be verified in code does not ship** — deleting the verifier fails the build, not the run
- [ ] Unknown-world families must not overlap World 1's five (contract-compliance, ops-reporting, config-audit, log-forensics, reconciliation)
- [ ] Gate: `bench.py` leakage guard returns **zero** findings on the new world
- [ ] Implement `world/independent_solver()` — solves from public files only, in code, no LLM (replaces the `solved = False  # placeholder` stub)

### Item 6 — Adversary + measured recovery (~1 h)
- [ ] `harness/adversary.py`: deterministic contradiction injector. **Concept only** from `glass-box-alpha/devils_advocate` (34 lines, an LLM agent class — do not absorb)
- [ ] Replace `recovery_rate()` (which only counts cycles resolved in ≤3 tests) with a **measured** counter: inject a detectable contradiction, record whether the hypothesis is revised
- [ ] Self-verification against gold-independent evidence in a clean sandbox; no arm may read gold while self-verifying
- [ ] Human-intervention audit: zero human-authored entries in a run log

### Item 7 — Independent grader + receipts wiring (~1.5 h)
- [ ] `score/independent_grader.py`: scores from **gold only**, imports **no** `harness/` code, runs after all arms finish
- [ ] Receipt every run manifest and every task grade into the chain; `verify_receipts_chain()` must return ok
- [ ] Verdict only from the independent grader: `PASS` / `INCONCLUSIVE` / `REJECT`

### Item 8 — Split + relay (~1 h)
- [ ] `spec/split.json`: deterministic dev/test split by the sha256 rule from GitHub's `apex/SPLIT.json` (first 3 = dev, next 7 = test per domain); re-running reproduces identical sets
- [ ] Port `apex/run_apex.py` + `SPLIT.json` from GitHub; wire to the in-repo arms
- [ ] `runner/relay.py`: absorb from GitHub verdict-bench (Nov 20). **Never executed in this repo** — first job is a smoke test
- [ ] Fix the parity defect already in the launcher: `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=3200` is set for **Arm B only** — 4× Arm A's output allowance. Normalise for both or disclose on the report

### Item 9 — Runs (~3 h)
- [ ] World-1 regression: MINIMAL / GATED, budget recorded
- [ ] Unknown-world run for both arms, graded only by item 7
- [ ] Harness² comparison on the same world, same model, same budget
- [ ] Freeze the world hash before any arm runs; abort on change
- [ ] Void-and-rerun policy marked explicitly in the report
- [ ] **Model route: RESOLVED by the live run — nemotron-3-ultra-550b over the relay**, matching what `RESULTS.md` claims

### Item 10 — Report (~1 h)
- [ ] McNemar, disagreement, Wilson bounds, receipts line — all from `stats.py`, not hand-written
- [ ] Disclosures: model and endpoint used, voided runs, what the result does and does not show
- [ ] Every claim maps to a committed artifact

### Item 11 — Operator mode (~1.5–2 h)
- [ ] `--model <id> --provider <name>`: relay started automatically, both arms at one budget, independent grade, receipts written
- [ ] Parity invariant: the spec must name the **same** model+provider for both arms or the run is refused
- [ ] Reuses the `agent-colosseum` model-town pattern for sweeps

---

## 3. Blockers that are not code — two are yours

1. **Push OmniCLI SHA `c181d7b`** — it is in **no reachable remote**: 404 on
   `code-hunter-extreme/omnicli`, and the private `Alexander-Sorrell-IT/omnicli`
   last pushed 2026-10-31. `git branch -r --contains c181d7b` returns nothing. Until
   this is pushed, "OmniCLI as shipped" cannot be rebuilt by anyone else.
2. **World-1 gold** — `world/private/tasks_gold.json` was never pushed and does not exist
   in any repo. If it cannot be regenerated from world data *in code*, item 9's World-1
   regression is impossible and the flagship numbers stay un-re-derivable. **This is the
   one real schedule risk.**
3. **Commit or revert the dirty OmniCLI submodule** in `code-hunter-extreme` — the
   port-back files (`discovery.py`, `hypothesis_engine.py`, `self_verify.py`,
   `harness2_adapter.py`, `tests/test_verdict_bench_port.py`) are uncommitted and exist
   on this disk only.

---

## 4. Decisions, resolved

| Question | Resolution |
|---|---|
| Model route | **nemotron-3-ultra-550b-a55b over the shared relay** — live-verified working; matches what `RESULTS.md` claimed |
| History | **Additive corrective commit** (`dd6c1df`), not an amend — history preserved |
| OmniCLI's role | **Folded into this program.** verdict-bench carries its own arm; "as shipped" meant a dirty submodule no clone could reproduce |

---

## 5. Ground rules (each earned by a failure)

1. **Read before claiming.** A description is a claim until code has been opened. Three times tonight a description was wrong and cost hours (`touchstone`, "9 repos", `RESULTS.md`'s setup).
2. **No number without reproduction.** A result that cannot be regenerated from committed code by someone else does not exist.
3. **No warnings, only block or allow.** A gate that warns is a gate the model learns to ignore.
4. **A failed arm returns an empty answer.** Never a fabricated placeholder. That is the single rule that separates this from the retracted result.
5. **Absorb machinery, rewrite domain glue.** The moment a source repo's domain assumptions leak into how verdict-bench scores, one reusable instrument becomes nine coupled ones.

---

## 6. Definition of done

A run where **all** of these hold: the world hash is frozen before any arm starts; both arms consumed the recorded budget within tolerance; no answer formula appears in any prompt, manifest or sandbox (the leakage guard proves it); every task grade is hashed to withheld gold and chained into a receipt log; an independent grader sharing no code with either arm produces the verdict; and the whole chain re-verifies **offline, by someone who is not you, from committed code alone**.

Until that exists, Ali's question — *"can it generalize under those conditions?"* — is unanswered, and everything built so far is the instrument that will answer it.

**Remaining: ~9 h of construction, plus the two blockers in §3.**