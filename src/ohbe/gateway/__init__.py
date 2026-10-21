"""LiteLLM OSS gateway adapter for the controlled track.

Core schemas do not import this package. The client sends chat completions
only to the egress-allowlisted proxy and records token usage from the
response. Proxy response cost is kept as a spend signal, not as an OHBE price.
"""

from ohbe.gateway.client import GatewayClient, gateway_origin, post_json
from ohbe.gateway.config import (
    gateway_environment,
    proxy_config_document,
    render_proxy_config,
    virtual_key_request,
)
from ohbe.gateway.constants import LITELLM_IMAGE, LITELLM_VERSION
from ohbe.gateway.metadata import GatewayCallMetadata, spend_logs_metadata
from ohbe.gateway.record import (
    GatewayError,
    ModelNotInPool,
    UsageNotAvailable,
    append_invocation,
    record_invocation,
)
from ohbe.gateway.reconcile import Reconciliation, SpendSignal, reconcile_response

__all__ = [
    "LITELLM_IMAGE",
    "LITELLM_VERSION",
    "GatewayCallMetadata",
    "GatewayClient",
    "GatewayError",
    "ModelNotInPool",
    "Reconciliation",
    "SpendSignal",
    "UsageNotAvailable",
    "append_invocation",
    "gateway_environment",
    "gateway_origin",
    "post_json",
    "proxy_config_document",
    "reconcile_response",
    "record_invocation",
    "render_proxy_config",
    "spend_logs_metadata",
    "virtual_key_request",
]
