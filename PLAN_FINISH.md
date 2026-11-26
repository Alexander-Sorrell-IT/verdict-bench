# PLAN — finish verdict-bench (from 2026-11-26, REMAINING_WORK.md)

Everything left, in dependency order, each step with the check that proves it done.
Nothing here is claimed from memory; the ground-truth survey below was read from disk
today. Construction target: **~9–11 h**, plus API wall time for the runs (Phase E).

**Rule for the whole plan:** one file at a time, a commit per finished step
(`f(scope): what it proves`), and no step is done until its *Verify* column passes in a
clean shell. A step that cannot pass its verify is a bug report, not a bug workaround.

---

## 0. Ground truth (verified on disk 2026-11-26 — do not re-litigate)

| Claim | Verified state |
|---|---|
| Item 5 gates | **EXIST**, `world/gates.py`, 12 tests, `5c57c71`. Live on the rebuilt world (post-A.1): exit 0, **64/64 passed**. Solver gates skip until A.2 lands `world/independent_solver.py` |
| Item 5 world rewrite | **A.1 DONE, `ef21429`**: gold + freeze now `world/private/` via the new `world/paths.py` (PUBLIC_ROOT vs PRIVATE_ROOT); manifest carries neither `families` nor `family`; dead `_generate_gold_answer` and the `solved = False` stub removed (real solver is A.2); `world_manifest_3.json` (claims 25 tasks, holds 3) deleted. Remaining: A.2 solver, A.3 gate-tree scan + `family` in LEAKY_KEYS, A.4 wiring, A.5 overlap test, A.6 leakage guard |
| Gates wired into run path | **NO** — zero callers of `check_world` in `runner/`, `harness/`, `score/` |
| `score/` | **DOES NOT EXIST** — item 7 starts from zero |
| `apex/` | **EMPTY DIR** — `SPLIT.json` + `run_apex.py` not yet ported |
| Parity launcher defect | `runner/run_arm.sh` **no longer exists** — the defect is retired by deletion; what remains is proving no reintroduced asymmetry (see 2.2) |
| World-1 world data | **GONE** — both `world_manifest*.json` files are World-2 (`world2-20261120`); no `tasks_gold*` anywhere. Only recorded attempts survive: `results/world1/world1_A_B_results.jsonl` (per-task `task_pass`, `criteria_passed/total`, **no gold hashes**) |
| Recovery metric | `runner/hypothesis.py:218` `recovery_rate()` = resolved-in-≤3-tests; consumed by `run_world.py:117` |
| Recovery path's model | **`runner/hypothesis.py` calls `qwen2.5-coder:14b` over local Ollama** — a *different* model from the graded nemotron-relay path. A recovery number measured here describes a model the benchmark never claims. Fixed in D.1 |
| `run_world.py` arm handling | **arm-name is a label only** — every arm runs the identical discovery→5-attempt-hypothesis→verify→grade pipeline. No per-arm model/prompt differentiation exists in this path |
| World-1 evidence | Only `results/world1/world1_A_B_results.jsonl` exists anywhere: 50 rows (2 arms × 25 tasks), fields = task_id, family, task_pass, criteria_passed/total, valid_json, wall_s. **No** task prompts, world files, gold hashes, model outputs, or commands |
| Arms/budget | `spec/arms.json`: one shared `Budget`, in-repo entrypoints, no shell-out |
| Harness² | `harness2_adapter.py` at repo root |
| Test suite | 92 passing, must run `-p no:hypothesispytest` (hypothesis 6.148.8 plugin crashes in terminal summary, post-run) |

**What the World-1 evidence can and cannot support** (scope amended 2026-11-26 after
live re-verification of the committed jsonl; operator accepted the amendment):
- CAN: offline re-verification via `stats.py` (attempts are committed) — of **three**
  recorded arms, not two. A third file exists on disk:
  `results/world1/world1_AF2_results.jsonl` (25 rows, AF2, recorded 25/25).
- The rc=124 (timeout-killed) rows must be voided first. Recomputed 2026-11-26:
  **A 18/25 pass, 2 of them rc=124; B 17/25, 4 of them rc=124; AF2 25/25, 17 of
  them rc=124** (AF2's clean total is 8/25). `stats.py` has **no** void/timeout
  handling — E.4 adds it. F.2 discloses all three arms and the timeout-coded passes.
- CANNOT: a live World-1 regression, for four independent reasons — (1) the **task
  prompts** are not recorded anywhere (only `task_id` + `family` survive); (2) the
  **world files** the arms read are not in any repo; (3) **no gold hashes** exist —
  even a re-run of the same answers could not be graded; (4) the launcher and agents
  that produced the results (`run_arm.sh`, the OmniCLI lineage) are gone.
  Reconstructing any of those four **fabricates** the thing being measured. Ali's
  question is answered on **World 2** instead, with the World-1 gap disclosed.

---

## Phase A — Item 5: the world (BLOCKS EVERYTHING — no run exists until it is clean)

| # | Step | Verify | Est |
|---|---|---|---|
| A.1 | `world/build_world.py` rewrite: drop `families` and per-task `family` from the agent-visible manifest; gold + freeze move **outside** the agent-visible tree (`world/private/`, new `world/paths.py` with the two roots); delete `_generate_gold_answer` | `python -m world.gates` on a rebuilt world: **zero** blocking | 30 m |
| A.2 | `world/independent_solver.py`: move the solver out of `build_world.py` into the file the gates already scan; implement per-family solving from `world_files/` **only, in code, no LLM** (config = read key/value; log = count pattern lines; hash = sha256 of the file the solver can read; env = reverse the value in the file; transform = read the script the manifest names) | solver reproduces every `gold_hash` **100 %**; `solver:not-a-stub` passes | 45 m |
| A.3 | Close the gate gap: `check_world` must scan the **agent-visible tree** (point the leak/separation gates at the real tree, not just `sandbox_dir`); add `"family"` to `LEAKY_KEYS` | a fixture world with gold sitting inside the agent tree **fails**; the clean world passes all gates | 20 m |
| A.4 | Wire the hard rule: `runner/bench.py` (and `run_world.py`) call `check_world` before any run and abort exit 1 on blocking; "deleting the verifier fails the build" becomes a test | run the suite with `world/independent_solver.py` deleted → run refuses, test passes | 15 m |
| A.5 | Family-overlap test (plan 3.3): World 2 families ∩ World 1 families = ∅, asserted in code, not eyeballed | `pytest tests/test_world_gate...` — overlap test green | 10 m |
| A.6 | Rebuild the world from the seed; `runner/bench.py` leakage guard returns **zero** findings on the new world artifacts | guard output: 0 findings | 10 m |

**A.2 antipattern to avoid:** the solver must not special-case "read the answer from a
sidecar". It solves the task the way an honest arm would — from public files — and the
hashes prove it. A solver that shortcuts is a grader that lies.

## Phase B — Item 8: split, relay, parity (before runs, cheap, one external fetch)

| # | Step | Verify | Est |
|---|---|---|---|
| B.1 | Reachability check: fetch GitHub `apex/SPLIT.json` + `run_apex.py` shapes (read-first, per ground rule 1) — if unreachable, this phase re-scopes to a local sha256 split and the disclosure says so | fetched shape recorded in the commit message | 10 m |
| B.2 | `spec/split.json`: deterministic dev/test split, sha256 rule (first 3 = dev, next 7 = test per domain); frozen before any arm runs | re-run produces byte-identical sets | 30 m |
| B.3 | Port `run_apex.py`'s split+run shape into `runner/run_world.py` (or a thin `runner/apex_split.py`); no execution of anything external in this repo | a dry-run over `results/` fixtures completes offline | 40 m |
| B.4 | Parity closure: no output-token / timeout / budget asymmetry between arms anywhere in `harness/` or specs — grep for `OUTPUT_TOKEN_MAX`, `max_tokens`, per-arm overrides; both arms must resolve to **one** `Budget` and one model+provider | parity test: same resolved spec for both arms; grep clean | 20 m |
| B.5 | `runner/relay.py`: absorb from GitHub verdict-bench (Nov 20); **first job is a smoke test**, never a graded run | smoke test transcript in commit | 30 m |

## Phase C — Item 7: the sealed grader (must exist before Phase E grades anything)

| # | Step | Verify | Est |
|---|---|---|---|
| C.1 | `score/independent_grader.py`: imports **no** `harness/` code; grades from gold only; runs after all arms finish | module imports with `harness/` on a broken path | 30 m |
| C.2 | Verdict from the independent grader only: `PASS` / `INCONCLUSIVE` / `REJECT`, with the rule for each written down (inconclusive = budget exceeded, voided rep, or arm returned empty on >X % tasks — decided before seeing results) | a scripted trial of each verdict path | 20 m |
| C.3 | Receipts: every run manifest (model, provider, budget, world hash, split hash, arm commits) and every task grade into the chain; `verify_receipts_chain()` returns ok; a flipped byte returns broken **with the seq** | tamper test aborts (already proven for runner receipts — re-prove for scores) | 40 m |

## Phase D — Item 6: adversary + measured recovery (makes the "recovers when wrong" claim measured)

| # | Step | Verify | Est |
|---|---|---|---|
| D.1 | **Re-point `runner/hypothesis.py`'s model at the relay** (`harness/model.py` + `spec/providers.json`, nemotron-3-ultra-550b-a55b) — recovery must be measured on the model under test, not on qwen over Ollama. Ollama path stays behind a `--local` debug flag only | engine runs a seeded task over the relay, key logged nowhere | 30 m |
| D.2 | `harness/adversary.py`: deterministic contradiction injector (concept from `glass-box-alpha/devils_advocate`, **do not** absorb its LLM-agent class). Injects one detectable contradiction at a seeded evaluation inside the hypothesis cycle | same seed → same injection, byte-identical log | 30 m |
| D.2b | **Apply to the HypothesisEngine path only** (where `recovery_rate()` is measured) — NOT to MINIMAL/GATED, which have no hypothesis cycles and would measure the adversary, not recovery. The control is a clean (un-injected) run of the same arm, same world, same budget — that pair is what makes recovery a measured McNemar comparison, not an anecdote | contradicted vs clean pair on one arm, budget equal | 10 m |
| D.3 | Replace `recovery_rate()` with a **measured** counter: after injection, did the arm revise the hypothesis? (revised→recovered; unchanged→failed; blank→empty-answer rule). Keep the old metric printed as `legacy:` for comparability | an arm that ignores the contradiction is recorded as **not** recovered | 30 m |
| D.4 | Self-verification in a clean sandbox against gold-independent evidence; no arm reads gold while self-verifying (gates already scan for this) | leakage scan of the self-verify sandbox: 0 findings | 20 m |
| D.5 | Human-intervention audit: run log carries only harness-authored entries | a synthetic "human edit" to the log is detected | 10 m |
| D.2 | Replace `recovery_rate()` with a **measured** counter: after injection, did the arm revise the hypothesis? (revised→recovered; unchanged→failed; blank→empty-answer rule). Keep the old metric printed as `legacy:` for comparability | an arm that ignores the contradiction is recorded as **not** recovered | 30 m |
| D.3 | Self-verification in a clean sandbox against gold-independent evidence; no arm reads gold while self-verifying (gates already scan for this) | leakage scan of the self-verify sandbox: 0 findings | 20 m |
| D.4 | Human-intervention audit: run log carries only harness-authored entries | a synthetic "human edit" to the log is detected | 10 m |

## Phase E — Item 9: the runs (API wall time; everything above must be green first)

| # | Step | Verify | Est |
|---|---|---|---|
| E.1 | Freeze `spec/split.json` + world hash; abort on any change | tamper test | 10 m |
| E.2 | World-2 run, both arms (MINIMAL / GATED), model route `nvidia/nemotron-3-ultra-550b-a55b` over the relay (live-verified 2026-11-26), budget recorded per arm | budget respected within tolerance; receipts chain ok | 2–4 h API |
| E.3 | Harness² comparison: same world, same model, same budget, via `harness2_adapter.py` | parity of spec recorded | 1–2 h API |
| E.4 | World-1: **cannot re-run** (no world data). Offline re-verification via `stats.py` over **both** jsonl files (A, B **and** AF2), with rc=124 rows voided per E.5 — add void/timeout handling to `stats.py` first | reproduction matches the recorded numbers **after voids**, and the pre-void numbers are published beside them | 45 m |
| E.5 | Void-and-rerun policy executed and marked in the report (voids already possible: FREETIER 429s — providers.json notes the throttle) | every void has a reason row | — |

## Phase F — Item 10: the report (generated, not written)

| # | Step | Verify | Est |
|---|---|---|---|
| F.1 | `results/report.py` (or `runner/report.py`): McNemar, disagreement, Wilson, receipts line — **all from `stats.py` + receipts**, zero hand-typed numbers | report regenerates byte-identical from committed artifacts | 45 m |
| F.2 | Disclosures: model + endpoint, voided runs, World-1 re-run impossibility, what gates cannot check (port the coverage list) | every claim maps to a file+commit | 20 m |
| F.3 | Verdict line from C.2 only: PASS / INCONCLUSIVE / REJECT | — | 5 m |

## Phase G — Item 11: operator mode (last; reuses everything proven above)

| # | Step | Verify | Est |
|---|---|---|---|
| G.1 | `--model <id> --provider <name>` on `runner/bench.py`: relay started automatically, both arms at one budget, independent grade, receipts written | a 2-task smoke run end-to-end | 45 m |
| G.2 | Parity invariant: the spec must name the **same** model+provider for both arms or the run is refused | mismatched spec → refusal, exit 1 | 15 m |
| G.3 | Sweep pattern from `agent-colosseum`'s model-town, wired to the operator flags | a 3-config sweep plans its own receipts | 30 m |

---

## Decisions (1 and 3 resolved 2026-11-26; 2 still open)

1. **World-1 scope — RESOLVED: offline re-verification only, with the amendment
   recorded above** (three arms, AF2 file disclosed, rc=124 rows voided before the
   comparison is quoted). The live regression stays declared impossible from
   committed code. Rebuilding the world by hand was rejected: it reintroduces
   unverifiable inputs.
2. **Run window for Phase E — OPEN.** Runs need the NVIDIA relay (live-verified key path)
   and 2–4 h of API time. Stage and dry-run everything A–D first, then execute E in one
   sitting?
3. **Who gets the adversary — RESOLVED: the HypothesisEngine path only** (D.2b).
   MINIMAL/GATED have no hypothesis cycles; an adversary there measures the
   adversary, not the arm's recovery. The control is a clean run of the same arm on
   the same world at the same budget — that pair is the measured McNemar comparison.
   Code-verified: `run_world.py`'s `arm_name` is a label only; every arm runs the
   identical discovery→hypothesis→verify→grade pipeline.

## Blockers (not code — yours, from REMAINING_WORK §3)

1. Push OmniCLI SHA `c181d7b` (no reachable remote).
2. World-1 gold — answered above (E.4 scope).
3. Commit or revert the dirty OmniCLI submodule files on this disk.

## Done when

REMAINING_WORK §6 holds, verbatim: frozen world hash before any arm; both arms inside
the recorded budget; zero leakage findings on prompts/manifests/sandboxes; every grade
receipt-chained to withheld gold; verdict only from `score/independent_grader.py`
(no `harness/` imports); the whole chain re-verifies offline, by someone else, from
committed code alone. That is the run that answers *"can it generalize under those
conditions?"* — everything above is the instrument.