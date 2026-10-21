"""Docker egress runner and the release-gate interpretation of probe output."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ohbe.environment.docker_runner import (
    DEFAULT_RUNNER_IMAGE,
    GATEWAY_ALIAS,
    GATEWAY_PORT,
    DockerEgressRunner,
    EgressBypassError,
    docker_available,
    gateway_run_args,
    interpret_probe_output,
    legacy_same_bridge_accept_args,
    network_bridge_name,
    network_create_args,
    parse_image_digest,
    probe_run_args,
)
from ohbe.environment.policy import EGRESS_POLICY_VERSION, controlled_track_policy

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "src" / "ohbe" / "environment" / "scripts" / "egress_probe.py"
COMPOSE = ROOT / "docker" / "compose.yaml"
DIGEST = "sha256:" + "ab" * 32


def _policy():
    return controlled_track_policy(gateway_host=GATEWAY_ALIAS, gateway_port=GATEWAY_PORT)


def _attempt(name: str, url: str, *, ok: bool, status: int | None = None, body: str | None = None) -> dict:
    item: dict[str, object] = {"name": name, "url": url, "ok": ok}
    if status is not None:
        item["status"] = status
    if body is not None:
        item["body"] = body
    if not ok:
        item["error"] = "URLError"
        item["detail"] = "blocked"
    return item


def _report_json(*, openai_ok: bool = False, anthropic_ok: bool = False, literal_ok: bool = False) -> str:
    return json.dumps(
        {
            "attempts": [
                _attempt(
                    "gateway",
                    f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}/health",
                    ok=True,
                    status=200,
                    body='{"ok": true, "role": "gateway"}',
                ),
                _attempt("api.openai.com", "https://api.openai.com/v1/models", ok=openai_ok, status=200 if openai_ok else None),
                _attempt(
                    "api.anthropic.com",
                    "https://api.anthropic.com/v1/messages",
                    ok=anthropic_ok,
                    status=200 if anthropic_ok else None,
                ),
                _attempt("literal-bypass", "http://1.1.1.1/", ok=literal_ok, status=200 if literal_ok else None),
            ]
        }
    )


def test_runner_requests_an_internal_network_and_not_the_host_network() -> None:
    assert network_create_args("ohbe-egress-test") == [
        "network",
        "create",
        "--internal",
        "ohbe-egress-test",
    ]
    probe = probe_run_args(
        name="probe",
        network="ohbe-egress-test",
        image=DEFAULT_RUNNER_IMAGE,
        script_path=PROBE,
        gateway_url=f"http://{GATEWAY_ALIAS}:{GATEWAY_PORT}/health",
    )
    assert probe[probe.index("--network") + 1] == "ohbe-egress-test"
    assert "host" not in probe
    gateway = gateway_run_args(
        name="gateway",
        network="ohbe-egress-test",
        image=DEFAULT_RUNNER_IMAGE,
        script_path=PROBE.parent / "gateway_mock.py",
    )
    assert "--network-alias" in gateway
    assert GATEWAY_ALIAS in gateway
    assert "--cap-drop" in probe and "ALL" in probe


def test_legacy_forward_rule_is_limited_to_the_same_bridge() -> None:
    args = legacy_same_bridge_accept_args("br-abcdef123456")
    assert args[:2] == ["iptables-legacy", "-I"]
    assert args.count("br-abcdef123456") == 2
    assert "ACCEPT" in args
    assert "DROP" not in args
    deleted = legacy_same_bridge_accept_args("br-abcdef123456", delete=True)
    assert "-D" in deleted
    assert "1" not in deleted


def test_bridge_name_comes_from_the_network_id() -> None:
    assert network_bridge_name({"Id": "abcdef1234567890", "Options": {}}) == "br-abcdef123456"
    assert (
        network_bridge_name(
            {"Id": "abcdef1234567890", "Options": {"com.docker.network.bridge.name": "ohbe0"}}
        )
        == "ohbe0"
    )


def test_image_digest_accepts_repo_digest_or_image_id() -> None:
    assert parse_image_digest({"RepoDigests": [f"python@{DIGEST}"], "Id": "sha256:" + "cd" * 32}) == DIGEST
    assert parse_image_digest({"RepoDigests": [], "Id": DIGEST}) == DIGEST


def test_probe_script_calls_providers_without_importing_ohbe() -> None:
    source = PROBE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name == "ohbe" or name.startswith("ohbe.") for name in imported)
    assert "https://api.openai.com/v1/models" in source
    assert "https://api.anthropic.com/v1/messages" in source
    assert "http://1.1.1.1/" in source


def test_compose_network_is_internal() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "internal: true" in text
    assert "ohbe-gateway" in text


def test_interpret_records_digest_when_provider_requests_fail() -> None:
    report = interpret_probe_output(
        _report_json(),
        policy=_policy(),
        image_ref=DEFAULT_RUNNER_IMAGE,
        image_digest=DIGEST,
    )
    assert report.gateway_ok
    assert report.environment.egress_policy_version == EGRESS_POLICY_VERSION
    assert report.environment.gateway_host == GATEWAY_ALIAS
    assert report.environment.gateway_port == GATEWAY_PORT
    assert report.environment.image_ref == DEFAULT_RUNNER_IMAGE
    assert report.environment.image_digest == DIGEST
    assert report.attempts_for_host("api.openai.com")[0].ok is False
    assert report.attempts_for_host("api.anthropic.com")[0].ok is False


@pytest.mark.parametrize(
    ("openai_ok", "anthropic_ok", "literal_ok"),
    [(True, False, False), (False, True, False), (False, False, True)],
)
def test_interpret_rejects_gateway_bypass(openai_ok: bool, anthropic_ok: bool, literal_ok: bool) -> None:
    with pytest.raises(EgressBypassError):
        interpret_probe_output(
            _report_json(openai_ok=openai_ok, anthropic_ok=anthropic_ok, literal_ok=literal_ok),
            policy=_policy(),
            image_ref=DEFAULT_RUNNER_IMAGE,
            image_digest=DIGEST,
        )


@pytest.mark.skipif(not docker_available(), reason="Docker daemon is not available")
def test_docker_blocks_provider_egress_and_allows_gateway() -> None:
    report = DockerEgressRunner(_policy()).run_probe()
    assert report.gateway_ok
    assert report.environment.image_digest is not None
    assert report.environment.image_digest.startswith("sha256:")
    assert len(report.environment.image_digest) == 7 + 64
    for host in ("api.openai.com", "api.anthropic.com"):
        matched = report.attempts_for_host(host)
        assert matched
        assert all(attempt.ok is False for attempt in matched)
    literal = [attempt for attempt in report.attempts if attempt.name == "literal-bypass"]
    assert literal and all(attempt.ok is False for attempt in literal)
