"""Void-and-rerun sweep (same rule for every arm). An attempt is VOID if the launcher failed
(rc 127) or it ended because the model endpoint failed: OmniCLI 'Agent stopped: retry:*' in
stderr, or an OpenCode event of type 'error'. Each void attempt is re-run once, identically.
  python runner/sweep_void.py --tag hosted
"""
import argparse, json, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "world"))


def void_reason(d: Path) -> str:
    m = d / "meta.end.json"
    if m.exists() and json.loads(m.read_text()).get("rc") == 127:
        return "launcher rc 127"
    err = (d / "trace.stderr").read_text(errors="replace") if (d / "trace.stderr").exists() else ""
    if "Agent stopped: retry:" in err:
        return "endpoint failure (OmniCLI retry:*)"
    out = d / "trace.stdout"
    if out.exists():
        for l in out.read_text(errors="replace").splitlines():
            if l.startswith("{"):
                try:
                    if json.loads(l).get("type") == "error":
                        return "endpoint failure (OpenCode error event)"
                except Exception:
                    pass
    return ""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", default="hosted"); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    import os; os.environ["RUN_TAG"] = a.tag
    import run_world as rw
    base = ROOT / "logs" / "world" / a.tag
    for d in sorted(p for p in base.glob("*/*") if p.is_dir() and not p.name.endswith("VOID") and ".VOID" not in p.name):
        why = void_reason(d)
        if not why:
            continue
        arm, lab = d.parent.name, d.name
        tid, rep = lab.rsplit("-r", 1)[0], int(lab.rsplit("-r", 1)[1])
        print(f"VOID {arm} {tid} r{rep}: {why}", flush=True)
        if a.dry:
            continue
        outs = [base / "results.jsonl", base / "results_af.jsonl", base / "results_af2.jsonl"]
        for o in outs:
            if o.exists():
                keep = [l for l in o.read_text().splitlines() if l and not (lambda r: r["arm"] == arm and r["task_id"] == tid and r["rep"] == rep)(json.loads(l))]
                o.write_text("".join(k + "\n" for k in keep))
        shutil.move(str(d), str(d) + ".VOID")
        r = rw.run_one(arm, tid, rep, 480)
        out = base / ("results_af.jsonl" if arm == "AF" else "results_af2.jsonl" if arm == "AF2" else "results.jsonl")
        with out.open("a") as f:
            f.write(json.dumps({k: r[k] for k in ("arm", "task_id", "family", "rep", "task_pass", "criteria_passed", "criteria_total", "valid_json", "wall_s", "rc")}) + "\n")
        print(f"[RERUN] {arm} {tid} r{rep}: pass={r['task_pass']} {r['criteria_passed']}/{r['criteria_total']} still_void={bool(void_reason(base/arm/lab))}", flush=True)
    print("SWEEP COMPLETE", flush=True)


if __name__ == "__main__":
    main()
