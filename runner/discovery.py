#!/usr/bin/env python3
# verdict-bench runner/discovery.py
#
# Environment Discovery Layer — for truly unknown worlds.
#
# Before any hypothesis, the harness must map the unknown environment:
#   * what tools are available
#   * what filesystem layout exists
#   * what output expectations exist (READMEs, schemas, examples)
#   * what failure modes look like (try bad commands, observe errors)
#
# Produces discovery.md — the map everything else builds on.

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

DISCOVERY_PATH = Path("discovery.md")


def _run(cmd: List[str], timeout: int = 10) -> Dict[str, Any]:
    """Run a command safely, capture stdout/stderr/exit code."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "cmd": " ".join(cmd),
            "exit_code": result.returncode,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
            "ok": result.returncode == 0,
        }
    except FileNotFoundError:
        return {
            "cmd": " ".join(cmd),
            "exit_code": -1,
            "stdout": "",
            "stderr": f"command not found: {cmd[0]}",
            "ok": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": " ".join(cmd),
            "exit_code": -2,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "ok": False,
        }
    except Exception as exc:  # noqa: BLE001 — discovery never crashes
        return {
            "cmd": " ".join(cmd),
            "exit_code": -3,
            "stdout": "",
            "stderr": f"error: {exc}",
            "ok": False,
        }


def discover_available_tools() -> List[Dict[str, Any]]:
    """Probe which binaries are on PATH."""
    common_tools = [
        "bash",
        "sh",
        "python3",
        "python",
        "curl",
        "wget",
        "ls",
        "cat",
        "find",
        "grep",
        "sed",
        "awk",
        "jq",
        "xxd",
        "file",
        "stat",
        "which",
        "env",
        "printenv",
        "ps",
        "kill",
        "sleep",
        "mkdir",
        "touch",
        "cp",
        "mv",
        "rm",
        "chmod",
        "tar",
        "zip",
        "unzip",
        "openssl",
        "md5sum",
        "sha256sum",
        "base64",
    ]
    results: List[Dict[str, Any]] = []
    for tool in common_tools:
        res = _run(["which", tool])
        if res["ok"]:
            version = _run([tool, "--version"], timeout=5)
            results.append(
                {
                    "tool": tool,
                    "path": res["stdout"].strip(),
                    "version": (
                        version.get("stdout", "").split("\n")[0]
                        if version["ok"]
                        else "unknown"
                    ),
                }
            )
    return results


def discover_filesystem() -> Dict[str, Any]:
    """Map the filesystem layout."""
    results: Dict[str, Any] = {}

    root_listing = _run(["ls", "-la", "/"])
    results["root"] = root_listing

    home_listing = _run(["ls", "-la", os.path.expanduser("~")])
    results["home"] = home_listing

    cwd_listing = _run(["ls", "-la", "."])
    results["cwd"] = cwd_listing

    env_vars = _run(["env"])
    results["environment"] = env_vars

    path_env = os.environ.get("PATH", "")
    results["path"] = path_env

    # Find common config/schema/example files
    config_find = _run(
        [
            "find",
            "/",
            "-maxdepth",
            "3",
            "(",
            "-name",
            "*.json",
            "-o",
            "-name",
            "*.yaml",
            "-o",
            "-name",
            "*.yml",
            "-o",
            "-name",
            "*.md",
            "-o",
            "-name",
            "*.toml",
            "-o",
            "-name",
            "*.cfg",
            ")",
            "2>/dev/null",
        ]
    )
    results["config_files"] = config_find

    return results


def discover_output_expectations(cwd: Path) -> Dict[str, Any]:
    """Look for READMEs, task descriptions, schema files, example outputs."""
    results: Dict[str, Any] = {}

    # Look for README-like files in cwd
    readme_files = list(cwd.glob("README*")) + list(cwd.glob("*.md"))
    results["readme_files"] = [str(p.relative_to(cwd)) for p in readme_files]

    # Look for schema files
    schema_files = list(cwd.glob("**/*.json")) + list(cwd.glob("**/*.yaml"))
    results["schema_like_files"] = [str(p.relative_to(cwd)) for p in schema_files[:20]]

    # Try to read any task description
    task_desc = None
    for name in ["TASK.md", "task.md", "task.txt", "README.md", "INSTRUCTIONS.md"]:
        p = cwd / name
        if p.exists():
            task_desc = p.read_text(errors="replace")[:3000]
            results["task_description_file"] = name
            results["task_description"] = task_desc
            break

    return results


def probe_failure_modes() -> List[Dict[str, Any]]:
    """Try intentionally bad commands to learn error message formats."""
    probes = [
        (["ls", "/nonexistent_path_xyz"], "file not found"),
        (["cat", "/nonexistent_file"], "file not found"),
        (["unknown_command_xyz"], "command not found"),
        (["curl", "http://127.0.0.1:1/invalid"], "connection refused"),
    ]
    results: List[Dict[str, Any]] = []
    for cmd, label in probes:
        res = _run(cmd, timeout=5)
        results.append(
            {
                "label": label,
                "cmd": " ".join(cmd),
                "exit_code": res["exit_code"],
                "stderr": res["stderr"][:500],
                "stdout": res["stdout"][:500],
            }
        )
    return results


def run_discovery(cwd: Optional[Path] = None) -> Path:
    """Run full environment discovery and write discovery.md."""
    cwd = cwd or Path.cwd()

    tools = discover_available_tools()
    fs = discover_filesystem()
    expectations = discover_output_expectations(cwd)
    failures = probe_failure_modes()

    lines: List[str] = []
    lines.append("# Environment Discovery Report")
    lines.append("")
    lines.append(f"Generated: {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()}")
    lines.append(f"Working directory: {cwd}")
    lines.append("")

    lines.append("## Available Tools")
    for t in tools:
        lines.append(f"- {t['tool']}: {t['path']} ({t['version']})")
    lines.append("")

    lines.append("## Filesystem Layout")
    lines.append("### Root")
    lines.append("```")
    lines.append(fs.get("root", {}).get("stdout", "")[:1500])
    lines.append("```")
    lines.append("")
    lines.append("### Home")
    lines.append("```")
    lines.append(fs.get("home", {}).get("stdout", "")[:1500])
    lines.append("```")
    lines.append("")
    lines.append("### CWD")
    lines.append("```")
    lines.append(fs.get("cwd", {}).get("stdout", "")[:1500])
    lines.append("```")
    lines.append("")

    lines.append("## Output Expectations")
    lines.append(f"README/task files: {expectations.get('readme_files', [])}")
    lines.append(f"Schema-like files: {expectations.get('schema_like_files', [])}")
    if expectations.get("task_description"):
        lines.append("### Task Description")
        lines.append("```")
        lines.append(expectations["task_description"][:2000])
        lines.append("```")
    lines.append("")

    lines.append("## Observed Failure Modes")
    for f in failures:
        lines.append(f"### {f['label']}")
        lines.append(f"Command: {f['cmd']}")
        lines.append(f"Exit code: {f['exit_code']}")
        lines.append(f"stderr: {f['stderr'][:300]}")
        lines.append("")

    discovery_md = "\n".join(lines)
    DISCOVERY_PATH.write_text(discovery_md)
    return DISCOVERY_PATH


if __name__ == "__main__":
    path = run_discovery()
    print(f"Discovery written to {path}")
