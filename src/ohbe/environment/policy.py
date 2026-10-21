"""Default-deny egress policy for the model-calling process.

The only allowlisted destination is the configured gateway host and port.
In the controlled track that gateway is LiteLLM. Step D does not import or
run LiteLLM; tests point the same allowlist at a mock HTTP listener.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

EGRESS_POLICY_VERSION = "ohbe-egress-0.1"

# Direct model-provider endpoints. The allowlist is still a single gateway:
# these names exist so a misconfigured gateway host is rejected, and so the
# release gate can name the calls that must fail.
DENIED_PROVIDER_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "api.groq.com",
        "api.mistral.ai",
        "api.cohere.ai",
        "api.cohere.com",
        "api.together.xyz",
        "api.together.ai",
        "openrouter.ai",
        "api.deepseek.com",
        "api.fireworks.ai",
        "api.perplexity.ai",
        "huggingface.co",
        "cdn-lfs.huggingface.co",
        "api-inference.huggingface.co",
    }
)

# Hosted LiteLLM. The controlled-track gateway is an operator proxy such as
# ohbe-gateway, never LiteLLM Cloud. A subdomain of these names is the same
# product. Match the registrable suffix, not a substring, so a lookalike
# domain that merely contains the letters is not classified as this cloud.
CLOUD_LITELLM_SUFFIXES = (
    "litellm.ai",
    "litellm.com",
    "litellm.cloud",
    "berri.ai",
)


class CloudLiteLLMDenied(ValueError):
    """A hosted LiteLLM origin was rejected before any socket was opened."""

    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(f"cloud LiteLLM endpoint is denied: {host}")


def is_cloud_litellm_host(host: str) -> bool:
    """True when `host` is a known hosted LiteLLM name or a subdomain of one."""

    normalized = normalize_host(host)
    if not normalized or is_ip_literal(normalized):
        return False
    for suffix in CLOUD_LITELLM_SUFFIXES:
        if normalized == suffix or normalized.endswith("." + suffix):
            return True
    return False


def normalize_host(host: str) -> str:
    text = host.strip().rstrip(".").lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError:
        return text
    return text


def is_denied_provider_host(host: str) -> bool:
    """True for a listed provider host and for OpenAI or Anthropic subdomains.

    `notopenai.com` is not a match. `foo.openai.com` and `api.anthropic.com` are.
    """

    normalized = normalize_host(host)
    if normalized in DENIED_PROVIDER_HOSTS:
        return True
    labels = normalized.split(".")
    if len(labels) < 2:
        return False
    tail = labels[-2:]
    return tail == ["openai", "com"] or tail == ["anthropic", "com"]


def is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class EgressVerdict:
    allowed: bool
    host: str
    port: int
    reason: str


class EgressDenied(PermissionError):
    """A destination was blocked before any packet was sent."""

    def __init__(self, verdict: EgressVerdict) -> None:
        self.verdict = verdict
        super().__init__(f"{verdict.reason} ({verdict.host}:{verdict.port})")


@dataclass(frozen=True)
class NetworkEgressPolicy:
    """Allow one gateway. Deny every other host and port."""

    gateway_host: str
    gateway_port: int
    version: str = EGRESS_POLICY_VERSION
    denied_provider_hosts: frozenset[str] = field(default_factory=lambda: DENIED_PROVIDER_HOSTS)

    def __post_init__(self) -> None:
        host = normalize_host(self.gateway_host)
        if not host or "://" in host or "/" in host or any(ch.isspace() for ch in host):
            raise ValueError("gateway_host must be a hostname or IP, not a URL")
        if not isinstance(self.gateway_port, int) or isinstance(self.gateway_port, bool):
            raise ValueError("gateway_port must be an integer")
        if self.gateway_port < 1 or self.gateway_port > 65535:
            raise ValueError("gateway_port is outside 1..65535")
        if not self.version:
            raise ValueError("egress policy version must be non-empty")
        denied = frozenset(normalize_host(item) for item in self.denied_provider_hosts)
        denied = denied | DENIED_PROVIDER_HOSTS
        if host in denied or is_denied_provider_host(host):
            raise ValueError("gateway host must not be a model-provider endpoint")
        if is_cloud_litellm_host(host):
            raise CloudLiteLLMDenied(host)
        object.__setattr__(self, "gateway_host", host)
        object.__setattr__(self, "denied_provider_hosts", denied)

    def evaluate(self, host: str, port: int) -> EgressVerdict:
        normalized = normalize_host(host)
        if not isinstance(port, int) or isinstance(port, bool) or port < 1 or port > 65535:
            return EgressVerdict(False, normalized, port, "destination port is not usable")
        if is_cloud_litellm_host(normalized):
            return EgressVerdict(False, normalized, port, "cloud LiteLLM endpoint is denied")
        if normalized == self.gateway_host and port == self.gateway_port:
            return EgressVerdict(True, normalized, port, "allowlisted gateway")
        if normalized in self.denied_provider_hosts or is_denied_provider_host(normalized):
            return EgressVerdict(
                False,
                normalized,
                port,
                "direct model-provider egress is denied",
            )
        return EgressVerdict(
            False,
            normalized,
            port,
            "destination is not the allowlisted gateway",
        )


def controlled_track_policy(*, gateway_host: str, gateway_port: int) -> NetworkEgressPolicy:
    """Policy for the controlled track: only the designated gateway."""

    return NetworkEgressPolicy(gateway_host=gateway_host, gateway_port=gateway_port)


@dataclass(frozen=True)
class HttpTarget:
    url: str
    scheme: str
    host: str
    port: int


def parse_http_target(url: str) -> HttpTarget:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname is None:
        raise ValueError(f"egress target must be an absolute http(s) URL, got {url!r}")
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return HttpTarget(url=url, scheme=parts.scheme, host=normalize_host(parts.hostname), port=port)


class EgressTransport(Protocol):
    def exchange(self, target: HttpTarget, body: bytes) -> bytes:
        """Deliver bytes to an already-authorized target."""


class EgressSimulator:
    """Apply the policy before a transport sees the destination.

    The transport used in tests accepts every host. A denied provider call
    must raise `EgressDenied` without invoking that transport.
    """

    def __init__(self, policy: NetworkEgressPolicy, transport: EgressTransport) -> None:
        self.policy = policy
        self.transport = transport

    def request(self, url: str, body: bytes = b"") -> bytes:
        target = parse_http_target(url)
        verdict = self.policy.evaluate(target.host, target.port)
        if not verdict.allowed:
            raise EgressDenied(verdict)
        return self.transport.exchange(target, body)
