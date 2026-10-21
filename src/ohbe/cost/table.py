"""Load a versioned offline pricing table.

The table is YAML with decimal strings. A YAML float is rejected so a rate
cannot lose precision before the hash is checked.

Two meters share one file:

- ``tokens``: provider list price per million prompt and completion tokens.
- ``normalized_gpu_seconds``: a rented GPU's hourly cost, divided by 3600
  at calculation time. The per-second rate is not stored in the file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from ohbe.cost.decimal import parse_decimal

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_HOUR = Decimal(3600)
METER_TOKENS = "tokens"
METER_GPU_SECONDS = "normalized_gpu_seconds"
_TOKEN_KEYS = frozenset({"completion_per_million", "meter", "model_id", "prompt_per_million"})
_GPU_MODEL_KEYS = frozenset({"gpu_type", "meter", "model_id"})
_GPU_KEYS = frozenset({"gpu_cost_per_hour", "gpu_type"})
_ROOT_KEYS = frozenset({"currency", "gpus", "models", "pricing_id", "version"})


class PricingError(ValueError):
    """The pricing file or the caller's PricingRef cannot be used."""


@dataclass(frozen=True)
class GpuRate:
    """One rented GPU. ``usd_per_second`` is ``gpu_cost_per_hour / 3600``."""

    gpu_type: str
    gpu_cost_per_hour: Decimal

    @property
    def usd_per_second(self) -> Decimal:
        return self.gpu_cost_per_hour / _HOUR


@dataclass(frozen=True)
class TokenModelPrice:
    """Per-million-token list price. That list price is also the normalized cost."""

    model_id: str
    meter: str
    prompt_per_million: Decimal
    completion_per_million: Decimal


@dataclass(frozen=True)
class GpuSecondModelPrice:
    """Local open-source model priced by normalized GPU seconds.

    ``gpu_type`` names a row in the table's GPU catalog. There is no provider
    token invoice for this meter.
    """

    model_id: str
    meter: str
    gpu_type: str


ModelPrice = TokenModelPrice | GpuSecondModelPrice


@dataclass(frozen=True)
class PricingTable:
    """One immutable price table. `content_hash` is the sha256 of the file bytes."""

    pricing_id: str
    version: str
    currency: str
    gpus: tuple[GpuRate, ...]
    models: tuple[ModelPrice, ...]
    content_hash: str
    source: str

    def price_for(self, model_id: str) -> ModelPrice | None:
        for model in self.models:
            if model.model_id == model_id:
                return model
        return None

    def gpu_for(self, gpu_type: str) -> GpuRate | None:
        for gpu in self.gpus:
            if gpu.gpu_type == gpu_type:
                return gpu
        return None


def load_pricing_table(path: str | Path) -> PricingTable:
    """Read a pricing YAML file and hash its exact bytes."""

    file = Path(path)
    raw = file.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise PricingError("pricing file must not start with a BOM")
    if b"\r" in raw:
        raise PricingError("pricing file must use LF newlines")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PricingError("pricing file is not valid UTF-8") from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PricingError(f"pricing file is not YAML: {exc}") from exc
    parsed = _parse_table(loaded)
    digest = hashlib.sha256(raw).hexdigest()
    return PricingTable(
        pricing_id=parsed[0],
        version=parsed[1],
        currency=parsed[2],
        gpus=parsed[3],
        models=parsed[4],
        content_hash=digest,
        source=str(file),
    )


def _parse_table(
    loaded: object,
) -> tuple[str, str, str, tuple[GpuRate, ...], tuple[ModelPrice, ...]]:
    if not isinstance(loaded, dict):
        raise PricingError("pricing file must be a mapping")
    extra = set(loaded) - _ROOT_KEYS
    if extra:
        raise PricingError(f"pricing file has unknown keys: {sorted(extra)}")
    missing = _ROOT_KEYS - set(loaded)
    if missing:
        raise PricingError(f"pricing file is missing keys: {sorted(missing)}")
    pricing_id = _nonempty_str(loaded.get("pricing_id"), field="pricing_id")
    version = _nonempty_str(loaded.get("version"), field="version")
    currency = loaded.get("currency")
    if not isinstance(currency, str) or _CURRENCY_RE.fullmatch(currency) is None:
        raise PricingError("currency must be a 3-letter uppercase ISO code")
    gpus = _parse_gpus(loaded.get("gpus"))
    models_raw = loaded.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise PricingError("models must be a non-empty list")
    known = {gpu.gpu_type for gpu in gpus}
    models: list[ModelPrice] = []
    seen: set[str] = set()
    for index, item in enumerate(models_raw):
        price = _parse_model(item, index=index, gpus=known)
        if price.model_id in seen:
            raise PricingError(f"duplicate model_id {price.model_id}")
        seen.add(price.model_id)
        models.append(price)
    return pricing_id, version, currency, gpus, tuple(models)


def _parse_gpus(value: object) -> tuple[GpuRate, ...]:
    if not isinstance(value, list):
        raise PricingError("gpus must be a list")
    rates: list[GpuRate] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise PricingError(f"gpus[{index}] must be a mapping")
        extra = set(item) - _GPU_KEYS
        if extra or set(item) != _GPU_KEYS:
            raise PricingError(f"gpus[{index}] must contain gpu_type and gpu_cost_per_hour")
        gpu_type = _nonempty_str(item.get("gpu_type"), field=f"gpus[{index}].gpu_type")
        if gpu_type in seen:
            raise PricingError(f"duplicate gpu_type {gpu_type}")
        seen.add(gpu_type)
        try:
            hourly = parse_decimal(item.get("gpu_cost_per_hour"), field="gpu_cost_per_hour")  # type: ignore[arg-type]
        except ValueError as exc:
            raise PricingError(f"gpus[{index}] {exc}") from exc
        if hourly < 0:
            raise PricingError(f"gpus[{index}] gpu_cost_per_hour must not be negative")
        rates.append(GpuRate(gpu_type=gpu_type, gpu_cost_per_hour=hourly))
    return tuple(rates)


def _parse_model(item: object, *, index: int, gpus: set[str]) -> ModelPrice:
    if not isinstance(item, dict):
        raise PricingError(f"models[{index}] must be a mapping")
    meter = item.get("meter")
    if meter == METER_TOKENS:
        return _parse_token_model(item, index=index)
    if meter == METER_GPU_SECONDS:
        return _parse_gpu_model(item, index=index, gpus=gpus)
    raise PricingError(
        f"models[{index}] meter must be {METER_TOKENS} or {METER_GPU_SECONDS}"
    )


def _parse_token_model(item: dict[object, object], *, index: int) -> TokenModelPrice:
    extra = set(item) - _TOKEN_KEYS
    if extra or set(item) != _TOKEN_KEYS:
        raise PricingError(f"models[{index}] token meter must contain per-million rates")
    model_id = _nonempty_str(item.get("model_id"), field=f"models[{index}].model_id")
    try:
        prompt = parse_decimal(item.get("prompt_per_million"), field="prompt_per_million")  # type: ignore[arg-type]
        completion = parse_decimal(
            item.get("completion_per_million"),  # type: ignore[arg-type]
            field="completion_per_million",
        )
    except ValueError as exc:
        raise PricingError(f"models[{index}] {exc}") from exc
    if prompt < 0 or completion < 0:
        raise PricingError(f"models[{index}] rates must not be negative")
    return TokenModelPrice(
        model_id=model_id,
        meter=METER_TOKENS,
        prompt_per_million=prompt,
        completion_per_million=completion,
    )


def _parse_gpu_model(item: dict[object, object], *, index: int, gpus: set[str]) -> GpuSecondModelPrice:
    extra = set(item) - _GPU_MODEL_KEYS
    if extra or set(item) != _GPU_MODEL_KEYS:
        raise PricingError(
            f"models[{index}] normalized_gpu_seconds meter must contain model_id and gpu_type"
        )
    model_id = _nonempty_str(item.get("model_id"), field=f"models[{index}].model_id")
    gpu_type = _nonempty_str(item.get("gpu_type"), field=f"models[{index}].gpu_type")
    if gpu_type not in gpus:
        raise PricingError(f"models[{index}] gpu_type {gpu_type} is not in gpus")
    return GpuSecondModelPrice(model_id=model_id, meter=METER_GPU_SECONDS, gpu_type=gpu_type)


def _nonempty_str(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise PricingError(f"{field} must be a non-empty string")
    return value
