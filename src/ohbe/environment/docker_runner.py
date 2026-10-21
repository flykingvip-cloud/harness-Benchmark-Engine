"""Docker runner that isolates the process talking to models.

The runner creates an internal Docker network, starts the gateway mock, and
runs a raw urllib probe on that network. Internal networks have no external
route. When the host's legacy iptables FORWARD policy is DROP, same-bridge
packets are dropped too; the runner then inserts a same-bridge ACCEPT so the
gateway stays reachable without opening provider egress.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from ohbe.core.execution import ExecutionEnvironment
from ohbe.environment.policy import NetworkEgressPolicy, normalize_host

DEFAULT_RUNNER_IMAGE = "python:3.12-alpine"
GATEWAY_ALIAS = "ohbe-gateway"
GATEWAY_PORT = 8080
_SCRIPTS = Path(__file__).resolve().parent / "scripts"
_REQUIRED_DENIED_HOSTS = ("api.openai.com", "api.anthropic.com")


class DockerRunnerError(RuntimeError):
    """The Docker runner could not complete an isolation probe."""


class EgressBypassError(DockerRunnerError):
    """A request outside the gateway succeeded. This fails the release gate."""


@dataclass(frozen=True)
class ProbeAttempt:
    name: str
    url: str
    ok: bool
    status: int | None = None
    error: str | None = None
    detail: str | None = None
    body: str | None = None


@dataclass(frozen=True)
class DockerProbeReport:
    attempts: tuple[ProbeAttempt, ...]
    environment: ExecutionEnvironment

    @property
    def gateway_ok(self) -> bool:
        return any(attempt.name == "gateway" and attempt.ok for attempt in self.attempts)

    def attempts_for_host(self, host: str) -> tuple[ProbeAttempt, ...]:
        normalized = normalize_host(host)
        return tuple(
            attempt
            for attempt in self.attempts
            if attempt.url and normalize_host(_hostname(attempt.url)) == normalized
        )


def docker_available(docker_bin: str = "docker") -> bool:
    try:
        result = subprocess.run(
            [docker_bin, "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def network_create_args(name: str) -> list[str]:
    return ["network", "create", "--internal", name]


def gateway_run_args(
    *,
    name: str,
    network: str,
    image: str,
    script_path: Path,
    port: int = GATEWAY_PORT,
) -> list[str]:
    return [
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        GATEWAY_ALIAS,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        f"OHBE_GATEWAY_PORT={port}",
        "-v",
        f"{script_path}:/opt/ohbe/gateway_mock.py:ro",
        image,
        "python",
        "-u",
        "/opt/ohbe/gateway_mock.py",
    ]


def probe_run_args(
    *,
    name: str,
    network: str,
    image: str,
    script_path: Path,
    gateway_url: str,
) -> list[str]:
    return [
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        network,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        f"OHBE_GATEWAY_URL={gateway_url}",
        "-v",
        f"{script_path}:/opt/ohbe/egress_probe.py:ro",
        image,
        "python",
        "-u",
        "/opt/ohbe/egress_probe.py",
    ]


def network_bridge_name(inspect_obj: dict[str, object]) -> str:
    options = inspect_obj.get("Options") or {}
    if isinstance(options, dict):
        named = options.get("com.docker.network.bridge.name")
        if isinstance(named, str) and named:
            return named
    network_id = inspect_obj.get("Id")
    if not isinstance(network_id, str) or len(network_id) < 12:
        raise DockerRunnerError("Docker network inspect did not include an id")
    return "br-" + network_id[:12]


def legacy_same_bridge_accept_args(bridge: str, *, delete: bool = False) -> list[str]:
    action = "-D" if delete else "-I"
    args = ["iptables-legacy", action, "DOCKER-FORWARD"]
    if not delete:
        args.append("1")
    args.extend(["-i", bridge, "-o", bridge, "-j", "ACCEPT"])
    return args


def parse_image_digest(inspect_obj: dict[str, object]) -> str:
    digests = inspect_obj.get("RepoDigests") or []
    if isinstance(digests, list):
        for item in digests:
            if isinstance(item, str) and "@sha256:" in item:
                return "sha256:" + item.split("@sha256:", 1)[1]
    image_id = inspect_obj.get("Id")
    if isinstance(image_id, str) and image_id.startswith("sha256:") and len(image_id) == 7 + 64:
        return image_id
    raise DockerRunnerError("image inspect did not include a sha256 digest")


def interpret_probe_output(
    stdout: str,
    *,
    policy: NetworkEgressPolicy,
    image_ref: str,
    image_digest: str,
) -> DockerProbeReport:
    """Turn probe JSON into a report, or raise if the release gate failed."""

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DockerRunnerError(f"egress probe did not return JSON: {exc}") from exc
    raw_attempts = payload.get("attempts") if isinstance(payload, dict) else None
    if not isinstance(raw_attempts, list) or not raw_attempts:
        raise DockerRunnerError("egress probe returned no attempts")
    attempts = tuple(_parse_attempt(item) for item in raw_attempts)
    gateway = [attempt for attempt in attempts if attempt.name == "gateway"]
    if len(gateway) != 1 or not gateway[0].ok or gateway[0].status != 200:
        raise DockerRunnerError("allowlisted gateway mock was not reachable")
    if gateway[0].body is None or "gateway" not in gateway[0].body:
        raise DockerRunnerError("gateway mock returned an unexpected body")
    for host in _REQUIRED_DENIED_HOSTS:
        verdict = policy.evaluate(host, 443)
        if verdict.allowed:
            raise DockerRunnerError(f"policy allowlists provider host {host}")
        matched = [attempt for attempt in attempts if _attempt_host(attempt) == host]
        if not matched:
            raise DockerRunnerError(f"egress probe did not attempt {host}")
        leaked = [attempt for attempt in matched if attempt.ok]
        if leaked:
            raise EgressBypassError(
                f"direct request to {host} succeeded; gateway bypass is not blocked"
            )
    literal = [attempt for attempt in attempts if attempt.name == "literal-bypass"]
    if not literal or any(attempt.ok for attempt in literal):
        raise EgressBypassError("literal public IP request was not blocked")
    environment = ExecutionEnvironment(
        egress_policy_version=policy.version,
        gateway_host=policy.gateway_host,
        gateway_port=policy.gateway_port,
        image_ref=image_ref,
        image_digest=image_digest,
    )
    return DockerProbeReport(attempts=attempts, environment=environment)


class DockerEgressRunner:
    """Run one egress probe under the internal-network policy."""

    def __init__(
        self,
        policy: NetworkEgressPolicy,
        *,
        image: str = DEFAULT_RUNNER_IMAGE,
        docker_bin: str = "docker",
    ) -> None:
        self.policy = policy
        self.image = image
        self.docker_bin = docker_bin

    def run_probe(self) -> DockerProbeReport:
        if self.policy.gateway_host != GATEWAY_ALIAS or self.policy.gateway_port != GATEWAY_PORT:
            raise DockerRunnerError(
                f"Docker probe allowlist must be {GATEWAY_ALIAS}:{GATEWAY_PORT}"
            )
        suffix = uuid.uuid4().hex[:10]
        network = f"ohbe-egress-{suffix}"
        gateway_name = f"ohbe-gw-{suffix}"
        probe_name = f"ohbe-probe-{suffix}"
        bridge: str | None = None
        inserted_rule = False
        gateway_started = False
        try:
            _check(_docker([*network_create_args(network)], docker_bin=self.docker_bin), "network create")
            inspected = json.loads(
                _check(
                    _docker(["network", "inspect", network], docker_bin=self.docker_bin),
                    "network inspect",
                ).stdout
            )[0]
            bridge = network_bridge_name(inspected)
            inserted_rule = _ensure_same_bridge_forward(bridge)
            _check(
                _docker(
                    gateway_run_args(
                        name=gateway_name,
                        network=network,
                        image=self.image,
                        script_path=_SCRIPTS / "gateway_mock.py",
                    ),
                    docker_bin=self.docker_bin,
                ),
                "gateway start",
            )
            gateway_started = True
            probe = _check(
                _docker(
                    probe_run_args(
                        name=probe_name,
                        network=network,
                        image=self.image,
                        script_path=_SCRIPTS / "egress_probe.py",
                        gateway_url=f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}/health",
                    ),
                    docker_bin=self.docker_bin,
                    timeout=60,
                ),
                "egress probe",
            )
            digest = parse_image_digest(_inspect_image(self.image, docker_bin=self.docker_bin))
            return interpret_probe_output(
                probe.stdout,
                policy=self.policy,
                image_ref=self.image,
                image_digest=digest,
            )
        finally:
            if gateway_started:
                _docker(["rm", "-f", gateway_name], docker_bin=self.docker_bin)
            _docker(["rm", "-f", probe_name], docker_bin=self.docker_bin)
            if inserted_rule and bridge is not None:
                _host(legacy_same_bridge_accept_args(bridge, delete=True))
            _docker(["network", "rm", network], docker_bin=self.docker_bin)


def _parse_attempt(item: object) -> ProbeAttempt:
    if not isinstance(item, dict):
        raise DockerRunnerError("egress probe attempt was not an object")
    name = item.get("name")
    url = item.get("url")
    ok = item.get("ok")
    if not isinstance(name, str) or not isinstance(url, str) or not isinstance(ok, bool):
        raise DockerRunnerError("egress probe attempt is missing name, url, or ok")
    status = item.get("status")
    error = item.get("error")
    detail = item.get("detail")
    body = item.get("body")
    return ProbeAttempt(
        name=name,
        url=url,
        ok=ok,
        status=status if isinstance(status, int) else None,
        error=error if isinstance(error, str) else None,
        detail=detail if isinstance(detail, str) else None,
        body=body if isinstance(body, str) else None,
    )


def _hostname(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def _attempt_host(attempt: ProbeAttempt) -> str:
    return normalize_host(_hostname(attempt.url))


def _inspect_image(image: str, *, docker_bin: str) -> dict[str, object]:
    result = _check(_docker(["image", "inspect", image], docker_bin=docker_bin), "image inspect")
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise DockerRunnerError("image inspect returned no object")
    return payload[0]


def _ensure_same_bridge_forward(bridge: str) -> bool:
    """Insert a same-bridge ACCEPT when legacy iptables would drop it.

    Returns whether this call inserted a rule that the caller must delete.
    """

    if not _legacy_forward_drops():
        return False
    _host(legacy_same_bridge_accept_args(bridge))
    return True


def _legacy_forward_drops() -> bool:
    result = _host(["iptables-legacy", "-S", "FORWARD"], check=False)
    if result.returncode != 0:
        return False
    return any(line.strip() == "-P FORWARD DROP" for line in result.stdout.splitlines())


def _host(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode != 0 and os.geteuid() != 0:
        sudo = subprocess.run(["sudo", "-n", *args], check=False, capture_output=True, text=True)
        if sudo.returncode == 0 or not check:
            return sudo
        result = sudo
    if check and result.returncode != 0:
        raise DockerRunnerError(result.stderr.strip() or f"command failed: {args[0]}")
    return result


def _docker(
    args: list[str],
    *,
    docker_bin: str,
    timeout: float | None = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [docker_bin, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DockerRunnerError(f"docker {' '.join(args[:2])} timed out") from exc
    except OSError as exc:
        raise DockerRunnerError(str(exc)) from exc


def _check(result: subprocess.CompletedProcess[str], action: str) -> subprocess.CompletedProcess[str]:
    if result.returncode == 0:
        return result
    detail = (result.stderr or result.stdout or "").strip()
    raise DockerRunnerError(f"{action} failed: {detail}")

