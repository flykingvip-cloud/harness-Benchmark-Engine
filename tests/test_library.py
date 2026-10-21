"""Harness library and model library gates. They do not extend HARNESS_ORDER."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.api.app import create_app
from ohbe.gateway.probe import ProbeError
from ohbe.library import (
    DiscoveredModel,
    LibraryGateError,
    LocalDiscoveryError,
    VLLM_API_KEY_ENV,
    VLLM_BASE_URL_ENV,
    install_distributions,
    parse_ollama_list,
    parse_vllm_listing,
    pinned_artifact_url,
    resolve_pinned_install,
    vllm_models_url,
)
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import (
    PreinstallVersionError,
    accept_version,
    admitted_version,
    catalog_adapter_ids,
    catalog_live_ids,
    entry_for,
)
from ohbe.remote_auth import RemoteAuthError, RemoteCredential, authenticate

ROOT = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3test-key-material\n-----END OPENSSH PRIVATE KEY-----\n"


def _remote_body(secret: str, *, secret_env: str = "") -> dict[str, object]:
    return {
        "target": "remote",
        "host": "10.0.0.8",
        "port": 22,
        "username": "ops",
        "auth_method": "password" if not secret_env else "ssh_key",
        "secret": "" if secret_env else secret,
        "secret_env": secret_env,
    }


def _client(tmp_path: Path, environ: dict[str, str] | None = None, probe: object | None = None) -> TestClient:
    return TestClient(create_app(tmp_path, environ=environ or {}, probe=probe))  # type: ignore[arg-type]


def test_catalog_lists_market_harnesses_without_growing_the_live_order() -> None:
    assert catalog_live_ids() == HARNESS_ORDER == ("openbot", "inspect_react")
    assert catalog_adapter_ids() == (
        "openbot",
        "inspect_react",
        "workbuddy",
        "openclaw",
        "deepseek_harness",
        "doubao_work",
        "qwen_work",
        "langgraph",
        "crewai",
        "autogen",
        "semantic_kernel",
        "claude_code",
    )
    assert "openclaw" not in HARNESS_ORDER


def test_ollama_scan_text_keeps_only_versioned_names() -> None:
    text = "NAME ID SIZE MODIFIED\nqwen2.5:7b abc 4 GB now\nllama3:latest def 4 GB now\nbare ghi 1 GB now\n"
    found = parse_ollama_list(text)
    assert [(item.name, item.revision) for item in found] == [("qwen2.5", "7b")]


def test_local_scan_admits_only_version_gated_harnesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def versions(name: str) -> str | None:
        if name == "langgraph":
            return "0.2.16"
        if name == "inspect-ai":
            return "9.9.9"
        if name == "crewai":
            return "latest"
        return None

    monkeypatch.setattr("ohbe.preinstall.distribution_version", versions)
    monkeypatch.setattr("ohbe.library.probe_host", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe")))
    client = _client(tmp_path, {"OPENBOT_COMMIT": SHA, "OPENBOT_BIN": str(ROOT / "pyproject.toml")})
    scanned = client.post("/v1/harness-library/scan", json={"target": "local", "host": "", "port": None})
    assert scanned.status_code == 200
    body = scanned.json()
    assert body["network"] == "not_contacted"
    assert body["harness_order"] == ["openbot", "inspect_react"]
    assert body["executed"] is False
    assert set(body["admitted"]) == {"openbot-benchmark", "langgraph-plan"}
    assert "crewai-plan" not in body["admitted"]
    listed = client.get("/v1/harness-configs").json()
    assert listed["harness_order"] == ["openbot", "inspect_react"]
    by_id = {item["profile_id"]: item for item in listed["profiles"]}
    assert "openclaw-plan" not in by_id
    assert by_id["langgraph-plan"]["version"] == "0.2.16"
    assert by_id["openbot-benchmark"]["version"] == SHA
    assert "inspect-react-pin" not in by_id


def test_preinstall_offers_deps_then_installs_without_extending_live_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"installed": False}

    def versions(name: str) -> str | None:
        if name == "crewai" and state["installed"]:
            return "0.80.0"
        return None

    artifact = "https://files.pythonhosted.org/packages/aa/bb/crewai-0.80.0-py3-none-any.whl"
    installed: list[tuple[str, ...]] = []

    def install(specs: tuple[str, ...]) -> None:
        installed.append(specs)
        state["installed"] = True

    def lookup(name: str) -> tuple[str, str | None]:
        assert name == "crewai"
        return ("0.80.0", artifact)

    monkeypatch.setattr("ohbe.preinstall.distribution_version", versions)
    monkeypatch.setattr("ohbe.library.install_distributions", install)
    monkeypatch.setattr("ohbe.library.lookup_released_version", lookup)
    client = _client(tmp_path)
    blocked = client.post(
        "/v1/harness-library/install",
        json={"adapter": "crewai", "target": "local", "host": "", "port": None, "install_deps": False, "timeout_seconds": 30},
    )
    assert blocked.status_code == 200
    assert blocked.json()["ok"] is False
    assert blocked.json()["reason"] == "deps_missing"
    assert blocked.json()["offer_install"] is True
    assert blocked.json()["harness_order"] == ["openbot", "inspect_react"]
    assert not (tmp_path / "harnesses").exists()

    saved = client.post(
        "/v1/harness-library/install",
        json={"adapter": "crewai", "target": "local", "host": "", "port": None, "install_deps": True, "timeout_seconds": 30},
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert saved.json()["reason"] == "admitted"
    assert saved.json()["registration"] == "plan"
    assert saved.json()["in_live_order"] is False
    assert saved.json()["version"] == "0.80.0"
    assert installed == [(artifact,)]
    text = (tmp_path / "harnesses" / "profiles" / "crewai-plan.yaml").read_text(encoding="utf-8")
    assert "status: plan" in text
    assert "live_run: rejected" in text
    assert "latest" not in text
    assert "://" not in text
    assert artifact not in text
    assert HARNESS_ORDER == ("openbot", "inspect_react")


def test_remote_harness_requires_auth_and_does_not_invent_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "correct-horse-battery"
    calls: list[str] = []

    def refuse_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tcp")

    def refuse_ssh(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ssh")

    monkeypatch.setattr(socket, "create_connection", refuse_socket)
    monkeypatch.setattr("ohbe.remote_auth.authenticate", refuse_ssh)
    client = _client(tmp_path)
    rejected = client.post(
        "/v1/harness-library/scan",
        json={"target": "remote", "host": "api.openai.com", "port": 22, "username": "ops", "auth_method": "password", "secret": secret},
    )
    assert rejected.status_code == 200
    assert rejected.json()["ok"] is False
    assert rejected.json()["reason"] == "rejected_host"
    assert rejected.json()["network"] == "not_contacted"
    assert secret not in rejected.text
    assert not (tmp_path / "harnesses").exists()

    missing = client.post("/v1/harness-library/scan", json={"target": "remote", "host": "10.0.0.8", "port": 22})
    assert missing.json()["reason"] == "auth_required"
    assert missing.json()["admitted"] == []
    assert missing.json()["network"] == "not_contacted"

    def unreachable(*_args: object, **_kwargs: object) -> None:
        raise RemoteAuthError("unreachable")

    monkeypatch.setattr("ohbe.remote_auth.authenticate", unreachable)
    offline = client.post(
        "/v1/harness-library/scan",
        json=_remote_body(secret),
    )
    assert offline.json()["reason"] == "unreachable"
    assert offline.json()["admitted"] == []
    assert secret not in offline.text

    monkeypatch.setattr("ohbe.remote_auth.authenticate", lambda *_args, **_kwargs: calls.append("auth"))
    gap = client.post("/v1/harness-library/install", json={**_remote_body(secret), "adapter": "crewai", "install_deps": True})
    assert gap.json()["reason"] == "inventory_unsupported"
    assert gap.json()["ok"] is False
    assert gap.json()["network"] == "probed"
    assert gap.json()["harness_order"] == ["openbot", "inspect_react"]
    assert secret not in gap.text
    assert calls == ["auth"]
    assert not (tmp_path / "harnesses").exists()

    monkeypatch.setattr(
        "ohbe.library.discover_remote_harnesses",
        lambda *_args, **_kwargs: (("semantic_kernel", "1.25.0"), ("inspect_react", "9.9.9"), ("crewai", "latest")),
    )
    reached = client.post("/v1/harness-library/scan", json=_remote_body(secret))
    assert reached.json()["ok"] is True
    assert reached.json()["network"] == "probed"
    assert reached.json()["admitted"] == ["semantic-kernel-plan"]
    assert reached.json()["harness_order"] == ["openbot", "inspect_react"]
    text = (tmp_path / "harnesses" / "profiles" / "semantic-kernel-plan.yaml").read_text(encoding="utf-8")
    assert secret not in text
    assert secret not in reached.text
    assert "latest" not in text
    assert HARNESS_ORDER == ("openbot", "inspect_react")


class _ListingProbe:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.bearers: list[str | None] = []

    def get_json(self, url: str, *, timeout: float = 2.0, bearer: str | None = None) -> tuple[int, dict[str, list[object]]]:
        self.calls.append(url)
        self.bearers.append(bearer)
        return 200, {"data": []}


def test_model_library_admits_through_the_same_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ohbe.library.probe_host", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "ohbe.library.discover_local_models",
        lambda *_args, **_kwargs: (
            DiscoveredModel(provider="ollama", name="qwen2.5", revision="7b"),
            DiscoveredModel("ollama", "llama3", "latest"),
        ),
    )
    probe = _ListingProbe()
    client = _client(tmp_path, probe=probe)
    secret = "sk-library-secret"
    gateway = "http://ohbe-gateway:4000"
    latest = client.post(
        "/v1/model-library/admit",
        json={
            "target": "local",
            "host": "",
            "port": None,
            "model_id": "vllm/lab",
            "provider": "vllm",
            "name": "lab",
            "revision": "latest",
            "litellm_model": "hosted_vllm/lab",
            "modality": "text",
            "strengths": ["reasoning"],
            "placement": "local",
            "api_key": secret,
            "gateway_url": gateway,
        },
    )
    assert latest.json()["reason"] == "version_rejected"
    assert probe.calls == []
    assert secret not in latest.text
    assert not (tmp_path / "models" / "catalog").exists()

    blocked = client.post("/v1/model-library/scan", json={"target": "remote", "host": "10.1.0.4", "port": 11434})
    assert blocked.json()["reason"] == "unreachable"
    assert blocked.json()["admitted"] == []

    broken = client.post(
        "/v1/model-library/scan",
        json={
            "target": "local",
            "host": "",
            "port": None,
            "provider": "ollama",
            "name": "qwen2.5",
            "revision": "latest",
            "model_id": "ollama/qwen2.5",
            "litellm_model": "ollama/qwen2.5",
        },
    )
    assert broken.json()["reason"] == "fields_rejected"
    assert broken.json()["admitted"] == []

    admitted = client.post(
        "/v1/model-library/admit",
        json={
            "target": "local",
            "host": "",
            "port": None,
            "model_id": "vllm/lab",
            "provider": "vllm",
            "name": "lab",
            "revision": "2026.04",
            "litellm_model": "hosted_vllm/lab",
            "modality": "code",
            "strengths": ["code"],
            "placement": "local",
            "api_key": secret,
            "gateway_url": gateway,
        },
    )
    assert admitted.json()["ok"] is True
    assert admitted.json()["reason"] == "admitted"
    assert probe.calls == [f"{gateway}/v1/models"]
    assert probe.bearers == [secret]
    assert secret not in admitted.text
    text = (tmp_path / "models" / "catalog" / "vllm__lab.yaml").read_text(encoding="utf-8")
    assert "placement: gateway" in text
    assert 'revision: "2026.04"' in text
    assert secret not in text
    assert "://" not in text
    intake = client.get("/v1/catalog/models").json()["models"]
    assert [item["model_id"] for item in intake] == ["vllm/lab"]
    assert intake[0]["accepted"] is True
    assert intake[0]["policy_match"] is True
    assert intake[0]["connectivity"] == "ok"

    denied = client.post(
        "/v1/model-library/admit",
        json={
            "target": "local",
            "host": "",
            "port": None,
            "model_id": "vllm/other",
            "provider": "vllm",
            "name": "other",
            "revision": "2026.04",
            "litellm_model": "hosted_vllm/other",
            "modality": "text",
            "strengths": ["reasoning"],
            "placement": "gateway",
            "api_key": secret,
            "gateway_url": "https://api.openai.com/v1",
        },
    )
    assert denied.json()["ok"] is False
    assert denied.json()["reason"] == "policy_mismatch"
    assert not (tmp_path / "models" / "catalog" / "vllm__other.yaml").exists()
    assert secret not in denied.text

    scanned = client.post("/v1/model-library/scan", json={"target": "local", "host": "", "port": None})
    assert scanned.json()["admitted"] == ["ollama/qwen2.5"]
    assert scanned.json()["missing"] == ["ollama/llama3"]
    local = (tmp_path / "models" / "catalog" / "ollama__qwen2.5.yaml").read_text(encoding="utf-8")
    assert "placement: local" in text or "placement: local" in local
    assert "placement: local" in local
    listed = client.get("/v1/model-catalog").json()
    assert {item["model_id"] for item in listed["entries"]} == {"vllm/lab", "ollama/qwen2.5"}
    both = {item["model_id"] for item in client.get("/v1/catalog/models").json()["models"]}
    assert both == {"vllm/lab", "ollama/qwen2.5"}


def test_cloud_admit_takes_revision_from_the_gateway_listing(tmp_path: Path) -> None:
    class _TaggedProbe:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_json(self, url: str, *, timeout: float = 2.0, bearer: str | None = None) -> tuple[int, dict[str, object]]:
            del timeout, bearer
            self.calls.append(url)
            return 200, {"data": [{"id": "qwen2.5:7b"}]}

    probe = _TaggedProbe()
    client = _client(tmp_path, probe=probe)
    secret = "sk-library-secret"
    derived = client.post(
        "/v1/model-library/admit",
        json={
            "target": "local",
            "host": "",
            "port": None,
            "model_id": "vllm/qwen2.5",
            "provider": "vllm",
            "name": "qwen2.5",
            "revision": "",
            "litellm_model": "hosted_vllm/qwen2.5",
            "modality": "text",
            "strengths": ["reasoning"],
            "placement": "gateway",
            "api_key": secret,
            "gateway_url": "http://ohbe-gateway:4000",
        },
    )
    assert derived.json()["ok"] is True
    assert derived.json()["reason"] == "admitted"
    assert derived.json()["version"] == "7b"
    assert probe.calls == ["http://ohbe-gateway:4000/v1/models"]
    text = (tmp_path / "models" / "catalog" / "vllm__qwen2.5.yaml").read_text(encoding="utf-8")
    assert 'revision: "7b"' in text
    assert "latest" not in text
    assert secret not in text

    empty = _ListingProbe()
    bare = _client(tmp_path / "bare", probe=empty)
    rejected = bare.post(
        "/v1/model-library/admit",
        json={
            "target": "local",
            "host": "",
            "port": None,
            "model_id": "vllm/qwen2.5",
            "provider": "vllm",
            "name": "qwen2.5",
            "litellm_model": "hosted_vllm/qwen2.5",
            "modality": "text",
            "strengths": ["reasoning"],
            "placement": "gateway",
            "api_key": secret,
            "gateway_url": "http://ohbe-gateway:4000",
        },
    )
    assert rejected.json()["reason"] == "version_rejected"
    assert rejected.json()["admitted"] == []
    assert empty.calls == ["http://ohbe-gateway:4000/v1/models"]
    assert not (tmp_path / "bare" / "models" / "catalog").exists()


def test_catalog_pin_and_release_lookup_never_emit_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(name: str) -> tuple[str, str | None]:
        raise AssertionError(name)

    monkeypatch.setattr("ohbe.library.lookup_released_version", refuse)
    inspect = entry_for("inspect_react")
    assert inspect is not None
    assert resolve_pinned_install(inspect) == "inspect-ai==0.3.277"
    crewai = entry_for("crewai")
    assert crewai is not None
    assert admitted_version(crewai, "latest") is None
    with pytest.raises(PreinstallVersionError):
        accept_version(crewai, "latest", {}, strict=False)

    monkeypatch.setattr("ohbe.library.lookup_released_version", lambda _name: ("latest", None))
    assert resolve_pinned_install(crewai) is None
    evil = "https://api.openai.com/packages/crewai-0.80.0.whl"
    monkeypatch.setattr("ohbe.library.lookup_released_version", lambda _name: ("0.80.0", evil))
    assert resolve_pinned_install(crewai) == "crewai==0.80.0"
    good = "https://files.pythonhosted.org/packages/aa/bb/crewai-0.80.0-py3-none-any.whl"
    assert pinned_artifact_url(good, "crewai", "0.80.0")
    assert not pinned_artifact_url(good, "crewai", "0.8")
    assert not pinned_artifact_url(good.replace("0.80.0", "latest"), "crewai", "0.80.0")


def test_install_rejects_floating_targets_before_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ohbe.library.subprocess.run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("pip")))
    with pytest.raises(LibraryGateError):
        install_distributions(("crewai",))
    with pytest.raises(LibraryGateError):
        install_distributions(("crewai==latest",))
    with pytest.raises(LibraryGateError):
        install_distributions(("https://api.openai.com/packages/crewai-0.80.0.whl",))


def test_exact_spec_installs_from_the_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"installed": False}

    def versions(name: str) -> str | None:
        if name == "inspect-ai" and state["installed"]:
            return "0.3.277"
        return None

    installed: list[tuple[str, ...]] = []

    def install(specs: tuple[str, ...]) -> None:
        installed.append(specs)
        state["installed"] = True

    monkeypatch.setattr("ohbe.preinstall.distribution_version", versions)
    monkeypatch.setattr("ohbe.library.install_distributions", install)
    monkeypatch.setattr("ohbe.library.lookup_released_version", lambda _name: (_ for _ in ()).throw(AssertionError("lookup")))
    client = _client(tmp_path)
    saved = client.post(
        "/v1/harness-library/install",
        json={"adapter": "inspect_react", "target": "local", "host": "", "port": None, "install_deps": True, "timeout_seconds": 30},
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert saved.json()["version"] == "0.3.277"
    assert saved.json()["in_live_order"] is True
    assert saved.json()["harness_order"] == ["openbot", "inspect_react"]
    assert installed == [("inspect-ai==0.3.277",)]
    text = (tmp_path / "harnesses" / "profiles" / "inspect-react-pin.yaml").read_text(encoding="utf-8")
    assert "latest" not in text
    assert "://" not in text


def test_remote_secret_env_is_not_written_and_install_hook_stays_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def auth(_host: str, _port: int, credential: RemoteCredential, **_kwargs: object) -> None:
        seen["secret"] = credential.secret

    def remote_install(host: str, port: int, credential: RemoteCredential, target: str) -> str:
        seen["target"] = target
        seen["installed_secret"] = credential.secret
        assert host == "10.0.0.8"
        assert port == 22
        return "0.80.0"

    monkeypatch.setattr("ohbe.remote_auth.authenticate", auth)
    monkeypatch.setattr("ohbe.library.lookup_released_version", lambda _name: ("0.80.0", None))
    monkeypatch.setattr("ohbe.library.remote_exec_supported", lambda: True)
    monkeypatch.setattr("ohbe.library.install_on_remote", remote_install)
    monkeypatch.setattr("ohbe.library.install_distributions", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local pip")))
    client = _client(tmp_path, {"OHBE_SSH_KEY": KEY})
    saved = client.post(
        "/v1/harness-library/install",
        json={
            **_remote_body(KEY, secret_env="OHBE_SSH_KEY"),
            "adapter": "crewai",
            "install_deps": True,
            "timeout_seconds": 30,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert saved.json()["version"] == "0.80.0"
    assert saved.json()["registration"] == "plan"
    assert seen["secret"] == KEY
    assert seen["installed_secret"] == KEY
    assert seen["target"] == "crewai==0.80.0"
    text = (tmp_path / "harnesses" / "profiles" / "crewai-plan.yaml").read_text(encoding="utf-8")
    assert KEY not in text
    assert "PRIVATE KEY" not in text
    assert "latest" not in text
    assert KEY not in saved.text
    assert "OHBE_SSH_KEY" not in text


def test_ssh_command_does_not_carry_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        seen["argv"] = list(argv)
        seen["env"] = dict(env)
        askpass = env.get("SSH_ASKPASS")
        if isinstance(askpass, str):
            seen["askpass"] = Path(askpass).read_text(encoding="utf-8")
            seen["askpass_path"] = askpass
        if "-i" in argv:
            key_path = argv[argv.index("-i") + 1]
            seen["key"] = Path(key_path).read_text(encoding="utf-8")
            seen["key_path"] = key_path
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ohbe.remote_auth.shutil.which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    monkeypatch.setattr("ohbe.remote_auth.subprocess.run", fake_run)
    authenticate("10.0.0.8", 22, RemoteCredential("ops", "password", "correct-horse"))
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert "correct-horse" not in argv
    env = seen["env"]
    assert isinstance(env, dict)
    assert env["OHBE_SSH_AUTH_SECRET"] == "correct-horse"
    assert "correct-horse" not in str(seen["askpass"])
    assert argv[-1] == "true"
    assert argv[argv.index("-F") + 1] == "/dev/null"
    askpass_path = seen["askpass_path"]
    assert isinstance(askpass_path, str)
    assert not Path(askpass_path).exists()

    authenticate("10.0.0.8", 22, RemoteCredential("ops", "ssh_key", KEY))
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert KEY not in argv
    assert "BEGIN OPENSSH PRIVATE KEY" not in " ".join(str(part) for part in argv)
    assert KEY.strip() in str(seen["key"])
    key_path = seen["key_path"]
    assert isinstance(key_path, str)
    assert not Path(key_path).exists()

    def denied(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("ssh")

    monkeypatch.setattr("ohbe.remote_auth.subprocess.run", denied)
    with pytest.raises(RemoteAuthError) as exc:
        authenticate("api.openai.com", 443, RemoteCredential("ops", "password", "correct-horse"))
    assert exc.value.reason == "rejected_host"
    assert "correct-horse" not in str(exc.value)


_VLLM_SECRET = "sk-vllm-local-secret"
_VLLM_ENV_SECRET = "sk-env-vllm-token"
_VLLM_ORIGIN = "http://127.0.0.1:8000"
_VLLM_LISTING = {
    "object": "list",
    "data": [
        {"id": "Qwen2.5-7B-Instruct", "object": "model", "owned_by": "vllm"},
        {"id": "qwen2.5:7b", "object": "model"},
        {"id": "openai/gpt-4o-mini", "object": "model"},
        {"id": "llama3:latest", "object": "model"},
        {"id": "lab", "revision": "2026.04", "litellm_params": {"model": "hosted_vllm/lab"}},
        {"id": "Qwen/Qwen2.5-7B-Instruct", "object": "model"},
        {"id": "qwen2.5", "version": "latest"},
    ],
}


class _VllmListingProbe:
    def __init__(self, payload: dict[str, object] | None = None, *, status: int = 200, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.bearers: list[str | None] = []
        self.payload = _VLLM_LISTING if payload is None else payload
        self.status = status
        self.fail = fail

    def get_json(self, url: str, *, timeout: float = 2.0, bearer: str | None = None) -> tuple[int, dict[str, object]]:
        del timeout
        self.calls.append(url)
        self.bearers.append(bearer)
        if self.fail:
            raise ProbeError("gateway unreachable")
        return self.status, self.payload


def _local_scan(client: TestClient, **extra: object) -> dict[str, object]:
    body = {"target": "local", "host": "", "port": None}
    body.update(extra)
    response = client.post("/v1/model-library/scan", json=body)
    assert response.status_code == 200
    parsed = response.json()
    assert isinstance(parsed, dict)
    return parsed


def test_vllm_listing_parser_keeps_concrete_local_ids() -> None:
    found = parse_vllm_listing(_VLLM_LISTING)
    assert [(item.provider, item.name, item.revision) for item in found] == [
        ("vllm", "Qwen2.5-7B-Instruct", "Qwen2.5-7B-Instruct"),
        ("vllm", "qwen2.5", "7b"),
        ("vllm", "lab", "2026.04"),
    ]
    assert vllm_models_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000/v1/models"
    assert vllm_models_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1/models"
    for blocked in (
        "https://api.openai.com/v1",
        "https://api.litellm.ai/v1",
        "http://127.0.0.1:8000/v1/chat/completions",
        "http://user:sk-vllm-local-secret@127.0.0.1:8000",
    ):
        with pytest.raises(LocalDiscoveryError) as exc:
            vllm_models_url(blocked)
        assert exc.value.reason == "policy_mismatch"
        assert exc.value.contacted is False
        assert _VLLM_SECRET not in str(exc.value)


def test_local_scan_discovers_vllm_models_without_writing_the_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama_calls: list[str] = []

    def ollama() -> tuple[DiscoveredModel, ...]:
        ollama_calls.append("list")
        return (DiscoveredModel(provider="ollama", name="qwen2.5", revision="7b"),)

    monkeypatch.setattr("ohbe.library.discover_ollama_models", ollama)
    probe = _VllmListingProbe()
    client = _client(tmp_path, probe=probe)
    saved = _local_scan(
        client,
        provider="vllm",
        name="qwen2.5",
        revision="7b",
        model_id="vllm/qwen2.5",
        litellm_model="hosted_vllm/qwen2.5",
        vllm_base_url=_VLLM_ORIGIN,
        api_key=_VLLM_SECRET,
    )
    assert saved["ok"] is True
    assert saved["reason"] == "admitted"
    assert saved["admitted"] == ["vllm/qwen2.5"]
    assert saved["spend"] == "none"
    assert saved["executed"] is False
    assert saved["spend_execution"] == "disabled"
    assert saved["live_run"] == "rejected"
    assert saved["live_spend"] == "locked"
    assert saved["network"] == "probed"
    assert probe.calls == [f"{_VLLM_ORIGIN}/v1/models"]
    assert probe.bearers == [_VLLM_SECRET]
    assert all("/v1/chat/completions" not in url for url in probe.calls)
    assert _VLLM_SECRET not in str(saved)
    text = (tmp_path / "models" / "catalog" / "vllm__qwen2.5.yaml").read_text(encoding="utf-8")
    assert "placement: local" in text
    assert 'revision: "7b"' in text
    assert "hosted_vllm/qwen2.5" in text
    assert _VLLM_SECRET not in text
    assert "://" not in text
    assert _VLLM_ORIGIN not in text
    assert not (tmp_path / "models" / "pools").exists()
    assert ollama_calls == ["list"]

    open_scan = _local_scan(client, vllm_base_url=_VLLM_ORIGIN)
    assert set(open_scan["admitted"]) == {  # type: ignore[arg-type]
        "vllm/qwen2.5",
        "vllm/Qwen2.5-7B-Instruct",
        "vllm/lab",
        "ollama/qwen2.5",
    }
    mixed = (tmp_path / "models" / "catalog" / "vllm__Qwen2.5-7B-Instruct.yaml").read_text(encoding="utf-8")
    assert "Qwen2.5-7B-Instruct" in mixed
    assert _VLLM_SECRET not in mixed
    listed = client.get("/v1/model-catalog").json()["entries"]
    assert "vllm/Qwen2.5-7B-Instruct" in {item["model_id"] for item in listed}


def test_vllm_discovery_fails_closed_and_keeps_spend_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ollama_calls: list[int] = []

    def ollama() -> tuple[DiscoveredModel, ...]:
        ollama_calls.append(1)
        return ()

    monkeypatch.setattr("ohbe.library.discover_ollama_models", ollama)
    down = _VllmListingProbe(fail=True)
    client = _client(tmp_path, probe=down)
    offline = _local_scan(client, vllm_base_url=_VLLM_ORIGIN, api_key=_VLLM_SECRET)
    assert offline["ok"] is False
    assert offline["reason"] == "connectivity_failed"
    assert offline["network"] == "probed"
    assert offline["admitted"] == []
    assert offline["spend"] == "none"
    assert offline["executed"] is False
    assert offline["spend_execution"] == "disabled"
    assert _VLLM_SECRET not in str(offline)
    assert not (tmp_path / "models" / "catalog").exists()
    assert down.calls == [f"{_VLLM_ORIGIN}/v1/models"]
    assert down.bearers == [_VLLM_SECRET]

    denied = _VllmListingProbe()
    denied_client = _client(tmp_path, probe=denied)
    blocked = _local_scan(
        denied_client,
        vllm_base_url=f"http://user:{_VLLM_SECRET}@127.0.0.1:8000",
        api_key=_VLLM_SECRET,
    )
    assert blocked["ok"] is False
    assert blocked["reason"] == "policy_mismatch"
    assert blocked["network"] == "not_contacted"
    assert blocked["admitted"] == []
    assert denied.calls == []
    assert _VLLM_SECRET not in str(blocked)
    assert not (tmp_path / "models" / "catalog").exists()

    shaped = _VllmListingProbe()
    shaped_client = _client(tmp_path, probe=shaped)
    rejected = _local_scan(shaped_client, vllm_base_url=_VLLM_ORIGIN, api_key="short")
    assert rejected["reason"] == "fields_rejected"
    assert shaped.calls == []
    assert ollama_calls == []

    empty = _VllmListingProbe(payload={"data": []})
    empty_client = _client(tmp_path, environ={VLLM_BASE_URL_ENV: _VLLM_ORIGIN, VLLM_API_KEY_ENV: _VLLM_SECRET}, probe=empty)
    quiet = _local_scan(empty_client)
    assert quiet["ok"] is True
    assert quiet["reason"] == "empty"
    assert quiet["network"] == "probed"
    assert quiet["admitted"] == []
    assert empty.bearers == [_VLLM_SECRET]
    assert _VLLM_SECRET not in str(quiet)

    override = _VllmListingProbe(payload={"data": [{"id": "lab", "revision": "2026.04"}]})
    override_client = _client(
        tmp_path,
        environ={
            VLLM_BASE_URL_ENV: "http://10.9.9.9:8000",
            VLLM_API_KEY_ENV: _VLLM_ENV_SECRET,
            "OHBE_ALLOW_LIVE_SPEND": "1",
        },
        probe=override,
    )
    spent = _local_scan(override_client, vllm_base_url=_VLLM_ORIGIN, api_key=_VLLM_SECRET)
    assert spent["admitted"] == ["vllm/lab"]
    assert spent["spend"] == "none"
    assert spent["executed"] is False
    assert spent["spend_execution"] == "disabled"
    assert spent["live_run"] == "rejected"
    assert spent["live_spend"] == "env_set"
    assert override.calls == [f"{_VLLM_ORIGIN}/v1/models"]
    assert override.bearers == [_VLLM_SECRET]
    catalog = (tmp_path / "models" / "catalog" / "vllm__lab.yaml").read_text(encoding="utf-8")
    assert _VLLM_SECRET not in catalog
    assert _VLLM_ENV_SECRET not in catalog
    assert "10.9.9.9" not in catalog
    assert "://" not in catalog
    assert not (tmp_path / "models" / "pools").exists()
    assert _VLLM_ENV_SECRET not in str(spent)
    assert _VLLM_SECRET not in str(spent)
