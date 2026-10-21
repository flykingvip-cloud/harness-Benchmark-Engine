# OHBE — OpenBot Harness Benchmark Engine

OHBE is a standalone Python benchmark engine for evaluating agent harnesses.
This repository is at **v0.1 Steps C–J**: versioned core schemas, append-only
trace serialization, Docker egress isolation, a LiteLLM OSS gateway client,
OpenBot and Inspect adapters, objective scoring, an offline cost calculator,
paired-run aggregation, a fixture pilot audit, a reproducible report, and a
manual live-pilot runner that does not start without credentials.

The import name is `ohbe`. The distribution name is
`openbot-harness-benchmark-engine`.

## What this step includes

- Versioned Pydantic models for tasks, harness configs and refs, model
  definitions, model pools, executions, model invocations, execution traces,
  and experiment (run) manifests
- Reproducibility fields: schema version, package version, seeds, content
  hashes, and an optional runtime fingerprint
- Append-only raw JSONL traces with a sha256 hash chain
- Versioned pricing references for a later offline cost calculator
- Token usage and cost values that stay **missing** or **invalid** when they
  were not actually reported — they are never filled in as zero
- Optional stubs for Oracle and Routing Regret
- Docker runner that places the model-calling process on an internal network
- Default-deny egress policy: only one designated gateway host and port
- Process-level socket guard plus a container probe that does not import OHBE
- Execution and experiment records of the egress policy version and image digest
- LiteLLM OSS gateway client (`litellm==1.104.2`,
  `ghcr.io/berriai/litellm:v1.104.2`) that sends pool metadata and records
  token usage
- Spend/response-cost kept as a proxy signal. It is not an OHBE price
- OpenBot black-box adapter with a LiteLLM-only benchmark profile
- Inspect AI `react()` reference adapter (`inspect-ai==0.3.277`) that runs
  the same pilot task and records the same task and model-pool identity
- Objective scorers (`contains`, `match`, `exact`) and a failure taxonomy that
  keeps infrastructure failures out of the task-failure count
- Offline cost calculator over `pricing/ohbe-offline-2026-10-09.yaml`,
  including token list prices and `normalized_gpu_seconds` for local models
- Paired aggregation: repetitions, timeout, budgets, and observed metrics
- Fixture pilot audit with a JSON gate report and an `ohbe.report_input` bundle
- Report renderer: `benchmark report` writes JSON, JSONL, Markdown, and HTML
  from that bundle, including the release-gate checklist
- HTTP surface `ohbe.api` (`/v1`) for status, doctor, the fixture catalog, and
  dry-run validation. It does not spend
- React + Vite workbench in `workbench/` that calls that surface

## What v0.1 scaffolding does not include

Steps C–J are the engine scaffolding. These items stay deferred:

- Oracle scoring (stub only; not computed)
- Routing regret (stub only; not computed)
- A claimed OpenBot-versus-Inspect production ranking
- A dashboard inside the engine report
- Tool-surface parity between OpenBot and Inspect
- Live spend on pull requests. `benchmark run` is manual, writes only what the
  harnesses returned, and is not part of `.github/workflows/ci.yml`

The workbench under `workbench/` is a separate client of the HTTP surface. It
shows the fixture catalog, the five evaluation readings, a dry-run
checklist, a Rounds page (方案 / Rounds), a Monitor page (监测 / Monitor), 模型入库 / Model Intake, and
Harness 预装 / Harness Preinstall. It is not an in-engine
dashboard, and it does not rank harnesses or spend against a provider. The
evaluation framework — object, gaps, design goals, pipeline, and research
questions — is
[docs/evaluation-framework.md](docs/evaluation-framework.md). That note also
defines the round lifecycle (`DraftPlan → ConfirmedSuite → Running →
RoundReport → ForkPlan`) and two modes. `paired_harness` is the default:
OpenBot versus Inspect `react()` on one shared pool, through mandatory
LiteLLM, inside the Docker egress policy. `cross_model` is the secondary
workbench: one harness stays fixed, the suite stays fixed, and models vary
so a cost/quality frontier can inform model choice and later OpenBot
adaptation. It is not a model-arena leaderboard. Oracle routing stays
deferred. Live spend on `/v1/rounds` stays dual-locked, and start is a
dry-run. The comparison under the 方案 list is still one shared slot board.
The per-plan target is [docs/plan-session-isolation.md](docs/plan-session-isolation.md).

Model-pool intake fields — local versus cloud/remote, keys kept out of pool
YAML, admit only when connectivity and policy both pass, and the `model_id` /
`litellm_model` / pricing triad — are the shared OHBE and OpenBot standard in
[docs/model-pool-intake.md](docs/model-pool-intake.md). OpenBot 平台端 and
企业端 use the same fields, key policy, and admit gates. OHBE-only fail-closed
extras are called out in that note.

The report states observed fixture results and uncertainty. A difference in
cost or success rate is not evidence of routing optimality and is not evidence
that one model is independent of another. OHBE core does not import Inspect
AI, LiteLLM, the environment package, the gateway package, harness adapters,
the evaluator, the cost calculator, the aggregator, the audit, or the report
renderer. The benchmark process does not call `litellm.completion`; only the
proxy process may reach a model provider. `inspect-ai` is an optional adapter
extra, not a core dependency.

## Requirements

Python 3.12 or newer, [uv](https://docs.astral.sh/uv/) or pip, Pydantic v2,
and pytest.

```bash
uv sync --extra dev --extra gateway
uv run pytest
uv run benchmark --help
```

With pip:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,gateway]"
.venv/bin/pytest
.venv/bin/benchmark --help
```

## Layout

```text
src/ohbe/core/          schemas, canonical hashes, JSONL traces
src/ohbe/environment/   egress policy, socket guard, Docker runner
src/ohbe/gateway/       LiteLLM proxy config, metadata, usage reconciliation
src/ohbe/adapters/      harness contract, OpenBot adapter, Inspect react() adapter
src/ohbe/eval/          objective scorers and failure categories
src/ohbe/cost/          versioned offline price table and cost engine
docs/                   evaluation framework, live-pilot notes, private-deploy checklist
docs/model-pool-intake.md     canonical intake fields (OHBE + OpenBot 平台端 / 企业端)
docs/evaluation-framework.md  harness taxonomy, gaps, pipeline, and RQs
src/ohbe/aggregate/     paired-run metrics and JSON/JSONL export hooks
src/ohbe/audit/         fixture pilot audit and the ohbe.report_input bundle
src/ohbe/report/        JSON, JSONL, Markdown, and HTML report renderer
src/ohbe/api/           HTTP surface: status, doctor, catalog, dry-run, rounds
workbench/              React + Vite client of `/v1`, including 方案 / Rounds
docs/sdk-surface.md     how `/v1` maps onto a future SDK
pricing/                offline price table ohbe-offline 2026-10-09
results/fixture-pilot/  checked-in fixture/demo report (not live spend)
docker/compose.yaml     internal-network probe (mock gateway + model caller)
docker/litellm/         pinned OSS proxy config and compose topology
docker/workbench/       Linux host-network UI, plus compose.desktop.yaml for Docker Desktop
Makefile                make doctor, workbench-up, and workbench-desktop-up
models/pools/               GPU-farm pool template (REPLACE_ME ids, no secrets)
experiments/live-pilot-smoke.yaml  manual paired-run manifest (no observed spend)
docs/live-pilot.md                 pins, LiteLLM URL, egress, audit, and report
results/live/                      live artifacts; gitignored except README
src/ohbe/live/                     manifest loader and benchmark run
src/ohbe/cli.py         benchmark version / verify-trace / check-egress / audit-pilot / report / run / doctor
.github/workflows/ci.yml  pull_request and main: pytest, plus a non-skipped egress job
docs/ci-local-fallback.md       when Actions runners cannot schedule: local pytest gate
.github/workflows/live-pilot.yml  workflow_dispatch only; fails closed without secrets
tests/                  round-trip, integrity, usage, boundary, egress, gateway, report, live pilot
```

## Egress

A tool sandbox with `network: none` does not cover the process that talks to
models. OHBE controls that process.

The controlled-track policy (`ohbe-egress-0.1`) is default deny. It allows
exactly one gateway host and port. Known provider hosts, including
`api.openai.com` and `api.anthropic.com`, cannot be configured as that
gateway. `EgressSimulator` checks the policy before any transport.
`EgressGuard` installs the same rule on `socket.getaddrinfo` and
`socket.socket.connect` for the life of the context: denied names fail before
DNS, and a connect to an address that was not resolved from the gateway fails
before the kernel sends a packet.

The Docker runner is the release gate for a caller that skips the Python
guard. `DockerEgressRunner` creates an `--internal` network, starts
`python:3.12-alpine` as the gateway mock (`ohbe-gateway:8080`), and runs a
stdlib urllib probe in a second container on that network. The probe does not
import OHBE. A successful run requires HTTP 200 from the gateway whose body
contains `gateway`, and failure for `https://api.openai.com`,
`https://api.anthropic.com`, and literal `http://1.1.1.1/`.

On hosts where `iptables-legacy` FORWARD policy is DROP, Docker only accepts
`docker0`, so same-bridge packets time out. The runner inserts one
same-bridge ACCEPT on `DOCKER-FORWARD` for the network it created and removes
it on cleanup. External forwarding stays dropped. `docker/compose.yaml`
documents the same topology; compose itself does not insert that host rule.

The runner writes `ExecutionEnvironment` onto the probe report: policy
version, gateway host and port, image reference, and `sha256:` image digest.
`Execution` and `Experiment` store that record in an optional `environment`
field. Core does not start containers.

```bash
benchmark check-egress
```

Exit 0 prints the policy version and image digest. Exit 1 means a bypass or
a runner error. Exit 2 means the Docker daemon is not available. On a
checkout without a daemon, pytest skips
`test_docker_blocks_provider_egress_and_allows_gateway`; the policy simulator
and socket-guard tests still run.

GitHub Actions does not accept that skip. `.github/workflows/ci.yml` has two
jobs, both on `ubuntu-latest` (Docker is installed there), triggered by every
pull request and by pushes to `main`:

| Job | What it proves |
| --- | --- |
| `pytest` | Python 3.12, `pip install -e ".[dev]"`, then the full `pytest` suite. The image `python:3.12-alpine` is pulled first so the live probe is not charged for the download. |
| `egress` | `docker info` must succeed. The same image is pulled. Pytest then runs only `test_docker_blocks_provider_egress_and_allows_gateway` and writes JUnit XML. The job exits non-zero unless that file records exactly one test, zero skips, zero failures, and zero errors. `benchmark check-egress` must then exit 0. |

A skipped probe is a green pytest process, so the JUnit check is what fails
the `egress` job. The probe talks to the in-network gateway mock. It is not
live OpenBot spend. The other optional skips (`OPENBOT_BIN`, the `inspect`
extra, `OHBE_INSPECT_LIVE`) stay local and are not this gate.

If Actions fails to schedule runners (empty steps, ~2s red check, or no
available runners), treat that as infrastructure — not a diff failure —
and verify with local pytest / workbench tests. See
[docs/ci-local-fallback.md](docs/ci-local-fallback.md).

## Traces

`TraceLog` appends one canonical JSON object per line. Each record stores
`prev_hash` and `record_hash`. The first record's `prev_hash` is the genesis
digest (`0` repeated 64 times). Verification recomputes every digest and
checks that sequence numbers stay contiguous.

The chain detects a changed, reordered, or partially rewritten record. It is
not a signature: rewriting a record and every hash after it yields another
internally valid chain. After a run is sealed, pin the head with
`TraceLog.verify(expected_head=...)` or `ExecutionTrace.head_hash`.

## Usage and price

`TokenUsage.status` is `present`, `missing`, or `invalid`. Counts exist only
when status is `present`, and a real zero must be supplied explicitly.
`PricingRef` points at a versioned table (id, version, optional URI and
external content hash). Core does not compute a price. `CostAssessment`
stays `not_computed` or `invalid`, with a null amount, until a later
calculator writes an explicit `computed` value.

## LiteLLM gateway

Install the optional extra when you want the version pin checked locally:

```bash
pip install -e ".[gateway]"
```

The controlled track publishes one model pool to LiteLLM OSS
`1.104.2` (`ghcr.io/berriai/litellm:v1.104.2`, not an enterprise image).
`render_proxy_config` writes a `model_list` that contains only that pool.
`virtual_key_request` repeats those model ids and does not send `*`. Upstream
API keys are `os.environ/NAME` references.

`GatewayClient` runs in the benchmark process. Its sockets are wrapped by the
Step D egress guard, so the only host it can reach is the proxy
(`ohbe-gateway:4000` in `docker/litellm/compose.yaml`). The model-caller
service is attached only to the internal network. The proxy service is also
attached to `upstream`, which is the path that may reach a provider. Do not
call `litellm.completion` from benchmark code; that SDK can open provider
connections itself.

Each call sends:

- `x-litellm-spend-logs-metadata`: JSON with `benchmark_run_id` (the
  experiment id), `execution_id`, `task_id`, `harness_id`, `purpose`, and,
  when known, `invocation_id` and `model_id`
- `x-litellm-tags`: the same fields as `key:value` tags
- the same object under `metadata.spend_logs_metadata` in the JSON body

Token counts come from the chat-completions `usage` object. A missing,
partial, non-integer, negative, or inconsistent object becomes `missing` or
`invalid` with null counts. An explicit zero stays `present`. By default
`GatewayClient.complete` raises `UsageNotAvailable` in the missing and invalid
cases after the invocation has been built, so a caller can store it and cannot
treat the call as zero tokens.

`x-litellm-response-cost` (or a body `response_cost`) is stored as a spend
signal, including an explicit `"0"`. It is not copied into
`CostAssessment.amount`. When usage is present, cost stays `not_computed` for
the later offline calculator. When usage is missing or invalid, cost is
`invalid`. `gateway_environment` records the pinned image ref on
`Execution.environment`; pass the repo digest when you have pulled it.

```bash
# from a checkout, with LITELLM_MASTER_KEY and provider keys set
docker compose -f docker/litellm/compose.yaml up litellm
```

Pytest covers the proxy protocol with a local mock. The live Docker probe
from Step D still skips when `docker info` fails. The LiteLLM version assertion
skips when the `gateway` extra is not installed.

## OpenBot adapter

`OpenBotAdapter` is a black-box CLI adapter. It does not import OpenBot.
`HarnessAdapter.run` takes one task, the model pool, the harness config, and
the egress environment, and returns an `Execution` plus an `ExecutionTrace`.
The trace is the source of truth. OpenBot stdout is an `openbot.transcript`
event marked supplementary.

The harness version is the OpenBot git commit. Pass a 40-character lowercase
SHA. When the private image is not available, use the placeholder
`openbot-commit-unavailable` and the image name `openbot-image-unavailable`.
Do not record a floating tag such as `latest`. The execution stores that
version on `harness.version`, the harness `config_hash`, and `model_pool_id`.

The benchmark profile allows only provider `litellm`. `baseUrl` is the gateway
host and port from `ExecutionEnvironment` (for the pinned proxy,
`http://ohbe-gateway:4000`). `apiKey` is the reference `${LITELLM_API_KEY}`,
never a literal provider key. `agents.defaults.models` is the pool allowlist
(`litellm/<model_id>`). Delivery, elevated tools, and channels are off. A
tool sandbox with `docker.network: none` does not lock the host process that
calls models, so the profile does not use that as the egress control. Run
OpenBot on the OHBE internal network, with `LITELLM_API_KEY` set and provider
keys unset.

```bash
export OPENBOT_BIN=/path/to/openbot
export LITELLM_API_KEY=...
export OPENBOT_STATE_DIR=/tmp/ohbe-openbot-state
```

`OPENBOT_STATE_DIR` must be an isolated directory, not the user home. The
adapter runs:

```bash
openbot onboard --non-interactive --auth-choice litellm-api-key
openbot agent -m "<task prompt>" --json --timeout 600 --local
```

It does not pass `--deliver`. After onboard, OHBE writes `openbot.json` in the
state directory so the allowlist stays authoritative.

Docker, when the image exists, joins the internal network rather than `none`:

```bash
docker run --rm --network ohbe_egress \
  -e OPENBOT_STATE_DIR=/state \
  -e LITELLM_API_KEY \
  -v "$OPENBOT_STATE_DIR:/state" \
  openbot@<git-sha> \
  openbot agent -m "<task prompt>" --json --timeout 600 --local
```

Replace `openbot@<git-sha>` with the pinned image. Until that image is
published, keep `openbot-image-unavailable` and run the CLI binary instead.
Pytest uses a fake `openbot` executable. A test that runs the real binary is
skipped unless `OPENBOT_BIN` is set.

## Inspect react() adapter

`InspectReactAdapter` implements the same `HarnessAdapter` contract as
`OpenBotAdapter`. It maps one OHBE task onto an Inspect `react()` eval and
writes an OHBE trace. The Inspect log is an `inspect.transcript` event marked
supplementary. Importing `ohbe` or `ohbe.adapters` does not import Inspect.

Install the optional extra:

```bash
pip install -e ".[inspect]"
```

That extra is pinned to `inspect-ai==0.3.277`. It is not part of the core
install. Inspect `0.3.277` reads `LITELLM_PROXY_BASE_URL` and `LITELLM_API_KEY`
(`LITELLM_PROXY_API_KEY` is the Inspect alias for the key). OHBE sets the base
URL to `http://<gateway-host>:<gateway-port>/v1` from `ExecutionEnvironment`,
because the OpenAI client appends `/chat/completions`. The model name is
`litellm-proxy/<model_id>`. Live evals pass `model_args={"model_info": False}`
so construction does not call `/model/info` on the proxy.

The solver is `react()` with the `bash` tool and `sandbox="docker"`. An
`expected.contains` target uses the `includes` scorer. An `expected.match`
target uses the `match` scorer. The Docker sandbox isolates tools. It does
not lock the host process that calls the model. That process still has to
use the OHBE gateway URL above.

`pilot_task()` is the shared sample (`ohbe-pilot-echo`, prompt
`Reply with the single word pong.`, target `pong`). `pilot_requests()` builds
two `HarnessRunRequest` values that share that task, the model pool, and the
egress environment. OpenBot and Inspect receive the same `task_id`, input,
pool id, and gateway. Their harness config hashes differ because the
harnesses differ. Neither execution records oracle or routing regret.

Pytest runs both adapters on that task. OpenBot uses the fake CLI. Inspect
uses an injected eval runner, so CI does not install Inspect or start a
proxy. The version check skips when the extra is absent. A live `react()`
eval is skipped unless `OHBE_INSPECT_LIVE=1` and `LITELLM_API_KEY` are set,
with the proxy reachable at the environment's gateway.

```bash
pip install -e ".[dev,inspect]"
pytest tests/test_paired_adapters.py
# optional, against a running OHBE LiteLLM proxy:
OHBE_INSPECT_LIVE=1 LITELLM_API_KEY=... pytest tests/test_paired_adapters.py -k live
```

## DeepSeek Harness adapter

`DeepSeekHarnessAdapter` implements the same `HarnessAdapter` contract as
`OpenBotAdapter` and `InspectReactAdapter`. It does not import DeepSeek
Harness. One task, the model pool, and the egress environment are the inputs.
The trace is the source of truth. Headless stdout is a `dsh.transcript` event
marked supplementary. The adapter does not enter `HARNESS_ORDER` and does not
unlock live spend. It does not record oracle scores or a harness ranking.

The harness version is the npm pin `@deepseek-ai/dsh@0.2.1-alpha.2`, which is
git tag `dsh-v0.2.1-alpha.2` and tarball
`https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-alpha.2.tgz`.
`latest` is rejected. Point `DSH_BIN` at that `dsh` binary and set
`DSH_VERSION` to `0.2.1-alpha.2` before the harness library will admit the
catalog row. A click without that local pin returns `install_unsupported`.
OHBE does not `npm install` it and does not pip-install a floating name.
The library profile stays `live_run: rejected`.

The same Harness library preinstall path also records WorkBuddy `5.7.7`,
OpenClaw npm `2026.10.1`, 千问办公 `1.2.5`, and 豆包工作 Windows `2.31.10`.
OpenClaw and WorkBuddy have opt-in adapters, described below. 千问办公 and
豆包工作 have closed adapters: `run` refuses before a process or a model
call. See `docs/qwen-work.md` and `docs/doubao-work.md`. 千问办公 does not
store the floating `/release/latest/` download. 豆包工作 stores the two
Windows CDN URLs and does not execute them. macOS still has no versioned
URL. Desktop and npm clicks return `install_unsupported` unless a local
version variable and binary already match. A matching 千问办公 or 豆包工作
pin admits a profile with `runnable: false` and still does not launch the
binary. A remote target still needs a host and SSH auth, then
`inventory_unsupported`. None of them enter `HARNESS_ORDER`.

The headless overlay allows only provider `litellm`. `baseURL` is
`http://<gateway-host>:<gateway-port>/v1` from `ExecutionEnvironment`.
`apiKeyEnv` is `LITELLM_API_KEY`. The key is the child environment, never a
YAML literal. `agent-default-model` is the selected pool member. Other
providers, `DEEPSEEK_API_KEY`, and proxy variables are removed. `DSH_HOME` is
an isolated directory, not `~/.dsh`.

```bash
export DSH_BIN=/path/to/dsh
export DSH_VERSION=0.2.1-alpha.2
export LITELLM_API_KEY=...
```

The adapter runs:

```bash
dsh --profile headless --patch "$DSH_HOME/ohbe-litellm.patch.yml" "<task prompt>"
```

`dsh` 0.2.1 prints the final assistant text on stdout and exits 0 when the
turn completes. It does not print a usage object. Missing usage stays missing,
not zero. The shipped headless profile still mounts its own shell, filesystem,
and DeepSeek web-search plugins. That tool surface is not OpenBot and not
Inspect `react()` with `bash`. Stripping `DEEPSEEK_API_KEY` stops the official
search plugin from authenticating; it does not make the tools the same.

Pytest uses a fake `dsh` executable. A test that runs the real binary is
skipped unless `DSH_BIN` is set, and that test only asks for `--version`.
See `docs/deepseek-harness.md`.

## OpenClaw adapter

`OpenClawAdapter` implements the same `HarnessAdapter` contract. OpenClaw the
product is an orchestration and channel layer: the Gateway, chat channels,
and plugins. A plugin can bridge a turn to DeepSeek Harness, Codex, or Claude
CLI. This adapter does not do that, and it does not pretend that bridge is a
peer harness.

The runnable path is one embedded `openclaw agent exec` turn. The written
config pins `agentRuntime.id` to the built-in runtime `openclaw`, disables
plugins, leaves channels empty, and does not start the OpenClaw Gateway. The
only model provider is `litellm`. `baseUrl` is
`http://<gateway-host>:<gateway-port>/v1` from `ExecutionEnvironment`.
`apiKey` is `${LITELLM_API_KEY}`. The key is the child environment, never a
YAML literal. The harness version is npm `openclaw@2026.10.1` (git tag
`v2026.10.1`, tarball
`https://registry.npmjs.org/openclaw/-/openclaw-2026.10.1.tgz`). `latest` is
rejected. The adapter does not enter `HARNESS_ORDER` and does not unlock live
spend. It does not record oracle scores or a harness ranking.

Point `OPENCLAW_BIN` at that binary and set `OPENCLAW_VERSION` to `2026.10.1`
before the harness library will admit the catalog row. The saved profile
labels `execution-path: embedded-agent`, `product-layer: orchestration-channel`,
and `plugin-bridge: refused`. `live_run` stays `rejected`. The rounds
whitelist `0.1.0` is a separate gate.

The exec tool surface is the `coding` profile with the sandbox off and
filesystem tools scoped to an isolated `--cwd`. That is not OpenBot and not
Inspect `react()` with `bash`. `--json` usage stays missing unless the
envelope has `input`/`output`/`total` (or chat-completions token fields).
`costUsd` is not an OHBE cost.

```bash
export OPENCLAW_BIN=/path/to/openclaw
export OPENCLAW_VERSION=2026.10.1
export LITELLM_API_KEY=...
```

Pytest uses a fake `openclaw` executable. A test that runs the real binary is
skipped unless `OPENCLAW_BIN` is set, and that test only asks for `--version`.
See `docs/openclaw.md`.

## WorkBuddy adapter

`WorkBuddyAdapter` implements the same `HarnessAdapter` contract. WorkBuddy
5.7.7 is a desktop app: three installers (macOS arm64, macOS x64, Windows
x64) and no Linux artifact. OHBE does not run those installers. A click
without `WORKBUDDY_VERSION=5.7.7` and an executable `WORKBUDDY_BIN` returns
`install_unsupported`. The saved profile says `desktop-install: unsupported`.

The runnable path is the CodeBuddy Code CLI bundled in that app, one
`codebuddy --print --output-format json` turn. It is not the WorkBuddy GUI
and not a model source piped into DeepSeek Harness. `models.json` names only
provider `litellm`. `url` is
`http://<gateway-host>:<gateway-port>/v1/chat/completions` from
`ExecutionEnvironment`. `apiKey` is `${LITELLM_API_KEY}`. The key is the
child environment, never a YAML literal. `CODEBUDDY_BASE_URL` is stripped:
that variable is the Anthropic-protocol override and can point at a vendor.
`-y` is not passed. WebFetch and WebSearch are disallowed. The harness
version is the desktop pin `5.7.7`, which is not the CLI `--version` string.
`latest` is rejected. The adapter does not enter `HARNESS_ORDER` and does
not unlock live spend. It does not record oracle scores or a harness ranking.

```bash
export WORKBUDDY_BIN=/path/to/codebuddy
export WORKBUDDY_VERSION=5.7.7
export LITELLM_API_KEY=...
```

Pytest uses a fake `codebuddy` executable. A test that runs the real binary
is skipped unless `WORKBUDDY_BIN` is set, and that test only asks for
`--version`. See `docs/workbuddy.md`.

## Objective evaluation

`score_task` reads `expected.contains`, then `expected.match`, then
`expected.exact`. The pilot task is a contains check for `pong`. `exact` and
`match` compare the stripped answer to the target. None of the scorers treat
a missing answer as a pass.

`judge_execution` recomputes that check from the trace. Inspect answers come
from `inspect.transcript`. OpenBot answers come from the agent stdout `text`
field. DeepSeek Harness answers come from `dsh.transcript`. OpenClaw answers
come from `openclaw.transcript`. WorkBuddy answers come from
`workbuddy.transcript`. An `evaluation.answer` event overrides those
transcripts. The trace stays the source of truth.

Failure categories are `timeout`, `budget`, `harness_error`, `eval_fail`,
`invalid_usage`, `egress_violation`, `infra_invalid`, and `cancelled`.
`eval_fail` is a task failure: the run stayed inside the protocol and missed
the objective. The other categories are invalid runs. They are counted apart
from task failures and they are left out of the success rate.

## Offline cost

Prices come from a YAML file, not from a provider and not from
`x-litellm-response-cost`. The committed table is
`pricing/ohbe-offline-2026-10-09.yaml` (`pricing_id` `ohbe-offline`, version
`2026-10-09`). Rates are decimal strings. A YAML float is rejected.
`load_pricing_table` hashes the file bytes. `price_trace` requires a
`PricingRef` whose id, version, and `content_hash` match that file.

Two meters share that file:

| Meter | What the row stores | `provider_cost` | `normalized_cost` |
| --- | --- | --- | --- |
| `tokens` | `prompt_per_million`, `completion_per_million` | The list price | The same list price |
| `normalized_gpu_seconds` | `gpu_type` pointing at `gpus[].gpu_cost_per_hour` | Computed `0` when the GPU price is computed. Local weights have no provider token invoice | Reported `normalized_gpu_seconds` times `gpu_cost_per_hour / 3600` |

The per-second rate is not written in the file. CostEngine divides the hourly
decimal by 3600. `rented-placeholder` at `"3.60"` is an offline stand-in so
that quotient is `0.001`. It is not an invoice. Replace the GPU type and the
hourly rate with the rental you record, then pin the new content hash.
`vllm/REPLACE_ME-instruct` and `ollama/REPLACE_ME` are the smoke-pool ids
and use that stand-in.

```bash
python -c "from ohbe.cost import load_pricing_table; print(load_pricing_table('pricing/ohbe-offline-2026-10-09.yaml').content_hash)"
```

Each `invocation.completed` event supplies token counts. The event's
`cost_amount` is ignored and is not copied into `provider_cost`. Missing or
invalid usage makes both fields invalid and makes the execution total
invalid, with a null amount. A later call's price is not used to fill the
gap. Present zeros are a computed `0`. For `normalized_gpu_seconds`, token
usage must be present and the event must also carry
`normalized_gpu_seconds` as a non-negative decimal string (a whole number of
seconds may be an integer). A JSON float is rejected. Missing seconds stay
null. They are not inferred from tokens or latency, and `provider_cost` is
not published as zero beside that gap. The execution total is
`normalized_cost`, summed only when every invocation was computed.

## Paired aggregation

`aggregate_paired` takes finished `(Execution, ExecutionTrace)` pairs for one
task and one model pool, plus a `PairedProtocol` (repetitions, timeout, and
optional token and cost budgets). It does not launch a harness. It judges
each trace, prices it, and builds one metrics row per harness:

- success rate, with a Wilson 95% interval when there is at least one valid run
- total model cost and cost per successful task
- prompt, completion, and total tokens
- mean latency, with a normal 95% interval when at least two latencies exist

A run that exceeds the timeout or a budget is invalid. It is not a task
failure, and its tokens are not added as zero. If any valid run is missing
usage, the harness cost and token totals are null rather than a partial sum.
Runs that set `oracle` or `routing_regret` are rejected. The report note is
fixed: observed results and uncertainty only.

`report_document`, `execution_documents`, `dump_json`, and `dump_jsonl` are
export hooks. They write canonical JSON. Calling `aggregate_paired` again on
the same records yields the same document.

## Pilot audit

The pilot suite is three objective micro-tasks:

| Task | Scorer | Target |
| --- | --- | --- |
| `ohbe-pilot-echo` | contains | `pong` |
| `ohbe-pilot-exact` | exact | `ok` |
| `ohbe-pilot-match` | match | `ready` |

`benchmark audit-pilot` scores a fixture corpus. It does not call OpenBot,
Inspect, or a model, and it does not invent spend. Each task is run on both
harnesses with the same input, egress environment, model pool, timeout, and
budgets. The echo task includes a missing-usage execution. The exact task
includes an objective miss. The match task includes a timeout.

The command writes an audit JSON document (`kind` `ohbe.pilot_audit`). Each
gate is `passed` true or false and carries evidence pointers. The gates are:

- same task input
- same initial egress environment
- same model pool and selected model
- same timeout, budgets, and repetitions
- missing usage is null rather than zero, including no partial cost sum
- timeout runs are invalid and are not task failures
- harness, model, task, schema, and pricing versions are recorded
- objective checks are stable for contains, exact, and match
- provider bypass fails the in-process egress policy; the checklist names
  `tests/test_egress_policy.py`,
  `tests/test_docker_egress.py::test_docker_blocks_provider_egress_and_allows_gateway`,
  and `benchmark check-egress`
- no oracle or routing-regret claim

`--report-input` writes `ohbe.report_input`: the audit document plus one
metrics document per task. `benchmark report` reads that object and nothing
from a live provider. The pricing URI on the fixture pool is the filename
`pricing/<table>.yaml`, so a relative path and an absolute path to the same
file produce the same pool hash.

```bash
benchmark audit-pilot \
  --pricing pricing/ohbe-offline-2026-10-09.yaml \
  --output audit.json \
  --report-input report-input.json
```

Known limitations, also stored on the audit document:

- Tool-surface parity is not claimed. OpenBot's tool profile and Inspect's
  `bash` Docker sandbox are not the same surface.
- Oracle scoring and routing regret are deferred.
- The executions are fixtures, not live model spend.
- This command's gateway check is the process policy. The Docker probe is
  still the network release gate.

## Report

`benchmark report` renders one `ohbe.report_input` file into a directory:

```bash
benchmark report --input report-input.json --out ./report-out
```

The directory receives `report.json`, `metrics.jsonl`, `report.md`, and
`report.html`. Rendering is a pure function of the parsed input: there is no
generation timestamp, and the same input produces the same file bytes. Exit 0
means the files were written, including when the audit itself failed. The
report then says the audit did not pass. Exit 2 means the input could not be
read. A null cost or token total is printed as null. It is not zero.

`report.json` records schema version `0.1`, the OHBE version, git commits and
harness versions present on the executions, the egress policy version, the
gateway, the image digest, and the pricing id, version, and content hash.
`routing_optimality` and `model_independence` are null.

The release-gate checklist in the Markdown, HTML, and `report.json` is:

| Gate | What it checks |
| --- | --- |
| `same_task_input` | Both harnesses record the same task id and input |
| `same_initial_environment` | One egress policy, gateway, and image |
| `same_model_pool` | One model pool and selected model id |
| `same_protocol` | Same timeout, budgets, and repetitions |
| `missing_usage_not_zero` | Missing usage stays null, with no partial cost |
| `invalid_not_task_failure` | Timeout and other invalid runs are not task failures |
| `versions_recorded` | Harness, model, task, schema, and pricing versions |
| `evaluator_reliability` | contains, exact, and match match the fixture roles |
| `gateway_bypass_denied` | Provider bypass fails the policy; Docker probe is named |
| `no_routing_claim` | Oracle and routing regret stay unset |

A checked-in sample from this fixture corpus lives in
`results/fixture-pilot/`. That sample is a fixture/demo. It is not live
OpenBot or Inspect production spend. Regenerate it with the commands in
`results/fixture-pilot/README.md`.

## Live pilot

OpenBot stays on the private server. OHBE calls LiteLLM on the rented GPU
server, and that proxy serves local vLLM and Ollama models only.
`experiments/live-pilot-smoke.yaml` names the three objective tasks, both
harnesses, and the GPU gateway `gpu-litellm:4000` as the sole egress
allowlist. Model ids are `REPLACE_ME` markers in
`models/pools/pool-gpu-farm.example.yaml`. The manifest does not store
observed tokens, prices, or cloud API keys. `benchmark doctor` checks
`LITELLM_BASE_URL`, `LITELLM_API_KEY`, that pool file, and the manifest
without opening a socket. `OPENBOT_BIN` is optional for the doctor.
`benchmark run --experiment …` refuses to
start when `OPENBOT_BIN`, `OPENBOT_COMMIT`, `LITELLM_API_KEY`, or
`LITELLM_BASE_URL` is missing, and it refuses a URL on OpenAI, Anthropic, or
HuggingFace. When the pins match the experiment, it runs the adapters, judges
the traces, prices reported usage when the model id is in the offline
table, aggregates, and writes `results/live/<experiment_id>/`. The smoke
ids are `normalized_gpu_seconds` rows, so a present token count is priced
only when the trace also records `normalized_gpu_seconds`. Then
`benchmark report` renders that `report-input.json`.

Install and a first smoke against the private OpenBot host and the GPU
LiteLLM are listed in `docs/private-deploy-checklist.md`. That checklist
does not start a paid run and does not change the manual workflow.

`results/fixture-pilot/` stays the checked-in fixture demo. `benchmark
audit-pilot` does not read the live directory. Default CI does not run the
live command and does not clone the private OpenBot repository.
`.github/workflows/live-pilot.yml` is `workflow_dispatch` only and exits
before `benchmark run` when a secret is empty. Pins, the proxy URL, and the
egress network are documented in `docs/live-pilot.md`.

## Workbench and the future SDK

Layering is `ohbe` core, then the HTTP API, then the workbench consumer.
`ohbe.api` is a thin FastAPI adapter, installed with the `api` extra
(`fastapi`, `httpx`, `uvicorn`). Core dependencies do not include it. The
routes under `/v1`, plus `GET /health`, are the public surface a future SDK
binds. `workbench/` only calls that surface. The map is `docs/sdk-surface.md`
and `GET /v1`.

```bash
pip install -e ".[dev,api]"
pytest
uvicorn ohbe.api.app:app --host 127.0.0.1 --port 8000
```

The server listens on `127.0.0.1:8000`. From `workbench/`, `npm install` and
`npm run dev` serve the UI on `127.0.0.1:5173` and proxy `/v1`. The header
toggle **中文 | English** changes UI labels only. See `workbench/README.md`
(For testers). API payloads stay English, and the workbench does not spend.

`POST /v1/dry-run` validates a gateway URL without opening a socket. Live
spend is locked unless the JSON field `live` is boolean `true` and
`OHBE_ALLOW_LIVE_SPEND` is exactly `1`. Even then this surface does not place
a model call. Hosted LiteLLM names (`litellm.ai`, `litellm.com`,
`litellm.cloud`, `berri.ai`, and their subdomains) are denied with HTTP 403
`cloud_litellm_denied` before any connection, including when both live flags
are set. The same denial is enforced on `NetworkEgressPolicy` and
`GatewayClient`.

## For testers

Use the workbench to look at this checkout. The pages read the HTTP surface.
They show the checked-in fixture report. Starting the UI does not run
`benchmark run` and does not call a model.

From the repository root, with Docker:

```bash
make workbench-up
```

Open <http://127.0.0.1:8080>. On a Linux private host, nginx and the API bind
loopback (`127.0.0.1:8080` and `127.0.0.1:8765`) on the host network, so a
Docker bridge is not required. macOS and Windows Docker Desktop cannot use
that host network: it does not reach the Mac loopback. Use
`make workbench-desktop-up` (`docker/workbench/compose.desktop.yaml`), which
publishes the same loopback ports on a bridge. That target pins Docker
context `desktop-linux` and uses `--pull never` so Hub IPv6/oauth flakes do
not re-pull base images already on the machine. `OHBE_ALLOW_LIVE_SPEND` is
empty inside the API container. The browser uses port 8080; nginx proxies
`/v1` and `/health`. Stop the Desktop stack with `make workbench-desktop-down`.
Mac git `insteadOf` and Hub notes: `docs/private-deploy-checklist.md`
「本机开发者注意（Mac）」.

| Page | What you should see |
| --- | --- |
| 总览 (`/`) | Engine version, `live_spend` locked, `spend_execution` disabled. 联调 is a summary on this page |
| 准备 | 模型池 (`/pools`) and Harness 库 (`/harnesses`) |
| 评测/方案 (`/rounds`) | Fixture catalog, comparison slots, and suite confirm. After confirm, 「去监测」 opens Monitor. `/experiments` opens this page |
| 监测 (`/monitor`) | Dry-run observation for a confirmed plan. `paired_harness` and `cross_model` share one shell. Spend stays locked |
| 报告 (`/results`) | Post-run report, linked from the end of a round. Default is fixture pilot `fixture-pilot` (`spend` `not_live`). A live `report.json` already under `results/live/` can be opened on the same page. Opening it does not unlock spend |
| 联调 (`/doctor`) | Opened from 总览. In-process checks: fixture report, egress denial, spend lock |

Confirm from the shell:

```bash
curl -fsS http://127.0.0.1:8080/v1/status
curl -fsS http://127.0.0.1:8080/v1/doctor
curl -fsS http://127.0.0.1:8080/v1/results
```

`/v1/status` reports `"live_spend":"locked"`. `/v1/results` includes
`fixture-pilot` (`source_kind` `fixture`, `spend` `not_live`). When
`results/live/<experiment_id>/report.json` or
`results/live/<experiment_id>/report/report.json` is already on disk, that
report is listed with `source_kind` `live` and can be opened. Reading it does
not run the pilot and does not unlock spend. End to end is not connected by
this page.

The offline preflight is a separate command. It reads the current shell and
does not open a socket. Put the GPU LiteLLM URL and virtual key in that shell
only, then:

```bash
export LITELLM_BASE_URL=http://gpu-litellm:4000/v1
export LITELLM_API_KEY=...
make doctor
```

`make doctor` prints the LiteLLM-only OpenBot profile and the next smoke
commands. It does not run those commands, and it does not print the key.
Stop the UI with `make workbench-down`.

The same steps in Chinese, including the SSH tunnel onto the private OpenBot
host, are section 5.2 of `docs/private-deploy-checklist.md`.

## v0.1 status and how to merge

Steps C–J on branch `cursor/ohbe-core-schemas-e7f6` are the v0.1 engine
scaffolding. Review and merge that branch into `main` through
[pull request #1](https://github.com/upbeyond/OpenBot-Harness-Benchmark-Engine/pull/1).

From a checkout of this branch:

```bash
uv sync --extra dev --extra gateway
uv run pytest
uv run benchmark --help
uv run benchmark check-egress
uv run benchmark audit-pilot \
  --pricing pricing/ohbe-offline-2026-10-09.yaml \
  --output audit.json \
  --report-input report-input.json
uv run benchmark report --input report-input.json --out ./report-out
```

`pytest` is the schema, trace, cost, audit, and report check. `check-egress`
is the Docker network release gate and exits 2 when the daemon is absent.
`audit-pilot` scores the fixture corpus. `report` republishes that input.
The pull request runs `.github/workflows/ci.yml`: job `pytest` runs the full
suite, and job `egress` fails unless the live Docker probe executed and
`benchmark check-egress` exited 0. See the Egress section. That workflow does
not run `benchmark run` and does not need the private OpenBot repository.
The manual live pilot is `docs/live-pilot.md`. The private-host install
checklist is `docs/private-deploy-checklist.md`.

After the pull request is approved, merge it into `main` on GitHub, or from
a current `main`:

```bash
git fetch origin
git checkout main
git pull origin main
git merge --ff-only origin/cursor/ohbe-core-schemas-e7f6
```

## 私有部署联调

OpenBot 装在私有服务器上。租用的 GPU 服务器只通过 LiteLLM 提供本地开源模型。
OHBE 装在私有 OpenBot 主机上，`LITELLM_BASE_URL` 指向 GPU 机的代理。出站
白名单、`OPENBOT_BIN` 与 commit 钉死、冒烟步骤，以及可复现性要记下的内容，
写在 `docs/private-deploy-checklist.md`。表里的 `rented-placeholder` / `3.60`
是离线占位，不是账单。密钥以后再配。默认 CI 不会发起 live spend。

工作台的启动步骤在清单第 5.2 节，接在 `benchmark doctor` 之后。
`make workbench-up` 在 `127.0.0.1:8080` 提供页面和 `/v1`，API 在
`127.0.0.1:8765`。macOS / Windows 的 Docker Desktop 用
`make workbench-desktop-up`：宿主机网络到不了 Mac 的 loopback；该目标固定
`desktop-linux` context，并用 `--pull never` 避免 Hub 重拉本地已有基础镜像。
容器里不设置 `OHBE_ALLOW_LIVE_SPEND=1`，也不写入密钥。
页面默认仍是夹具 `fixture-pilot`。磁盘上已有的 live `report.json` 可以在报告页打开。这不表示端到端已经连通，也不解锁花费。
Mac 上 git `insteadOf` 与 Hub 注意见 `docs/private-deploy-checklist.md`「本机开发者注意（Mac）」。

## License

MIT. Copyright (c) 2026 upbeyond.
