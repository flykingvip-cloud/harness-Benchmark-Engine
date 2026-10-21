"""Load a live-pilot experiment manifest.

The file describes tasks, harnesses, the model pool, and budgets. It does not
carry observed token counts, prices, or oracle scores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ohbe.core.base import SCHEMA_VERSION
from ohbe.cost.decimal import parse_decimal
from ohbe.core.model import ModelDefinition
from ohbe.environment.policy import EGRESS_POLICY_VERSION, is_denied_provider_host, normalize_host
from ohbe.pools.template import PoolTemplateError, load_pool_template, model_definitions

LIVE_PILOT_KIND = "ohbe.live_pilot"
# Live pair only. Opt-in adapters such as OpenClaw and DeepSeek Harness stay
# out until this tuple is changed on purpose.
HARNESS_ORDER = ("openbot", "inspect_react")
_EXPERIMENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_NETWORK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RESERVED_NETWORKS = frozenset({"none", "host", "bridge", "default"})
_FORBIDDEN = frozenset(
    {
        "amount",
        "api_key",
        "apiKey",
        "completion_tokens",
        "cost_amount",
        "model_independence",
        "oracle",
        "prompt_tokens",
        "routing_optimality",
        "routing_regret",
        "spend",
        "success_rate",
        "token_usage",
        "total_tokens",
        "usage",
    }
)
_ROOT_KEYS = frozenset(
    {
        "egress",
        "experiment_id",
        "harnesses",
        "kind",
        "max_cost",
        "max_total_tokens",
        "model_pool",
        "model_pool_file",
        "name",
        "pricing",
        "repetitions",
        "schema_version",
        "seed",
        "tasks",
        "timeout_seconds",
    }
)
_POOL_KEYS = frozenset({"models", "pool_id"})
_EGRESS_KEYS = frozenset({"gateway_host", "gateway_port", "network", "policy_version"})
_TASK_KEYS = frozenset({"expected", "name", "prompt", "task_id", "version"})
_OBJECTIVE_KEYS = frozenset({"contains", "exact", "match"})


def _optional_cost(value: object) -> str:
    if not isinstance(value, str):
        raise LivePilotError("max_cost must be a positive decimal string")
    try:
        parsed = parse_decimal(value, field="max_cost")
    except ValueError as exc:
        raise LivePilotError("max_cost must be a positive decimal string") from exc
    if parsed <= 0:
        raise LivePilotError("max_cost must be positive")
    return value


class LivePilotError(ValueError):
    """The manifest or the live prerequisites cannot be used."""


@dataclass(frozen=True)
class LiveTaskSpec:
    """One objective task. The prompt is the harness input."""

    task_id: str
    name: str
    version: str
    prompt: str
    scorer: str
    target: str


@dataclass(frozen=True)
class LiveModelSpec:
    """One pool member. Metadata is the proxy mapping, not a price."""

    model_id: str
    provider: str
    name: str
    revision: str
    litellm_model: str


@dataclass(frozen=True)
class LiveEgressSpec:
    """Gateway the harnesses must use. Not a provider URL."""

    policy_version: str
    network: str
    gateway_host: str
    gateway_port: int


@dataclass(frozen=True)
class LivePilotSpec:
    """Checked experiment file. Budgets are ceilings, not observed spend."""

    experiment_id: str
    name: str
    seed: int
    repetitions: int
    timeout_seconds: int
    max_total_tokens: int
    max_cost: str | None
    pricing_path: str
    pool_id: str
    pool_file: str | None
    models: tuple[LiveModelSpec, ...]
    harnesses: tuple[str, ...]
    egress: LiveEgressSpec
    tasks: tuple[LiveTaskSpec, ...]
    source: str


def load_live_pilot(path: str | Path) -> LivePilotSpec:
    """Read and validate one live-pilot YAML file."""

    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise LivePilotError(f"could not read experiment: {exc}") from exc
    if "\r" in text:
        raise LivePilotError("experiment file must use LF newlines")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LivePilotError(f"experiment file is not YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise LivePilotError("experiment file must be a YAML mapping")
    _reject_forbidden(loaded, "experiment")
    _exact_keys(loaded, _ROOT_KEYS, "experiment")
    if loaded.get("schema_version") != SCHEMA_VERSION:
        raise LivePilotError(f"schema_version must be {SCHEMA_VERSION}")
    if loaded.get("kind") != LIVE_PILOT_KIND:
        raise LivePilotError(f"kind must be {LIVE_PILOT_KIND}")
    experiment_id = _text(loaded.get("experiment_id"), "experiment_id")
    if _EXPERIMENT_ID.fullmatch(experiment_id) is None:
        raise LivePilotError("experiment_id must be a lowercase slug")
    name = _text(loaded.get("name"), "name")
    seed = _int(loaded.get("seed"), "seed", minimum=0)
    repetitions = _int(loaded.get("repetitions"), "repetitions", minimum=1, maximum=3)
    timeout_seconds = _int(loaded.get("timeout_seconds"), "timeout_seconds", minimum=1)
    max_total_tokens = _int(loaded.get("max_total_tokens"), "max_total_tokens", minimum=1)
    max_cost = _optional_cost(loaded.get("max_cost")) if "max_cost" in loaded else None
    pricing = _text(loaded.get("pricing"), "pricing")
    if pricing.startswith("/") or ".." in Path(pricing).parts or not pricing.endswith((".yaml", ".yml")):
        raise LivePilotError("pricing must be a relative YAML path")
    _relative_file(pricing, file, "pricing")
    pool_id, pool_file, models = _load_pool(loaded, file)
    harnesses = _harnesses(loaded.get("harnesses"))
    egress = _egress(loaded.get("egress"))
    tasks = _tasks(loaded.get("tasks"))
    return LivePilotSpec(
        experiment_id=experiment_id,
        name=name,
        seed=seed,
        repetitions=repetitions,
        timeout_seconds=timeout_seconds,
        max_total_tokens=max_total_tokens,
        max_cost=max_cost,
        pricing_path=pricing,
        pool_id=pool_id,
        pool_file=pool_file,
        models=models,
        harnesses=harnesses,
        egress=egress,
        tasks=tasks,
        source=str(file),
    )


def _load_pool(
    loaded: dict[str, Any],
    experiment_file: Path,
) -> tuple[str, str | None, tuple[LiveModelSpec, ...]]:
    has_file = "model_pool_file" in loaded
    has_inline = "model_pool" in loaded
    if has_file == has_inline:
        raise LivePilotError("experiment must set exactly one of model_pool or model_pool_file")
    if has_file:
        relative = _text(loaded.get("model_pool_file"), "model_pool_file")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise LivePilotError("model_pool_file must be a relative path")
        if path.parts[:2] != ("models", "pools") or path.suffix not in {".yaml", ".yml"}:
            raise LivePilotError("model_pool_file must match models/pools/*.yaml")
        resolved = _relative_file(relative, experiment_file, "model pool")
        try:
            template = load_pool_template(resolved)
        except PoolTemplateError as exc:
            raise LivePilotError(str(exc)) from exc
        return template.pool.pool_id, relative, tuple(_live_model(model) for model in template.pool.models)
    return _pool(loaded.get("model_pool"))


def _pool(value: object) -> tuple[str, None, tuple[LiveModelSpec, ...]]:
    if not isinstance(value, dict):
        raise LivePilotError("model_pool must be a mapping")
    _reject_forbidden(value, "model_pool")
    _exact_keys(value, _POOL_KEYS, "model_pool")
    pool_id = _text(value.get("pool_id"), "model_pool.pool_id")
    try:
        definitions = model_definitions(value.get("models"))
    except PoolTemplateError as exc:
        raise LivePilotError(str(exc)) from exc
    return pool_id, None, tuple(_live_model(model) for model in definitions)


def _live_model(model: ModelDefinition) -> LiveModelSpec:
    revision = model.revision or ""
    litellm_model = model.metadata.get("litellm_model")
    if not isinstance(litellm_model, str) or not litellm_model:
        raise LivePilotError("litellm_model must be a non-empty string")
    return LiveModelSpec(
        model_id=model.model_id,
        provider=model.provider,
        name=model.name,
        revision=revision,
        litellm_model=litellm_model,
    )


def _relative_file(relative: str, experiment_file: Path, label: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise LivePilotError(f"{label} must be a relative path")
    cwd_path = Path.cwd() / path
    if cwd_path.is_file():
        return cwd_path
    repo_path = experiment_file.resolve().parent.parent / path
    if repo_path.is_file():
        return repo_path
    raise LivePilotError(f"{label} file not found: {relative}")


def _harnesses(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise LivePilotError("harnesses must be a list")
    names = tuple(item for item in value)
    if names != HARNESS_ORDER:
        raise LivePilotError("harnesses must be [openbot, inspect_react]")
    return HARNESS_ORDER


def _egress(value: object) -> LiveEgressSpec:
    if not isinstance(value, dict):
        raise LivePilotError("egress must be a mapping")
    _reject_forbidden(value, "egress")
    _exact_keys(value, _EGRESS_KEYS, "egress")
    policy = _text(value.get("policy_version"), "egress.policy_version")
    if policy != EGRESS_POLICY_VERSION:
        raise LivePilotError(f"egress.policy_version must be {EGRESS_POLICY_VERSION}")
    network = _text(value.get("network"), "egress.network")
    if _NETWORK.fullmatch(network) is None or network.lower() in _RESERVED_NETWORKS:
        raise LivePilotError("egress.network must name the OHBE internal network")
    host = normalize_host(_text(value.get("gateway_host"), "egress.gateway_host"))
    if not host or "://" in host or "/" in host:
        raise LivePilotError("egress.gateway_host must be a hostname or IP")
    if is_denied_provider_host(host):
        raise LivePilotError("egress.gateway_host must not be a model-provider endpoint")
    port = _int(value.get("gateway_port"), "egress.gateway_port", minimum=1, maximum=65535)
    return LiveEgressSpec(
        policy_version=policy,
        network=network,
        gateway_host=host,
        gateway_port=port,
    )


def _tasks(value: object) -> tuple[LiveTaskSpec, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise LivePilotError("tasks must list 1 to 3 objective tasks")
    tasks: list[LiveTaskSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise LivePilotError(f"tasks[{index}] must be a mapping")
        _reject_forbidden(item, f"tasks[{index}]")
        _exact_keys(item, _TASK_KEYS, f"tasks[{index}]")
        task_id = _text(item.get("task_id"), "task_id")
        if task_id in seen:
            raise LivePilotError(f"duplicate task_id {task_id}")
        seen.add(task_id)
        expected = item.get("expected")
        if not isinstance(expected, dict):
            raise LivePilotError(f"{task_id} expected must be a mapping")
        _reject_forbidden(expected, f"{task_id}.expected")
        present = [key for key in _OBJECTIVE_KEYS if key in expected]
        if len(present) != 1 or set(expected) != set(present):
            raise LivePilotError(f"{task_id} expected must set exactly one of contains, match, exact")
        scorer = present[0]
        target = _text(expected.get(scorer), f"{task_id}.expected.{scorer}")
        tasks.append(
            LiveTaskSpec(
                task_id=task_id,
                name=_text(item.get("name"), "name"),
                version=_text(item.get("version"), "version"),
                prompt=_text(item.get("prompt"), "prompt"),
                scorer=scorer,
                target=target,
            )
        )
    return tuple(tasks)


def _reject_forbidden(value: object, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _FORBIDDEN:
                raise LivePilotError(f"{path}.{key} is not part of a live pilot manifest")
            _reject_forbidden(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden(item, f"{path}[{index}]")


def _exact_keys(value: dict[str, Any], allowed: frozenset[str], label: str, *, allow_empty: bool = False) -> None:
    extra = set(value) - allowed
    if extra:
        raise LivePilotError(f"{label} has unknown fields: {', '.join(sorted(extra))}")
    if not allow_empty and not set(value) <= allowed:
        raise LivePilotError(f"{label} is incomplete")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LivePilotError(f"{field} must be a non-empty string")
    return value


def _int(value: object, field: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LivePilotError(f"{field} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        raise LivePilotError(f"{field} is outside {minimum}..{maximum if maximum is not None else 'inf'}")
    return value
