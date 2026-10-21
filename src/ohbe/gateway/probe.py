"""Scoped GET probe for LiteLLM health and model listing.

This client has no chat-completions method. ``/v1/chat/completions`` is refused
before a socket is opened. When a credential is present it is sent only as
``Authorization: Bearer`` on ``GET /v1/models``. It is never placed in the URL.
An unauthenticated ``/health/liveliness`` GET is not used once a key is present.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

HEALTH_PROBE_PATH = "/health/liveliness"
LIST_PROBE_PATH = "/v1/models"
_PROBE_PATHS = frozenset({HEALTH_PROBE_PATH, LIST_PROBE_PATH})


class ProbeError(Exception):
    """The scoped probe did not complete. No completion was requested."""


@dataclass(frozen=True)
class GatewayUrlParts:
    """Pieces of a gateway URL. Userinfo stays off any later error string."""

    scheme: str
    path: str
    username: str | None
    password: str | None
    query: str
    fragment: str
    hostname: str | None


def split_gateway_url(url: str) -> GatewayUrlParts:
    parts = urlsplit(url.strip())
    return GatewayUrlParts(
        scheme=parts.scheme,
        path=parts.path,
        username=parts.username,
        password=parts.password,
        query=parts.query,
        fragment=parts.fragment,
        hostname=parts.hostname,
    )


def guard_probe_url(url: str) -> None:
    """Refuse every URL that is not the scoped health or listing GET."""

    parts = split_gateway_url(url)
    if parts.scheme not in {"http", "https"} or parts.hostname is None:
        raise ProbeError("probe URL is not an http(s) origin")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ProbeError("probe URL must not carry userinfo, a query, or a fragment")
    if parts.path not in _PROBE_PATHS:
        raise ProbeError("probe path is not the scoped health or listing endpoint")


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
        raise ProbeError("gateway redirect is not allowed")


class UrllibProbe:
    """GET the scoped probe path. This client has no chat-completions method."""

    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        guard_probe_url(url)
        headers = {"Accept": "application/json"}
        if bearer is not None:
            if not bearer or any(character.isspace() for character in bearer):
                raise ProbeError("probe credential was rejected")
            headers["Authorization"] = f"Bearer {bearer}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect)
        try:
            with opener.open(request, timeout=timeout) as response:
                status = int(response.status)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            try:
                raw = exc.read()
            except (http.client.RemoteDisconnected, ConnectionError, TimeoutError) as read_exc:
                raise ProbeError("gateway unreachable") from read_exc
        except ProbeError:
            raise
        except urllib.error.URLError as exc:
            raise ProbeError("gateway unreachable") from exc
        except (http.client.RemoteDisconnected, ConnectionError, TimeoutError) as exc:
            # RemoteDisconnected is ConnectionResetError, not URLError; map it
            # like other unreachable cases so callers return connectivity_failed
            # instead of letting a bare exception become HTTP 500.
            raise ProbeError("gateway unreachable") from exc
        if not raw:
            return status, {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProbeError("probe response is not JSON") from exc
        if not isinstance(parsed, dict):
            raise ProbeError("probe response is not a JSON object")
        return status, parsed
