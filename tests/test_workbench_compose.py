"""The private workbench stack stays loopback-only and does not unlock spend."""

from __future__ import annotations

from pathlib import Path

import yaml

from ohbe.pools.doctor import extract_smoke_commands

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker" / "workbench" / "compose.yaml"
DESKTOP_COMPOSE_PATH = ROOT / "docker" / "workbench" / "compose.desktop.yaml"
DOCKERFILE_API = ROOT / "docker" / "workbench" / "Dockerfile.api"
DOCKERFILE_UI = ROOT / "docker" / "workbench" / "Dockerfile.ui"
NGINX = ROOT / "docker" / "workbench" / "nginx.conf"
DESKTOP_NGINX = ROOT / "docker" / "workbench" / "nginx.desktop.conf"
MAKEFILE = ROOT / "Makefile"
CHECKLIST = ROOT / "docs" / "private-deploy-checklist.md"
README = ROOT / "README.md"

FORBIDDEN_ENV = (
    "LITELLM_MASTER_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
)
LITELLM_FROM_ENV = {
    "LITELLM_BASE_URL": "${LITELLM_BASE_URL:-}",
    "LITELLM_API_KEY": "${LITELLM_API_KEY:-}",
}


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_compose_locks_spend_and_does_not_publish_the_api() -> None:
    document = _compose()
    services = document["services"]
    api = services["api"]
    ui = services["ui"]

    assert document["name"] == "ohbe-workbench"
    assert "networks" not in document
    assert "ports" not in api
    assert "ports" not in ui
    assert api["network_mode"] == "host"
    assert ui["network_mode"] == "host"
    assert api["environment"]["OHBE_ALLOW_LIVE_SPEND"] == ""
    assert api["environment"]["OHBE_ROOT"] == "/opt/ohbe"
    for name, value in LITELLM_FROM_ENV.items():
        assert api["environment"][name] == value
        assert name not in ui.get("environment", {})
    assert api["volumes"] == [
        "../../models:/opt/ohbe/models:rw",
        "../../harnesses:/opt/ohbe/harnesses:rw",
    ]
    for name in FORBIDDEN_ENV:
        assert name not in api["environment"]
        assert name not in ui.get("environment", {})
    assert ui["depends_on"]["api"]["condition"] == "service_healthy"

    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "OHBE_ALLOW_LIVE_SPEND=1" not in raw
    assert 'OHBE_ALLOW_LIVE_SPEND: "1"' not in raw
    assert "models/" in raw and ":rw" in raw
    assert "LITELLM_BASE_URL" in raw and "LITELLM_API_KEY" in raw
    assert "sk-" not in raw
    rendered = yaml.dump(document)
    assert "benchmark run" not in rendered
    for name in FORBIDDEN_ENV:
        assert name not in rendered


def test_desktop_compose_publishes_loopback_and_keeps_spend_locked() -> None:
    document = yaml.safe_load(DESKTOP_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = document["services"]
    api = services["api"]
    ui = services["ui"]

    assert document["name"] == "ohbe-workbench"
    assert "network_mode" not in api
    assert "network_mode" not in ui
    assert api["ports"] == ["127.0.0.1:8765:8765"]
    assert ui["ports"] == ["127.0.0.1:8080:8080"]
    assert api["command"][api["command"].index("--host") + 1] == "0.0.0.0"
    assert api["environment"]["OHBE_ALLOW_LIVE_SPEND"] == ""
    assert api["environment"]["OHBE_ROOT"] == "/opt/ohbe"
    for name, value in LITELLM_FROM_ENV.items():
        assert api["environment"][name] == value
        assert name not in ui.get("environment", {})
    assert api["volumes"] == [
        "../../models:/opt/ohbe/models:rw",
        "../../harnesses:/opt/ohbe/harnesses:rw",
    ]
    assert ui["volumes"] == ["./nginx.desktop.conf:/etc/nginx/conf.d/default.conf:ro"]
    assert ui["depends_on"]["api"]["condition"] == "service_healthy"
    for name in FORBIDDEN_ENV:
        assert name not in api["environment"]
        assert name not in ui.get("environment", {})

    raw = DESKTOP_COMPOSE_PATH.read_text(encoding="utf-8")
    assert "OHBE_ALLOW_LIVE_SPEND=1" not in raw
    assert 'OHBE_ALLOW_LIVE_SPEND: "1"' not in raw
    assert "sk-" not in raw
    assert "gpu-litellm" not in raw
    assert "0.0.0.0:" not in raw
    nginx = DESKTOP_NGINX.read_text(encoding="utf-8")
    assert "listen 8080;" in nginx
    assert "proxy_pass http://api:8765;" in nginx
    assert "proxy_pass http://api:8765/health;" in nginx
    assert "127.0.0.1:8765" not in nginx
    assert "api.openai.com" not in nginx
    assert "sk-" not in nginx


def test_images_serve_uvicorn_and_the_static_ui() -> None:
    api = DOCKERFILE_API.read_text(encoding="utf-8")
    ui = DOCKERFILE_UI.read_text(encoding="utf-8")
    nginx = NGINX.read_text(encoding="utf-8")

    assert 'uvicorn", "ohbe.api.app:app"' in api
    assert '"--host", "127.0.0.1"' in api
    assert '"--port", "8765"' in api
    assert "0.0.0.0" not in api
    assert "OHBE_ALLOW_LIVE_SPEND=" in api
    assert ".[api]" in api
    assert "litellm" not in api.split("FROM", 1)[1].lower()
    assert "inspect-ai" not in api

    assert "npm run build" in ui
    assert "nginxinc/nginx-unprivileged:1.27-alpine" in ui
    assert "listen 127.0.0.1:8080;" in nginx
    assert "0.0.0.0" not in nginx
    assert "location /v1" in nginx
    assert "proxy_pass http://127.0.0.1:8765;" in nginx
    assert "location = /health" in nginx
    assert "proxy_pass http://127.0.0.1:8765/health;" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "api.openai.com" not in nginx
    assert "api.litellm.ai" not in nginx


def test_make_targets_do_not_start_a_live_run() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "\ndoctor:" in text
    assert "\nworkbench-up:" in text
    assert "\nworkbench-down:" in text
    assert "\nworkbench-desktop-up:" in text
    assert "\nworkbench-desktop-down:" in text
    assert "scripts/private-host-check.sh" in text
    assert "docker/workbench/compose.yaml" in text
    assert "docker/workbench/compose.desktop.yaml" in text
    assert "benchmark run" not in text
    assert "OHBE_ALLOW_LIVE_SPEND" not in text


def test_checklist_starts_the_workbench_after_doctor_and_keeps_smoke_manual() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    doctor = text.index("## 5.1")
    workbench = text.index("## 5.2 打开工作台")
    smoke = text.index("## 6. 冒烟步骤")
    assert doctor < workbench < smoke
    section = text[workbench:smoke]
    assert "make workbench-up" in section
    assert "make workbench-desktop-up" in section
    assert "compose.desktop.yaml" in section
    assert "127.0.0.1:8080" in section
    assert "OHBE_ALLOW_LIVE_SPEND" in section
    assert "fixture-pilot" in section
    assert "make doctor" in section
    commands = extract_smoke_commands(text)
    assert "benchmark run" in commands
    assert "workbench-up" not in commands
    assert "docker compose" not in commands


def test_readme_tells_testers_how_to_open_the_ui() -> None:
    text = README.read_text(encoding="utf-8")
    assert "## For testers" in text
    assert "make workbench-up" in text
    assert "make workbench-desktop-up" in text
    assert "compose.desktop.yaml" in text
    assert "http://127.0.0.1:8080" in text
    assert "make doctor" in text
    assert "fixture-pilot" in text
    assert "/results" in text
