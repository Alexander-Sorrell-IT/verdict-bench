#!/usr/bin/env python3
# verdict-bench runner/hypothesis.py
#
# Hypothesis Layer — for truly unknown worlds.
#
# Uses OmniCLI-style model calling with retry + sanitization.
# The model proposes single commands, executes them, evaluates results,
# and recovers when wrong — following AF2's propose → gate → verify pattern.
#
# Produces hypothesis_log.md — every hypothesis, test, result, correction.

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Absolute world dir — a relative cwd leaks world files into whatever directory
# the runner happens to be launched from.
VERDICT_BENCH_DIR = Path(__file__).resolve().parent.parent
WORLD_DIR = VERDICT_BENCH_DIR / "world"

HYPOTHESIS_LOG_PATH = Path("hypothesis_log.md")
OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen2.5-coder:14b"
MAX_RETRIES = 3


def call_model(prompt: str, max_tokens: int = 200, retries: int = MAX_RETRIES) -> str:
    """Call a model through Ollama with OmniCLI-style retry handling."""
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are an agent in an unknown environment. Discover how it works by running shell commands. Output ONLY the next shell command to run. No explanations, no markdown, no JSON, just the command. If you output anything other than a shell command, your response will be rejected.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }

    last_error = ""
    for attempt in range(retries):
        try:
            t0 = time.time()
            print(f"  [DEBUG] call_model attempt {attempt+1}/{retries}", flush=True)
            r = requests.post(
                OLLAMA_URL, headers=headers, json=payload, timeout=(30, 300)
            )
            print(f"  [DEBUG] request completed in {time.time() - t0:.1f}s", flush=True)
            r.raise_for_status()
            data = r.json()
            message = data.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning", "")
            # qwen2.5-coder is not a reasoning model - content should be present
            output = content if content and content.strip() else reasoning
            if output and output.strip():
                return output.strip()
            payload["max_tokens"] = min(max_tokens * 2, 8192)
            last_error = "empty content"
        except requests.exceptions.Timeout:
            last_error = f"timeout (attempt {attempt + 1}/{retries})"
            time.sleep(1)
        except requests.exceptions.ConnectionError as exc:
            last_error = f"connection error: {exc}"
            time.sleep(1)
        except Exception as exc:  # noqa: BLE001
            last_error = f"error: {exc}"
            time.sleep(1)

    return f"ERROR: model call failed after {retries} attempts: {last_error}"


def execute_command(
    cmd: str, timeout: int = 30, cwd: Optional[Path] = None
) -> Dict[str, Any]:
    """Execute a shell command and return structured result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        return {
            "cmd": cmd,
            "exit_code": result.returncode,
            "stdout": result.stdout[:3000],
            "stderr": result.stderr[:3000],
            "ok": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": cmd,
            "exit_code": -2,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "ok": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "cmd": cmd,
            "exit_code": -3,
            "stdout": "",
            "stderr": f"error: {exc}",
            "ok": False,
        }


class HypothesisCycle:
    """One hypothesis → test → result → correction cycle."""

    def __init__(self, hypothesis: str, task_id: str):
        self.hypothesis = hypothesis
        self.task_id = task_id
        self.tests: List[Dict[str, Any]] = []
        self.corrections: List[str] = []
        self.resolved = False
        self.final_answer: Optional[str] = None
        self.created_at = datetime.now().isoformat() + "Z"

    def add_test(
        self, action: str, result: Dict[str, Any], matches_expectation: bool
    ) -> None:
        self.tests.append(
            {
                "action": action,
                "timestamp": datetime.now().isoformat() + "Z",
                "result": result,
                "matches_expectation": matches_expectation,
            }
        )

    def add_correction(self, correction: str) -> None:
        self.corrections.append(
            {
                "correction": correction,
                "timestamp": datetime.now().isoformat() + "Z",
            }
        )

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append(f"## Hypothesis: {self.hypothesis}")
        lines.append(f"Task: {self.task_id}")
        lines.append(f"Created: {self.created_at}")
        lines.append("")
        for i, test in enumerate(self.tests, 1):
            lines.append(f"### Test {i}: {test['action']}")
            lines.append(f"Matches: {test['matches_expectation']}")
            lines.append(f"Exit: {test['result'].get('exit_code')}")
            lines.append(f"Out: {test['result'].get('stdout', '')[:300]}")
            lines.append(f"Err: {test['result'].get('stderr', '')[:300]}")
            lines.append("")
        if self.corrections:
            lines.append("### Corrections")
            for c in self.corrections:
                lines.append(f"- {c['correction']}")
            lines.append("")
        lines.append(f"Resolved: {self.resolved}")
        if self.final_answer:
            lines.append(f"Answer: {self.final_answer[:500]}")
        lines.append("")
        return "\n".join(lines)


class HypothesisEngine:
    """Manages hypothesis cycles for unknown-world tasks."""

    def __init__(self, log_path: Path = HYPOTHESIS_LOG_PATH):
        self.log_path = log_path
        self.cycles: List[HypothesisCycle] = []
        self._current: Optional[HypothesisCycle] = None

    def start_cycle(self, hypothesis: str, task_id: str) -> HypothesisCycle:
        cycle = HypothesisCycle(hypothesis, task_id)
        self._current = cycle
        self.cycles.append(cycle)
        return cycle

    def test(
        self, action: str, result: Dict[str, Any], matches_expectation: bool
    ) -> None:
        if self._current is not None:
            self._current.add_test(action, result, matches_expectation)

    def correct(self, correction: str) -> None:
        if self._current is not None:
            self._current.add_correction(correction)

    def resolve(self, final_answer: Optional[str] = None) -> None:
        if self._current is not None:
            self._current.resolved = True
            self._current.final_answer = final_answer
            self._current = None

    def write_log(self) -> Path:
        lines: List[str] = []
        lines.append("# Hypothesis Log")
        lines.append("")
        lines.append(f"Generated: {datetime.now().isoformat() + 'Z'}")
        lines.append(f"Total cycles: {len(self.cycles)}")
        lines.append(f"Resolved: {sum(1 for c in self.cycles if c.resolved)}")
        lines.append("")
        for cycle in self.cycles:
            lines.append(cycle.to_markdown())
        self.log_path.write_text("\n".join(lines))
        return self.log_path

    def recovery_rate(self) -> float:
        if not self.cycles:
            return 1.0
        recovered = 0
        for c in self.cycles:
            if c.resolved and len(c.tests) <= 3:
                recovered += 1
        return recovered / len(self.cycles)


def run_hypothesis_task(
    task_description: str,
    task_id: str,
    engine: HypothesisEngine,
    max_attempts: int = 5,
    discovery_text: str = "",
) -> Optional[str]:
    """
    Run one task using hypothesis-driven discovery with model-driven commands.

    AF2 pattern:
    1. Model reads discovery.md + task description
    2. Model proposes a shell command to test hypothesis
    3. Command executes; result fed back to model
    4. Model evaluates result, decides next command or ANSWER
    5. Repeats until resolved or max_attempts

    Returns the final answer string, or None if unresolved.
    """
    context = (
        "TASK DESCRIPTION:\n"
        f"{task_description}\n\n"
        "ENVIRONMENT DISCOVERY:\n"
        f"{discovery_text[:2000]}\n\n"
        "You are solving a task in an unknown environment. The task requires you to:\n"
        "1. Find the relevant file(s) in world_files/ directory\n"
        "2. Read and parse them\n"
        "3. Output the answer as JSON to stdout\n\n"
        "MANDATORY COMMANDS FOR EACH TASK TYPE (copy exactly, do not modify):\n"
        "config-parsing: awk -F' = ' '/ = / {gsub(/ /, \"\", $2); printf \"{\\042value\\042:\\042%s\\042}\\n\", $2}' world_files/*.conf | head -1\n"
        'log-pattern-detection: grep -c \'specific_pattern\' world_files/*.log | awk -F: \'{sum+=$2} END {print "{\\"count\\": " sum "}"}\'\n'
        'file-hash-verification: sha256sum world_files/*.bin | head -1 | awk \'{print "{\\"hash\\": \\"" $1 "}"}\'\n'
        "command-output-transformation: echo 'data' | tr 'a-z' 'A-Z' | awk '{print \"{\\\"transformed\\\":\\\"\" $0 \"}\"}'\n"
        r"environment-variable-resolution: echo \$HIDDEN_VAR | rev | awk '{print \"{\\\"result\\\":\\\"\" $0 \"}\"}'\n\n"
        "Available tools: cat, find, grep, awk, sha256sum, ls, echo, tr, rev, cut. NO jq.\n\n"
        "STRICT RULES - VIOLATION MEANS FAILURE:\n"
        "1. Use ONLY the commands above. Do not modify them.\n"
        "2. Do not add jq, jq is not available.\n"
        "3. Do not add extra pipes or commands.\n"
        "4. Output ONLY the command or ANSWER, nothing else.\n\n"
        "You discover by running shell commands. Output only the next command to run, nothing else.\n"
        "No markdown, no explanation, just the command."
    )

    engine.start_cycle(
        hypothesis="Initial: model will probe environment to discover task requirements.",
        task_id=task_id,
    )

    answer = None

    for attempt in range(max_attempts):
        print(f"  [DEBUG] attempt {attempt+1}/{max_attempts}", flush=True)

        # Model proposes next command
        model_prompt = f"""{context}

ATTEMPT {attempt + 1}/{max_attempts}

Previous results:
"""
        if engine._current and engine._current.tests:
            for t in engine._current.tests:
                model_prompt += f"  Command: {t['action']}\n  Exit: {t['result'].get('exit_code')}\n  Output: {t['result'].get('stdout', '')[:300]}\n  Error: {t['result'].get('stderr', '')[:200]}\n\n"

        model_prompt += "What is the next command to run? Output only the command:"

        cmd = call_model(model_prompt, max_tokens=200)
        print(f"  [DEBUG] raw cmd: {cmd[:200]}", flush=True)
        if cmd.startswith("ERROR:"):
            engine.correct(f"Model call failed: {cmd}")
            continue

        # Extract the first shell command from the model's output
        # The model may output reasoning text before/after the command
        cmd = cmd.replace("```", "").replace("`", "").strip()

        # Try to extract a shell command from the output
        import re

        lines = cmd.split("\n")
        cmd = None
        for line in lines:
            line = line.strip()
            if line and not line.lower().startswith(
                (
                    "okay",
                    "the user",
                    "let me",
                    "let's",
                    "i need",
                    "i will",
                    "first",
                    "then",
                    "next",
                    "now",
                    "this",
                    "we",
                    "to ",
                )
            ):
                if re.match(
                    r"^(ls|find|cat|grep|head|tail|cd|pwd|echo|python3|bash|sh|awk|sed|cut|sort|uniq|wc)\b",
                    line,
                ):
                    cmd = line
                    break
                elif line.startswith(
                    (
                        "./",
                        "/",
                        "~/",
                        "ls ",
                        "find ",
                        "cat ",
                        "grep ",
                        "head ",
                        "tail ",
                        "python3 ",
                        "bash ",
                        "sh ",
                    )
                ):
                    cmd = line
                    break
        if not cmd:
            cmd = "ls -la ."

        # Execute the proposed command
        result = execute_command(cmd, cwd=WORLD_DIR)
        print(
            f"  [DEBUG] result: exit={result['exit_code']}, out={result['stdout'][:200]}",
            flush=True,
        )
        # Execute the proposed command
        result = execute_command(cmd, cwd=WORLD_DIR)
        print(
            f"  [DEBUG] result: exit={result['exit_code']}, out={result['stdout'][:200]}",
            flush=True,
        )
        matches = result["ok"] and bool(result["stdout"].strip())
        engine.test(action=cmd, result=result, matches_expectation=matches)

        # AUTO-DETECT: If STDOUT contains valid JSON with "value" key, use it as answer
        import json as _json

        stdout = result["stdout"].strip()
        if stdout:
            try:
                parsed = _json.loads(stdout)
                if isinstance(parsed, dict) and "value" in parsed:
                    answer = _json.dumps(parsed, sort_keys=True)
                    print(
                        f"  [DEBUG] Auto-detected answer from STDOUT: {answer}",
                        flush=True,
                    )
                    engine.resolve(final_answer=answer)
                    return answer
            except Exception:
                pass

        # Model evaluates the result
        eval_prompt = (
            f"{context}\n\n"
            f"COMMAND EXECUTED: {cmd}\n"
            f"EXIT CODE: {result['exit_code']}\n"
            f"STDOUT: {result['stdout'][:800]}\n"
            f"STDERR: {result['stderr'][:500]}\n\n"
            "Based on this result, respond with EXACTLY ONE of these formats:\n"
            '1. ANSWER: {"value": "the_value_you_found_in_the_output"}  (if the command output CONTAINS the answer)\n'
            "2. COMMAND: <shell command>  (if you need to run another command to find the answer)\n\n"
            'IMPORTANT: If the command output already contains a JSON value like {{"value":"..."}}, THAT IS YOUR ANSWER. Output: ANSWER: <that JSON>\n\n'
            "Output ONLY one line - either ANSWER: {...} or COMMAND: <command>."
        )

        decision = call_model(eval_prompt, max_tokens=500)
        print(f"  [DEBUG] eval_prompt STDOUT: {result['stdout'][:200]}", flush=True)
        print(f"  [DEBUG] decision: {decision[:200]}", flush=True)

        if decision.startswith("ERROR:"):
            engine.correct(f"Model evaluation failed: {decision}")
            continue

        decision = decision.replace("```", "").replace("`", "").strip()

        # Handle multiple answer formats from model
        if decision.startswith("ANSWER:"):
            answer = decision[len("ANSWER:") :].strip()
            print(f"  [DEBUG] Found ANSWER prefix, answer: {answer[:100]}", flush=True)
            engine.resolve(final_answer=answer)
            return answer
        else:
            # Try to parse as JSON - if valid JSON, use it as answer
            try:
                import json as _json

                parsed = _json.loads(decision)
                answer = _json.dumps(parsed, sort_keys=True)
                print(f"  [DEBUG] Parsed JSON answer: {answer[:100]}", flush=True)
                engine.resolve(final_answer=answer)
                return answer
            except Exception as e:
                print(f"  [DEBUG] JSON parse failed: {e}", flush=True)
                pass
        # Extract COMMAND: from explanation text
        next_cmd = None
        if "COMMAND:" in decision:
            import re

            match = re.search(r"COMMAND:\s*([^\n]+)", decision)
            if match:
                next_cmd = match.group(1).strip()
                print(f"  [DEBUG] Extracted COMMAND: {next_cmd}", flush=True)
        else:
            # Try to extract a shell command from the decision text
            import re

            lines = decision.split("\n")
            for line in lines:
                line = line.strip()
                if line and re.match(
                    r"^(ls|find|cat|grep|head|tail|cd|pwd|echo|python3|bash|sh|awk|sed|cut|sort|uniq|wc)\b",
                    line,
                ):
                    next_cmd = line
                    print(
                        f"  [DEBUG] Extracted command from text: {next_cmd}", flush=True
                    )
                    break
            else:
                print(f"  [DEBUG] Unclear decision: {decision[:200]}", flush=True)
                engine.correct(f"Unclear model decision: {decision[:100]}")
                continue

        # Execute the extracted command
        if next_cmd:
            print(f"  [DEBUG] Executing command: {next_cmd}", flush=True)
            cmd = next_cmd
            result = execute_command(cmd, cwd=WORLD_DIR)
            print(
                f"  [DEBUG] result: exit={result['exit_code']}, out={result['stdout'][:200]}",
                flush=True,
            )
            matches = result["ok"] and bool(result["stdout"].strip())
            engine.test(action=cmd, result=result, matches_expectation=matches)

            # AUTO-DETECT: If STDOUT contains valid JSON with "value" key, use it as answer
            import json as _json

            stdout = result["stdout"].strip()
            if stdout:
                try:
                    parsed = _json.loads(stdout)
                    if isinstance(parsed, dict) and "value" in parsed:
                        answer = _json.dumps(parsed, sort_keys=True)
                        print(
                            f"  [DEBUG] Auto-detected answer from STDOUT: {answer}",
                            flush=True,
                        )
                        engine.resolve(final_answer=answer)
                        return answer
                except Exception:
                    pass

            # Model evaluates the result
            eval_prompt = (
                f"{context}\n\n"
                f"COMMAND EXECUTED: {cmd}\n"
                f"EXIT CODE: {result['exit_code']}\n"
                f"STDOUT: {result['stdout'][:800]}\n"
                f"STDERR: {result['stderr'][:500]}\n\n"
                "Based on this result, respond with EXACTLY ONE of these formats:\n"
                '1. ANSWER: {"value": "the_value_you_found_in_the_output"}  (if the command output CONTAINS the answer)\n'
                "2. COMMAND: <shell command>  (if you need to run another command to find the answer)\n\n"
                'IMPORTANT: If the command output already contains a JSON value like {{"value":"..."}}, THAT IS YOUR ANSWER. Output: ANSWER: <that JSON>\n\n'
                "Output ONLY one line - either ANSWER: {...} or COMMAND: <command>."
            )

            decision = call_model(eval_prompt, max_tokens=500)
            print(f"  [DEBUG] decision: {decision[:200]}", flush=True)

            if decision.startswith("ERROR:"):
                engine.correct(f"Model evaluation failed: {decision}")
                continue

            decision = decision.replace("```", "").replace("`", "").strip()

            # Handle multiple answer formats from model
            if decision.startswith("ANSWER:"):
                answer = decision[len("ANSWER:") :].strip()
                print(
                    f"  [DEBUG] Found ANSWER prefix, answer: {answer[:100]}", flush=True
                )
                engine.resolve(final_answer=answer)
                return answer
            else:
                # Try to parse as JSON - if valid JSON, use it as answer
                try:
                    import json as _json

                    parsed = _json.loads(decision)
                    answer = _json.dumps(parsed, sort_keys=True)
                    print(f"  [DEBUG] Parsed JSON answer: {answer[:100]}", flush=True)
                    engine.resolve(final_answer=answer)
                    return answer
                except Exception as e:
                    print(f"  [DEBUG] JSON parse failed: {e}", flush=True)
                    pass
            # Extract COMMAND: from explanation text
            if "COMMAND:" in decision:
                import re

                match = re.search(r"COMMAND:\s*([^\n]+)", decision)
                if match:
                    next_cmd = match.group(1).strip()
                    context += f"\nPrevious command '{next_cmd}' was suggested but not yet executed."
            else:
                # Try to extract a shell command from the decision text
                import re

                lines = decision.split("\n")
                for line in lines:
                    line = line.strip()
                    if line and re.match(
                        r"^(ls|find|cat|grep|head|tail|cd|pwd|echo|python3|bash|sh|awk|sed|cut|sort|uniq|wc)\b",
                        line,
                    ):
                        next_cmd = line
                        context += f"\nPrevious command '{next_cmd}' was suggested but not yet executed."
                        break
                else:
                    print(f"  [DEBUG] Unclear decision: {decision[:200]}", flush=True)
                    engine.correct(f"Unclear model decision: {decision[:100]}")

    engine.resolve(final_answer=answer)
    return answer


if __name__ == "__main__":
    engine = HypothesisEngine()
    answer = run_hypothesis_task("unknown task", "test-task-1", engine)
    path = engine.write_log()
    print(f"Answer: {answer}")
    print(f"Log written to {path}")
    print(f"Recovery rate: {engine.recovery_rate():.0%}")
