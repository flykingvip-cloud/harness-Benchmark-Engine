# DeepSeek Harness adapter

Comparison runs can call `DeepSeekHarnessAdapter` on the same task, model pool,
and egress record as OpenBot and Inspect `react()`. This is not a live-pilot
harness. `HARNESS_ORDER` stays `openbot`, then `inspect_react`. Saving the
catalog row does not spend and does not rank harnesses.

## Pin

| Field | Value |
| --- | --- |
| npm | `@deepseek-ai/dsh@0.2.1-alpha.2` |
| git tag | `dsh-v0.2.1-alpha.2` |
| catalog version | `0.2.1-alpha.2` |
| tarball | `https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-alpha.2.tgz` |

`latest` is not a harness version. A local click runs
`npm install --ignore-scripts` on that tarball, then admits the row when
`package.json` and the bin match the pin. Package scripts are not run.
The install prefix is `harnesses/installs/deepseek_harness/`. Point
`DSH_BIN` at `node_modules/.bin/dsh` there, or set `OHBE_ROOT` so
`which_dsh` finds that shim. A matching `DSH_VERSION` and `DSH_BIN` still
admit the row without calling npm. OHBE does not look up a PyPI artifact
for it. The same preinstall route records WorkBuddy, OpenClaw,
豆包工作, and 千问办公. OpenClaw has a separate opt-in adapter
([openclaw.md](openclaw.md)). That path is OpenClaw's embedded agent, not a
bridge into this harness. WorkBuddy has a separate opt-in adapter
([workbuddy.md](workbuddy.md)). That path is the bundled CodeBuddy CLI, not
a model source for this harness. 豆包工作 and 千问办公 have closed adapters
that refuse to run ([doubao-work.md](doubao-work.md), [qwen-work.md](qwen-work.md)).

```bash
npm install -g @deepseek-ai/dsh@0.2.1-alpha.2
export DSH_BIN="$(command -v dsh)"
export DSH_VERSION=0.2.1-alpha.2
export LITELLM_API_KEY=...   # process environment only, never YAML
```

## LiteLLM path

The adapter writes `$DSH_HOME/ohbe-litellm.patch.yml` and runs:

```bash
dsh --profile headless --patch "$DSH_HOME/ohbe-litellm.patch.yml" "<task prompt>"
```

The patch has one provider, `litellm`, with `api: openai-completions`,
`apiKeyEnv: LITELLM_API_KEY`, and `baseURL` set to
`http://<gateway-host>:<gateway-port>/v1` from `ExecutionEnvironment`.
`agent-default-model` uses the task's `model_id` when it is in the pool,
otherwise the first pool member. A model id outside the pool is not launched.
Provider keys and proxy variables are dropped from the child environment.
`DSH_HOME` is an isolated directory.

The catalog profile records `provider: litellm`, `profile: headless`, the
package name, and the git tag. It does not store a URL or a key.
`live_run` stays `rejected`.

## Assumptions and tool-surface caveats

These follow the public `dsh` 0.2.1 CLI, not a private fork:

- `--profile` and `--patch` are launcher flags and must come before the task text.
- Headless prints the final assistant text on stdout. Exit 0 means the turn completed. Exit non-zero, or an empty completion, is a failed execution.
- There is no documented `--json` usage switch. Plain text leaves usage `missing`. A JSON object on stdout can carry `usage`; a missing object is not stored as zero.
- The shipped headless profile still mounts shell, filesystem, and DeepSeek web-search plugins. That is not OpenBot's benchmark profile and not Inspect `react()` with the `bash` tool and a Docker sandbox. OHBE removes `DEEPSEEK_API_KEY`, so the official search plugin cannot authenticate. It does not claim the tools are the same.
- `--patch` is assumed to replace the `llm-pi-ai` provider map and `agent-default-model` for that process. The first real `dsh` at this pin should be checked with `dsh --profile headless --patch ... --dump-config` before a comparison run. That check is not part of CI.
