"""The live pilot workflow is manual and stays out of default CI."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / ".github" / "workflows" / "live-pilot.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _runs(job: dict) -> list[str]:
    return [step.get("run", "") for step in job["steps"] if "run" in step]


def test_live_workflow_is_manual_and_fails_closed() -> None:
    data = yaml.safe_load(LIVE.read_text(encoding="utf-8"))
    assert set(data["on"]) == {"workflow_dispatch"}
    assert "pull_request" not in data["on"]
    assert "push" not in data["on"]
    assert "schedule" not in data["on"]
    jobs = data["jobs"]
    assert set(jobs) == {"live"}
    job = jobs["live"]
    assert job["runs-on"] == "ubuntu-latest"
    runs = _runs(job)
    joined = "\n".join(runs)
    assert "missing secret:" in joined
    assert "live pilot refuses to run without secrets" in joined
    assert "exit 1" in joined
    assert 'benchmark run --experiment "${EXPERIMENT}"' in joined
    secret_step = next(step for step in job["steps"] if step.get("name", "").startswith("Fail closed"))
    run_step = next(step for step in job["steps"] if step.get("name") == "Run the live pilot")
    assert job["steps"].index(secret_step) < job["steps"].index(run_step)
    for name in ("LITELLM_API_KEY", "LITELLM_BASE_URL", "OPENBOT_COMMIT", "OPENBOT_BIN"):
        assert name in secret_step["env"]
        assert name in run_step["env"]
    text = LIVE.read_text(encoding="utf-8")
    assert "GPU farm LiteLLM host" in text
    assert "huggingface.co" in text
    assert "git clone" not in text
    assert "actions/checkout@v4" in text
    assert text.count("uses: actions/checkout@") == 1
    assert "pip install -e \".[dev,inspect]\"" in text


def test_default_ci_does_not_run_the_live_pilot() -> None:
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    trigger = data["on"]
    assert "workflow_dispatch" not in trigger
    assert "pull_request" in trigger
    text = CI.read_text(encoding="utf-8")
    assert "live-pilot" not in text
    assert "OPENBOT_BIN" not in text
    assert "benchmark run" not in text
