"""HTTP client for the LiteLLM proxy on the allowlisted gateway.

This module speaks the proxy's ``/v1/chat/completions`` route with the
standard library. It does not call ``litellm.completion``, because that
client can open provider connections from the benchmark process. Step D
egress stays wrapped around the socket used here, and redirects are rejected.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ohbe.core.base import AwareDatetime
from ohbe.core.invocation import ModelInvocation
from ohbe.core.model import ModelDefinition, ModelPool
from ohbe.core.trace import TraceLog
from ohbe.core.usage import UsageStatus
from ohbe.environment.guard import EgressGuard
from ohbe.environment.policy import (
    EgressDenied,
    EgressVerdict,
    NetworkEgressPolicy,
    is_cloud_litellm_host,
    parse_http_target,
)
from ohbe.gateway.constants import CHAT_COMPLETIONS_PATH
from ohbe.gateway.metadata import (
    GatewayCallMetadata,
    build_chat_body,
    request_headers,
    spend_logs_metadata,
)
from ohbe.gateway.record import (
    GatewayTransportError,
    ModelNotInPool,
    UsageNotAvailable,
    append_invocation,
    record_invocation,
)
from ohbe.gateway.reconcile import Reconciliation


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise GatewayTransportError(f"gateway redirect is not allowed: {newurl}")


def gateway_origin(policy: NetworkEgressPolicy, *, scheme: str = "http") -> str:
    """Origin of the allowlisted proxy, including host and port."""

    if scheme not in {"http", "https"}:
        raise ValueError("scheme must be http or https")
    host = policy.gateway_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{scheme}://{host}:{policy.gateway_port}"


def post_json(
    policy: NetworkEgressPolicy,
    url: str,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    *,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    """POST JSON to ``url`` only when ``policy`` allows that host and port."""

    target = parse_http_target(url)
    if is_cloud_litellm_host(target.host):
        raise EgressDenied(
            EgressVerdict(False, target.host, target.port, "cloud LiteLLM endpoint is denied")
        )
    verdict = policy.evaluate(target.host, target.port)
    if not verdict.allowed:
        raise EgressDenied(verdict)
    payload = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers=dict(headers),
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect)
    try:
        with EgressGuard(policy):
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
                response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, EgressDenied):
            raise exc.reason from exc
        raise GatewayTransportError(str(exc.reason or exc)) from exc
    except EgressDenied:
        raise
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayTransportError("gateway response is not JSON") from exc
    if not isinstance(parsed, dict):
        raise GatewayTransportError("gateway response is not a JSON object")
    return status, response_headers, parsed


class GatewayClient:
    """Call the allowlisted LiteLLM proxy and record an OHBE invocation."""

    def __init__(
        self,
        *,
        policy: NetworkEgressPolicy,
        api_key: str,
        pool: ModelPool | None = None,
        scheme: str = "http",
        timeout: float = 30.0,
    ) -> None:
        if scheme not in {"http", "https"}:
            raise ValueError("scheme must be http or https")
        if not isinstance(api_key, str) or not api_key or any(ch.isspace() for ch in api_key):
            raise ValueError("api_key must be a non-empty token")
        self.policy = policy
        self.api_key = api_key
        self.pool = pool
        self.scheme = scheme
        self.timeout = timeout

    def chat_url(self) -> str:
        return gateway_origin(self.policy, scheme=self.scheme) + CHAT_COMPLETIONS_PATH

    def complete(
        self,
        *,
        model: ModelDefinition,
        messages: list[Mapping[str, str]],
        metadata: GatewayCallMetadata,
        invocation_id: str,
        seq: int,
        started_at: AwareDatetime | None = None,
        finished_at: AwareDatetime | None = None,
        trace: TraceLog | None = None,
        record_id: str | None = None,
        recorded_at: datetime | None = None,
        require_usage: bool = True,
    ) -> ModelInvocation:
        """POST one chat completion and record usage.

        When ``require_usage`` is true, missing or invalid usage raises
        ``UsageNotAvailable`` after the invocation exists. Counts on that
        invocation stay null.
        """

        self._require_in_pool(model)
        metadata = _with_call_ids(metadata, invocation_id=invocation_id, model_id=model.model_id)
        request_body = build_chat_body(model.model_id, messages, metadata)
        headers = request_headers(metadata, api_key=self.api_key)
        status, response_headers, response_body = post_json(
            self.policy,
            self.chat_url(),
            request_body,
            headers,
            timeout=self.timeout,
        )
        invocation, reconciliation = record_invocation(
            invocation_id=invocation_id,
            execution_id=metadata.execution_id,
            seq=seq,
            model=model,
            request_body=dict(request_body),
            http_status=status,
            response_body=response_body,
            response_headers=response_headers,
            started_at=started_at,
            finished_at=finished_at,
        )
        self._trace(trace, invocation, reconciliation, metadata, record_id, recorded_at)
        if require_usage and invocation.usage.status is not UsageStatus.PRESENT:
            raise UsageNotAvailable(invocation, reconciliation)
        return invocation

    def _require_in_pool(self, model: ModelDefinition) -> None:
        if self.pool is None:
            return
        allowed = {item.model_id for item in self.pool.models}
        if model.model_id not in allowed:
            raise ModelNotInPool(f"{model.model_id} is not in pool {self.pool.pool_id}")

    def _trace(
        self,
        trace: TraceLog | None,
        invocation: ModelInvocation,
        reconciliation: Reconciliation,
        metadata: GatewayCallMetadata,
        record_id: str | None,
        recorded_at: datetime | None,
    ) -> None:
        if trace is None:
            return
        if record_id is None or recorded_at is None:
            raise TypeError("record_id and recorded_at are required when trace is set")
        append_invocation(
            trace,
            invocation,
            reconciliation,
            record_id=record_id,
            recorded_at=recorded_at,
            metadata=spend_logs_metadata(metadata),
        )


def _with_call_ids(
    metadata: GatewayCallMetadata,
    *,
    invocation_id: str,
    model_id: str,
) -> GatewayCallMetadata:
    return GatewayCallMetadata(
        benchmark_run_id=metadata.benchmark_run_id,
        execution_id=metadata.execution_id,
        task_id=metadata.task_id,
        harness_id=metadata.harness_id,
        purpose=metadata.purpose,
        invocation_id=metadata.invocation_id or invocation_id,
        model_id=metadata.model_id or model_id,
    )
