# VENDORED — not original verdict-bench code.
# Source: matrixbuilderops/groundwork -> filelens/src/summarize.py
# Absorbed under ABSORPTION_PLAN.md row #5 (mechanism: ABSORB).
# Required Notice: Copyright SignalCore Inc. and Bastion Fold LLC
# Required Notice: Commercial licensing: matrixbuilderops@proton.me
# License: PolyForm Noncommercial 1.0.0 — https://polyformproject.org/licenses/noncommercial/1.0.0
# Noncommercial use is free. Any commercial use requires a separate paid license.
# This notice must travel with every copy: do not strip it.
# Deviation from original: reformatted with black to pass verdict-bench's gate.
# Permitted by the license's Changes and New Works License. Logic is unchanged.

"""
Summarizer — generates a plain-English description of what a file does.
Uses the file outline + first/last content chunks to produce a summary
without needing to read the entire file.

Designed so an AI agent can understand a file's purpose in one call
before deciding whether to dig deeper.
"""
from __future__ import annotations
from pathlib import Path
from .outline import build_outline


def summarize_file(path: str) -> str:
    """
    Return a plain-English summary of what a file does.

    Strategy:
    1. Build the outline (structure — free, no LLM needed)
    2. Read the first 40 lines (imports, top-level docstring, module intent)
    3. Read the last 20 lines (often contains main() or entry point)
    4. Compose a summary from outline + head + tail

    This is intentionally LLM-free so it works offline and fast.
    For a richer LLM-powered summary, pass the output of this function
    to your AI model as context.

    Args:
        path: Path to the file to summarize.

    Returns:
        A structured plain-text summary the AI can read in one shot.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    outline = build_outline(path)

    head = "\n".join(lines[:40])
    tail = "\n".join(lines[-20:]) if len(lines) > 40 else ""

    parts = [
        f"FILE: {p.name}",
        f"LANGUAGE: {outline.language}",
        f"SIZE: {outline.total_lines} lines",
        "",
        "STRUCTURE:",
        outline.render(),
        "",
        "HEAD (first 40 lines):",
        head,
    ]

    if tail:
        parts += ["", "TAIL (last 20 lines):", tail]

    return "\n".join(parts)
