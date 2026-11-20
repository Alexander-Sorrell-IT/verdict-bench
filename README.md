# verdict-bench

**The model proposes; deterministic executed checks own the verdict.**

A harness-vs-harness benchmark built APEX-style: same model, same tasks, same tools, same budget,
one fixed grader. It compares Alexander Sorrell's harness (OmniCLI) against Google's Harness²
(stock, and after its own Propose-Probe-Compose improvement loop), on:

1. **World 1, "Halvorsen Freight: Ops & Audit Desk".** A seeded synthetic company (125 files: ledger,
   bank statement, contracts, invoices, SSH/VPN logs, HR, service configs, policies, tickets) with
   25 tasks across 5 families and 87 binary criteria. Every gold answer is computed from the data
   in code; every criterion is graded in code (no LLM judge).
2. **APEX-Agents** (Mercor), run through Mercor's own Archipelago environment and grader, with one
   fixed judge model for every arm. The dataset is gated: bring your own Hugging Face access.

## Integrity rules
- Gold, criteria and graders never enter an agent workspace; Harness²'s improver never sees scores.
- World 1 is frozen by hash before any arm runs (`world/FREEZE_world1.json`); a dev world
  (different seed) is the only place harness changes are developed.
- Validation before any run: gold 25/25, empty answers 0/25, one-value perturbations 0/25, and an
  independent solver that reads only the public files reproduces gold 25/25.
- Void rule, same for every arm: an attempt that ended because the infrastructure failed
  (launcher error, Docker failure, model endpoint failure) is voided and re-run once.
- All model traffic for all arms goes through one relay (`runner/relay.py`): one throttle and
  one retry policy, and the real API key never enters a container.

## Layout
| Path | What |
|---|---|
| `world/` | generator + gold (`build_world.py`), grader, runner, Pass@1/CI report, resume tool, independent solver |
| `runner/` | sandbox images, per-arm launcher, shared relay, void sweep, OmniCLI patches |
| `harness2_adapter/` | adapters that run Google's Harness² (its own `run_domain` loop) on these tasks; stock base harness |
| `apex/` | APEX-Agents wrapper over Mercor's Archipelago env + grader; pre-registered dev/test split |

## OmniCLI variants (patches in `runner/omnicli-patches/`)
- **A**: OmniCLI as shipped.
- **AF**: + completion gate (a run can't end until the deliverable exists) + 900 s shell default.
- **AF2**: + output-validation gate (the deliverable must parse and carry the task's schema keys) + script-first work method.
- **AF2-MCP**: + MCP bridge (streamable HTTP) so OmniCLI can use APEX's apps.

## Reproduce World 1
```bash
python world/build_world.py                  # regenerate world + gold (seed 20261120)
python -I world/selfcheck/independent_solver.py world/public/world /tmp/out   # gold check
python world/run_world.py --arms A B --k 1   # needs the sandbox image + a model endpoint (see runner/)
python world/report.py logs/world/<tag>/results.jsonl
```

Third-party: Harness² (Apache-2.0, google-research/harness2), Archipelago (Mercor), OpenCode. Their
code is not vendored here; adapters call it.
