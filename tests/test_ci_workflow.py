"""The GitHub Actions workflow keeps the Docker egress gate from being skipped."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _runs(job: dict) -> list[str]:
    return [step.get("run", "") for step in job["steps"] if "run" in step]


def test_ci_runs_pytest_and_refuses_a_skipped_egress_probe() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    trigger = data["on"]
    assert trigger["pull_request"] is None
    assert trigger["push"]["branches"] == ["main"]
    jobs = data["jobs"]
    assert set(jobs) == {"pytest", "workbench", "egress"}
    for name in ("pytest", "egress"):
        job = jobs[name]
        assert job["runs-on"] == "ubuntu-latest"
        versions = [
            str(step["with"]["python-version"])
            for step in job["steps"]
            if isinstance(step.get("with"), dict) and "python-version" in step["with"]
        ]
        assert versions == ["3.12"]
    workbench = jobs["workbench"]
    assert workbench["runs-on"] == "ubuntu-latest"
    node_versions = [
        str(step["with"]["node-version"])
        for step in workbench["steps"]
        if isinstance(step.get("with"), dict) and "node-version" in step["with"]
    ]
    assert node_versions == ["22"]
    workbench_runs = "\n".join(_runs(workbench))
    assert "npm ci" in workbench_runs
    assert "npm test" in workbench_runs
    assert "npm run build" in workbench_runs
    assert "benchmark run" not in workbench_runs
    assert "OHBE_ALLOW_LIVE_SPEND" not in workbench_runs
    assert 'pip install -e ".[dev,api]"' in "\n".join(_runs(jobs["pytest"]))
    assert any('pip install -e ".[dev]"' in run for run in _runs(jobs["egress"]))
    assert any(run.strip() == "pytest" for run in _runs(jobs["pytest"]))
    egress = "\n".join(_runs(jobs["egress"]))
    assert "docker info" in egress
    assert "test_docker_blocks_provider_egress_and_allows_gateway" in egress
    assert "benchmark check-egress" in egress
    assert "skipped" in egress
    assert "tests != 1" in egress
