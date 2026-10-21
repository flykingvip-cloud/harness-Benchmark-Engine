"""Gateway client: metadata echo, usage capture, and provider bypass."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ohbe.core.pricing import CostStatus
from ohbe.core.trace import TraceLog
from ohbe.core.usage import UsageStatus
from ohbe.environment.guard import EgressGuard
from ohbe.environment.policy import EgressDenied, controlled_track_policy
from ohbe.gateway.client import GatewayClient, post_json
from ohbe.gateway.constants import LITELLM_VERSION
from ohbe.gateway.metadata import GatewayCallMetadata, decode_spend_logs_metadata
from ohbe.gateway.record import ModelNotInPool, UsageNotAvailable, record_invocation
from ohbe.gateway.version import installed_litellm_version
from tests.samples import NOW, sample_model, sample_pool


class _Proxy(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.server.hits += 1  # type: ignore[attr-defined]
        self.server.captured = {  # type: ignore[attr-defined]
            "path": self.path,
            "body": json.loads(raw.decode("utf-8")),
            "spend_header": self.headers.get("x-litellm-spend-logs-metadata"),
            "tags": self.headers.get("x-litellm-tags"),
        }
        mode = self.server.mode  # type: ignore[attr-defined]
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://api.openai.com/v1/chat/completions")
            self.end_headers()
            return
        if mode == "missing":
            payload = {"id": "chatcmpl-missing", "choices": [{"message": {"role": "assistant", "content": "hi"}}]}
            cost = "0"
        else:
            payload = {
                "id": "chatcmpl-ok",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
            cost = "0.000214"
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("x-litellm-response-cost", cost)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch):
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Proxy)
    server.hits = 0  # type: ignore[attr-defined]
    server.captured = None  # type: ignore[attr-defined]
    server.mode = "ok"  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    policy = controlled_track_policy(gateway_host="127.0.0.1", gateway_port=port)
    try:
        yield server, policy
    finally:
        server.shutdown()
        server.server_close()


def _metadata() -> GatewayCallMetadata:
    return GatewayCallMetadata(
        benchmark_run_id="exp-1",
        execution_id="exec-1",
        task_id="task-001",
        harness_id="harness-echo",
        purpose="benchmark",
    )


def test_client_records_usage_and_echoes_metadata(proxy) -> None:
    server, policy = proxy
    client = GatewayClient(policy=policy, api_key="sk-ohbe-test", pool=sample_pool())
    invocation = client.complete(
        model=sample_model(),
        messages=[{"role": "user", "content": "hi"}],
        metadata=_metadata(),
        invocation_id="inv-1",
        seq=0,
        started_at=NOW,
        finished_at=NOW,
    )
    captured = server.captured
    decoded = decode_spend_logs_metadata(captured["spend_header"])
    assert captured["path"] == "/v1/chat/completions"
    assert decoded["benchmark_run_id"] == "exp-1"
    assert decoded["execution_id"] == "exec-1"
    assert decoded["invocation_id"] == "inv-1"
    assert decoded["model_id"] == "openai/gpt-test"
    assert captured["body"]["metadata"]["spend_logs_metadata"] == decoded
    assert "purpose:benchmark" in captured["tags"]
    assert invocation.usage.prompt_tokens == 3
    assert invocation.usage.total_tokens == 4
    assert invocation.cost.amount is None
    assert invocation.cost.status is CostStatus.NOT_COMPUTED
    assert invocation.request["model"] == "openai/gpt-test"


def test_missing_usage_fails_closed_and_is_traced(proxy, tmp_path) -> None:
    server, policy = proxy
    server.mode = "missing"
    client = GatewayClient(policy=policy, api_key="sk-ohbe-test")
    log = TraceLog(tmp_path / "trace.jsonl")
    with pytest.raises(UsageNotAvailable) as caught:
        client.complete(
            model=sample_model(),
            messages=[{"role": "user", "content": "hi"}],
            metadata=_metadata(),
            invocation_id="inv-2",
            seq=0,
            started_at=NOW,
            finished_at=NOW,
            trace=log,
            record_id="rec-0",
            recorded_at=NOW,
        )
    invocation = caught.value.invocation
    assert invocation.usage.status is UsageStatus.MISSING
    assert invocation.usage.prompt_tokens is None
    assert invocation.cost.amount is None
    assert invocation.cost.status is CostStatus.INVALID
    assert caught.value.reconciliation.spend_signal.amount == "0"
    record = log.read()[0]
    assert record.payload["prompt_tokens"] is None
    assert record.payload["cost_amount"] is None
    assert record.payload["metadata"]["task_id"] == "task-001"
    kept = client.complete(
        model=sample_model(),
        messages=[{"role": "user", "content": "hi"}],
        metadata=_metadata(),
        invocation_id="inv-3",
        seq=1,
        started_at=NOW,
        finished_at=NOW,
        require_usage=False,
    )
    assert kept.usage.prompt_tokens is None
    assert kept.cost.amount is None


def test_provider_endpoints_and_redirects_are_denied(proxy) -> None:
    server, policy = proxy
    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "http://1.1.1.1/v1/chat/completions",
    ):
        with pytest.raises(EgressDenied):
            post_json(policy, url, {"model": "gpt-test"}, {"Content-Type": "application/json"})
    assert server.hits == 0
    server.mode = "redirect"
    client = GatewayClient(policy=policy, api_key="sk-ohbe-test")
    from ohbe.gateway.record import GatewayTransportError

    with pytest.raises(GatewayTransportError, match="redirect"):
        client.complete(
            model=sample_model(),
            messages=[{"role": "user", "content": "hi"}],
            metadata=_metadata(),
            invocation_id="inv-4",
            seq=0,
            started_at=NOW,
            finished_at=NOW,
        )


def test_model_outside_the_pool_is_not_sent(proxy) -> None:
    server, policy = proxy
    client = GatewayClient(policy=policy, api_key="sk-ohbe-test", pool=sample_pool())
    outsider = sample_model(model_id="other/model", provider="other", name="model")
    with pytest.raises(ModelNotInPool):
        client.complete(
            model=outsider,
            messages=[{"role": "user", "content": "hi"}],
            metadata=_metadata(),
            invocation_id="inv-5",
            seq=0,
        )
    assert server.hits == 0


def _caused_by_egress_denial(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, EgressDenied):
            return True
        current = current.__cause__ or current.__context__
    return False


def test_litellm_sdk_records_gateway_usage_and_cannot_call_providers(
    proxy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    version = installed_litellm_version()
    if version is None:
        pytest.skip("litellm extra is not installed")
    assert version == LITELLM_VERSION
    import litellm

    litellm.telemetry = False
    server, policy = proxy
    messages = [{"role": "user", "content": "hi"}]
    with EgressGuard(policy):
        response = litellm.completion(
            model="openai/gpt-test",
            messages=messages,
            api_base=f"http://127.0.0.1:{server.server_address[1]}/v1",
            api_key="sk-ohbe-test",
            extra_headers={"x-litellm-spend-logs-metadata": '{"purpose":"benchmark"}'},
        )
    invocation, reconciliation = record_invocation(
        invocation_id="inv-sdk",
        execution_id="exec-1",
        seq=0,
        model=sample_model(),
        request_body={"model": "openai/gpt-test", "messages": messages},
        http_status=200,
        response_body={
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        },
        response_headers={"x-litellm-response-cost": "0.000214"},
        started_at=NOW,
        finished_at=NOW,
    )
    assert invocation.usage.prompt_tokens == 3
    assert invocation.usage.total_tokens == 4
    assert invocation.cost.amount is None
    assert reconciliation.spend_signal.amount == "0.000214"
    assert server.captured["spend_header"] == '{"purpose":"benchmark"}'
    with pytest.raises(Exception) as caught:
        with EgressGuard(policy):
            litellm.completion(
                model="openai/gpt-test",
                messages=messages,
                api_key="sk-ohbe-test",
            )
    assert _caused_by_egress_denial(caught.value)
