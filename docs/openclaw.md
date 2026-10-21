# OpenClaw adapter

OpenClaw is an orchestration and channel layer. Its Gateway connects
sessions, tools, and chat channels. Plugins can replace the turn executor
with another agent, including DeepSeek Harness (`dsh/...`), Codex, or Claude
CLI. That path is a bridge. It is not an identical peer of OpenBot or Inspect
`react()`.

`OpenClawAdapter` does not start the Gateway and does not take that bridge.
It runs one embedded `openclaw agent exec` turn and pins `agentRuntime.id` to
the built-in runtime `openclaw`. Plugins are disabled. The model route is
LiteLLM only. `HARNESS_ORDER` stays `openbot`, then `inspect_react`. Saving
the catalog row does not spend and does not rank harnesses.

## Pin

| Field | Value |
| --- | --- |
| npm | `openclaw@2026.10.1` |
| git tag | `v2026.10.1` |
| catalog version | `2026.10.1` |
| tarball | `https://registry.npmjs.org/openclaw/-/openclaw-2026.10.1.tgz` |

`latest` is not a harness version. A local click runs
`npm install --ignore-scripts` on that tarball, then admits the row when
`package.json` and the bin match the pin. Package scripts are not run.
The install prefix is `harnesses/installs/openclaw/`. Point `OPENCLAW_BIN`
at `node_modules/.bin/openclaw` there, or set `OHBE_ROOT` so
`which_openclaw` finds that shim. A matching `OPENCLAW_VERSION` and
`OPENCLAW_BIN` still admit the row without calling npm. The rounds
inventory still allows only OpenClaw `0.1.0`. That whitelist is a different
gate from this npm pin.

```bash
npm install -g openclaw@2026.10.1
export OPENCLAW_BIN="$(command -v openclaw)"
export OPENCLAW_VERSION=2026.10.1
export LITELLM_API_KEY=...   # process environment only, never YAML
```

## LiteLLM path

The adapter writes `$OPENCLAW_STATE_DIR/ohbe-openclaw.json` and the task text,
then runs:

```bash
openclaw agent exec --json \
  --config "$OPENCLAW_STATE_DIR/ohbe-openclaw.json" \
  --state-dir "$OPENCLAW_STATE_DIR" \
  --cwd "$OPENCLAW_STATE_DIR" \
  --model "litellm/<model_id>" \
  --timeout <seconds> \
  --message-file "$OPENCLAW_STATE_DIR/ohbe-task.txt"
```

The config has one provider, `litellm`, with `models.mode: replace`,
`api: openai-completions`, `apiKey: ${LITELLM_API_KEY}`, and `baseUrl` set to
`http://<gateway-host>:<gateway-port>/v1` from `ExecutionEnvironment`.
`openai-completions` posts to `{baseUrl}/chat/completions`. The primary model
is `litellm/<model_id>` for the task's `model_id` when it is in the pool,
otherwise the first pool member. A model id outside the pool is not launched.

`agentRuntime.id` is `openclaw` on the provider, on each pool model, and on
the selected ref. The legacy alias `pi` is rejected. `plugins.enabled` is
false and `plugins.allow` is empty, so a DeepSeek Harness plugin cannot take
the turn. Channels are empty, delivery is off, and the OpenClaw Gateway
section is absent. `--auth-env-only` is not passed: that flag refuses
`--config`.

Provider keys, proxy variables, `DEEPSEEK_API_KEY`, and
`OPENCLAW_AGENT_RUNTIME` are dropped from the child environment.
`OPENCLAW_STATE_DIR` is an isolated directory, not `~/.openclaw`.

The catalog profile records `provider: litellm`, `profile: agent-exec`,
`runtime: openclaw`, `execution-path: embedded-agent`,
`product-layer: orchestration-channel`, and `plugin-bridge: refused`. It does
not store a URL or a key. `live_run` stays `rejected`.

## What this run is, and what it is not

These follow the public OpenClaw 2026.10.1 CLI, not a private fork:

- The product's usual control plane is the Gateway plus channels. This adapter does not exercise that plane.
- `agent exec` is the headless embedded agent. With the built-in runtime pinned, OpenClaw owns the model loop. A plugin runtime would own it instead, and this config refuses that.
- Exit 0 with a JSON envelope `ok: true`, `status: ok`, and a non-empty `final` is a completed turn. Exit 2 or `status: timeout` is a timeout. Non-JSON stdout is a failed execution.
- Usage is read from `usage.input`, `usage.output`, and `usage.total`, or from a chat-completions usage object. A missing object stays missing, not zero. `costUsd` is not an OHBE cost.
- Exec defaults select the `coding` tool profile, leave the sandbox off, and restrict filesystem tools to `--cwd`. That is not OpenBot's benchmark profile and not Inspect `react()` with the `bash` tool and a Docker sandbox.
- `plugins.enabled: false` is the control this adapter writes. A host plugin that ignores it would be outside this contract. CI uses a fake CLI and does not load plugins.
- The first real `openclaw` at this pin should be checked with `openclaw --version` and one `agent exec --json` against the OHBE gateway before a comparison run. That check is not part of CI. Loopback and the recorded gateway host are the only `baseUrl`. `allowPrivateNetwork` is left unset.

WorkBuddy's runnable path is the bundled CodeBuddy CLI, not the desktop GUI. See [workbuddy.md](workbuddy.md). 豆包工作 and 千问办公 have closed adapters that refuse to run. See [doubao-work.md](doubao-work.md) and [qwen-work.md](qwen-work.md).
