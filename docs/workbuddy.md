# WorkBuddy adapter

WorkBuddy 5.7.7 is a desktop coding app. The publisher ships three installers
and no Linux artifact:

- macOS arm64 `.dmg`
- macOS x64 `.dmg`
- Windows x64 user `.exe`

A library click downloads the installer for the current OS and does not
launch it. Linux has no artifact, so the click returns `os_unsupported`
and downloads nothing. `desktop-install` stays `unsupported`: the downloaded
file is not the bundled CLI, and the profile is not admitted from the
installer alone.

The non-GUI path is the CodeBuddy Code CLI bundled inside the app
(`codebuddy` or `cbc`). `WorkBuddyAdapter` runs one headless `--print` turn
on that binary. It does not launch the WorkBuddy GUI. It does not register
WorkBuddy as a model source for DeepSeek Harness. It is not an identical
peer of OpenBot or Inspect `react()`: permission skip is not passed, so the
desktop agent's tool session is not this run.

The model route is LiteLLM only. `HARNESS_ORDER` stays `openbot`, then
`inspect_react`. Saving the catalog row does not spend and does not rank
harnesses.

## Pin

| Field | Value |
| --- | --- |
| desktop version | `5.7.7` |
| catalog version | `5.7.7` |
| installers | three official v5.7.7 packages (macOS arm64, macOS x64, Windows x64) |
| CLI | bundled `codebuddy` / `cbc` |

`latest` is not a harness version. The library admits the row when
`WORKBUDDY_VERSION` is exactly `5.7.7` and `WORKBUDDY_BIN` is an executable.
That path does not run the GUI installer. A click without those variables
downloads the matching desktop file on macOS or Windows and records
`downloaded-not-executed`. It does not `npm install` CodeBuddy.

The desktop pin is not the bundled CLI's `--version` string. Public notes
for other WorkBuddy builds show an inner `@tencent-ai/codebuddy-code`
version that is not `5.7.7`. That inner version is not a separate catalog
pin. Point `WORKBUDDY_BIN` at the `codebuddy` binary inside the installed
app. On macOS that file has been published at:

```text
/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
/Applications/WorkBuddy AI.app/Contents/Resources/app.asar.unpacked/cli/bin/codebuddy
```

OHBE does not search those paths.

```bash
export WORKBUDDY_BIN=/path/to/codebuddy
export WORKBUDDY_VERSION=5.7.7
export LITELLM_API_KEY=...   # process environment only, never YAML
```

## LiteLLM path

The adapter writes three files under an isolated `CODEBUDDY_CONFIG_DIR` and
sends the task on stdin:

```bash
codebuddy --print --output-format json \
  --model "<model_id>" \
  --settings "$CODEBUDDY_CONFIG_DIR/settings.json" \
  --setting-sources user \
  --strict-mcp-config \
  --mcp-config "$CODEBUDDY_CONFIG_DIR/mcp.json" \
  --disallowedTools WebFetch,WebSearch
```

`models.json` lists only the pool. Each row has `vendor: litellm`,
`supportsToolCall: false`, `apiKey: ${LITELLM_API_KEY}`, and `url` set to
`http://<gateway-host>:<gateway-port>/v1/chat/completions` from
`ExecutionEnvironment`. CodeBuddy's custom-model docs require that full
chat-completions path. `--model` is the pool member id from the task when it
is in the pool, otherwise the first pool member. A model id outside the pool
is not launched.

`CODEBUDDY_BASE_URL` is not set. That variable is the Anthropic-protocol
override, and the public examples point it at a vendor. `CODEBUDDY_API_KEY`
and `CODEBUDDY_AUTH_TOKEN` are stripped for the same reason. The key the
process may use is `LITELLM_API_KEY`, referenced from `models.json`.

Settings pin the same model for `lite` and `reasoning`, and deny `WebFetch`
and `WebSearch`. The MCP file is empty and `--strict-mcp-config` is set.
The built-in plugin marketplace is skipped. `-y` and
`--dangerously-skip-permissions` are not passed. `CODEBUDDY_IS_SANDBOX` is
not set.

Provider keys and proxy variables are dropped. `HOME` and
`CODEBUDDY_CONFIG_DIR` are the isolated run directory, not `~/.codebuddy`.

The catalog profile records `provider: litellm`, `profile: headless-print`,
`product-layer: desktop-app`, `execution-path: bundled-codebuddy-cli`,
`model-route: openai-chat-completions`, `desktop-install: unsupported`,
`base-url-override: refused`, `dsh-model-source: refused`, and
`permissions: not-skipped`. It does not store a URL or a key. `live_run`
stays `rejected`.

## What this run is, and what it is not

These follow the public CodeBuddy Code CLI docs, not a private fork, and
not a captured WorkBuddy 5.7.7 `--help`:

- The product's usual surface is the desktop app. This adapter does not exercise that GUI.
- `--print` is the headless CLI. Exit 0 with JSON `result` text (or the older `response` field), `is_error` not true, and subtype missing or `success`, is a completed turn. A non-zero exit or `subtype: timeout` is a failure. Non-JSON stdout is a failed execution.
- Usage is read from chat-completions token fields, from `input_tokens` / `output_tokens` / `total_tokens`, or from `input` / `output` / `total`. A missing object stays missing. An object without a total stays invalid, not zero. `total_cost_usd` is not an OHBE cost.
- There is no documented whole-turn `--timeout`. The adapter stops the process at the harness timeout and asks the CLI for the same budget through `CODEBUDDY_FIRST_TOKEN_TIMEOUT_MS` and `CODEBUDDY_STREAM_TIMEOUT_MS`. `CODEBUDDY_MAX_RETRIES=0` asks for no retry. A real binary may clamp that.
- Print mode is assumed to honor `models.json` when `--model` names an id in that file. If a 5.7.7 binary ignores the file and calls the product cloud, that run is outside this contract and should fail closed when the JSON names another endpoint.
- Tool calls that need permission are blocked because `-y` is absent. That is narrower than OpenBot and narrower than Inspect `react()` with `bash`. It is also narrower than the desktop agent.
- The first real binary at desktop `5.7.7` should be checked with `codebuddy --version` and one `--print` against the OHBE gateway before a comparison run. `--version` will not print `5.7.7`. That check is not part of CI.

豆包工作 and 千问办公 have closed adapters that refuse to run. See [doubao-work.md](doubao-work.md) and [qwen-work.md](qwen-work.md).
