"""Arm C on World 1: Harness2 with ONE Propose-Probe-Compose step per task (parallel, k=1),
harnessing-level recursion threading lessons across the tasks of each family (domain), as in
the paper. Same model as executor AND editor (Harness2's own default when the improver model
is unset). The method never sees gold, criteria, the grader, or any score.

The scored output for a task is the execution Harness2 itself selects (summary.json ->
selections[mode].chosen_run_id); it is graded by world/grade_world.py after the loop ends.

  python runner/h2_world.py --families reconciliation log-forensics --exec-wall 480
"""
from __future__ import annotations

import argparse, json, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "runner")); sys.path.insert(0, str(ROOT / "world"))
import h2_absence as H  # noqa: E402  (shared OpenCode-in-docker plumbing, proposer, renderer)
import grade_world as gw  # noqa: E402
from harness2.core.schedule import mode_token, run_domain  # noqa: E402
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec  # noqa: E402

PUB = ROOT / "world" / "public"
FAMILY = {tid: t["family"] for tid, t in gw.GOLD.items()}


def prompt_for(ws: Path) -> str:  # identical wording to world/run_world.py
    return (f"Read {ws}/TASK.md and complete it. The company files are in {ws}/world/. "
            f"Write your answer to {ws}/output/answer.json exactly as TASK.md specifies. "
            f"Work autonomously and check your answer against the files before you finish.")


class WorldAdapter(H.AbsenceLedgerAdapter):
    def __init__(self, root: Path, exec_timeout: int):
        super().__init__(root, exec_timeout)
        ch = ChannelSpec("opencode", f"ollama/{H.MODEL}", effort="", steps=120, timeout=1800)
        self.spec = BenchSpec(
            name="world1-halvorsen", runs_root=root / "runs", install_mode="workspace_config",
            prompts=BenchPrompts(work="ops and audit desk work over a company's shared drive: "
                                      "reconciliations, log forensics, contract compliance, "
                                      "config audits and ops reporting; deliverable is one JSON file"),
            channels={"proposer": ch, "selector": ch},
            leak_globs=("tasks_gold*", "FREEZE*", "grade_world*"),
        )

    def tasks(self, split="all"):
        return sorted(FAMILY)

    def domain_of(self, task_id):
        return FAMILY[task_id]

    def task_prompt(self, task_id):
        return (PUB / "tasks" / f"{task_id}.md").read_text()

    def task_files(self, task_id):
        return PUB / "world"

    def execute(self, task_id, harness_dir, run_id, *, timeout=None):
        self.execution_count += 1
        run_dir = self.root / "executions" / run_id
        ws = run_dir / "ws"
        if ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True)
        shutil.copytree(PUB / "world", ws / "world")
        shutil.copy(PUB / "tasks" / f"{task_id}.md", ws / "TASK.md")
        (ws / "output").mkdir(); (ws / "scratch").mkdir()
        H.render_harness(Path(harness_dir or self.base_harness()), ws)
        (ws / "opencode.json").write_text(json.dumps(H.oc_config(H.MODEL)))
        wall = int(min(timeout or self.exec_timeout, self.exec_timeout))
        t0 = time.time()
        p = H.docker_opencode(ws, prompt_for(ws), wall, {}, f"h2w-{run_id}"[:60])
        (run_dir / "trace.jsonl").write_text(p.stdout)
        (run_dir / "trace.stderr").write_text(p.stderr)
        (run_dir / "meta.json").write_text(json.dumps({"rc": p.returncode, "wall_s": time.time() - t0,
                                                       "task": task_id, "harness": str(harness_dir)}))
        return run_dir

    def delivered(self, run_dir):
        return (run_dir / "ws" / "output" / "answer.json").is_file()


def grade_selected(root: Path, out: Path) -> list[dict]:
    rows = []
    for summ in sorted(root.rglob("summary.json")):
        s = json.loads(summ.read_text())
        tid = s.get("task_id")
        if tid not in FAMILY:
            continue
        for mode, sel in (s.get("selections") or {}).items():
            rid = sel.get("chosen_run_id") or ""
            ans = root / "executions" / rid / "ws" / "output" / "answer.json" if rid else Path("/nonexistent")
            r = gw.grade(ans, tid)
            row = {"arm": "C", "task_id": tid, "family": FAMILY[tid], "rep": 1, "task_pass": r["task_pass"],
                   "criteria_passed": r["criteria_passed"], "criteria_total": r["criteria_total"],
                   "valid_json": r["valid_json"], "chosen": sel.get("chosen_label"), "chosen_is_base": sel.get("chosen_is_base"),
                   "selection_error": sel.get("error", "")[:200], "wall_s": None, "rc": None}
            rows.append(row)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT / "h2-world-runs")
    ap.add_argument("--families", nargs="+", default=sorted(set(FAMILY.values())))
    ap.add_argument("--exec-wall", type=int, default=480)
    ap.add_argument("--k", type=int, default=1)
    a = ap.parse_args(argv)
    for fam in a.families:
        root = (a.root / fam).resolve(); root.mkdir(parents=True, exist_ok=True)
        ad = WorldAdapter(root, a.exec_wall)
        prop = H.LocalOpencodeProposer(ad.spec.channels["proposer"], audit_dir=root / "audit")
        tids = [t for t in ad.tasks() if FAMILY[t] == fam]
        t0 = time.time()
        res = run_domain(fam, tids, a.k, adapter=ad, prop=prop, runs_root=ad.spec.runs_root,
                         library_root=root / "library" / f"k{a.k}", modes=(mode_token("parallel"),))
        res.update(execution_count=ad.execution_count, editor_calls=len(prop._calls), wall_s=time.time() - t0)
        (root / "result.json").write_text(json.dumps(res, indent=2, default=str))
        rows = grade_selected(root, root / "graded.jsonl")
        for r in rows:
            print(f"[{time.strftime('%H:%M:%S')}] C {r['task_id']}: pass={r['task_pass']} "
                  f"{r['criteria_passed']}/{r['criteria_total']} json={r['valid_json']} chosen={r['chosen']}", flush=True)
        print(f"[{time.strftime('%H:%M:%S')}] C family {fam} done: {ad.execution_count} executions, "
              f"{len(prop._calls)} editor calls, {time.time()-t0:.0f}s", flush=True)
    print("C WORLD COMPLETE", flush=True)


if __name__ == "__main__":
    main()
