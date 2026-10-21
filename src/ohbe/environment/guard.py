"""Process-wide socket guard for the model-calling process.

`socket.getaddrinfo` and `socket.socket.connect` are wrapped while the guard
is active. Disallowed names are rejected before DNS. A connect to an address
that was not produced by resolving the gateway is rejected before the kernel
sends a packet.
"""

from __future__ import annotations

import socket
from types import TracebackType

from ohbe.environment.policy import EgressDenied, EgressVerdict, NetworkEgressPolicy, is_ip_literal


class EgressGuard:
    """Install the egress policy on this process's standard sockets."""

    def __init__(self, policy: NetworkEgressPolicy) -> None:
        self.policy = policy
        self._allowed_peers: set[tuple[str, int]] = set()
        if is_ip_literal(policy.gateway_host):
            self._allowed_peers.add((policy.gateway_host, policy.gateway_port))
        self._original_getaddrinfo = None
        self._original_connect = None
        # A plain function on the class is bound to the socket. A bound
        # EgressGuard method would swallow that socket argument.
        self._installed_connect = None

    def __enter__(self) -> EgressGuard:
        if getattr(socket, "_ohbe_egress_guard", None) is not None:
            raise RuntimeError("an OHBE egress guard is already installed")
        self._original_getaddrinfo = socket.getaddrinfo
        self._original_connect = socket.socket.connect

        def connect(sock: socket.socket, address: object) -> None:
            self._connect(sock, address)

        self._installed_connect = connect
        socket.getaddrinfo = self._getaddrinfo  # type: ignore[assignment]
        socket.socket.connect = connect  # type: ignore[method-assign]
        socket._ohbe_egress_guard = self  # type: ignore[attr-defined]
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._original_getaddrinfo is not None:
            socket.getaddrinfo = self._original_getaddrinfo  # type: ignore[assignment]
        if self._original_connect is not None:
            socket.socket.connect = self._original_connect  # type: ignore[method-assign]
        socket._ohbe_egress_guard = None  # type: ignore[attr-defined]

    def _getaddrinfo(self, host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        assert self._original_getaddrinfo is not None
        if host is not None:
            self._authorize_name(host, port)
        results = self._original_getaddrinfo(host, port, *args, **kwargs)
        if host is not None and self.policy.evaluate(*_name_and_port(host, port)).allowed:
            for item in results:
                sockaddr = item[4]
                self._allowed_peers.add((str(sockaddr[0]), int(sockaddr[1])))
        return results

    def _connect(self, sock: socket.socket, address: object) -> None:
        assert self._original_connect is not None
        peer = _peer_from_address(address)
        if peer is not None and peer not in self._allowed_peers:
            host, port = peer
            verdict = self.policy.evaluate(host, port) if _port_ok(port) else EgressVerdict(
                False,
                host,
                port,
                "destination is not the allowlisted gateway",
            )
            if verdict.allowed and (host, port) not in self._allowed_peers:
                verdict = EgressVerdict(
                    False,
                    host,
                    port,
                    "destination is not the allowlisted gateway",
                )
            raise EgressDenied(verdict)
        self._original_connect(sock, address)

    def _authorize_name(self, host: object, port: object) -> None:
        text, number = _name_and_port(host, port)
        verdict = self.policy.evaluate(text, number)
        if not verdict.allowed:
            raise EgressDenied(verdict)


def _port_ok(port: int) -> bool:
    return 1 <= port <= 65535


def _coerce_port(port: object) -> int:
    if isinstance(port, int) and not isinstance(port, bool):
        return port
    if isinstance(port, str) and port.isdigit():
        return int(port)
    return 0


def _coerce_host(host: object) -> str:
    if isinstance(host, bytes):
        return host.decode("utf-8", "replace")
    return str(host)


def _name_and_port(host: object, port: object) -> tuple[str, int]:
    return _coerce_host(host), _coerce_port(port)


def _peer_from_address(address: object) -> tuple[str, int] | None:
    if not isinstance(address, tuple) or len(address) < 2:
        return None
    host, port = address[0], address[1]
    if isinstance(port, int) and not isinstance(port, bool) and isinstance(host, str):
        return host, port
    return None
