"""Run World 1 tasks under each arm, grade each attempt, write results.jsonl.

  python world/run_world.py --arms A B --tasks R1 L1 C1 K1 D1 --k 1 --wall 480

Each attempt gets a FRESH workspace: world/ (public files only) + TASK.md + output/.
The private gold never enters a workspace. Same prompt, model and wall clock on every arm.
"""
from __future__ import annotations

import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "world"))
import grade_world as gw  # noqa: E402

PUB = Path(os.environ.get("WORLD_ROOT", ROOT / "world")) / "public"
TAG = os.environ.get("RUN_TAG", "pilot7b")


def prompt_for(ws: Path) -> str:
    return (f"Read {ws}/TASK.md and complete it. The company files are in {ws}/world/. "
            f"Write your answer to {ws}/output/answer.json exactly as TASK.md specifies. "
            f"Work autonomously and check your answer against the files before you finish.")


def required_keys(tid: str) -> list[str]:
    """Top-level keys of the JSON shape the task itself states (TASK.md deliverable block)."""
    import re
    m = re.search(r"```json\n(.*?)\n```", (PUB / "tasks" / f"{tid}.md").read_text(), re.S)
    return list(json.loads(m.group(1)).keys()) if m else []


def run_one(arm: str, tid: str, rep: int, wall: int) -> dict:
    label = f"{tid}-r{rep}"
    ws = ROOT / "world-runs" / TAG / arm / label
    logs = ROOT / "logs" / "world" / TAG / arm / label
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    shutil.copytree(PUB / "world", ws / "world")
    shutil.copy(PUB / "tasks" / f"{tid}.md", ws / "TASK.md")
    (ws / "output").mkdir(); (ws / "scratch").mkdir()
    env = dict(os.environ, WS_OVERRIDE=str(ws), LOGS_OVERRIDE=str(logs), WALL=str(wall),
               PROMPT_OVERRIDE=prompt_for(ws), REQUIRED_OUTPUT=str(ws / "output" / "answer.json"),
               REQUIRED_KEYS=",".join(required_keys(tid)))
    t0 = time.time()
    subprocess.run([str(ROOT / "runner" / "run_arm.sh"), arm, label], env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ans = logs / "submission" / "answer.json"
    r = gw.grade(ans, tid)
    r.update(arm=arm, rep=rep, wall_s=round(time.time() - t0, 1),
             rc=json.loads((logs / "meta.end.json").read_text()).get("rc") if (logs / "meta.end.json").exists() else None)
    (logs / "grade.json").write_text(json.dumps(r, indent=2))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", nargs="+", default=["A", "B"])
    ap.add_argument("--tasks", nargs="+", default=sorted(gw.GOLD))
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--wall", type=int, default=480)
    ap.add_argument("--out", default=str(ROOT / "logs" / "world" / "results.jsonl"))
    ap.add_argument("--parallel", type=int, default=1, help="concurrent attempts (hosted endpoint only)")
    a = ap.parse_args()
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    jobs = []
    for rep in range(1, a.k + 1):
        for tid in a.tasks:
            # alternate arm order per task so neither arm systematically goes first
            for arm in (a.arms if (rep + a.tasks.index(tid)) % 2 else list(reversed(a.arms))):
                jobs.append((arm, tid, rep))
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def go(job):
        arm, tid, rep = job
        r = run_one(arm, tid, rep, a.wall)
        with lock, out.open("a") as f:
            f.write(json.dumps({k: r[k] for k in ("arm", "task_id", "family", "rep", "task_pass",
                                                 "criteria_passed", "criteria_total", "valid_json", "wall_s", "rc")}) + "\n")
            print(f"[{time.strftime('%H:%M:%S')}] {arm} {tid} r{rep}: pass={r['task_pass']} "
                  f"{r['criteria_passed']}/{r['criteria_total']} json={r['valid_json']}", flush=True)

    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        list(ex.map(go, jobs))
    print("WORLD RUN COMPLETE", flush=True)


if __name__ == "__main__":
    main()
