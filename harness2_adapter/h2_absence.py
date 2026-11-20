"""Arm C: one real Harness2 improvement step (Propose-Probe-Compose, parallel, k=1) on the
Absence Ledger, with the same local Qwen as executor AND editor.

The adapter follows harness2/demo.py's DemoAdapter shape. What the method sees: the task
prompt, the agent's trajectories and its deliverable files. What it never sees: scorer/,
labels, the pilot grader, or any score. RunView carries no score by design.

  .venv-bench/bin/python runner/h2_absence.py --loop-timeout 900
"""
from __future__ import annotations

import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "h2" / "harness2-main"))

from harness2.core import tree  # noqa: E402
from harness2.core.proposer import Proposer, _opencode_cost, _opencode_stdout  # noqa: E402
from harness2.core.schedule import mode_token, run_domain  # noqa: E402
from harness2.core.spec import BenchPrompts, BenchSpec, ChannelSpec  # noqa: E402
from harness2.substrate.opencode import OpencodeSubstrate, render_events  # noqa: E402

MODEL = os.environ.get("MODEL", "qwen25-7b-pilot")
OLLAMA_URL = os.environ.get("ENDPOINT_URL", "http://host.lima.internal:11434/v1")
API_KEY = Path(os.environ["ENDPOINT_KEY_FILE"]).read_text().strip() if os.environ.get("ENDPOINT_KEY_FILE") else "ollama"
IMAGE = "ali-bench-agent:pilot"
TASK = "absence-ledger"
TEMPLATE = ROOT / "arms" / "C"          # instruction.md + data/ (identical to A and B)
BASE = ROOT / "h2" / "harness2-main" / "third_party" / "jb_runner" / "harness_baseline_oc"


def oc_config(model: str, extra: dict | None = None) -> dict:
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "Ollama (local)",
                                "options": {"baseURL": OLLAMA_URL, "apiKey": "{env:BENCH_API_KEY}"},
                                "models": {model: {"name": model, "tools": True}}}},
        "model": f"ollama/{model}",
    }
    cfg.update(extra or {})
    return cfg


def docker_opencode(cwd: Path, prompt: str, wall: int, env: dict[str, str], title: str):
    """Run OpenCode in the shared sandbox with only `cwd` mounted (same path inside)."""
    perm = {"external_directory": {"*": "deny", str(cwd): "allow", f"{cwd}/**": "allow"}}
    envs = sum((["-e", f"{k}={v}"] for k, v in
                {"OPENCODE_PERMISSION": json.dumps(perm),
                 "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": "32000", "BENCH_API_KEY": API_KEY, **env}.items()), [])
    cmd = ["docker", "run", "--rm", "--user", "root", "-v", f"{cwd}:{cwd}", "-w", str(cwd), *envs,
           IMAGE, "timeout", "--signal=INT", "--kill-after=60", str(wall),
           "opencode", "run", prompt, "--dir", str(cwd), "--model", f"ollama/{MODEL}",
           "--title", title, "--format", "json"]
    return subprocess.run(cmd, text=True, capture_output=True, timeout=wall + 180)


def render_harness(harness_dir: Path, ws: Path) -> None:
    """Place an OpenCode harness tree into a workspace (APPEND mode, as JobBench does):
    always-on prose -> AGENTS.md; skills/subagents/plugins -> .opencode/; scripts -> harness_scripts/."""
    surf = OpencodeSubstrate().surface()
    parts = [t for f in surf.always_on
             if (harness_dir / f).is_file() and (t := (harness_dir / f).read_text(errors="replace").strip())]
    for sk in sorted(harness_dir.glob("skills/*/SKILL.md")):
        d = ws / ".opencode" / "skills" / sk.parent.name
        d.mkdir(parents=True, exist_ok=True); shutil.copy(sk, d / "SKILL.md")
    for ag in sorted(harness_dir.glob("sub_agents/*/AGENT.md")):
        d = ws / ".opencode" / "agent"; d.mkdir(parents=True, exist_ok=True)
        shutil.copy(ag, d / f"{ag.parent.name}.md")
    for pl in sorted(harness_dir.glob("plugin/*.ts")):
        d = ws / ".opencode" / "plugin"; d.mkdir(parents=True, exist_ok=True); shutil.copy(pl, d / pl.name)
    scripts = sorted(harness_dir.glob("scripts/*.py"))
    if scripts:
        d = ws / "harness_scripts"; d.mkdir(exist_ok=True)
        for s in scripts:
            shutil.copy(s, d / s.name)
        parts.append("# HELPER SCRIPTS\nAvailable at `harness_scripts/`: "
                     + ", ".join(s.name for s in scripts))
    (ws / "AGENTS.md").write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")


def task_prompt_for(ws: Path) -> str:
    return (f"Read {ws}/instruction.md and complete the task it describes. Your working directory "
            f"is {ws}. The data is in {ws}/data/. Write your submission to {ws}/output/ exactly as "
            f"the instruction specifies. You may use {ws}/scratch/ for anything else. Work "
            f"autonomously until the submission is written and you have checked that it loads and "
            f"predicts on the validation data.")


def fresh_workspace(ws: Path) -> None:
    """A copy of the frozen arm template, with instruction paths re-pointed at `ws`."""
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "output").mkdir(parents=True); (ws / "scratch").mkdir()
    shutil.copytree(TEMPLATE / "data", ws / "data")
    text = (TEMPLATE / "instruction.md").read_text().replace(str(TEMPLATE), str(ws))
    (ws / "instruction.md").write_text(text)


class AbsenceLedgerAdapter:
    def __init__(self, root: Path, exec_timeout: int):
        self.root = root
        self.exec_timeout = exec_timeout
        self.substrate = OpencodeSubstrate()
        channel = ChannelSpec("opencode", f"ollama/{MODEL}", effort="", steps=120, timeout=1800)
        self.spec = BenchSpec(
            name="absence-ledger-pilot",
            runs_root=root / "runs",
            install_mode="workspace_config",
            prompts=BenchPrompts(work="build a predictor that authenticates claims about "
                                      "destroyed ledger entries from survival masks (tabular ML)"),
            channels={"proposer": channel, "selector": channel},
            leak_globs=("test_labels*", "challenge*", "*.parquet"),
        )
        self.execution_count = 0
        base = self.base_harness()
        if not base.exists():
            shutil.copytree(BASE, base)

    def tasks(self, split: str = "all") -> list[str]:
        return [TASK]

    def domain_of(self, task_id: str) -> str:
        return "ml-forensics"

    def task_prompt(self, task_id: str) -> str:
        return task_prompt_for(Path("<workspace>"))

    def task_files(self, task_id: str) -> Path:
        return TEMPLATE

    def base_harness(self) -> Path:
        return self.root / "base"

    def validate_tree(self, harness: tree.Tree) -> None:
        return None

    def execute(self, task_id, harness_dir, run_id, *, timeout=None) -> Path:
        self.execution_count += 1
        run_dir = self.root / "executions" / run_id
        ws = run_dir / "ws"
        fresh_workspace(ws)
        render_harness(Path(harness_dir or self.base_harness()), ws)
        (ws / "opencode.json").write_text(json.dumps(oc_config(MODEL)))
        wall = int(min(timeout or self.exec_timeout, self.exec_timeout))
        t0 = time.time()
        p = docker_opencode(ws, task_prompt_for(ws), wall, {}, f"h2-{run_id}")
        (run_dir / "trace.jsonl").write_text(p.stdout)
        (run_dir / "trace.stderr").write_text(p.stderr)
        (run_dir / "meta.json").write_text(json.dumps({"rc": p.returncode, "wall_s": time.time() - t0,
                                                       "harness": str(harness_dir)}))
        return run_dir

    def delivered(self, run_dir: Path) -> bool:
        return (run_dir / "ws" / "output" / "predictor.py").is_file()

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        return render_events(run_dir / "trace.jsonl", obs_trunc=obs_trunc, max_chars=120_000)

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        out = run_dir / "ws" / "output"
        return {p.name: p.read_text(errors="replace") for p in sorted(out.glob("*"))
                if p.is_file() and p.suffix in (".py", ".json", ".txt", ".md")}

    def deliverable_text(self, run_dir: Path) -> str:
        return "\n\n".join(f"## {k}\n{v}" for k, v in self.deliverable_files(run_dir).items())


class LocalOpencodeProposer(Proposer):
    """Harness2's Proposer with its OpenCode session routed to the sandboxed local model.
    Mission, prompt, step cap and answer harvesting are Harness2's own (_opencode body)."""

    def env(self) -> dict[str, str]:
        return {}

    def _opencode(self, cwd: Path, mission: str, system: str, wall: int):
        cwd = Path(cwd)
        (cwd / "MISSION.md").write_text((f"{system}\n\n---\n\n" if system else "") + mission,
                                        encoding="utf-8")
        out = cwd / "output"; out.mkdir(exist_ok=True)
        (cwd / "opencode.json").write_text(json.dumps(
            oc_config(MODEL, {"agent": {"build": {"steps": self.spec.steps}}})), encoding="utf-8")
        prompt = ("Read MISSION.md first. Do what it asks. Reply with the requested output as "
                  "your final message AND save it verbatim to output/answer.md.")
        p = docker_opencode(cwd, prompt, wall, {}, "h2-editor")
        cost = _opencode_cost(p.stdout)
        text, err = _opencode_stdout(p.stdout)
        if err:
            return "", cost, err
        ans = out / "answer.md"
        if ans.is_file() and ans.read_text(errors="replace").strip():
            return ans.read_text(errors="replace"), cost, ""
        if text.strip():
            return text, cost, ""
        return "", cost, f"rc={p.returncode}: {(p.stderr or '').strip()[-300:] or 'empty reply'}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT / "h2-runs")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--mode", choices=("parallel", "sequential"), default="parallel")
    ap.add_argument("--loop-timeout", type=int, default=900, help="wall per in-loop execution")
    a = ap.parse_args(argv)
    root = a.root.resolve(); root.mkdir(parents=True, exist_ok=True)
    adapter = AbsenceLedgerAdapter(root, a.loop_timeout)
    prop = LocalOpencodeProposer(adapter.spec.channels["proposer"], audit_dir=root / "audit")
    t0 = time.time()
    result = run_domain(adapter.domain_of(TASK), adapter.tasks(), a.k, adapter=adapter, prop=prop,
                        runs_root=adapter.spec.runs_root, library_root=root / "library" / f"k{a.k}",
                        modes=(mode_token(a.mode),))
    result["execution_count"] = adapter.execution_count
    result["editor_calls"] = len(prop._calls)
    result["loop_wall_s"] = time.time() - t0
    (root / "result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({k: result[k] for k in ("done", "failed", "execution_count", "editor_calls",
                                             "loop_wall_s")}))
    return int(result["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
