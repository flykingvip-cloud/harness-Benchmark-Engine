"""Pinned LiteLLM OSS gateway.

The benchmark process does not call provider SDKs. It calls this proxy, and
Step D egress allows only that proxy host. The image is the OSS build; the
enterprise image is not a substitute.
"""

from __future__ import annotations

LITELLM_VERSION = "1.104.2"
LITELLM_IMAGE = "ghcr.io/berriai/litellm:v1.104.2"
LITELLM_GATEWAY_PORT = 4000
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

SPEND_LOGS_METADATA_HEADER = "x-litellm-spend-logs-metadata"
TAGS_HEADER = "x-litellm-tags"
RESPONSE_COST_HEADER = "x-litellm-response-cost"
