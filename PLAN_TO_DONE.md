# PLAN TO DONE — verdict-bench, from A.1 (`ef21429`, 2026-11-26)

Plan of record for finishing. Supersedes the **phasing** in `PLAN_FINISH.md` (which
stays as the record of why each step exists, with its verified ground-truth table) and
the item list in `REMAINING_WORK.md`. Nothing here is claimed from memory; every row's
starting state was verified on disk today.

**Rule, unchanged:** one step at a time, a commit per finished step, and no step is
done until its *Verify* passes in a clean shell. A step that cannot pass its verify is
a bug report, not a workaround.

---

## Done when — the only definition of finished

`REMAINING_WORK.md` §6, verbatim: the world hash is frozen before any arm starts; both
arms consumed the recorded budget within tolerance; no answer formula appears in any
prompt, manifest or sandbox (the leakage guard proves it); every task grade is hashed
to withheld gold and chained into a receipt log; an independent grader sharing no code
with either arm produces the verdict; and the whole chain re-verifies **offline, by
someone who is not you, from committed code alone**.

Until that exists, Ali's question is unanswered and everything built is the instrument.

---

## Banked already (verified 2026-11-26, do not re-litigate)

92 tests passing at plan time; **118 passing after Phase A**: 68 world-gate checks live on
the rebuilt world; the independent solver reproduces 25/25 gold hashes from public
files; the leakage guard returns 0 findings on the new artifacts; both runners refuse a
gate-blocked world with exit 1 before any arm loads; hash-chained receipts with 8 tests;
arms at one shared `Budget`; the nemotron relay live-verified; World-1's 18/25 vs 17/25
reproduced from the committed attempts; World-2's 25/25 retracted to 0/25 by the repo's
own grader. **Clean-room:** from `git archive HEAD` alone — 68/68 gates, 25/25 solver,
118 tests, no working-tree or absolute-path dependency.

---

## The critical path — order matters, a row starts only when the row above passes

### Phase A — DONE (ef21429 → f256d5d; CHECKPOINT A passed 2026-11-26)

| # | Step | Verify | Est |
|---|---|---|---|
| A.2 | `world/independent_solver.py`: per-family solver from `world_files/` only, in code, no LLM (config = read key/value; log = count pattern lines; hash = sha256 of the file the solver can read; env = reverse the value in the file; transform = read the script the manifest names) | reproduces every `gold_hash` **100 %**; the 3 skipped solver gates turn green | 45 m |
| A.3 | Close the gate gap: `check_world` scans the **agent-visible tree** (`PUBLIC_ROOT` minus `private/`), not just `sandbox_dir`; add `"family"` to `LEAKY_KEYS` **and** `bench.py`'s `LEAKY_MANIFEST_KEYS` | a fixture world with gold inside the agent tree **fails**; the clean world passes all gates | 20 m |
| A.4 | Wire the hard rule: `runner/bench.py` + `run_world.py` call `check_world` before any run, abort exit 1; deleting the verifier fails the build | run the suite with `world/independent_solver.py` deleted → run refuses, test passes | 15 m |
| A.5 | Family-overlap test in code: World 2 families ∩ World 1 families = ∅ | `pytest` green | 10 m |
| A.6 | Rebuild the world from the seed; `bench.py` leakage guard on the new artifacts | **0 findings** | 10 m |

**CHECKPOINT A — the world is safe to run on. PASSED 2026-11-26.** Stop here, show
the gate report and the guard output, get the go-ahead before any relay money is spent.
(Passed: 68/68 gates, solver 25/25, guard 0 findings, both runners refuse exit 1,
clean-room re-verify from `git archive HEAD` green.)

### Phase B — split, relay, parity closure (2 h 10 m; one external fetch)

| # | Step | Verify | Est |
|---|---|---|---|
| B.1 | Reachability read: GitHub `apex/SPLIT.json` + `run_apex.py` shapes — if unreachable, re-scope to a local sha256 split and disclose it | fetched shape in the commit message | 10 m |
| B.2 | `spec/split.json`: deterministic dev/test split, sha256 rule (first 3 = dev, next 7 = test per domain) | re-run produces byte-identical sets | 30 m |
| B.3 | Port `run_apex.py`'s split+run shape into `runner/` — nothing external executes in this repo | a dry-run over `results/` fixtures completes offline | 40 m |
| B.4 | Parity closure: no output-token / timeout / budget asymmetry anywhere in `harness/` or specs; both arms resolve to **one** `Budget` and one model+provider | parity test + grep clean | 20 m |
| B.5 | `runner/relay.py`; **first job is a smoke test**, never a graded run | smoke transcript in the commit | 30 m |

**CHECKPOINT B — the relay is proven on a smoke run, not a graded one.**

### Phase C — the sealed grader (1 h 30 m; must exist before E grades anything)

| # | Step | Verify | Est |
|---|---|---|---|
| C.1 | `score/independent_grader.py`: imports **no** `harness/` code; grades from gold only; runs after all arms finish | module imports with `harness/` on a broken path | 30 m |
| C.2 | Verdict rules written before results: PASS / INCONCLUSIVE / REJECT (inconclusive = budget exceeded, voided rep, or empty answer on >X % tasks) | a scripted trial of each verdict path | 20 m |
| C.3 | Receipts: every run manifest and every task grade into the chain; tamper flip breaks it **with the seq** | tamper test aborts | 40 m |

**CHECKPOINT C — the verdict machinery is proven on scripted trials, before any real
result exists to bias the rules.**

### Phase D — the adversary and measured recovery (2 h; D.1 first)

| # | Step | Verify | Est |
|---|---|---|---|
| D.1 | Re-point `runner/hypothesis.py` at the relay (`harness/model.py` + `spec/providers.json`, nemotron-3-ultra-550b-a55b) — recovery must be measured on the model under test, not qwen/Ollama. Ollama stays behind a `--local` debug flag | engine runs a seeded task over the relay, key logged nowhere | 30 m |
| D.2 | `harness/adversary.py`: deterministic contradiction injector (concept from `glass-box-alpha/devils_advocate`, not its LLM class); one detectable contradiction at a seeded evaluation inside the hypothesis cycle | same seed → same injection, byte-identical log | 30 m |
| D.3 | Scope: the **HypothesisEngine path only** — MINIMAL/GATED have no hypothesis cycles and would measure the adversary, not recovery. The control is a clean run of the same arm, same world, same budget; that pair is the McNemar comparison | contradicted vs clean pair on one arm, budget equal | 10 m |
| D.4 | Replace `recovery_rate()` with a measured counter: after injection, did the arm revise the hypothesis? (revised→recovered; unchanged→failed; blank→empty-answer rule). Old metric printed as `legacy:` | an arm that ignores the contradiction is recorded **not** recovered | 30 m |
| D.5 | Self-verification in a clean sandbox against gold-independent evidence | leakage scan of the self-verify sandbox: 0 findings | 20 m |
| D.6 | Human-intervention audit: run log carries only harness-authored entries | a synthetic "human edit" to the log is detected | 10 m |

(Renumbered — the old table carried D.2/D.3/D.4 twice under two different scopes.)

### Phase F — the report generator, built BEFORE the runs (1 h 10 m)

| # | Step | Verify | Est |
|---|---|---|---|
| F.1 | `results/report.py`: McNemar, disagreement, Wilson, receipts line — all from `stats.py` + receipts, zero hand-typed numbers | report regenerates byte-identical from committed artifacts | 45 m |
| F.2 | Disclosures: model + endpoint, voided runs, World-1 re-run impossibility (4 missing inputs + the AF2 file + rc=124 rows), what the gates cannot check | every claim maps to a file+commit | 20 m |
| F.3 | Verdict line from C.2 only | scripted trial agrees | 5 m |

Build it now; regenerate it after E. The run window is API time — none of it should be
spent debugging the report script.

### Phase E — the runs (45 m prep, then one 4–7 h API sitting; gated on decision 2)

| # | Step | Verify | Est |
|---|---|---|---|
| E.1 | Freeze `spec/split.json` + world hash; abort on any change | tamper test | 10 m |
| E.2 | World-2 run, both arms, nemotron relay, budget recorded per arm | budget respected within tolerance; receipts chain ok | 2–4 h |
| E.3 | Harness² comparison: same world, same model, same budget | parity of spec recorded | 1–2 h |
| E.4 | World-1: **cannot re-run** (4 missing inputs). Offline re-verification via `stats.py` over **both** jsonl files (A, B, AF2), with `rc=124` rows voided — add void/timeout handling to `stats.py` first | reproduction matches after voids; the pre-void numbers published beside them | 45 m |
| E.5 | Void-and-rerun policy executed (FREETIER 429s noted in providers.json) | every void has a reason row | — |

**GATE — decision 2 (run window) is answered, relay key live-verified, and Checkpoints
A–C and D.1 are green.** Then E runs in one sitting.

### Phase G — operator mode (1 h 30 m; last)

| # | Step | Verify | Est |
|---|---|---|---|
| G.1 | `--model <id> --provider <name>` on `runner/bench.py`: relay started automatically, both arms at one budget, independent grade, receipts written | a 2-task smoke run end-to-end | 45 m |
| G.2 | Parity invariant: the spec must name the **same** model+provider for both arms or the run is refused | mismatched spec → refusal, exit 1 | 15 m |
| G.3 | Colosseum model-town sweep, wired to the operator flags | a 3-config sweep plans its own receipts | 30 m |

---

## Sequencing in one line

**A.2 → A.3 → A.4 → A.5 → A.6 → [CHECKPOINT A] → B.1–B.5 → [B] → C.1–C.3 → [C] →
D.1 → D.2 → D.3 → D.4 → D.5 → D.6 → F.1–F.3 → [E prep] → E.1–E.5 (one sitting) →
F regenerate → G.1–G.3.**

**Totals:** ~11 h construction + E prep, then one 4–7 h API sitting, then G. The report
regenerates from the receipts in minutes.

## Out-of-band work (any time, own commits, none of it blocks the path)

- Kill-list debt: `hypothesis.py`'s ~25 debug prints, duplicated eval loop,
  `engine._current` private access, 3 mypy errors; `run_world.py`'s mypy error on
  `import grader`. Do before E so the run window is clean.
- Blocker 3 (dirty submodule): **CLOSED** 2026-11-26 — tree clean, omnicli pinned
  `a364538`. Record only.
- Blocker 1 (`c181d7b` unpushable — remote 404s, SHA safe in local main): **operator**,
  no code depends on it.

## Decisions still with the operator (2, neither blocks the next step)

1. **Freeze determinism** (needed before E.1): `created_at`/`frozen_at` make a benign
   rebuild change the frozen hash, so E.1 would abort on a rebuild. Recommendation:
   derive both from the seed so a rebuild reproduces the freeze byte-for-byte.
2. **Phase E run window** (needed before E.2): stage and dry-run everything, then one
   sitting — recommended — versus interleaving runs with the remaining build.

## Risks, each with the mitigation already in the path

| Risk | Mitigation |
|---|---|
| Relay throttle (FREETIER 429) degrades a run silently | E.5: every void carries a reason row |
| A gate-blocking world reaches an arm mid-run | A.4 refuses the run; E.1's frozen hash aborts |
| Recovery measured on a model the benchmark never claims | D.1 precedes the adversary |
| World-1 headline inherits timeout-killed passes | E.4 voids before the comparison is quoted |
| The 25/25 failure mode returns (a number from prose) | F.1 generates from receipts only; C gates the verdict |