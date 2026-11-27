"""Comprehensive unit tests for the v2 harness, grader, discovery, and APEX report upgrades."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

from harness.budget import Budget  # noqa: E402
from harness.gates import (  # noqa: E402
    AntiHallucinationGate,
    EvidenceGroundingGate,
    GateContext,
    GateEngine,
    SandboxGate,
    default_gates,
)
from harness.minimal_arm import (  # noqa: E402
    ARM_MAX_ATTEMPTS,
    _extract_answer,
    _extract_command,
    run,
    run_gated,
    run_task,
)
from harness.model import DEFAULT_MAX_TOKENS, ModelClient, ModelError, _extract_content  # noqa: E402
from harness2_adapter import h2_world  # noqa: E402
import discovery  # noqa: E402
import operator_mode  # noqa: E402
import report  # noqa: E402
import stats  # noqa: E402
from score.independent_grader import EMPTY_RATE_CEILING, grade_run, render as render_grade  # noqa: E402
from world.paths import GOLD_PATH  # noqa: E402

SPLIT_PATH = REPO_ROOT / "spec" / "split.json"


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ScriptedClient:
    def __init__(self, replies: List[Any], start_calls: int = 0) -> None:
        self.replies = list(replies)
        self.calls: List[List[Dict[str, str]]] = []
        self._base_calls = start_calls
        self.model = "stub/model"
        self.base_url = "http://127.0.0.1:8787/v1"

    @property
    def call_count(self) -> int:
        return self._base_calls + len(self.calls)

    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 2048) -> str:
        self.calls.append(list(messages))
        if not self.replies:
            raise ModelError("exhausted", error_kind="general")
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return str(nxt)


# ------------------------------------------------------------ 1. harness/model.py


def test_model_default_max_tokens_and_extract_precedence():
    assert DEFAULT_MAX_TOKENS >= 2048

    # content wins over reasoning_content, reasoning, tool_calls
    resp = _FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": "  cat a.txt  ",
                        "reasoning_content": "thinking",
                        "reasoning": "old",
                        "tool_calls": [
                            {"function": {"name": "shell", "arguments": '{"command": "ls"}'}}
                        ],
                    }
                }
            ]
        }
    )
    assert _extract_content(resp) == "cat a.txt"

    # reasoning_content falls back when content is null (NVIDIA NIM / Nemotron shape)
    resp_rc = _FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning_content": 'ANSWER: {"value": "ok"}',
                        "reasoning": "fallback",
                    }
                }
            ]
        }
    )
    assert _extract_content(resp_rc) == 'ANSWER: {"value": "ok"}'

    # reasoning falls back when content and reasoning_content are blank
    resp_r = _FakeResponse(
        {"choices": [{"message": {"content": "", "reasoning_content": "", "reasoning": "ls -la"}}]}
    )
    assert _extract_content(resp_r) == "ls -la"

    # tool_calls falls back when text fields are all blank
    resp_tc = _FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "world_files/W2-000.conf"}',
                                }
                            }
                        ],
                    }
                }
            ]
        }
    )
    extracted = json.loads(_extract_content(resp_tc))
    assert extracted == {"tool": "read_file", "arguments": {"path": "world_files/W2-000.conf"}}

    # all blank raises ModelError with error_kind="empty_completion"
    resp_empty = _FakeResponse({"choices": [{"message": {"content": None}}]})
    with pytest.raises(ModelError) as exc:
        _extract_content(resp_empty)
    assert exc.value.error_kind == "empty_completion"


def test_model_client_records_error_calls_and_sets_error_kind(monkeypatch):
    import requests

    client = ModelClient(
        base_url="http://127.0.0.1:9999/v1", api_key="k", model="m", retries=1
    )

    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: _FakeResponse({"error": "bad"}, status_code=400),
    )
    with pytest.raises(ModelError) as exc_http:
        client.complete([{"role": "user", "content": "hi"}])
    assert exc_http.value.error_kind == "http"
    assert client.call_count == 1
    assert client.calls[-1].status == "http_400"

    def _boom(*a: Any, **kw: Any) -> Any:
        raise ConnectionError("relay down")

    monkeypatch.setattr(requests, "post", _boom)
    with pytest.raises(ModelError) as exc_net:
        client.complete([{"role": "user", "content": "hi"}])
    assert exc_net.value.error_kind == "transport"
    assert client.call_count == 2
    assert client.calls[-1].status == "transport_error"


# ------------------------------------------------------------ 2. runner/stats.py


def test_transport_dead_empty_attempts_are_voided_and_explained(tmp_path):
    rows_path = tmp_path / "rows.jsonl"
    records = [
        {
            "arm": "A",
            "task_id": "W2-000",
            "task_pass": True,
            "criteria_passed": 1,
            "criteria_total": 1,
            "answer": '{"value": "ok"}',
            "answer_empty": False,
            "transport_dead": False,
        },
        {
            "arm": "A",
            "task_id": "W2-001",
            "task_pass": False,
            "criteria_passed": 0,
            "criteria_total": 1,
            "answer": "",
            "answer_empty": True,
            "transport_dead": True,
            "stopped_reason": "transport_error: transport",
            "wall_s": 1.2,
        },
        {
            "arm": "A",
            "task_id": "W2-002",
            "task_pass": False,
            "criteria_passed": 0,
            "criteria_total": 1,
            "answer": "",
            "answer_empty": True,
            "stopped_reason": "transport_error: http",
            "wall_s": 0.4,
        },
        # Non-empty answer must NEVER be voided as transport-dead empty.
        {
            "arm": "A",
            "task_id": "W2-003",
            "task_pass": False,
            "criteria_passed": 0,
            "criteria_total": 1,
            "answer": '{"transformed": "WRONG"}',
            "answer_empty": False,
            "transport_dead": True,
        },
    ]
    rows_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    attempts = stats.load_attempts(rows_path)
    voids = stats.load_voids(rows_path)
    assert [a.task_id for a in attempts] == ["W2-000", "W2-003"]
    assert [v["task_id"] for v in voids] == ["W2-001", "W2-002"]
    assert all("transport" in v["reason"] for v in voids)


# ------------------------------------------------------------ 3. minimal_arm.py


def test_tool_call_translation_and_strict_answer_extraction():
    # Direct command wrapper
    assert _extract_command('{"command": "cat world_files/W2-003.sh"}') == (
        "cat world_files/W2-003.sh"
    )
    # Named read tool
    assert _extract_command('{"tool": "read", "path": "world_files/W2-003.sh"}') == (
        "cat world_files/W2-003.sh"
    )
    # OpenAI function wrapper
    assert _extract_command(
        '{"function": {"name": "bash", "arguments": "{\\"cmd\\": \\"sh world_files/W2-003.sh\\"}"}}'
    ) == "sh world_files/W2-003.sh"
    # Non-whitelisted command inside tool call still rejected
    assert _extract_command('{"command": "rm -rf /"}') is None
    # Tool-call JSON must NEVER be parsed as an ANSWER
    assert _extract_answer('{"command": "cat world_files/W2-003.sh"}') is None
    assert _extract_answer('{"tool": "read", "path": "a.txt"}') is None
    # Prose starting with an ambiguous command word is rejected
    assert _extract_command("find the value inside the file please") is None
    assert _extract_command("find . -name '*.conf'") == "find . -name '*.conf'"

    # Strict ANSWER extraction handles fences and trailing text via raw_decode
    assert (
        _extract_answer('Here you go.\nANSWER: ```json\n{"transformed": "ABC_123"}\n``` done')
        == '{"transformed": "ABC_123"}'
    )
    assert _extract_answer('```json\n{"value": "42"}\n```') == '{"value": "42"}'
    assert _extract_answer("ANSWER: not valid json") is None


def test_minimal_arm_deliverable_cleanup_delta_calls_and_transport_dead(tmp_path):
    (tmp_path / "data.conf").write_text("k = value_99\n")
    stale = tmp_path / "deliverable.json"
    stale.write_text('{"stale": true}')

    # Pre-loaded shared client already has 10 prior calls.
    client = _ScriptedClient(
        [ModelError("relay unreachable", error_kind="transport")],
        start_calls=10,
    )
    budget = Budget(max_steps=20, max_model_calls=20, max_wall_seconds=60.0)
    attempt = run_task(
        {"task_id": "W2-000", "description": 'Output JSON: {"value": "<v>"}'},
        client,
        tmp_path,
        engine=None,
        arm="MINIMAL",
        budget=budget,
    )
    assert not stale.exists(), "stale deliverable must be cleaned before each task"
    assert attempt.transport_dead is True
    assert attempt.stopped_reason == "transport_error: transport"
    assert attempt.model_calls == 1, "model_calls must record delta, not cumulative client total"


def test_gated_arm_bounces_ungrounded_answer_then_accepts_verified(tmp_path):
    (tmp_path / "env.txt").write_text("RAW=hidden_val\n")
    # 1st reply: ungrounded guess (never ran a command) -> EvidenceGroundingGate bounces
    # 2nd reply: runs python3 to compute reversed string -> stdout contains "lav_neddih"
    # 3rd reply: grounded ANSWER -> accepted!
    client = _ScriptedClient(
        [
            'ANSWER: {"result": "lav_dennih"}',
            "python3 -c \"print('hidden_val'[::-1])\"",
            'ANSWER: {"result": "lav_neddih"}',
        ]
    )
    budget = Budget(max_steps=20, max_model_calls=20, max_wall_seconds=60.0)
    attempt = run_gated(
        {"task_id": "W2-004", "description": 'Output JSON: {"result": "<rev>"}'},
        budget,
        str(tmp_path),
        client=client,
    )
    assert attempt.stopped_reason == "answered"
    assert attempt.answer == '{"result": "lav_neddih"}'
    assert any("pre_answer/evidence_grounding" in b for b in attempt.gate_blocks)
    assert ARM_MAX_ATTEMPTS == 8
    assert callable(run)


# ------------------------------------------------------------ 4-6. harness/gates.py


def test_evidence_grounding_gate_rules(tmp_path):
    gate = EvidenceGroundingGate()
    # Missing required key
    v1 = gate.check(
        GateContext(
            stage="pre_answer",
            sandbox=tmp_path,
            required_keys=("value",),
            candidate_answer='{"wrong": "abc"}',
            observed_stdout="abc",
        )
    )
    assert not v1.allowed and "missing required keys" in v1.reason

    # Empty string value
    v2 = gate.check(
        GateContext(
            stage="pre_answer",
            sandbox=tmp_path,
            required_keys=("value",),
            candidate_answer='{"value": "  "}',
            observed_stdout="abc",
        )
    )
    assert not v2.allowed and "empty string" in v2.reason

    # Ungrounded value before verification
    v3 = gate.check(
        GateContext(
            stage="pre_answer",
            sandbox=tmp_path,
            required_keys=("result",),
            candidate_answer='{"result": "3178_elav_dennih"}',
            observed_stdout="RAW=hidden_vale_8713",
            verification_done=False,
        )
    )
    assert not v3.allowed and "not directly observed" in v3.reason

    # Allowed when observed in stdout
    v4 = gate.check(
        GateContext(
            stage="pre_answer",
            sandbox=tmp_path,
            required_keys=("result",),
            candidate_answer='{"result": "3178_eulav_neddih"}',
            observed_stdout="3178_eulav_neddih\n",
            verification_done=False,
        )
    )
    assert v4.allowed is True

    # Allowed after single verification bounce
    v5 = gate.check(
        GateContext(
            stage="pre_answer",
            sandbox=tmp_path,
            required_keys=("result",),
            candidate_answer='{"result": "3178_eulav_neddih"}',
            observed_stdout="raw",
            verification_done=True,
        )
    )
    assert v5.allowed is True


def test_sandbox_gate_regex_devnull_and_embedded_escapes(tmp_path):
    gate = SandboxGate()
    # Legitimate awk/sed regexes and /dev/null stay allowed
    assert gate.check(
        GateContext(stage="pre_tool", sandbox=tmp_path, command="awk '/ERROR/ {print $2}' a.log")
    ).allowed
    assert gate.check(
        GateContext(stage="pre_tool", sandbox=tmp_path, command="sed '/^#/d' a.conf")
    ).allowed
    assert gate.check(
        GateContext(stage="pre_tool", sandbox=tmp_path, command="grep foo a.log 2>/dev/null")
    ).allowed

    # Escapes stay blocked
    assert not gate.check(
        GateContext(stage="pre_tool", sandbox=tmp_path, command="cat ~/secret.txt")
    ).allowed
    assert not gate.check(
        GateContext(stage="pre_tool", sandbox=tmp_path, command="cat ../outside.txt")
    ).allowed
    assert not gate.check(
        GateContext(
            stage="pre_tool",
            sandbox=tmp_path,
            command="python3 -c \"print(open('/etc/passwd').read())\"",
        )
    ).allowed


def test_gate_engine_propagates_repairs_to_caller_and_later_gates(tmp_path):
    engine = GateEngine(gates=[SandboxGate(), AntiHallucinationGate()])
    ctx = GateContext(stage="pre_tool", sandbox=tmp_path, command="git reset --hard")
    verdict = engine.run(ctx)
    assert verdict.allowed is True
    assert verdict.repair == "git reset"
    assert ctx.command == "git reset"
    assert len(engine.repairs) == 1
    assert any(isinstance(g, EvidenceGroundingGate) for g in default_gates())


# ------------------------------------------------------------ 7. operator_mode.py


def test_operator_mode_sandbox_layout_task_no_double_wrap_and_base_url(tmp_path, monkeypatch):
    src = tmp_path / "wf"
    src.mkdir()
    (src / "W2-000.conf").write_text("key = val\n")

    copied = operator_mode._sandbox_copy(src)
    try:
        assert (copied / "world_files" / "W2-000.conf").is_file()
    finally:
        import shutil

        shutil.rmtree(copied, ignore_errors=True)

    task = {
        "task_id": "W2-000",
        "description": "Read the config.",
        "evidence_file": "W2-000.conf",
    }
    augmented = operator_mode.task_for_arm(task)
    assert "world_files/W2-000.conf" in augmented["description"]
    assert "Your working directory is" not in augmented["description"]
    # Idempotent evidence naming
    assert operator_mode.task_for_arm(augmented)["description"] == augmented["description"]

    # Verify base_url_override flows into client_kwargs when not via_relay
    captured: Dict[str, Any] = {}

    def _fake_run_once(
        provider: str,
        model: str,
        spec: Dict[str, Any],
        manifest_path: Path,
        gold_path: Path,
        sandbox: Path,
        out: Path,
        target: float,
        client_kwargs: Dict[str, Any],
        execute: bool,
        freeze_record: Any = None,
    ) -> Dict[str, Any]:
        captured["client_kwargs"] = dict(client_kwargs)
        return {"status": "planned"}

    monkeypatch.setattr(operator_mode, "_run_once", _fake_run_once)
    operator_mode.run_config(
        provider="nvidia",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        spec_path=REPO_ROOT / "spec" / "arms.json",
        manifest_path=REPO_ROOT / "world" / "world_manifest.json",
        gold_path=GOLD_PATH,
        sandbox=REPO_ROOT / "world" / "world_files",
        out=tmp_path / "out",
        execute=False,
        base_url_override="http://127.0.0.1:8787/v1",
    )
    assert captured["client_kwargs"]["base_url"] == "http://127.0.0.1:8787/v1"


def test_operator_mode_isolates_sandboxes_between_arms(tmp_path, monkeypatch):
    """Arm 1 writing deliverable.json containing a gold answer must not trip Arm 2's leakage guard."""
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    mini_spec = tmp_path / "arms.json"
    mini_spec.write_text(
        json.dumps(
            {
                "budget": {"max_steps": 100, "max_model_calls": 100, "max_wall_seconds": 60.0},
                "arms": {
                    "ARM1": "harness.minimal_arm:run",
                    "ARM2": "harness.minimal_arm:run_gated",
                },
            }
        )
    )
    seen_sandboxes: List[Path] = []

    def _fake_runner(task: Dict[str, Any], budget: Any, sandbox: Path, client: Any = None) -> Any:
        from harness.minimal_arm import Attempt

        sb = Path(sandbox)
        if sb not in seen_sandboxes:
            seen_sandboxes.append(sb)
        gold_ans = json.dumps(gold[task["task_id"]]["gold_answer"], sort_keys=True)
        # Write the real gold answer into deliverable.json just like minimal_arm does.
        (sb / "deliverable.json").write_text(gold_ans)
        att = Attempt(task["task_id"], "FAKE")
        att.answer = gold_ans
        att.steps = 1
        att.model_calls = 1
        att.stopped_reason = "answered"
        return att

    monkeypatch.setattr(operator_mode, "enforce_world_gates", lambda *a, **kw: None)
    monkeypatch.setattr(operator_mode, "enforce_freeze", lambda *a, **kw: {"status": "frozen-ok"})
    monkeypatch.setattr(operator_mode, "build_client", lambda *a, **kw: _ScriptedClient([]))
    monkeypatch.setattr(operator_mode, "resolve_runner", lambda ep: _fake_runner)

    out = tmp_path / "run_out"
    res = operator_mode.run_config(
        provider="nvidia",
        model="stub/model",
        spec_path=mini_spec,
        manifest_path=REPO_ROOT / "world" / "world_manifest.json",
        gold_path=GOLD_PATH,
        sandbox=REPO_ROOT / "world" / "world_files",
        out=out,
        execute=True,
    )
    assert res["status"] == "ran"
    assert res["verdict"] == "PASS"
    assert res["receipts_ok"] is True
    assert len(seen_sandboxes) == 2
    assert seen_sandboxes[0] != seen_sandboxes[1]
    assert not seen_sandboxes[0].exists() and not seen_sandboxes[1].exists()


# ------------------------------------------------------------ 8. runner/discovery.py


def test_discovery_is_strictly_scoped_to_sandbox_cwd(tmp_path):
    (tmp_path / "world_files").mkdir()
    (tmp_path / "world_files" / "sample.conf").write_text("a = 1\n")
    (tmp_path / "README.md").write_text("Sandbox readme\n")

    fs = discovery.discover_filesystem(tmp_path)
    assert set(fs.keys()) == {"cwd", "tree", "config_files"}
    assert "sample.conf" in fs["tree"]["stdout"]
    assert "sample.conf" in fs["config_files"]["stdout"]

    probes = discovery.probe_failure_modes(tmp_path)
    assert all("curl" not in p["cmd"] for p in probes)
    assert all(" /" not in p["cmd"] for p in probes)

    out_md = discovery.run_discovery(tmp_path)
    assert out_md == tmp_path.resolve() / "discovery.md"
    assert out_md.is_file()
    text = out_md.read_text()
    assert "Sandbox Filesystem Layout" in text
    assert "sample.conf" in text
    assert "## Environment Variables" not in text


# ------------------------------------------------------------ 9. h2_world.py (Arm C)


def test_h2_world_stateful_probe_history_and_apex_split_hygiene():
    from hypothesis import build_sandbox, cleanup_sandbox

    client = _ScriptedClient(
        [
            "cat world_files/W2-000.conf",
            "ls world_files",
            'ANSWER: {"value": "value_2022"}',
        ]
    )
    task = {
        "task_id": "W2-000",
        "family": "config-parsing",
        "description": 'Read config and output JSON: {"value": "<v>"}.',
        "evidence_file": "W2-000.conf",
    }
    sandbox = build_sandbox()
    try:
        frozen_lessons = ["always read the named evidence file first"]
        rows = h2_world.run_family(
            "test",
            [task],
            client,
            sandbox,
            probes=2,
            budget=Budget(max_steps=10, max_model_calls=10, max_wall_seconds=60.0),
            lessons=frozen_lessons,
            learn_lessons=False,
            splits={"W2-000": "test"},
        )
    finally:
        cleanup_sandbox(sandbox)

    assert len(rows) == 1
    row = rows[0]
    assert row["split"] == "test"
    assert row["task_pass"] is True
    assert row["stopped_reason"] == "answered"
    assert row["steps"] == 3
    assert row["model_calls"] == 3
    # 2nd probe prompt must contain the 1st probe's command output (stateful history)
    assert len(client.calls) == 3
    assert "value_2022" in client.calls[1][-1]["content"]
    # Split system prompts: propose vs compose
    assert client.calls[0][0]["content"] == h2_world.PROPOSE_SYSTEM_PROMPT
    assert client.calls[2][0]["content"] == h2_world.COMPOSE_SYSTEM_PROMPT
    # Held-out test hygiene: learn_lessons=False must NOT ask for a lesson
    assert not any("What did you learn" in c[-1]["content"] for c in client.calls)
    assert frozen_lessons == ["always read the named evidence file first"]


# ------------------------------------------------------------ 10. independent_grader.py


def test_independent_grader_voids_splits_and_three_arm_mcnemar(tmp_path):
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    task_ids = sorted(gold)
    rows: List[Dict[str, Any]] = []

    # 3 arms: MINIMAL (23/25 + 0 voids), GATED (25/25), C (10/25 + 6 transport-dead voids)
    for idx, tid in enumerate(task_ids):
        gold_ans = json.dumps(gold[tid]["gold_answer"], sort_keys=True)
        wrong_ans = json.dumps({"value": "wrong"})

        # GATED passes all 25
        rows.append(
            {
                "arm": "GATED",
                "task_id": tid,
                "task_pass": True,
                "criteria_passed": 1,
                "criteria_total": 1,
                "answer": gold_ans,
                "answer_empty": False,
                "steps": 2,
                "model_calls": 2,
                "wall_s": 1.5,
                "stopped_reason": "answered",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "base_url": "http://127.0.0.1:8787/v1",
                "reason": "exact hash match",
            }
        )
        # MINIMAL passes 23, fails 2 (non-empty)
        m_pass = idx < 23
        rows.append(
            {
                "arm": "MINIMAL",
                "task_id": tid,
                "task_pass": m_pass,
                "criteria_passed": 1 if m_pass else 0,
                "criteria_total": 1,
                "answer": gold_ans if m_pass else wrong_ans,
                "answer_empty": False,
                "steps": 3,
                "model_calls": 3,
                "wall_s": 2.0,
                "stopped_reason": "answered",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "base_url": "http://127.0.0.1:8787/v1",
                "reason": "exact hash match" if m_pass else "mismatch",
            }
        )
        # C has 6 transport-dead empty attempts (voided -> must NOT inflate empty_answer_rate!)
        # and passes all remaining 19 live tasks
        if idx < 6:
            rows.append(
                {
                    "arm": "C",
                    "task_id": tid,
                    "task_pass": False,
                    "criteria_passed": 0,
                    "criteria_total": 1,
                    "answer": "",
                    "answer_empty": True,
                    "transport_dead": True,
                    "stopped_reason": "transport_error: transport",
                    "wall_s": 0.5,
                    "model": "nvidia/nemotron-3-ultra-550b-a55b",
                    "base_url": "http://127.0.0.1:8787/v1",
                    "reason": "empty",
                }
            )
        else:
            rows.append(
                {
                    "arm": "C",
                    "task_id": tid,
                    "task_pass": True,
                    "criteria_passed": 1,
                    "criteria_total": 1,
                    "answer": gold_ans,
                    "answer_empty": False,
                    "steps": 4,
                    "model_calls": 4,
                    "wall_s": 3.0,
                    "stopped_reason": "answered",
                    "model": "nvidia/nemotron-3-ultra-550b-a55b",
                    "base_url": "http://127.0.0.1:8787/v1",
                    "reason": "exact hash match",
                }
            )

    rows_file = tmp_path / "three_arms.jsonl"
    rows_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    graded = grade_run(rows_file, GOLD_PATH, target=0.70, split_path=SPLIT_PATH)
    by_arm = {a["arm"]: a for a in graded["arms"]}
    assert by_arm["C"]["voided"] == 6
    assert by_arm["C"]["tasks_scored"] == 19
    assert by_arm["C"]["empty_answer_rate"] == 0.0
    assert by_arm["C"]["empty_answer_rate"] <= EMPTY_RATE_CEILING
    assert by_arm["C"]["verdict"] == "PASS"
    assert by_arm["GATED"]["splits"]["dev"]["tasks_scored"] == 15
    assert by_arm["GATED"]["splits"]["test"]["tasks_scored"] == 10
    assert by_arm["GATED"]["splits"]["test"]["pass_at_1"] == 1.0
    assert graded["verdict"] == "PASS"

    pairs = graded["comparison"]["pairs"]
    assert [p["arms"] for p in pairs] == [["C", "GATED"], ["C", "MINIMAL"], ["GATED", "MINIMAL"]]
    rendered = render_grade(graded)
    assert "voided 6" in rendered
    assert "dev 15/15" in rendered
    assert "test 10/10" in rendered


# ------------------------------------------------------------ 11. runner/report.py APEX


def test_apex_report_real_arm_names_domain_enrichment_and_html_determinism(tmp_path):
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    task_ids = sorted(gold)[:6]
    rows: List[Dict[str, Any]] = []

    for idx, tid in enumerate(task_ids):
        gold_ans = json.dumps(gold[tid]["gold_answer"], sort_keys=True)
        wrong_ans = json.dumps({"value": "wrong"})
        # Deliberately omit "family" and "split" to test split.json enrichment
        rows.append(
            {
                "arm": "GATED",
                "task_id": tid,
                "task_pass": True,
                "criteria_passed": 1,
                "criteria_total": 1,
                "answer": gold_ans,
                "answer_empty": False,
                "steps": 2,
                "model_calls": 2,
                "wall_s": 1.0,
                "gate_blocks": ["pre_answer/evidence_grounding: bounced once"] if idx == 0 else [],
                "repairs": [{"gate": "sandbox", "action": "git reset"}] if idx == 1 else [],
                "stopped_reason": "answered",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "base_url": "http://127.0.0.1:8787/v1",
                "reason": "exact hash match",
            }
        )
        m_pass = idx < 4
        rows.append(
            {
                "arm": "MINIMAL",
                "task_id": tid,
                "task_pass": m_pass,
                "criteria_passed": 1 if m_pass else 0,
                "criteria_total": 1,
                "answer": gold_ans if m_pass else wrong_ans,
                "answer_empty": False,
                "steps": 3,
                "model_calls": 3,
                "wall_s": 1.5,
                "stopped_reason": "answered" if m_pass else "unparseable_reply",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "base_url": "http://127.0.0.1:8787/v1",
                "reason": "exact hash match" if m_pass else "mismatch",
            }
        )

    rows_file = tmp_path / "apex.jsonl"
    rows_file.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    first = report.build_report(rows_file, GOLD_PATH, split_path=SPLIT_PATH)
    second = report.build_report(rows_file, GOLD_PATH, split_path=SPLIT_PATH)
    assert first["text"] == second["text"]
    assert first["html"] == second["html"]

    # Real arm names in discordant tasks (NOT hardcoded "A"/"B")
    pair = first["comparison"]["pairs"][0]
    assert pair["arms"] == ["GATED", "MINIMAL"]
    assert len(pair["tasks"]) == 2
    assert all(t["winner"] == "GATED" and t["loser"] == "MINIMAL" for t in pair["tasks"])
    assert "**GATED** won over MINIMAL" in first["text"]

    # Domain enriched from split.json (not "unknown")
    domain_names = {d["domain"] for d in first["domains"]}
    assert "unknown" not in domain_names
    assert "config-parsing" in domain_names

    # Leaderboard ordering (#1 GATED, #2 MINIMAL)
    assert [e["arm"] for e in first["leaderboard"]] == ["GATED", "MINIMAL"]
    assert first["leaderboard"][0]["gate_blocks"] == 1
    assert first["leaderboard"][0]["repairs"] == 1

    # CLI --out and --html generate standalone zero-dependency HTML
    md_out = tmp_path / "REPORT.md"
    html_out = tmp_path / "REPORT.html"
    rc = report.main(
        [
            "--rows",
            str(rows_file),
            "--gold",
            str(GOLD_PATH),
            "--split",
            str(SPLIT_PATH),
            "--out",
            str(md_out),
            "--html",
            str(html_out),
        ]
    )
    assert rc in (0, 1)
    assert md_out.read_text() == first["text"]
    html_text = html_out.read_text()
    assert html_text == first["html"]
    assert "<!DOCTYPE html>" in html_text
    assert "<script" not in html_text
    assert "https://" not in html_text
    assert "Executive Leaderboard" in html_text
    assert "GATED" in html_text and "MINIMAL" in html_text
