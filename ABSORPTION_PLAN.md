# ABSORPTION PLAN — what comes in, from where, into what

Written 2026-11-27. Every row below is verified by reading the source, not by its
description. Mechanism is one of: **ABSORB** (vendor the file, add the license notice,
keep it working), **PORT** (rewrite the concept for verdict-bench's interfaces),
**CONCEPT** (take the idea, write the code here).

**Governing rule: absorb machinery, rewrite domain glue.** The moment a source repo's
domain assumptions leak into how verdict-bench scores, a reusable instrument becomes nine
coupled ones.

---

## Where pieces land in verdict-bench

```
runner/
  receipts.py       ABSORBED  tamper-evident result chain   (acme-refund-agent)
  bench.py          NEW       budget parity + hash grading + leakage guard
  stats.py          NEW       McNemar, disagreement, Wilson bounds
  relay.py          PORT      shared model relay for every arm   (verdict-bench @ GitHub)
harness/            NEW       the in-repo arm — replaces the OmniCLI submodule
  model.py          NEW       one OpenAI-compatible client
  gates.py          PORT      AF2 gates, concepts from CLI-Enforcement
  tools.py          ABSORB    file reading (FileLens, 612 lines, stdlib-only)
  adversary.py      PORT      deterministic contradiction injector (concept from devils_advocate)
world/
  build_world.py    REWRITE   no families, no answer formulas, gold outside the agent tree
  gates.py          PORT      per-world verification gates (pattern: alignerr 18 gates)
  grader.py         EXISTS    hash grading
score/
  independent_grader.py  NEW  scores from gold only, imports no harness/ code
spec/ providers.json, arms.json, split.json   NEW
```

## The table

| # | Source | Target | Mechanism | Verification | Status |
|---|---|---|---|---|---|
| 1 | `acme-refund-agent/backend/receipts.py` | `runner/receipts.py` | **ABSORB** | 8 tests, 89% coverage, black/flake8/mypy/vulture/bandit clean | **DONE** |
| 2 | `acme-refund-agent/backend/verify_claims.py` | Phase 7 report format | **CONCEPT** | every claim in the report maps to a committed artifact | to do |
| 3 | `CLI-Enforcement` (16k, README read) | `harness/gates.py` | **PORT** | each gate has a unit test that fails without it | read-first: `bash_gate.py`, `enforce_check.py`, stage mapping |
| 4 | `alignerr-task-factory` (4k, README read) | `world/gates.py` + the no-paradox rule | **PORT** | a task that cannot be verified in code fails the **build**, not the run | read-first: `verify/gates.py` |
| 5 | `groundwork/filelens` (612 lines, **read**) | `harness/tools.py` | **ABSORB** | outline/search/chunk/summarize work on the world's files; stdlib only | ready, needs license notice |
| 6 | `glass-box-alpha/agents/devils_advocate` (34 lines, **read**) | `harness/adversary.py` | **CONCEPT** | inject a contradiction → a wrong hypothesis is recorded as wrong, then as recovered | do NOT absorb: it is an LLM agent class, not code |
| 7 | `agent-colosseum` (README read) | Phase 8 operator mode | **CONCEPT** | `--model X` sweep runs one scenario across N models; chaos injection as a run-vadility check | to do |
| 8 | `verdict-bench @ GitHub` (Nov 20) | `runner/relay.py`, `apex/run_apex.py`, `SPLIT.json`, `world/private/tasks_gold.json`, `harness2_adapter/` | **ABSORB** | relay retries 429/5xx before any byte reaches an arm; split rule reproduces the same sets | **read-first: gold is NOT in that repo and must be rebuilt** |
| 9 | `truthfit.py` (read) | the verifier doctrine, written as bench.py's acceptance test | **CONCEPT** | on failure the tool writes nothing but the report — no number | to do |

## Order, because it is dependency-bound

1. **Phase 0 blockers** — push OmniCLI SHA `c181d7b` (currently in no reachable remote) and rebuild World-1 gold from world data. Nothing downstream is real until these land.
2. **#5 FileLens** — the arm needs to read files before it can do anything. Vendoring it first means Phase 2 has a tool from day one.
3. **#3 + #6 gates and adversary** — the arm's two behavioral components. Read-first, then port.
4. **#4 world gates** — Phase 3's gate suite. Read-first, then port.
5. **#8 relay + split + gold** — Phase 4, 6, 8 plumbing.
6. **#2 + #7 + #9** — reporting and operator mode, last, because they only describe what already ran.

## Deliberately NOT absorbed

| Source | Why not |
|---|---|
| `touchstone` (18,877 lines) | NVIDIA HGX telemetry (fct/bbx/thermal). Unrelated. Found by description-matching, retracted after reading. |
| `CLI-Enforcement` wholesale (16k) | Ports its gate *architecture*; its 16k lines are a CLI-agent enforcement product, not a benchmark arm |
| `alignerr-task-factory` wholesale | Ports the no-paradox rule and the gate pattern; the generator targets Alignerr's ML file format |
| `devils_advocate` | 34-line LLM agent class. The concept (contradict the hypothesis) is right; the implementation writes ~60 deterministic lines here |
| OmniCLI wholesale (26,337 lines) | Excluded by design. verdict-bench carries its own arm; "as shipped" meant a dirty submodule no clone could reproduce |

## Attribution

`groundwork/LICENSE` carries a required notice — *Copyright SignalCore Inc. and Bastion
Fold LLC; commercial licensing: matrixbuilderops@proton.me*. Any vendored file keeps that
notice verbatim at the top, so an absorbed file is never mistaken for verdict-bench's own
work, and the chain of provenance survives into whatever Ali reads.

## Estimate this buys

~6 h of authoring becomes integration: the file tool (3 h → 1 h), the gate suite
(5 h → 2 h), the adversary (4 h → 1 h). Net saving ~8 h against writing all of it —
**conditional on the three read-first items** (#3, #4, #8's gold), which is exactly the
assumption the last plan got wrong. Those reads are Phase 1, priced at ~2 h, and they come
before any of the ports are started.