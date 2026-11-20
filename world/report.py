"""Pass@1 + criteria rate per arm and per family, with bootstrap 95% CIs (resampling tasks).

  python world/report.py logs/world/results.jsonl
"""
import json, random, sys
from collections import defaultdict


def boot(vals, n=5000, seed=0):
    if not vals:
        return (float("nan"),) * 3
    r = random.Random(seed); m = sum(vals) / len(vals)
    s = sorted(sum(r.choice(vals) for _ in vals) / len(vals) for _ in range(n))
    return m, s[int(0.025 * n)], s[int(0.975 * n) - 1]


rows = [json.loads(l) for l in open(sys.argv[1])]
by = defaultdict(lambda: defaultdict(list))
for r in rows:
    by[r["arm"]][r["task_id"]].append(r)
fams = sorted({r["family"] for r in rows})
print("| Arm | Tasks | Attempts | Pass@1 (95% CI) | Criteria passed (95% CI) |")
print("|---|---|---|---|---|")
for arm in sorted(by):
    # per-task mean over repeats, then bootstrap over tasks
    p = [sum(x["task_pass"] for x in v) / len(v) for v in by[arm].values()]
    c = [sum(x["criteria_passed"] / x["criteria_total"] for x in v) / len(v) for v in by[arm].values()]
    pm, pl, ph = boot(p); cm, cl, ch = boot(c)
    print(f"| {arm} | {len(p)} | {sum(len(v) for v in by[arm].values())} | {pm:.1%} ({pl:.0%}-{ph:.0%}) | {cm:.1%} ({cl:.0%}-{ch:.0%}) |")
print("\n| Family | " + " | ".join(f"Arm {a} pass / criteria" for a in sorted(by)) + " |")
print("|---|" + "---|" * len(by))
for f in fams:
    cells = []
    for arm in sorted(by):
        xs = [x for v in by[arm].values() for x in v if x["family"] == f]
        cells.append(f"{sum(x['task_pass'] for x in xs)}/{len(xs)} / {sum(x['criteria_passed'] for x in xs)}/{sum(x['criteria_total'] for x in xs)}" if xs else "-")
    print(f"| {f} | " + " | ".join(cells) + " |")
