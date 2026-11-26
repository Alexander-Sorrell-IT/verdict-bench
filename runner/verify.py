#!/usr/bin/env python3
# verdict-bench runner/verify.py
#
# Self-Verification Layer — for truly unknown worlds.
#
# AF2's completion gate checks that a deliverable exists and parses.
# Unknown worlds need more:
#   * Re-run the deliverable in isolation (does it actually work?)
#   * Compare output against any available schema/example
#   * Verify without re-reading the task description (independence)
#   * Run twice for reproducibility
#
# Produces verification_report.md — pass/fail with evidence.

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

VERIFICATION_REPORT_PATH = Path("verification_report.md")


def _run(cmd: List[str], timeout: int = 30) -> Dict[str, Any]:
    """Run a command safely, capture everything."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "cmd": " ".join(cmd),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "ok": result.returncode == 0,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "cmd": " ".join(cmd),
            "exit_code": -1,
            "stdout": "",
            "stderr": str(exc),
            "ok": False,
        }


def file_hash(path: Path) -> Optional[str]:
    """SHA-256 of a file, for reproducibility checking."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def verify_deliverable(
    deliverable_path: Path,
    schema_hints: Optional[Dict[str, Any]] = None,
    cwd: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Verify a deliverable independently.

    Checks:
    1. File exists and is non-empty
    2. Can be parsed (JSON, text, or shell-executable)
    3. Running it produces consistent output (twice)
    4. Output matches schema hints if provided
    5. No dependence on re-reading the task description
    """
    cwd = cwd or Path.cwd()
    report: Dict[str, Any] = {
        "deliverable": str(deliverable_path),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "checks": [],
        "overall": "unknown",
    }

    # Check 1: File exists and non-empty
    if not deliverable_path.exists():
        report["checks"].append(
            {
                "check": "file_exists",
                "passed": False,
                "detail": f"{deliverable_path} does not exist",
            }
        )
        report["overall"] = "fail"
        return report

    size = deliverable_path.stat().st_size
    if size == 0:
        report["checks"].append(
            {
                "check": "file_non_empty",
                "passed": False,
                "detail": "file is empty (0 bytes)",
            }
        )
        report["overall"] = "fail"
        return report

    report["checks"].append(
        {
            "check": "file_exists",
            "passed": True,
            "detail": f"{deliverable_path} exists ({size} bytes)",
        }
    )
    report["checks"].append(
        {
            "check": "file_non_empty",
            "passed": True,
            "detail": f"size = {size} bytes",
        }
    )

    # Check 2: Parse the deliverable
    content = deliverable_path.read_text(errors="replace")
    parsed = None
    parse_error = None

    # Try JSON
    try:
        parsed = json.loads(content)
        report["checks"].append(
            {
                "check": "parse_json",
                "passed": True,
                "detail": "valid JSON",
            }
        )
    except json.JSONDecodeError:
        # Try shell script
        if content.strip().startswith("#!") or "bash" in content[:100].lower():
            report["checks"].append(
                {
                    "check": "parse_shell",
                    "passed": True,
                    "detail": "executable shell script",
                }
            )
            parsed = content
        else:
            report["checks"].append(
                {
                    "check": "parse_text",
                    "passed": True,
                    "detail": "plain text output",
                }
            )
            parsed = content

    # Check 3: Schema validation (if hints provided)
    if schema_hints and isinstance(parsed, dict):
        missing_keys = [
            k for k in schema_hints.get("required_keys", []) if k not in parsed
        ]
        if missing_keys:
            report["checks"].append(
                {
                    "check": "schema_validation",
                    "passed": False,
                    "detail": f"missing required keys: {missing_keys}",
                }
            )
            report["overall"] = "fail"
        else:
            report["checks"].append(
                {
                    "check": "schema_validation",
                    "passed": True,
                    "detail": "all required keys present",
                }
            )

    # Check 4: Reproducibility — run twice, same output
    if deliverable_path.suffix == ".sh":
        run1 = _run(["bash", str(deliverable_path)], timeout=30)
        run2 = _run(["bash", str(deliverable_path)], timeout=30)
        if run1["ok"] and run2["ok"]:
            same = run1["stdout"] == run2["stdout"]
            report["checks"].append(
                {
                    "check": "reproducibility",
                    "passed": same,
                    "detail": f"run1 hash={hashlib.sha256(run1['stdout'].encode()).hexdigest()[:12]}, "
                    f"run2 hash={hashlib.sha256(run2['stdout'].encode()).hexdigest()[:12]}, "
                    f"same={same}",
                }
            )
            if not same:
                report["overall"] = "fail"
        else:
            report["checks"].append(
                {
                    "check": "reproducibility",
                    "passed": False,
                    "detail": f"run1 ok={run1['ok']}, run2 ok={run2['ok']}",
                }
            )
            report["overall"] = "fail"

    # Check 5: Independence — deliverable doesn't reference task description
    task_refs = [
        "TASK.md",
        "task.md",
        "task.txt",
        "README.md",
        "task_description",
        "the task",
        "the question",
    ]
    for ref in task_refs:
        if ref in content.lower():
            report["checks"].append(
                {
                    "check": "independence",
                    "passed": False,
                    "detail": f"deliverable references task file: {ref}",
                }
            )
            report["overall"] = "fail"
            break
    else:
        report["checks"].append(
            {
                "check": "independence",
                "passed": True,
                "detail": "no references to task description files",
            }
        )

    # Determine overall
    if report["overall"] != "fail":
        all_passed = all(c["passed"] for c in report["checks"])
        report["overall"] = "pass" if all_passed else "fail"

    return report


def write_verification_report(report: Dict[str, Any]) -> Path:
    lines: List[str] = []
    lines.append("# Verification Report")
    lines.append("")
    lines.append(f"Deliverable: {report['deliverable']}")
    lines.append(f"Timestamp: {report['timestamp']}")
    lines.append(f"Overall: {report['overall'].upper()}")
    lines.append("")
    lines.append("## Checks")
    for check in report["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"- [{'x' if check['passed'] else ' '}] {check['check']}: {status}"
        )
        lines.append(f"  Detail: {check['detail']}")
    lines.append("")

    VERIFICATION_REPORT_PATH.write_text("\n".join(lines))
    return VERIFICATION_REPORT_PATH


if __name__ == "__main__":
    import sys

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output.json")
    report = verify_deliverable(path)
    rpt_path = write_verification_report(report)
    print(f"Overall: {report['overall']}")
    print(f"Report written to {rpt_path}")
