"""The evaluation gate runs with the test suite, so CI fails when a judgement regresses.

Offline suites only: semantic cache (bge-small), PII, injection and output redaction. The Llama
Guard suite needs a local Ollama and runs with `make evals-live`.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals import run


def test_every_offline_eval_gate_passes(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    code = run.main(["--report", str(report)])
    suites = {s["name"]: s for s in json.loads(report.read_text())}
    assert set(suites) == {"semantic_cache", "pii", "injection", "output_redaction"}
    failing = {n: s["metrics"] for n, s in suites.items() if not s["passed"]}
    assert code == 0, f"eval gates failed: {failing}"
    # a wrong cache hit serves another question's answer: that gate is exact
    assert suites["semantic_cache"]["metrics"]["precision"] == 1.0
