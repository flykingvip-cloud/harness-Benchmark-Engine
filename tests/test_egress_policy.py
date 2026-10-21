"""Egress policy: provider calls are blocked, the gateway mock is allowed."""

from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from ohbe.core import ExecutionEnvironment
from ohbe.environment import (
    DENIED_PROVIDER_HOSTS,
    EGRESS_POLICY_VERSION,
    EgressDenied,
    EgressGuard,
    EgressSimulator,
    controlled_track_policy,
)
from ohbe.environment.policy import HttpTarget, parse_http_target
from tests.samples import sample_execution, sample_experiment


class _GatewayHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b'{"ok":true,"role":"gateway"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        return


class _PermissiveTransport:
    """Would succeed for every host if the policy did not run first."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def exchange(self, target: HttpTarget, body: bytes) -> bytes:
        self.calls.append((target.host, target.port))
        return b'{"ok":true,"role":"gateway"}'


def test_policy_allowlists_only_the_gateway_host_and_port() -> None:
    policy = controlled_track_policy(gateway_host="OHBE-Gateway.", gateway_port=4000)
    assert policy.version == EGRESS_POLICY_VERSION
    assert policy.gateway_host == "ohbe-gateway"
    allowed = policy.evaluate("ohbe-gateway", 4000)
    assert allowed.allowed
    wrong_port = policy.evaluate("ohbe-gateway", 443)
    assert not wrong_port.allowed
    lookalike = policy.evaluate("ohbe-gateway.evil.test", 4000)
    assert not lookalike.allowed
    for host in DENIED_PROVIDER_HOSTS:
        verdict = policy.evaluate(host.upper(), 443)
        assert not verdict.allowed
        assert "model-provider" in verdict.reason


def test_provider_host_cannot_be_configured_as_the_gateway() -> None:
    with pytest.raises(ValueError, match="model-provider"):
        controlled_track_policy(gateway_host="api.openai.com", gateway_port=443)


def test_openai_and_anthropic_subdomains_are_denied() -> None:
    policy = controlled_track_policy(gateway_host="ohbe-gateway", gateway_port=4000)
    for host in ("foo.openai.com", "openai.com", "bar.anthropic.com", "anthropic.com"):
        verdict = policy.evaluate(host, 443)
        assert not verdict.allowed
        assert "model-provider" in verdict.reason
    lookalike = policy.evaluate("notopenai.com", 443)
    assert not lookalike.allowed
    assert "model-provider" not in lookalike.reason
    with pytest.raises(ValueError, match="model-provider"):
        controlled_track_policy(gateway_host="foo.openai.com", gateway_port=443)


def test_simulator_blocks_provider_calls_before_the_transport() -> None:
    policy = controlled_track_policy(gateway_host="ohbe-gateway", gateway_port=4000)
    transport = _PermissiveTransport()
    simulator = EgressSimulator(policy, transport)
    body = simulator.request("http://ohbe-gateway:4000/v1/chat/completions", body=b"{}")
    assert b"gateway" in body
    assert transport.calls == [("ohbe-gateway", 4000)]
    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "https://API.OPENAI.com/v1/models",
        "http://example.com/v1/chat/completions",
    ):
        with pytest.raises(EgressDenied):
            simulator.request(url)
    assert transport.calls == [("ohbe-gateway", 4000)]


def test_socket_guard_allows_gateway_mock_and_blocks_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GatewayHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    policy = controlled_track_policy(gateway_host="127.0.0.1", gateway_port=port)
    dns_calls: list[object] = []
    connect_calls: list[object] = []
    original_dns = socket.getaddrinfo
    original_connect = socket.socket.connect

    def spy_dns(host, dns_port, *args, **kwargs):  # type: ignore[no-untyped-def]
        dns_calls.append(host)
        return original_dns(host, dns_port, *args, **kwargs)

    def spy_connect(sock: socket.socket, address: object) -> None:
        connect_calls.append(address)
        original_connect(sock, address)

    socket.getaddrinfo = spy_dns  # type: ignore[assignment]
    socket.socket.connect = spy_connect  # type: ignore[method-assign]
    try:
        with EgressGuard(policy):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                payload = response.read()
            assert response.status == 200
            assert b"gateway" in payload
            posted = urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    data=b"{}",
                    method="POST",
                ),
                timeout=2,
            )
            with posted:
                assert b"gateway" in posted.read()
            for url in (
                "https://api.openai.com/v1/models",
                "https://api.anthropic.com/v1/messages",
            ):
                with pytest.raises(urllib.error.URLError) as caught:
                    urllib.request.urlopen(url, timeout=2)
                assert isinstance(caught.value.reason, EgressDenied)
            before = len(connect_calls)
            with pytest.raises(EgressDenied):
                socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("1.1.1.1", 443))
            assert len(connect_calls) == before
        assert "api.openai.com" not in dns_calls
        assert "api.anthropic.com" not in dns_calls
        assert "127.0.0.1" in dns_calls
    finally:
        socket.getaddrinfo = original_dns  # type: ignore[assignment]
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        server.shutdown()
        server.server_close()


def test_execution_records_policy_version_and_image_digest() -> None:
    environment = ExecutionEnvironment(
        egress_policy_version=EGRESS_POLICY_VERSION,
        gateway_host="ohbe-gateway",
        gateway_port=8080,
        image_ref="python:3.12-alpine",
        image_digest="sha256:" + "cd" * 32,
    )
    execution = sample_execution(environment=environment)
    experiment = sample_experiment(environment=environment)
    assert execution.environment == environment
    assert experiment.environment == environment
    assert execution.execution_hash != sample_execution().execution_hash
    other = sample_execution(
        environment=environment.model_copy(update={"egress_policy_version": "ohbe-egress-other"})
    )
    assert other.execution_hash != execution.execution_hash
    with pytest.raises(ValidationError):
        ExecutionEnvironment(
            egress_policy_version=EGRESS_POLICY_VERSION,
            gateway_host="ohbe-gateway",
            gateway_port=8080,
            image_digest="sha256:abcd",
        )
    with pytest.raises(ValidationError):
        ExecutionEnvironment(
            egress_policy_version=EGRESS_POLICY_VERSION,
            gateway_host="https://ohbe-gateway",
            gateway_port=8080,
        )


def test_parse_http_target_rejects_relative_urls() -> None:
    with pytest.raises(ValueError):
        parse_http_target("/v1/models")
    target = parse_http_target("https://api.openai.com/v1/models")
    assert target.host == "api.openai.com"
    assert target.port == 443
