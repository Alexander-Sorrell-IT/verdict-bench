"""Run APEX-Agents tasks (Mercor, gated HF dataset) under our harness arms, graded by
Mercor's own Archipelago grader with ONE fixed judge for every arm.

Per task: fresh Archipelago environment container -> populate world + task files ->
register all MCP apps -> run the harness against the MCP gateway -> capture its final
answer -> snapshot -> write an Archipelago trajectory.json -> Mercor's grader (output_llm).

  python apex/run_apex.py --arm B --task-index 0 --wall 1200
Arms: B (Harness2 stock = OpenCode) | AF2 (OmniCLI + gates + MCP bridge) | REF (Mercor's react_toolbelt agent)
"""
from __future__ import annotations

import argparse, json, os, shutil, subprocess, sys, tarfile, tempfile, time, uuid, zipfile
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download, snapshot_download

ROOT = Path(__file__).resolve().parent.parent
ARCH = ROOT / "apex" / "archipelago-main"
EX = ARCH / "examples" / "hugging_face_task"
HF_DATASET = "mercor/apex-agents"
SUBSYSTEMS = ["filesystem", ".apps_data"]
MODEL = os.environ.get("MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
JUDGE = os.environ.get("JUDGE_MODEL", "mistralai/mistral-large-4-1t-a52b")
RELAY_HOST = "http://127.0.0.1:8787/v1"          # judge + REF agent run on the host
RELAY_DOCKER = "http://host.lima.internal:8787/v1"  # harness containers
TOKEN = (ROOT / "secrets" / "hf.key").read_text().strip()


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def task_prompt(task) -> str:
    # Identical for every arm. The task prompt is Mercor's verbatim; the suffix only says where
    # the tools are and how the answer is collected.
    return (task["prompt"].strip() + "\n\n---\nThe work environment (files, documents, spreadsheets, "
            "PDFs, email, chat, calendar) is available ONLY through the tools of the MCP server named "
            "'apex'. Use those tools to find and read what you need. When you are done, reply with your "
            "complete final answer as your last message.")


# ---------------------------------------------------------------- environment
def start_env(port: int) -> str:
    name = f"apex-env-{port}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", f"{port}:8080",
                    "--env-file", str(ARCH / "environment" / ".env"), "apex-env:local"], check=True, capture_output=True)
    url = f"http://127.0.0.1:{port}"
    for _ in range(180):
        try:
            if httpx.get(f"{url}/health", timeout=5).status_code == 200:
                return url
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("environment did not become healthy")


def populate(url: str, root: Path, out: Path, label: str):
    for sub in SUBSYSTEMS:
        d = root / sub
        if not d.exists():
            continue
        entries = list(d.rglob("*"))
        if not entries:
            continue
        tp = out / f"{label}_{sub}.tar.gz"
        with tarfile.open(tp, "w:gz") as tar:
            tar.dereference = True
            for e in entries:
                tar.add(e, arcname=str(e.relative_to(d)), recursive=False)
        r = httpx.post(f"{url}/data/populate", files={"archive": (tp.name, tp.read_bytes(), "application/gzip")},
                       params={"subsystem": sub}, timeout=600)
        r.raise_for_status()


def tar_gz_to_zip(p: Path) -> Path:
    z = p.with_suffix("").with_suffix(".zip")
    with tarfile.open(p) as tar, zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in tar.getmembers():
            if m.isfile():
                zf.writestr(m.name, tar.extractfile(m).read())
    return z


# ---------------------------------------------------------------- harness arms
def run_opencode(ws: Path, mcp_url: str, prompt: str, wall: int, logs: Path, harness_dir: Path | None = None) -> tuple[str, str]:
    """Harness2 stock = OpenCode with Harness2's (empty) base harness, MCP gateway attached."""
    ws.mkdir(parents=True, exist_ok=True)
    if harness_dir:
        sys.path.insert(0, str(ROOT / "runner")); import h2_absence as H  # noqa
        H.render_harness(harness_dir, ws)
    else:
        shutil.copy(ROOT / "runner" / "armB_harness" / "AGENTS.md", ws / "AGENTS.md")
    (ws / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "provider": {"ollama": {"npm": "@ai-sdk/openai-compatible", "name": "relay",
                                "options": {"baseURL": RELAY_DOCKER, "apiKey": "{env:BENCH_API_KEY}"},
                                "models": {MODEL: {"name": MODEL, "tools": True}}}},
        "model": f"ollama/{MODEL}",
        "mcp": {"apex": {"type": "remote", "url": mcp_url, "enabled": True}},
    }))
    perm = {"external_directory": {"*": "deny", str(ws): "allow", f"{ws}/**": "allow"}}
    cmd = ["docker", "run", "--rm", "--user", "root", "-v", f"{ws}:{ws}", "-w", str(ws),
           "-e", f"OPENCODE_PERMISSION={json.dumps(perm)}", "-e", "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=32000",
           "-e", "BENCH_API_KEY=relay", "ali-bench-agent:pilot", "timeout", "--signal=INT", "--kill-after=60", str(wall),
           "opencode", "run", prompt, "--dir", str(ws), "--model", f"ollama/{MODEL}", "--format", "json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall + 240)
    (logs / "trace.jsonl").write_text(p.stdout); (logs / "trace.stderr").write_text(p.stderr)
    final = ""
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("type") == "text":
                final = e.get("part", {}).get("text", "") or final
    return final, f"rc={p.returncode}"


def run_omnicli(ws: Path, mcp_url: str, prompt: str, wall: int, logs: Path, image: str) -> tuple[str, str]:
    ws.mkdir(parents=True, exist_ok=True)
    cfgdir = logs / "omni-config"; cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "config.json").write_text(json.dumps({"providers": {"ollama": {
        "name": "relay", "base_url": RELAY_DOCKER, "env_key": "OLLAMA_API_KEY", "default_model": MODEL,
        "description": "bench", "requires_auth_for_models": False, "fallback_models": [], "verification_panel": []}},
        "leadership": {"enabled": False}}))
    (logs / "prompt.txt").write_text(prompt)
    ans = ws / "final_answer.md"
    cmd = ["docker", "run", "--rm", "--user", "root", "-v", f"{ws}:{ws}", "-v", f"{logs}:/logs", "-w", str(ws),
           "-e", "OMNICLI_CONFIG_DIR=/logs/omni-config", "-e", "OLLAMA_API_KEY=relay",
           "-e", f"OMNI_MCP_URL={mcp_url}", "-e", f"OMNI_REQUIRED_OUTPUT={ans}",
           image, "timeout", "--signal=INT", "--kill-after=60", str(wall),
           "bash", "-lc", f"omni agent -p ollama -m '{MODEL}' --max-turns 400 --allow-shell --no-ground --workspace '{ws}' \"$(cat /logs/prompt.txt)\n\nAlso save your complete final answer verbatim to {ans}.\""]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall + 240)
    (logs / "trace.stdout").write_text(p.stdout); (logs / "trace.stderr").write_text(p.stderr)
    final = ans.read_text(errors="replace") if ans.exists() else ""
    if not final and "Final Answer:" in p.stdout:
        final = p.stdout.rsplit("Final Answer:", 1)[1].strip()
    return final, f"rc={p.returncode}"


# ---------------------------------------------------------------- grading
def grade(task, world_zip: Path, final_zip: Path, traj: Path, out: Path) -> dict:
    verifiers = [{"verifier_id": c["verifier_id"], "verifier_version": 1, "world_id": task["world_id"],
                  "task_id": task["task_id"], "eval_config_id": "ec_output_llm",
                  "verifier_values": {"criteria": c["criteria"], "is_primary_objective": i == 0},
                  "verifier_index": i, "verifier_dependencies": None} for i, c in enumerate(task.get("rubric", []))]
    (out / "verifiers.json").write_text(json.dumps(verifiers, indent=2))
    (out / "grading_settings.json").write_text(json.dumps({"llm_judge_model": f"openai/{JUDGE}", "llm_judge_extra_args": None}))
    gf = out / "grades.json"
    env = dict(os.environ, OPENAI_API_BASE=RELAY_HOST, OPENAI_API_KEY="relay")
    p = subprocess.run(["uv", "run", "python", "-m", "runner.main", "--grading-run-id", f"gr_{uuid.uuid4().hex[:8]}",
                        "--trajectory-id", traj.stem, "--initial-snapshot", str(world_zip), "--final-snapshot", str(final_zip),
                        "--trajectory", str(traj), "--grading-settings", str(out / "grading_settings.json"),
                        "--verifiers", str(out / "verifiers.json"), "--eval-configs", str(EX / "eval_configs.json"),
                        "--scoring-config", str(EX / "scoring_config.json"), "--output", str(gf)],
                       cwd=ARCH / "grading", env=env, capture_output=True, text=True, timeout=1800)
    (out / "grading.log").write_text(p.stdout[-20000:] + "\n--- stderr ---\n" + p.stderr[-20000:])
    return json.loads(gf.read_text()) if gf.exists() else {"error": "no grades.json", "rc": p.returncode}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["B", "AF2", "REF"])
    ap.add_argument("--task-index", type=int); ap.add_argument("--task-id")
    ap.add_argument("--wall", type=int, default=1200); ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--rep", type=int, default=1); ap.add_argument("--tag", default="apex")
    a = ap.parse_args()
    tasks = json.load(open(ROOT / "apex" / "data" / "tasks_and_rubrics.json"))
    task = tasks[a.task_index] if a.task_index is not None else next(t for t in tasks if t["task_id"] == a.task_id)
    out = ROOT / "logs" / a.tag / a.arm / f"{task['task_name']}-r{a.rep}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    log(f"{a.arm} {task['task_name']} [{task['domain']}] output={task['expected_output']}")
    url = start_env(a.port)
    t0 = time.time(); status = "failed"; final = ""; note = ""
    try:
        wz = hf_hub_download(HF_DATASET, f"world_files_zipped/{task['world_id']}.zip", repo_type="dataset", token=TOKEN)
        world_zip = out / "world.zip"; shutil.copy(wz, world_zip)
        with tempfile.TemporaryDirectory() as tmp:
            zipfile.ZipFile(world_zip).extractall(tmp); populate(url, Path(tmp), out, "world")
        if task.get("task_input_files"):
            sd = snapshot_download(HF_DATASET, repo_type="dataset", token=TOKEN, allow_patterns=[f"task_files/{task['task_id']}/**"])
            td = Path(sd) / "task_files" / task["task_id"]
            if td.exists():
                populate(url, td, out, "task")
        httpx.post(f"{url}/apps", json=json.load(open(EX / "mcp_config_all_oss_servers.json")), timeout=600).raise_for_status()
        prompt = task_prompt(task)
        mcp_docker = f"http://host.lima.internal:{a.port}/mcp/"
        ws = ROOT / "apex-runs" / a.tag / a.arm / f"{task['task_name']}-r{a.rep}"
        if ws.exists():
            shutil.rmtree(ws)
        if a.arm == "B":
            final, note = run_opencode(ws, mcp_docker, prompt, a.wall, out)
        elif a.arm == "AF2":
            final, note = run_omnicli(ws, mcp_docker, prompt, a.wall, out, "ali-bench-agent:omniaf2-mcp")
        status = "completed" if final.strip() else "failed"
    except Exception as e:
        note = f"harness/env error: {type(e).__name__}: {e}"
    elapsed = time.time() - t0
    traj = out / "trajectory.json"
    traj.write_text(json.dumps({"messages": [{"role": "user", "content": task_prompt(task)},
                                             {"role": "assistant", "content": final}],
                                "status": status, "time_elapsed": elapsed, "final_answer": final or None}, indent=2))
    with httpx.stream("POST", f"{url}/data/snapshot", timeout=600) as r:
        r.raise_for_status()
        tg = out / "final_snapshot.tar.gz"
        with open(tg, "wb") as f:
            for ch in r.iter_bytes(65536):
                f.write(ch)
    final_zip = tar_gz_to_zip(tg)
    subprocess.run(["docker", "rm", "-f", f"apex-env-{a.port}"], capture_output=True)
    g = grade(task, out / "world.zip", final_zip, traj, out) if status == "completed" else {"skipped": "no final answer"}
    score = (g.get("scoring_results") or {}).get("final_score")
    crit = [(v.get("verifier_id"), (v.get("score") if isinstance(v, dict) else None)) for v in g.get("verifier_results", [])]
    row = {"arm": a.arm, "task_id": task["task_id"], "task_name": task["task_name"], "domain": task["domain"], "rep": a.rep,
           "status": status, "final_score": score, "n_criteria": len(task.get("rubric", [])), "verifier_scores": crit,
           "wall_s": round(elapsed, 1), "note": note, "judge": JUDGE, "model": MODEL}
    (out / "row.json").write_text(json.dumps(row, indent=2))
    with open(ROOT / "logs" / a.tag / "results.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    log(f"[APEX] {a.arm} {task['task_name']}: status={status} score={score} ({elapsed:.0f}s) {note}")


if __name__ == "__main__":
    main()
