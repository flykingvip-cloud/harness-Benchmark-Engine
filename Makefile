# Private-host commands. Neither target starts a paid run.
#
#   make doctor                  offline preflight; does not open a socket
#   make workbench-up            Linux host-network UI on 127.0.0.1:8080
#   make workbench-down          stop that stack
#   make workbench-desktop-up    Docker Desktop bridge UI on 127.0.0.1:8080
#   make workbench-desktop-down  stop that stack
#
# `make doctor` needs LITELLM_BASE_URL and LITELLM_API_KEY in the current
# shell. It prints the next smoke commands and does not run them.
# Workbench compose files substitute those variables when set and leave them
# empty otherwise. They do not store key values.
#
# Docker Desktop (macOS/Windows): workbench-desktop-* pins context
# `desktop-linux` and uses `up --build --pull never` so a Hub IPv6/oauth
# flake does not re-pull base images that are already local. Override with
# DOCKER_DESKTOP_CONTEXT=… if needed. See docs/private-deploy-checklist.md
# 「本机开发者注意（Mac）」 for git insteadOf and Hub notes.
#
# Git on some Macs: a global url.https://github.com/.insteadof rewrites
# git@github.com: → HTTPS. Do not edit ~/.gitconfig from this repo. For one
# fetch/push, cancel it with:
#   git -c url.https://github.com/.insteadof= fetch origin
#   git -c url.https://github.com/.insteadof= push origin HEAD

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COMPOSE := docker compose -f $(ROOT)/docker/workbench/compose.yaml
# Pin Docker Desktop context; avoid Hub re-pull during local rebuilds.
DOCKER_DESKTOP_CONTEXT ?= desktop-linux
COMPOSE_DESKTOP := docker --context $(DOCKER_DESKTOP_CONTEXT) compose -f $(ROOT)/docker/workbench/compose.desktop.yaml

.DEFAULT_GOAL := help

.PHONY: help doctor workbench-up workbench-down workbench-desktop-up workbench-desktop-down

help:
	@printf '%s\n' \
		'make doctor                  offline preflight; does not open a socket' \
		'make workbench-up            start the Linux workbench on 127.0.0.1:8080' \
		'make workbench-down          stop the Linux workbench' \
		'make workbench-desktop-up    start the Docker Desktop workbench on 127.0.0.1:8080' \
		'make workbench-desktop-down  stop the Docker Desktop workbench'

doctor:
	$(ROOT)/scripts/private-host-check.sh

workbench-up:
	$(COMPOSE) up --build -d

workbench-down:
	$(COMPOSE) down

workbench-desktop-up:
	$(COMPOSE_DESKTOP) up --build --pull never -d

workbench-desktop-down:
	$(COMPOSE_DESKTOP) down
