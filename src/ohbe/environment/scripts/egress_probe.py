"""Raw urllib probe for the Docker egress release gate.

This process does not import OHBE. It uses the standard library so a bypass
of the Python guard is what gets measured. The gateway request must succeed.
Direct provider requests, and a literal public IP, must fail.
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

socket.setdefaulttimeout(float(os.environ.get("OHBE_PROBE_TIMEOUT", "3")))

GATEWAY_URL = os.environ.get("OHBE_GATEWAY_URL", "http://ohbe-gateway:8080/health")
PROVIDER_URLS = (
    "https://api.openai.com/v1/models",
    "https://api.anthropic.com/v1/messages",
)
LITERAL_URL = "http://1.1.1.1/"


def _once(name: str, url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=socket.getdefaulttimeout()) as response:
            payload = response.read(4096)
            return {
                "name": name,
                "url": url,
                "ok": True,
                "status": response.status,
                "body": payload.decode("utf-8", "replace"),
            }
    except Exception as exc:  # noqa: BLE001 - the probe must report every failure mode
        detail = str(exc)
        if isinstance(exc, urllib.error.URLError) and exc.reason is not None:
            detail = str(exc.reason)
        return {
            "name": name,
            "url": url,
            "ok": False,
            "error": type(exc).__name__,
            "detail": detail,
        }


def _attempt(name: str, url: str, *, retries: int) -> dict[str, object]:
    last: dict[str, object] = {"name": name, "url": url, "ok": False, "error": "not-run"}
    for attempt_index in range(retries):
        last = _once(name, url)
        if last["ok"] or attempt_index + 1 == retries:
            return last
        time.sleep(0.2)
    return last


def main() -> None:
    attempts = [_attempt("gateway", GATEWAY_URL, retries=20)]
    for url in PROVIDER_URLS:
        host = urlsplit(url).hostname or url
        attempts.append(_attempt(host, url, retries=1))
    attempts.append(_attempt("literal-bypass", LITERAL_URL, retries=1))
    print(json.dumps({"attempts": attempts}), flush=True)


if __name__ == "__main__":
    main()
