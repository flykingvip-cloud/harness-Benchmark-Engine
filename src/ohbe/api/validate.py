"""Dry-run validation. This module never opens a socket and never spends."""

from __future__ import annotations

from collections.abc import Mapping

from ohbe.api.catalog import Catalog
from ohbe.api.doctor import live_spend_state
from ohbe.api.schemas import Check, DryRunRequest, DryRunResponse
from ohbe.api.surface import LIVE_SPEND_ENV
from ohbe.environment.policy import (
    CloudLiteLLMDenied,
    EgressDenied,
    EgressSimulator,
    HttpTarget,
    controlled_track_policy,
    is_cloud_litellm_host,
    parse_http_target,
)


class ValidationRejected(Exception):
    """The dry-run request is refused. No transport ran."""

    def __init__(self, status_code: int, code: str, message: str, *, gateway_host: str | None = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.gateway_host = gateway_host
        super().__init__(message)


class _RefuseTransport:
    def exchange(self, target: HttpTarget, body: bytes) -> bytes:
        raise AssertionError(f"dry-run transport was invoked for {target.host}")


def validate_dry_run(
    request: DryRunRequest,
    catalog: Catalog,
    environ: Mapping[str, str],
) -> DryRunResponse:
    """Validate a gateway URL and optional catalog ids without contacting them."""

    try:
        target = parse_http_target(request.gateway_url.strip())
    except ValueError as exc:
        raise ValidationRejected(400, "gateway_rejected", str(exc)) from exc
    if is_cloud_litellm_host(target.host):
        raise ValidationRejected(
            403,
            "cloud_litellm_denied",
            "Cloud LiteLLM URLs are denied. This API does not spend against hosted LiteLLM.",
            gateway_host=target.host,
        )
    if request.live and live_spend_state(environ) != "env_set":
        raise ValidationRejected(
            403,
            "live_spend_locked",
            f"Live spend requires JSON live=true and {LIVE_SPEND_ENV}=1. The request was not executed.",
            gateway_host=target.host,
        )
    try:
        policy = controlled_track_policy(gateway_host=target.host, gateway_port=target.port)
    except CloudLiteLLMDenied as exc:
        raise ValidationRejected(
            403,
            "cloud_litellm_denied",
            "Cloud LiteLLM URLs are denied. This API does not spend against hosted LiteLLM.",
            gateway_host=exc.host,
        ) from exc
    except ValueError as exc:
        raise ValidationRejected(403, "gateway_rejected", str(exc), gateway_host=target.host) from exc

    transport = _RefuseTransport()
    simulator = EgressSimulator(policy, transport)
    denied: list[str] = []
    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "https://api.litellm.ai/v1/chat/completions",
    ):
        try:
            simulator.request(url)
        except EgressDenied:
            denied.append(url)
        except AssertionError as exc:
            raise ValidationRejected(500, "transport_invoked", str(exc), gateway_host=target.host) from exc
    if len(denied) != 3:
        raise ValidationRejected(
            403,
            "gateway_rejected",
            "The egress policy did not deny a provider or cloud LiteLLM URL.",
            gateway_host=target.host,
        )

    checks: list[Check] = [
        Check(
            id="gateway_not_cloud",
            passed=True,
            summary="The gateway host is not a known cloud LiteLLM endpoint.",
            evidence=[f"host:{target.host}", f"port:{target.port}"],
        ),
        Check(
            id="provider_bypass_denied",
            passed=True,
            summary="Provider and cloud LiteLLM URLs are denied before any transport.",
            evidence=denied,
        ),
        Check(
            id="spend_not_executed",
            passed=True,
            summary="Dry-run validation does not place a model call.",
            evidence=["executed:false", "spend:none", "network:not_contacted"],
        ),
    ]
    if request.experiment_id is not None:
        try:
            experiment = catalog.experiment(request.experiment_id)
        except KeyError as exc:
            raise ValidationRejected(
                404,
                "experiment_not_found",
                f"Unknown experiment: {request.experiment_id}",
                gateway_host=target.host,
            ) from exc
        checks.append(
            Check(
                id="experiment_known",
                passed=True,
                summary="The experiment is in the fixture catalog.",
                evidence=[f"experiment:{experiment.experiment_id}", f"spend:{experiment.spend}"],
            )
        )
    if request.pool_id is not None:
        try:
            pool = catalog.pool(request.pool_id)
        except KeyError as exc:
            raise ValidationRejected(
                404,
                "pool_not_found",
                f"Unknown model pool: {request.pool_id}",
                gateway_host=target.host,
            ) from exc
        checks.append(
            Check(
                id="pool_known",
                passed=True,
                summary="The model pool is the engine fixture pool.",
                evidence=[f"pool:{pool.pool_id}", f"hash:{pool.content_hash}"],
            )
        )
    if request.live:
        checks.append(
            Check(
                id="live_not_executed",
                passed=True,
                summary=(
                    f"{LIVE_SPEND_ENV} is set and live was requested. "
                    "This surface still does not place a model call."
                ),
                evidence=["spend_execution:disabled"],
            )
        )
    return DryRunResponse(
        ok=True,
        mode="dry_run",
        executed=False,
        spend="none",
        network="not_contacted",
        live_requested=request.live,
        gateway_host=target.host,
        gateway_port=target.port,
        checks=checks,
    )
