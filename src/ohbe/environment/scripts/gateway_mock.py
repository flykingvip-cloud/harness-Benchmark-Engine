"""HTTP stand-in for the LiteLLM gateway.

Step E replaces this process with a pinned LiteLLM image. Step D only needs
a listener on the allowlisted host and port. This file is copied into the
gateway container and must keep working without the OHBE package.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("OHBE_GATEWAY_PORT", "8080"))


class GatewayHandler(BaseHTTPRequestHandler):
    def _respond(self) -> None:
        body = json.dumps({"ok": True, "role": "gateway"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._respond()

    def do_POST(self) -> None:  # noqa: N802
        self._respond()

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler).serve_forever()


if __name__ == "__main__":
    main()
