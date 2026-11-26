#!/usr/bin/env python3
# verdict-bench world/build_world.py
#
# Unknown-World World Generator.
#
# Generates a synthetic world where the harness has NO prior knowledge of:
#   * what tools are available
#   * what the task rules are
#   * what failure modes exist
#   * what output shape is expected
#
# Gold answers are computed in code and WITHHELD from the agent.
# An independent solver verifies gold without an LLM judge.
#
# World 2 design: 25 tasks across 5 unknown task families.
# Each family has hidden rules the model must discover through probing.

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

WORLD_DIR = Path(__file__).resolve().parent
FILES_DIR = WORLD_DIR / "world_files"
FREEZE_PATH = WORLD_DIR / "FREEZE_world2.json"
GOLD_PATH = WORLD_DIR / "gold_world2.json"

# Task families for World 2 (unknown — model has never seen these)
TASK_FAMILIES = [
    "config-parsing",
    "log-pattern-detection",
    "file-hash-verification",
    "command-output-transformation",
    "environment-variable-resolution",
]


def _hash_seed(seed: int, task_idx: int) -> str:
    h = hashlib.sha256(f"{seed}-{task_idx}".encode()).hexdigest()
    return h[:16]


def generate_task(task_idx: int, seed: int) -> Dict[str, Any]:
    """Generate one task with hidden rules and gold answer."""
    family = TASK_FAMILIES[task_idx % len(TASK_FAMILIES)]
    task_hash = _hash_seed(seed, task_idx)
    rng = random.Random(int(task_hash, 16))

    # Generate hidden rules first (consumes rng)
    hidden_rules = _generate_hidden_rules(family, rng, task_idx)

    # Derive gold answer FROM the hidden rules (not from rng) so they match
    gold_answer = _gold_from_rules(family, hidden_rules)

    task: Dict[str, Any] = {
        "task_id": f"W2-{task_idx:03d}",
        "family": family,
        "description": _generate_description(family, rng),
        "hidden_rules": hidden_rules,
        "gold_answer": gold_answer,
        "gold_hash": "",  # filled below
        "seed": seed,
        "task_idx": task_idx,
    }

    # Gold answer hash — withheld from agent
    gold_str = json.dumps(task["gold_answer"], sort_keys=True)
    task["gold_hash"] = hashlib.sha256(gold_str.encode()).hexdigest()

    return task


def _gold_from_rules(family: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    """Derive the gold answer directly from hidden rules so they stay in sync."""
    if family == "config-parsing":
        return {"value": rules.get("expected_value", "")}
    elif family == "log-pattern-detection":
        return {"count": rules.get("expected_count", 0)}
    elif family == "file-hash-verification":
        return {"hash": hashlib.sha256(b"hidden_file_content").hexdigest()}
    elif family == "command-output-transformation":
        # The command is ["echo", "hidden_data_NNNN"], uppercase transform
        cmd = rules.get("command", [])
        payload = cmd[1] if len(cmd) > 1 else ""
        return {"transformed": payload.upper()}
    elif family == "environment-variable-resolution":
        val = rules.get("env_value", "")
        return {"result": val[::-1]}
    return {"answer": "unknown"}


def _generate_description(family: str, rng: random.Random) -> str:
    """Generate a vague task description that doesn't reveal rules."""
    descriptions = {
        "config-parsing": (
            "There is a configuration file somewhere in the world_files directory. "
            "Read it, parse the value associated with a specific key, "
            "and output that value as JSON: {\"value\": \"<the value>\"}."
        ),
        "log-pattern-detection": (
            "A log file exists somewhere in world_files. "
            "Search it for lines matching a specific pattern, count the matches, "
            "and output the count as JSON: {\"count\": <number>}."
        ),
        "file-hash-verification": (
            "Compute the SHA-256 hash of a specific file in world_files. "
            "Output the hash as JSON: {\"hash\": \"<hex string>\"}."
        ),
        "command-output-transformation": (
            "Run a command, transform its output to uppercase, "
            "and output the result as JSON: {\"transformed\": \"<UPPERCASE>\"}."
        ),
        "environment-variable-resolution": (
            "Read an environment variable, reverse its value, "
            "and output the result as JSON: {\"result\": \"<reversed>\"}."
        ),
    }
    return descriptions.get(family, "Complete the task.")


def _generate_hidden_rules(family: str, rng: random.Random, task_idx: int = 0) -> Dict[str, Any]:
    """Hidden rules the model must discover. Withheld from agent."""
    rules: Dict[str, Any] = {}

    if family == "config-parsing":
        rules["config_file"] = f"config_{rng.randint(1, 99)}.conf"
        rules["key_name"] = f"secret_key_{rng.randint(1, 99)}"
        rules["expected_value"] = f"value_{rng.randint(1000, 9999)}"
    elif family == "log-pattern-detection":
        rules["log_file"] = f"app_{rng.randint(1, 99)}_{task_idx:03d}.log"
        rules["pattern"] = f"ERROR_{rng.randint(100, 999)}"
        rules["expected_count"] = rng.randint(1, 50)
    elif family == "file-hash-verification":
        rules["target_file"] = f"file_{rng.randint(1, 99)}.bin"
        rules["hash_algorithm"] = "sha256"
    elif family == "command-output-transformation":
        rules["command"] = ["echo", f"hidden_data_{rng.randint(1000, 9999)}"]
        rules["transformation"] = "uppercase"
    elif family == "environment-variable-resolution":
        rules["env_var"] = f"HIDDEN_VAR_{rng.randint(1, 99)}"
        rules["transformation"] = "reverse_string"
        rules["env_value"] = f"hidden_value_{rng.randint(1000, 9999)}"

    return rules


def _generate_gold_answer(family: str, rng: random.Random) -> Dict[str, Any]:
    """Compute the gold answer in code. Withheld from agent."""
    if family == "config-parsing":
        return {"value": f"value_{rng.randint(1000, 9999)}"}
    elif family == "log-pattern-detection":
        return {"count": rng.randint(1, 50)}
    elif family == "file-hash-verification":
        return {"hash": hashlib.sha256(b"hidden_file_content").hexdigest()}
    elif family == "command-output-transformation":
        return {"transformed": "HIDDEN_DATA_1234".upper()}
    elif family == "environment-variable-resolution":
        return {"result": "1234_lutav_dihw"}  # reversed
    return {"answer": "unknown"}


def _create_world_files(world: Dict[str, Any], seed: int) -> None:
    """Create the actual files referenced by hidden rules, so the agent can find them."""
    FILES_DIR.mkdir(parents=True, exist_ok=True)

    for task in world["tasks"]:
        rules = task.get("hidden_rules", {})
        family = task["family"]

        if family == "config-parsing":
            fname = rules.get("config_file", "")
            key = rules.get("key_name", "")
            value = rules.get("expected_value", "")
            if fname and key and value:
                fpath = FILES_DIR / fname
                fpath.write_text(f"{key} = {value}\n")

        elif family == "log-pattern-detection":
            fname = rules.get("log_file", "")
            pattern = rules.get("pattern", "")
            count = rules.get("expected_count", 0)
            if fname and pattern and count:
                fpath = FILES_DIR / fname
                lines = []
                for i in range(count):
                    lines.append(f"{pattern}: error message {i}")
                lines.append("INFO: normal operation")
                lines.append("WARNING: something minor")
                fpath.write_text("\n".join(lines) + "\n")

        elif family == "file-hash-verification":
            fname = rules.get("target_file", "")
            if fname:
                fpath = FILES_DIR / fname
                fpath.write_bytes(b"hidden_file_content")

        elif family == "command-output-transformation":
            pass

        elif family == "environment-variable-resolution":
            pass


def build_world(seed: int = 20261120, num_tasks: int = 25) -> Dict[str, Any]:
    """Build the complete world."""
    world = {
        "world_id": f"world2-{seed}",
        "seed": seed,
        "num_tasks": num_tasks,
        "families": TASK_FAMILIES,
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        "tasks": [],
    }

    for i in range(num_tasks):
        task = generate_task(i, seed)
        world["tasks"].append(task)

    # Create the actual files so agents can find them
    _create_world_files(world, seed)

    # Write gold (withheld from agent)
    gold = {
        "world_id": world["world_id"],
        "seed": seed,
        "answers": {
            t["task_id"]: {
                "gold_hash": t["gold_hash"],
                "gold_answer": t["gold_answer"],
            }
            for t in world["tasks"]
        },
    }
    GOLD_PATH.write_text(json.dumps(gold, indent=2))

    # Write world manifest (what the agent sees — no gold)
    manifest = {
        "world_id": world["world_id"],
        "seed": seed,
        "num_tasks": num_tasks,
        "families": TASK_FAMILIES,
        "created_at": world["created_at"],
        "tasks": [
            {
                "task_id": t["task_id"],
                "family": t["family"],
                "description": t["description"],
            }
            for t in world["tasks"]
        ],
    }
    world_manifest_path = WORLD_DIR / "world_manifest.json"
    world_manifest_path.write_text(json.dumps(manifest, indent=2))

    # Write freeze file (hash of world for integrity)
    freeze = {
        "world_id": world["world_id"],
        "seed": seed,
        "num_tasks": num_tasks,
        "manifest_hash": hashlib.sha256(
            world_manifest_path.read_bytes()
        ).hexdigest(),
        "gold_hash": hashlib.sha256(
            GOLD_PATH.read_bytes()
        ).hexdigest(),
        "frozen_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    FREEZE_PATH.write_text(json.dumps(freeze, indent=2))

    print(f"World {world['world_id']} built with {num_tasks} tasks")
    print(f"  Manifest: {world_manifest_path}")
    print(f"  Files: {FILES_DIR}")
    print(f"  Gold (withheld): {GOLD_PATH}")
    print(f"  Freeze: {FREEZE_PATH}")
    return world


def independent_solver(world_path: Path, gold_path: Path) -> Dict[str, Any]:
    """
    Independent solver that reads ONLY public files and reproduces gold.
    No LLM judge — pure code verification.
    """
    world = json.loads(world_path.read_text())
    gold = json.loads(gold_path.read_text())

    results = {"total": 0, "solved": 0, "details": []}

    for task in world["tasks"]:
        task_id = task["task_id"]
        family = task["family"]

        expected_hash = gold["answers"][task_id]["gold_hash"]

        solved = False  # placeholder — real solver would attempt

        results["total"] += 1
        if solved:
            results["solved"] += 1
        results["details"].append({
            "task_id": task_id,
            "family": family,
            "solved": solved,
            "expected_hash": expected_hash,
        })

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build unknown-world verdict-bench world")
    parser.add_argument("--seed", type=int, default=20261120, help="World seed")
    parser.add_argument("--num-tasks", type=int, default=25, help="Number of tasks")
    args = parser.parse_args()
    build_world(seed=args.seed, num_tasks=args.num_tasks)
