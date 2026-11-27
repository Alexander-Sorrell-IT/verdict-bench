# PLAN TO DONE — verdict-bench, from CHECKPOINT A (2026-11-26)

Plan of record, revised at the checkpoint. **Phase A is DONE** (`ef21429` →
`f256d5d`): the world is clean, verified, and both runners refuse a blocked one.
This revision re-baselines the remainder against what Phase A actually cost and
marks where each open decision bites. The rule is unchanged: one step at a time,
a commit per finished step, and no step is done until its *Verify* passes in a
clean shell.

## The lesson Phase A taught, priced into every estimate below

Phase A was estimated at 1 h 40 m and took ~2.5 h. Not drift — two real defects
the plan had assumed away: the world shipped paradoxes on 20 of 25 tasks (A.2),
and the leakage guard contradicted the documented leak rule (A.6). Both surfaced
only because every verify ran for real. So each phase below carries named slack
and states its analogous unknown. **Assume ~12 h of construction if nothing new
breaks; ~18 h at the observed defect rate.**

## Done when — the only definition of finished

`REMAINING_WORK.md` §6, verbatim: the world hash is frozen before any arm starts;
both arms consumed the recorded budget within tolerance; no answer formula appears
in any prompt, manifest or sandbox (the leakage guard proves it); every task grade
is hashed to withheld gold and chained into a receipt log; an independent grader
sharing no code with either arm produces the verdict; and the whole chain
re-verifies **offline, by someone who is not you, from committed code alone**.

## Banked (verified 2026-11-26 — do not re-litigate)

118 tests passing (92 at plan time). 68 world-gate checks live on the rebuilt
world. The independent solver reproduces **25/25** gold hashes from public files,
in code, no LLM — and gold is byte-identical across a rebuild. The leakage guard
returns **0 findings** on the world artifacts. Both runners call `check_world`
before any arm and exit 1 on blocking; a deleted verifier blocks the world.
World 2 families ∩ World 1 families = ∅, asserted in code. Hash-chained receipts.
Arms at one shared `Budget`. The nemotron relay live-verified once, on one task.
World-1's 18/25 vs 17/25 reproduced from the committed attempts. World-2's 25/25
retracted to 0/25 by the repo's own grader. **Clean-room:** from `git archive
HEAD` alone — 68/68 gates, 25/25 solver, 118 tests, no working-tree or
absolute-path dependency.

---

## Phase B — split, relay, parity closure (3 h; one external fetch)

| # | Step | Verify | Est |
|---|---|---|---|
| B.1 | Reachability read: GitHub `apex/SPLIT.json` + `run_apex.py` shapes — if unreachable, re-scope to a local sha256 split and disclose it | fetched shape in the commit message | 10 m |
| B.2 | `spec/split.json`: deterministic dev/test split, sha256 rule (first 3 = dev, next 7 = test per domain) | re-run produces byte-identical sets | 30 m |
| B.3 | Port `run_apex.py`'s split+run shape into `runner/run_world.py`; nothing external executes here. **Also replaces the `verification == "pass"` summary that manufactured the old 25/25** with grading against withheld gold | a dry-run over `results/` fixtures completes offline; the manufactured-pass code is gone | 45 m |
| B.4 | Parity closure: no output-token / timeout / budget asymmetry anywhere in `harness/` or specs; both arms resolve to **one** `Budget` and one model+provider | parity test + grep clean | 20 m |
| B.5 | `runner/relay.py`; **first job is a smoke test**, never a graded run | smoke transcript in the commit | 30 m |
| B.6 | World freeze determinism: `created_at`/`frozen_at` derive from the seed so a rebuild reproduces the freeze byte-for-byte (decision 1 — executing here unless you object) | rebuild twice → identical freeze hash | 20 m |

**CHECKPOINT B — the relay answers a seeded task and the smoke transcript is
committed.** Analogous unknown: the apex fetch may be unreachable; the relay
absorb may not match `spec/providers.json`'s shape.

## Phase C — the sealed grader (2 h)

| # | Step | Verify | Est |
|---|---|---|---|
| C.1 | `score/independent_grader.py`: imports **no** `harness/` code; grades from gold only; runs after all arms finish. `world/grader.py`'s sealed signature is the reference shape | module imports with `harness/` on a broken path | 35 m |
| C.2 | Verdict rules written before results: PASS / INCONCLUSIVE / REJECT (inconclusive = budget exceeded, voided rep, or empty answer on >X % tasks) | a scripted trial of each verdict path | 25 m |
| C.3 | Receipts: every run manifest and every task grade into the chain; tamper flip breaks it **with the seq** | tamper test aborts | 40 m |

**CHECKPOINT C — the verdict machinery is proven on scripted trials, before any
real result exists to bias the rules.**

## Phase D — the adversary and measured recovery (2 h 30 m; highest risk)

| # | Step | Verify | Est |
|---|---|---|---|
| D.1 | Re-point `runner/hypothesis.py` at the relay (nemotron-3-ultra-550b-a55b) — **and clear the kill-list debt in that file first**: ~25 debug prints, the duplicated eval loop, `engine._current` private access, 3 mypy errors. Ollama stays behind a `--local` debug flag | engine runs a seeded task over the relay, key logged nowhere; file is lint/type clean | 45 m |
| D.2 | `harness/adversary.py`: deterministic contradiction injector (concept from `glass-box-alpha/devils_advocate`, not its LLM class); one detectable contradiction at a seeded evaluation inside the hypothesis cycle | same seed → same injection, byte-identical log | 30 m |
| D.3 | Scope: the **HypothesisEngine path only** — MINIMAL/GATED have no hypothesis cycles and would measure the adversary, not recovery. The control is a clean run of the same arm, same world, same budget; that pair is the McNemar comparison | contradicted vs clean pair on one arm, budget equal | 10 m |
| D.4 | Replace `recovery_rate()` with a measured counter: after injection, did the arm revise the hypothesis? (revised→recovered; unchanged→failed; blank→empty-answer rule). Old metric printed as `legacy:` | an arm that ignores the contradiction is recorded **not** recovered | 30 m |
| D.5 | Self-verification in a clean sandbox against gold-independent evidence | leakage scan of the self-verify sandbox: 0 findings | 20 m |
| D.6 | Human-intervention audit: run log carries only harness-authored entries | a synthetic "human edit" to the log is detected | 15 m |

Analogous unknown: **none of this machinery has ever executed**, and D.4 defines
a metric whose semantics are fixed now, before results exist.

## Phase F — the report generator, built BEFORE the runs (1 h 30 m)

| # | Step | Verify | Est |
|---|---|---|---|
| F.1 | `results/report.py`: McNemar, disagreement, Wilson, receipts line — all from `stats.py` + receipts, zero hand-typed numbers. **Also gives `stats.py` the `rc=124` void/timeout handling E.4 depends on** (currently zero code) | report regenerates byte-identical from committed artifacts | 50 m |
| F.2 | Disclosures: model + endpoint, voided runs, World-1 re-run impossibility (4 missing inputs + the AF2 file + the timeout-coded passes), what the gates cannot check | every claim maps to a file+commit | 20 m |
| F.3 | Verdict line from C.2 only | scripted trial agrees | 5 m |

Build it now; regenerate after E. The run window is API time — none of it should
be spent debugging the report script.

## E prep (1 h) — done before the sitting, so the sitting only runs

| # | Step | Verify | Est |
|---|---|---|---|
| E.1 | Freeze `spec/split.json` + world hash; abort on any change | tamper test | 10 m |
| E.4a | Stats voids: `rc=124` rows excluded per the void policy, pre-void numbers published beside the post-void ones | a scripted three-arm trial over the committed jsonl | 30 m |
| E.4b | World-1 offline re-verification (A, B, **AF2**) from the committed attempts | reproduction matches after voids | 15 m |

## Phase E — the runs (one sitting, 4–7 h API) — GATED on decision 2

| # | Step | Verify | Est |
|---|---|---|---|
| E.2 | World-2 run, both arms, nemotron relay, budget recorded per arm | budget respected within tolerance; receipts chain ok | 2–4 h |
| E.3 | Harness² comparison: same world, same model, same budget | parity of spec recorded | 1–2 h |
| E.5 | Void-and-rerun policy executed (FREETIER 429s noted in providers.json) | every void has a reason row | — |

**GATE — Checkpoints A–C green, D.1 green, decision 2 answered, E.1 freeze
tested.** The analogous unknown is the whole phase: the graded path has never run
a full world end-to-end, and the relay has been live-verified once, on one task.

## Phase G — operator mode (2 h; last)

| # | Step | Verify | Est |
|---|---|---|---|
| G.1 | `--model <id> --provider <name>` on `runner/bench.py`: relay started automatically, both arms at one budget, independent grade, receipts written | a 2-task smoke run end-to-end | 50 m |
| G.2 | Parity invariant: the spec must name the **same** model+provider for both arms or the run is refused | mismatched spec → refusal, exit 1 | 20 m |
| G.3 | Colosseum model-town sweep, wired to the operator flags | a 3-config sweep plans its own receipts | 30 m |

---

## Sequencing in one line

**B.1–B.6 → [CHECKPOINT B] → C.1–C.3 → [CHECKPOINT C] → D.1–D.6 → F.1–F.3 →
E.1/E.4 prep → [DECISION 2] → E.2–E.5 (one sitting) → F regenerate → G.1–G.3.**

**Totals:** ~11 h 45 m of construction at plan rate, ~18 h at Phase A's observed
defect rate, then one 4–7 h API sitting. The report regenerates in minutes
afterwards.

## What needs you vs what I execute

- **NEEDS YOU — decision 2, the Phase E run window.** Stage everything, then one
  sitting (recommended), versus interleaving runs with the remaining build.
- **EXECUTING UNLESS YOU OBJECT — decision 1, freeze determinism**: derive the
  timestamps from the seed (B.6). No downside; a non-reproducible freeze would
  abort E.1 on a benign rebuild.
- **Everything else**: mine, in order, one step per commit.

## Out-of-band (no code depends on it)

- Blocker 1 — OmniCLI SHA `c181d7b` unpushable (remote 404s; the SHA is safe in
  local main). **Operator.**
- Blocker 3 (dirty submodule) — **CLOSED** 2026-11-26: tree clean, omnicli pinned
  `a364538`. Recorded, nothing to do.
- `run_world.py`'s bare `grader` import (1 mypy error, pre-existing) — dies when
  B.3 rewrites that file's grading path.

## Kill-list status (from PLAN_2026-11-27.md)

**Claimed:** `families`/`family` in agent-visible manifests; gold + `FREEZE_*`
inside the agent tree; `verify.py`'s shared-path overwrite; `run_world.py`'s
`verification == "pass"` manufactured-25/25 summary (B.3 finishes it);
`build_world.py`'s dead `_generate_gold_answer` + stubbed `independent_solver()`;
`run_arm.sh` (retired by deletion). **Remaining:** `hypothesis.py`'s ~25 debug
prints, duplicated eval loop, `engine._current` private access, 3 mypy errors —
all scheduled inside D.1; the stale `world/discovery.md` sample (untracked
gitignored artifact — regenerate or leave).

## Risks, each with the mitigation already in the path

| Risk | Mitigation |
|---|---|
| The plan's estimates are wrong by ~1.5× until D proves itself | checkpoints after B and C; the riskiest phase (D) is scheduled before any API spend |
| Relay throttle (FREETIER 429) degrades a run silently | E.5: every void carries a reason row |
| A gate-blocking world reaches an arm mid-run | A.4 refuses the run; E.1's frozen hash aborts |
| Recovery measured on a model the benchmark never claims | D.1 precedes the adversary |
| World-1 headline inherits timeout-killed passes | E.4 voids before the comparison is quoted |
| The 25/25 failure mode returns (a number from prose) | F.1 generates from receipts only; C gates the verdict |
| Phase E runs at a scale the pieces have never seen | B.5's smoke test + Checkpoint C exist to make E the boring phase |