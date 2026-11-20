"""Rebuild a results file from the attempt logs (the source of truth) and run only what is missing.

VALID attempt = meta.end.json exists and rc is not 125 (docker failure) / 127 (launcher bug), and the
trace shows no endpoint failure. Everything else is moved aside as .VOID-<reason> and re-run once.

  RUN_TAG=hosted python world/resume_world.py --arms A B --k 2 --out logs/world/hosted/results.jsonl --parallel 2
"""
import argparse, json, os, shutil, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "world")); sys.path.insert(0, str(ROOT / "runner"))
import run_world as rw  # noqa: E402
from sweep_void import void_reason  # noqa: E402

FIELDS = ("arm", "task_id", "family", "rep", "task_pass", "criteria_passed", "criteria_total", "valid_json", "wall_s", "rc")


def classify(d: Path) -> str:
    m = d / "meta.end.json"
    if not m.exists():
        return "killed"
    rc = json.loads(m.read_text()).get("rc")
    if rc in (125, 127):
        return f"rc{rc}"
    return void_reason(d) and "endpoint" or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", required=True); ap.add_argument("--k", type=int, required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--wall", type=int, default=480); ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    tag = os.environ.get("RUN_TAG", "hosted")
    base = ROOT / "logs" / "world" / tag
    tasks = sorted(rw.gw.GOLD)
    valid, todo, voided = [], [], 0
    for arm in a.arms:
        for tid in tasks:
            for rep in range(1, a.k + 1):
                d = base / arm / f"{tid}-r{rep}"
                if d.is_dir() and (d / "grade.json").exists() and not classify(d):
                    g = json.loads((d / "grade.json").read_text())
                    valid.append({k: g.get(k) for k in FIELDS})
                    continue
                if d.is_dir():
                    why = classify(d) or "no-grade"
                    if not a.dry:
                        dest = Path(f"{d}.VOID-{why}")
                        n = 1
                        while dest.exists():
                            n += 1; dest = Path(f"{d}.VOID-{why}{n}")
                        shutil.move(str(d), str(dest))
                    voided += 1
                todo.append((arm, tid, rep))
    print(f"valid={len(valid)} voided_now={voided} to_run={len(todo)}", flush=True)
    if a.dry:
        return
    out = Path(a.out)
    out.write_text("".join(json.dumps(r) + "\n" for r in valid))
    lock = threading.Lock()

    def go(job):
        arm, tid, rep = job
        r = rw.run_one(arm, tid, rep, a.wall)
        why = classify(base / arm / f"{tid}-r{rep}")
        with lock:
            if not why:
                with out.open("a") as f:
                    f.write(json.dumps({k: r[k] for k in FIELDS}) + "\n")
            print(f"[{time.strftime('%H:%M:%S')}] {arm} {tid} r{rep}: pass={r['task_pass']} "
                  f"{r['criteria_passed']}/{r['criteria_total']}{'  VOID:' + why if why else ''}", flush=True)

    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        list(ex.map(go, todo))
    print("RESUME COMPLETE", flush=True)


if __name__ == "__main__":
    main()
