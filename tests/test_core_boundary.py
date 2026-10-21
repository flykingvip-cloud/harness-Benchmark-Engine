"""Core stays free of Inspect AI, LiteLLM, and adapter dependencies."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import ohbe
from ohbe.core.base import SCHEMA_VERSION, OHBEModel

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "ohbe"
SCHEMA_MODELS = frozenset(
    {
        "BudgetCaps",
        "CostAssessment",
        "DimensionSummary",
        "DifficultyWeight",
        "DispatchJob",
        "Execution",
        "ExecutionEnvironment",
        "ExecutionTrace",
        "Experiment",
        "FixedHarnessModelMetrics",
        "HarnessConfig",
        "HarnessRef",
        "ModelDefinition",
        "ModelFitRecommendation",
        "ModelInvocation",
        "ModelPool",
        "ModelPoolRef",
        "ObservationEvent",
        "OracleStub",
        "PricingRef",
        "RoundPlan",
        "RoundReport",
        "RoutingRegretStub",
        "RuntimeFingerprint",
        "StopConditions",
        "SuiteTask",
        "Task",
        "TaskSuite",
        "TaxonomyFilter",
        "TokenUsage",
        "TraceRecord",
    }
)


def _module_root(name: str | None) -> str | None:
    if not name:
        return None
    return name.split(".", 1)[0]


def _forbidden_import(path: Path, node: ast.AST, roots: frozenset[str]) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _module_root(alias.name) in roots:
                return f"{path}:{node.lineno}: import {alias.name}"
    elif isinstance(node, ast.ImportFrom):
        if _module_root(node.module) in roots:
            return f"{path}:{node.lineno}: from {node.module} import ..."
    elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
        target = node.args[0].value
        if not isinstance(target, str) or _module_root(target) not in roots:
            return None
        func = node.func
        if isinstance(func, ast.Name) and func.id == "__import__":
            return f"{path}:{node.lineno}: __import__({target!r})"
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            return f"{path}:{node.lineno}: import_module({target!r})"
    return None


def _import_offenders(root: Path, roots: frozenset[str]) -> list[str]:
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            found = _forbidden_import(path, node, roots)
            if found is not None:
                offenders.append(found)
    return offenders


def test_package_version_and_schema_version() -> None:
    assert ohbe.__version__ == "0.1.0"
    assert SCHEMA_VERSION == "0.1"
    from importlib.metadata import version

    assert version("openbot-harness-benchmark-engine") == "0.1.0"


def test_every_core_model_is_versioned() -> None:
    import ohbe.core  # noqa: F401

    found: set[str] = set()

    def walk(cls: type[OHBEModel]) -> None:
        for sub in cls.__subclasses__():
            found.add(sub.__name__)
            assert "schema_version" in sub.model_fields
            assert sub.model_fields["schema_version"].default == SCHEMA_VERSION
            walk(sub)

    walk(OHBEModel)
    assert found == SCHEMA_MODELS


def test_core_source_has_no_forbidden_imports() -> None:
    inspect_offenders = _import_offenders(PACKAGE, frozenset({"inspect_ai"}))
    allowed = (PACKAGE / "adapters" / "inspect_react").resolve()
    outside = [
        item
        for item in inspect_offenders
        if allowed not in Path(item.split(":", 1)[0]).resolve().parents
    ]
    assert outside == []
    assert inspect_offenders
    assert _import_offenders(PACKAGE / "core", frozenset({"litellm", "inspect_ai"})) == []
    assert _import_offenders(PACKAGE / "environment", frozenset({"litellm", "inspect_ai"})) == []
    assert _import_offenders(PACKAGE / "gateway", frozenset({"inspect_ai"})) == []
    assert _import_offenders(PACKAGE / "adapters" / "openbot", frozenset({"inspect_ai"})) == []
    for name in ("eval", "cost", "aggregate", "audit", "report", "pools", "api"):
        assert _import_offenders(
            PACKAGE / name,
            frozenset({"inspect_ai", "litellm", "docker", "urllib", "socket"}),
        ) == []


def test_import_does_not_load_forbidden_modules() -> None:
    script = (
        "import sys\n"
        "import ohbe.core\n"
        "loaded = [\n"
        "    name for name in sys.modules\n"
        "    if name in {'litellm', 'inspect_ai'} or name.split('.', 1)[0] in {'litellm', 'inspect_ai'}\n"
        "]\n"
        "raise SystemExit(0 if not loaded else 1)\n"
    )
    result = __import__("subprocess").run(
        [sys.executable, "-c", script],
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0


def test_pyproject_dependencies_stay_in_core_scope() -> None:
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = " ".join(data["project"]["dependencies"]).lower()
    assert "litellm" not in core
    assert "inspect-ai" not in core
    assert "inspect_ai" not in core
    assert "docker" not in core
    extras = data["project"]["optional-dependencies"]
    assert extras["gateway"] == ["litellm==1.104.2"]
    assert "fastapi" in " ".join(extras["api"]).lower()
    assert extras["inspect"] == ["inspect-ai==0.3.277"]
    for name, extra in extras.items():
        blob = " ".join(extra).lower()
        assert "docker" not in blob
        if name != "gateway":
            assert "litellm" not in blob
        if name != "inspect":
            assert "inspect-ai" not in blob
            assert "inspect_ai" not in blob


def test_core_does_not_import_environment_or_container_runtimes() -> None:
    core = PACKAGE / "core"
    offenders: list[str] = []
    for path in core.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                module = " ".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
            if module and (
                module == "docker"
                or module.startswith("docker.")
                or module == "ohbe.environment"
                or module.startswith("ohbe.environment.")
                or module == "ohbe.gateway"
                or module.startswith("ohbe.gateway.")
                or module == "ohbe.adapters"
                or module.startswith("ohbe.adapters.")
                or module == "ohbe.eval"
                or module.startswith("ohbe.eval.")
                or module == "ohbe.cost"
                or module.startswith("ohbe.cost.")
                or module == "ohbe.aggregate"
                or module.startswith("ohbe.aggregate.")
                or module == "ohbe.audit"
                or module.startswith("ohbe.audit.")
                or module == "ohbe.report"
                or module.startswith("ohbe.report.")
                or module == "ohbe.api"
                or module.startswith("ohbe.api.")
                or module == "fastapi"
                or module.startswith("fastapi.")
            ):
                offenders.append(f"{path}:{node.lineno}: {module}")
    assert offenders == []


def test_importing_gateway_does_not_import_litellm() -> None:
    script = (
        "import sys\n"
        "import ohbe.core\n"
        "import ohbe.gateway\n"
        "import ohbe.adapters\n"
        "banned = [name for name in sys.modules if name.split('.', 1)[0] in {'litellm', 'inspect_ai'}]\n"
        "raise SystemExit(0 if not banned else 1)\n"
    )
    result = __import__("subprocess").run(
        [sys.executable, "-c", script],
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0
