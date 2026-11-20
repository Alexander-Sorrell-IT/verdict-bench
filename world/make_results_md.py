"""Write RESULTS.md for the public repo from the scored results files.

  python world/make_results_md.py --out ~/verdict-bench/RESULTS.md
"""
import argparse, json, random, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAM = {"R": "reconciliation", "L": "log-forensics", "C": "contract-compliance", "K": "config-audit", "D": "ops-reporting"}
NAMES = {"A": "OmniCLI (Alex's harness), as shipped", "B": "Harness² stock (Google)",
         "C": "Harness² + its improvement loop (Google)", "AF2": "OmniCLI + deterministic gates (AF2)"}


def load(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def boot(v, n=10000, seed=0):
    rng = random.Random(seed); m = sum(v) / len(v)
    s = sorted(sum(rng.choice(v) for _ in v) / len(v) for _ in range(n))
    return m, s[int(.025 * n)], s[int(.975 * n) - 1]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); a = ap.parse_args()
    rows = [r for r in load(ROOT / "logs/world/hosted/results.jsonl") if r["rep"] == 1]
    rows += [dict(r, arm="AF2") for r in load(ROOT / "logs/world/hosted/results_af2.jsonl") if r["rep"] == 1]
    rows += [r for r in load(ROOT / "h2-world-runs/graded_all.jsonl")]
    by = defaultdict(dict)
    for r in rows:
        by[r["arm"]][r["task_id"]] = r
    tasks = sorted({t for d in by.values() for t in d})
    arms = [x for x in ["A", "B", "AF2", "C"] if x in by]
    L = ["# Results: OmniCLI vs Google Harness² on World 1", "",
         f"_Generated {time.strftime('%Y-%m-%d %H:%M %Z')} from the per-attempt results in `results/`._", "",
         "**Setup:** World 1 (Halvorsen Freight ops & audit desk), 25 tasks, 87 binary criteria graded in code. "
         "Same model for every arm (`nvidia/nemotron-3-ultra-550b-a55b`), same prompt, same 8-minute wall per attempt, "
         "same sandbox image family, all model traffic through one shared relay. Pass@1 = one attempt per task (k=1). "
         "95% intervals are bootstrap over tasks.", "",
         "| Arm | Tasks scored | Pass@1 (95% CI) | Criteria passed | Wrong answers* |", "|---|---|---|---|---|"]
    for arm in arms:
        v = list(by[arm].values())
        m, lo, hi = boot([x["task_pass"] for x in v])
        cp = sum(x["criteria_passed"] for x in v); ct = sum(x["criteria_total"] for x in v)
        wrong = sum(1 for x in v if x.get("valid_json") and not x["task_pass"])
        L.append(f"| {NAMES[arm]} | {len(v)}/25 | **{m:.0%}** ({lo:.0%}–{hi:.0%}) | {cp}/{ct} | {wrong} |")
    L += ["", "\\*Wrong answer = the arm wrote a valid answer that failed a criterion. Every other failure is a run that "
          "ended without delivering an answer.", ""]
    if "A" in by and "B" in by:
        both = sorted(set(by["A"]) & set(by["B"]))
        d = [by["A"][t]["task_pass"] - by["B"][t]["task_pass"] for t in both]
        m, lo, hi = boot(d)
        L += ["## Head-to-head: OmniCLI (as shipped) vs Harness² stock", "",
              f"On the {len(both)} tasks both completed: OmniCLI {sum(by['A'][t]['task_pass'] for t in both)}, "
              f"Harness² {sum(by['B'][t]['task_pass'] for t in both)}. Paired difference {m:+.0%} "
              f"(95% CI {lo:+.0%} to {hi:+.0%}). "
              + ("**Statistically indistinguishable at this sample size.**" if lo < 0 < hi else "**Difference is outside the 95% interval.**"), "",
              f"- OmniCLI-only wins: {', '.join(t for t in both if by['A'][t]['task_pass'] and not by['B'][t]['task_pass']) or 'none'}",
              f"- Harness²-only wins: {', '.join(t for t in both if by['B'][t]['task_pass'] and not by['A'][t]['task_pass']) or 'none'}", ""]
    L += ["## Per-task matrix (✓ pass, ✗ fail, · not yet run)", "", "| Task | Family | " + " | ".join(arms) + " |",
          "|---|---|" + "---|" * len(arms)]
    for t in tasks:
        cells = []
        for arm in arms:
            x = by[arm].get(t)
            cells.append("·" if x is None else ("✓" if x["task_pass"] else "✗") + f" {x['criteria_passed']}/{x['criteria_total']}")
        L.append(f"| {t} | {FAM[t[0]]} | " + " | ".join(cells) + " |")
    L += ["", "## Per family (passed / scored)", "", "| Family | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for f in FAM.values():
        cells = []
        for arm in arms:
            xs = [x for x in by[arm].values() if FAM[x["task_id"][0]] == f]
            cells.append(f"{sum(x['task_pass'] for x in xs)}/{len(xs)}" if xs else "·")
        L.append(f"| {f} | " + " | ".join(cells) + " |")
    L += ["", "## Disclosures", "",
          "- **Free-tier model endpoint.** NVIDIA's per-model quota (HTTP 429) throttled every arm equally through the shared relay.",
          "- **Void rule (all arms):** attempts ended by infrastructure (launcher error, Docker I/O failure after the host disk filled, "
          "model-endpoint failure) were voided and re-run once under identical settings. One OmniCLI attempt that graded pass but ended "
          "in a Docker error was voided too.",
          "- **OmniCLI variants:** AF2 adds a completion gate, an output-validation gate and a script-first method, developed only on a "
          "separate dev world (different seed); this world (the test world) was frozen by hash before any arm ran and never used for tuning.",
          "- **Harness² arms:** B is Harness²'s own stock base harness on OpenCode 1.14.18 (its pinned version); C runs Harness²'s own "
          "`run_domain` Propose-Probe-Compose loop with the same model as executor and editor (its default).",
          "- **Still running when generated:** any arm with fewer than 25 tasks scored. This file is regenerated as results land.", ""]
    Path(a.out).expanduser().write_text("\n".join(L) + "\n")
    print("\n".join(L[:20]))


if __name__ == "__main__":
    main()
