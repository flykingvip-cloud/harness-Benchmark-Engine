"""Read-only doctor for the workbench integration checklist.

Checks call the engine in process. They do not start Docker, open a provider
socket, or place a model call.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from ohbe import __version__
from ohbe.api.catalog import Catalog, CatalogUnavailable
from ohbe.api.schemas import Check, DoctorResponse
from ohbe.api.surface import ENGINE_ID, LIVE_SPEND_ENV, LIVE_SPEND_UNLOCK_VALUE, SURFACE_ID
from ohbe.core.base import SCHEMA_VERSION
from ohbe.environment.policy import (
    CloudLiteLLMDenied,
    EgressDenied,
    EgressSimulator,
    HttpTarget,
    controlled_track_policy,
    is_cloud_litellm_host,
)

_PROVIDER_URLS = (
    "https://api.openai.com/v1/chat/completions",
    "https://api.anthropic.com/v1/messages",
)
_CLOUD_URLS = (
    "https://api.litellm.ai/v1",
    "https://org.proxy.litellm.ai/v1/chat/completions",
    "https://API.LITELLM.AI/v1",
)


class _CountingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def exchange(self, target: HttpTarget, body: bytes) -> bytes:
        self.calls += 1
        raise AssertionError(f"doctor transport was invoked for {target.host}")


def live_spend_state(environ: Mapping[str, str] | None = None) -> Literal["locked", "env_set"]:
    """``locked`` unless the live-spend environment variable is exactly ``1``."""

    env = os.environ if environ is None else environ
    if env.get(LIVE_SPEND_ENV) == LIVE_SPEND_UNLOCK_VALUE:
        return "env_set"
    return "locked"


def collect_doctor(catalog: Catalog, environ: Mapping[str, str] | None = None) -> DoctorResponse:
    """Build the checklist. A failed check stays in the body; the process still answers."""

    env = os.environ if environ is None else environ
    checks = [
        _engine_version(),
        _schema(),
        _pricing_and_pool(catalog),
        _fixture_report(catalog),
        _egress_denies_providers_and_cloud(),
        _cloud_host_cannot_be_the_gateway(),
        _live_spend_locked(env),
    ]
    return DoctorResponse(
        ok=all(check.passed for check in checks),
        surface=SURFACE_ID,
        ohbe_version=__version__,
        schema_version=SCHEMA_VERSION,
        live_spend=live_spend_state(env),
        spend_execution="disabled",
        checks=checks,
    )


def _engine_version() -> Check:
    return Check(
        id="engine_version",
        passed=bool(__version__),
        summary="The ohbe engine package imports and reports a version.",
        evidence=[f"ohbe:{__version__}", f"engine:{ENGINE_ID}"],
    )


def _schema() -> Check:
    return Check(
        id="schema_version",
        passed=SCHEMA_VERSION == "0.1",
        summary="Core schema version is 0.1.",
        evidence=[f"schema:{SCHEMA_VERSION}"],
    )


def _pricing_and_pool(catalog: Catalog) -> Check:
    try:
        pool = catalog.pools()[0]
    except (CatalogUnavailable, OSError, ValueError) as exc:
        return Check(
            id="offline_pool",
            passed=False,
            summary="The offline pricing table or fixture pool could not be built.",
            evidence=[str(exc)],
        )
    return Check(
        id="offline_pool",
        passed=pool.hash_matches_report,
        summary="The engine built the fixture pool from the offline price table and its hash matches the report.",
        evidence=[f"pool:{pool.pool_id}", f"hash:{pool.content_hash}"],
    )


def _fixture_report(catalog: Catalog) -> Check:
    try:
        result = catalog.result("fixture-pilot")
    except (CatalogUnavailable, KeyError, OSError, ValueError) as exc:
        return Check(
            id="fixture_report",
            passed=False,
            summary="The checked-in fixture report could not be read.",
            evidence=[str(exc)],
        )
    passed = result.kind == "ohbe.report" and result.label == "fixture" and result.spend == "not_live"
    return Check(
        id="fixture_report",
        passed=passed and result.audit_passed,
        summary="The fixture report is ohbe.report, labeled fixture, and marked not_live.",
        evidence=[
            f"kind:{result.kind}",
            f"label:{result.label}",
            f"spend:{result.spend}",
            f"audit_passed:{result.audit_passed}",
        ],
    )


def _egress_denies_providers_and_cloud() -> Check:
    policy = controlled_track_policy(gateway_host="ohbe-gateway", gateway_port=4000)
    transport = _CountingTransport()
    simulator = EgressSimulator(policy, transport)
    denied: list[str] = []
    for url in _PROVIDER_URLS + _CLOUD_URLS:
        try:
            simulator.request(url)
        except EgressDenied:
            denied.append(url)
        except AssertionError as exc:
            return Check(
                id="egress_fail_closed",
                passed=False,
                summary="The egress check invoked a transport.",
                evidence=[str(exc)],
            )
    passed = len(denied) == len(_PROVIDER_URLS) + len(_CLOUD_URLS) and transport.calls == 0
    return Check(
        id="egress_fail_closed",
        passed=passed,
        summary="Provider hosts and cloud LiteLLM URLs fail the egress policy before any transport.",
        evidence=denied,
    )


def _cloud_host_cannot_be_the_gateway() -> Check:
    evidence: list[str] = []
    try:
        controlled_track_policy(gateway_host="api.litellm.ai", gateway_port=443)
    except CloudLiteLLMDenied as exc:
        evidence.append(f"rejected:{exc.host}")
    else:
        return Check(
            id="cloud_litellm_fail_closed",
            passed=False,
            summary="A cloud LiteLLM host was accepted as the gateway.",
            evidence=["host:api.litellm.ai"],
        )
    classified = is_cloud_litellm_host("api.litellm.ai") and not is_cloud_litellm_host("ohbe-gateway")
    return Check(
        id="cloud_litellm_fail_closed",
        passed=classified,
        summary="Cloud LiteLLM hosts cannot be configured as the OHBE gateway.",
        evidence=evidence + ["local:ohbe-gateway"],
    )


def _live_spend_locked(environ: Mapping[str, str]) -> Check:
    state = live_spend_state(environ)
    locked = state == "locked"
    evidence = [f"{LIVE_SPEND_ENV}:{'set' if not locked else 'unset'}", "spend_execution:disabled"]
    if locked:
        summary = "Live spend is locked. The API does not place a model call."
    else:
        summary = (
            f"{LIVE_SPEND_ENV} is set. A live flag would still not place a model call from this API."
        )
    return Check(
        id="live_spend_locked",
        passed=locked,
        summary=summary,
        evidence=evidence,
    )
