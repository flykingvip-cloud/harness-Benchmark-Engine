"""Inspect `react()` plan pointed at the OHBE LiteLLM proxy.

`inspect-ai==0.3.277` reads `LITELLM_PROXY_BASE_URL` and `LITELLM_API_KEY`
(or `LITELLM_PROXY_API_KEY`). The model name is `litellm-proxy/<model_id>`.
A Docker sandbox covers tools. It does not lock the host process that calls
the model; that process must use this proxy URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ohbe.adapters.openbot.profile import OpenBotProfileError, litellm_base_url
from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelPool
from ohbe.core.task import Task

INSPECT_AI_VERSION = "0.3.277"
LITELLM_PROXY_BASE_URL = "LITELLM_PROXY_BASE_URL"
LITELLM_API_KEY = "LITELLM_API_KEY"


class InspectProfileError(ValueError):
    """The Inspect plan would bypass the LiteLLM gateway."""


@dataclass(frozen=True)
class InspectEvalPlan:
    """What one `react()` eval will see. The API key is not part of the trace."""

    model: str
    base_url: str
    sample_input: str
    sample_target: str
    scorer: str
    solver: str
    sandbox: str
    tools: tuple[str, ...]
    env: dict[str, str]
    task_id: str
    task_input: dict[str, Any]
    model_id: str
    model_pool_ids: tuple[str, ...]


@dataclass(frozen=True)
class InspectEvalOutcome:
    """Result of a mock or live Inspect eval."""

    success: bool
    score: str | None = None
    answer: str | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None


def inspect_harness_config(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int = 600,
    version: str = INSPECT_AI_VERSION,
) -> HarnessConfig:
    """Harness document pinned to the Inspect package version."""

    if version != INSPECT_AI_VERSION:
        raise InspectProfileError(f"Inspect adapter is pinned to inspect-ai=={INSPECT_AI_VERSION}")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise InspectProfileError("timeout_seconds must be a positive integer")
    return HarnessConfig.build(
        harness_id="inspect-react",
        name="inspect-react",
        version=version,
        parameters={
            "solver": "react",
            "provider": "litellm-proxy",
            "gateway_host": environment.gateway_host,
            "gateway_port": environment.gateway_port,
            "models": [model.model_id for model in pool.models],
            "scorer": "includes",
            "sandbox": "docker",
            "timeout_seconds": timeout_seconds,
        },
    )


def objective_target(task: Task) -> tuple[str, str]:
    """Return `(scorer, target)` for an objective string check.

    `expected.exact` uses Inspect's `match` scorer, which is the full-string
    check. OHBE still records the task scorer as `exact` when it judges the
    trace.
    """

    expected = task.expected or {}
    contains = expected.get("contains")
    if isinstance(contains, str) and contains:
        return "includes", contains
    match = expected.get("match")
    if isinstance(match, str) and match:
        return "match", match
    exact = expected.get("exact")
    if isinstance(exact, str) and exact:
        return "match", exact
    raise InspectProfileError(
        "objective scorer requires expected.contains, expected.match, or expected.exact"
    )


def selected_model_id(task: Task, pool: ModelPool) -> str:
    requested = task.input.get("model_id")
    allowed = [model.model_id for model in pool.models]
    if isinstance(requested, str) and requested:
        if requested not in allowed:
            raise InspectProfileError(f"{requested} is not in the model pool allowlist")
        return requested
    if not allowed:
        raise InspectProfileError("model pool is empty")
    return allowed[0]


def proxy_base_url(environment: ExecutionEnvironment) -> str:
    """OpenAI-compatible base URL on the OHBE gateway, including `/v1`."""

    try:
        origin = litellm_base_url(environment)
    except OpenBotProfileError as exc:
        raise InspectProfileError("Inspect model calls must use the OHBE gateway") from exc
    return origin.rstrip("/") + "/v1"


def build_eval_plan(
    *,
    task: Task,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    api_key: str,
) -> InspectEvalPlan:
    """Describe a `react()` eval that can reach only the gateway."""

    if not api_key or any(character.isspace() for character in api_key):
        raise InspectProfileError("LITELLM_API_KEY must be a non-empty token")
    scorer, target = objective_target(task)
    prompt = task.input.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise InspectProfileError("task input.prompt must be a non-empty string")
    model_id = selected_model_id(task, pool)
    base_url = proxy_base_url(environment)
    if "api.openai.com" in base_url or "api.anthropic.com" in base_url:
        raise InspectProfileError("Inspect model calls must use the OHBE gateway")
    env = {
        LITELLM_PROXY_BASE_URL: base_url,
        LITELLM_API_KEY: api_key,
    }
    return InspectEvalPlan(
        model=f"litellm-proxy/{model_id}",
        base_url=base_url,
        sample_input=prompt,
        sample_target=target,
        scorer=scorer,
        solver="react",
        sandbox="docker",
        tools=("bash",),
        env=env,
        task_id=task.task_id,
        task_input=dict(task.input),
        model_id=model_id,
        model_pool_ids=tuple(model.model_id for model in pool.models),
    )


def check_inspect_binding(config: HarnessConfig, *, pool: ModelPool, environment: ExecutionEnvironment) -> None:
    """Reject a harness config that is not this Inspect react profile."""

    if config.harness_id != "inspect-react" or config.version != INSPECT_AI_VERSION:
        raise InspectProfileError("harness config is not the pinned inspect-react profile")
    parameters = config.parameters
    if parameters.get("solver") != "react" or parameters.get("provider") != "litellm-proxy":
        raise InspectProfileError("harness config must use react() through litellm-proxy")
    if parameters.get("sandbox") != "docker" or parameters.get("scorer") != "includes":
        raise InspectProfileError("harness config must use the docker sandbox and an includes scorer")
    if parameters.get("gateway_host") != environment.gateway_host:
        raise InspectProfileError("harness gateway host does not match the egress record")
    if parameters.get("gateway_port") != environment.gateway_port:
        raise InspectProfileError("harness gateway port does not match the egress record")
    if list(parameters.get("models") or []) != [model.model_id for model in pool.models]:
        raise InspectProfileError("harness model list does not match the pool")


def public_plan(plan: InspectEvalPlan) -> dict[str, Any]:
    """Trace view of a plan. The API key is omitted."""

    return {
        "base_url": plan.base_url,
        "env_names": sorted(key for key in plan.env if key != LITELLM_API_KEY),
        "model": plan.model,
        "model_id": plan.model_id,
        "model_pool_ids": list(plan.model_pool_ids),
        "sample_input": plan.sample_input,
        "sample_target": plan.sample_target,
        "sandbox": plan.sandbox,
        "scorer": plan.scorer,
        "solver": plan.solver,
        "task_id": plan.task_id,
        "task_input": plan.task_input,
        "tools": list(plan.tools),
    }


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop secret values before anything is stored."""

    return {key: value for key, value in env.items() if key != LITELLM_API_KEY}
