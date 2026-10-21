# OHBE HTTP surface and the future SDK

`ohbe` is the engine. The FastAPI package `ohbe.api` is a thin adapter in
front of that engine. The routes under `/v1` are the public surface a future
SDK should bind. The React app in `workbench/` is one client of that surface.
It is not a second engine, and it does not score, price, or call a model.

Layering is one direction:

```text
ohbe core  →  HTTP API (ohbe.api)  →  workbench consumer
```

`ohbe` core owns schemas, egress, pricing, and the fixture audit. The HTTP
API adapts that core. The workbench is a consumer of the API. A future SDK
binds the same HTTP routes and does not sit beside the workbench as a second
engine.

A future Python or JavaScript client should call these routes (or a generated
client of `GET /v1/openapi.json`). It should not reimplement egress, the
offline price table, or the fixture catalog.

## Routes

| HTTP | Future SDK | Engine |
| --- | --- | --- |
| `GET /v1` | `OhbeClient.surface()` | `ohbe.api.surface` |
| `GET /health` | `OhbeClient.health()` | process liveness; no catalog read |
| `GET /v1/health` | `OhbeClient.health()` | same probe on the versioned surface |
| `GET /v1/status` | `OhbeClient.status()` | `ohbe.__version__`, schema version, spend lock |
| `GET /v1/doctor` | `OhbeClient.doctor()` | in-process checklist over policy, pricing, and the fixture report |
| `GET /v1/experiments` | `OhbeClient.experiments.list()` | fixture catalog |
| `GET /v1/experiments/{experiment_id}` | `OhbeClient.experiments.get(id)` | fixture catalog |
| `GET /v1/pools` | `OhbeClient.pools.list()` | `ohbe.audit.fixtures.pilot_pool` |
| `GET /v1/pools/{pool_id}` | `OhbeClient.pools.get(id)` | `pilot_pool` |
| `GET /v1/results` | `OhbeClient.results.list()` | `results/fixture-pilot/report.json`, plus `results/live/<id>/report.json` when that file is already on disk |
| `GET /v1/results/{result_id}` | `OhbeClient.results.get(id)` | that report; `source_kind` is `fixture` or `live` |
| `POST /v1/dry-run` | `OhbeClient.validate()` | `ohbe.environment.policy` |
| `GET /v1/catalog/models` | `OhbeClient.catalog.models()` | admitted catalog |
| `GET /v1/catalog/harnesses` | `OhbeClient.catalog.harnesses()` | install inventory and version-gated whitelists |
| `POST /v1/catalog/harnesses` | `OhbeClient.catalog.harnesses.select()` | select registered harness versions |
| `GET /v1/rounds/comparison-slots` | `OhbeClient.rounds.comparisonSlots()` | residual process list; plan tabs do not read it |
| `POST /v1/rounds/comparison-slots` | `OhbeClient.rounds.comparisonSlots.replace()` | `409 slots_unscoped`; use `PUT /v1/rounds/plans/{plan_id}/slots` |
| `GET /v1/rounds/plans` | `OhbeClient.rounds.plans.list()` | `ohbe.api.rounds` |
| `GET /v1/rounds/plans/{plan_id}` | `OhbeClient.rounds.plans.get(id)` | round plan document, including `slots_status` |
| `GET /v1/rounds/plans/{plan_id}/slots` | `OhbeClient.rounds.plans.slots(id)` | that plan's draft or frozen slot copy |
| `PUT /v1/rounds/plans/{plan_id}/slots` | `OhbeClient.rounds.plans.slots.replace(id)` | replaces that plan's draft copy; confirmed is `409 slots_frozen` |
| `POST /v1/rounds/plans` | `OhbeClient.rounds.plans.create()` | request `model_ids` become that plan's draft slots; does not read the global list and does not spend |
| `POST /v1/rounds/plans/{plan_id}/suite` | `OhbeClient.rounds.plans.suite(id)` | combinatorial draft, or an edited draft |
| `POST /v1/rounds/plans/{plan_id}/confirm` | `OhbeClient.rounds.plans.confirm(id)` | freezes `suite_hash` and that plan's stored slots |
| `POST /v1/rounds/plans/{plan_id}/start` | `OhbeClient.rounds.plans.start(id)` | Dry-run dispatch on `pool-pilot`; `executed` stays false |
| `POST /v1/rounds/plans/{plan_id}/fork` | `OhbeClient.rounds.plans.fork(id)` | new draft after a round report; copies the parent slot snapshot by value |
| `GET /v1/rounds/suites` | `OhbeClient.rounds.suites.list()` | draft and confirmed suites |
| `GET /v1/rounds/suites/{suite_id}` | `OhbeClient.rounds.suites.get(id)` | one suite |
| `GET /v1/rounds/reports` | `OhbeClient.rounds.reports.list()` | round reports |
| `GET /v1/rounds/reports/{report_id}` | `OhbeClient.rounds.reports.get(id)` | findings, five readings, frontier note |
| `GET /v1/rounds/jobs/{job_id}` | `OhbeClient.rounds.jobs.get(id)` | dispatch status and visible events |
| `GET /v1/rounds/jobs/{job_id}/events` | `OhbeClient.rounds.jobs.events(id)` | polling contract for the same events |
| `POST /v1/rounds/jobs/{job_id}/tick` | `OhbeClient.rounds.jobs.tick(id)` | advance the dry-run phase ladder |
| `GET /v1/rounds/jobs/{job_id}/stream` | `OhbeClient.rounds.jobs.stream(id)` | one-shot SSE replay; not a live socket |
| `GET /v1/analysis/model-fit` | `OhbeClient.analysis.modelFit()` | fixture recommendations under one harness |

`GET /v1` returns this map. The checked-in catalog is the fixture pilot. Its
`spend` is `not_live`. A null cost on a result is a missing total, not zero.

`GET /v1/results` keeps that fixture row and also lists a live report when
either `results/live/<experiment_id>/report.json` or
`results/live/<experiment_id>/report/report.json` is already on disk.
`<experiment_id>` matches `^[a-z0-9][a-z0-9-]{0,63}$` and is not
`fixture-pilot`. If both files exist, the directory-level `report.json` is
the one opened. `source_kind` is `live` for those rows and `fixture` for the
checked-in report. The fixture row stays first. This read does not run
`benchmark run`, does not call a model, and does not unlock spend. The
response does not include `routing_optimality`, `model_independence`, or an
oracle score. A null token total stays null.

## Spend

This surface does not place a model call. `spend_execution` is `disabled`.

`POST /v1/dry-run` validates a gateway URL and, when supplied, an experiment
id and a pool id. The response is `mode: dry_run`, `executed: false`,
`spend: none`, `network: not_contacted`.

Live spend stays locked unless both of these are present:

1. JSON field `live` is boolean `true` (the string `"true"` is rejected).
2. The process environment variable `OHBE_ALLOW_LIVE_SPEND` is exactly `1`.
   `true`, `yes`, and an empty string do not unlock it.

When both are present, the route still does not execute spend. The check
`live_not_executed` records that the flags were seen and that this surface
did not call a model. A later SDK method that actually spends would be a
separate engine call, still subject to the cloud rule below, and it is not
implemented here.

The round routes use the same lock. `POST /v1/rounds/plans`, suite, confirm,
start, fork, and tick accept `live: true` only when `OHBE_ALLOW_LIVE_SPEND`
is exactly `1`, and even then `executed` is false. `paired_harness` is the
default mode. `cross_model` fixes one harness and varies models for
adaptation signals. `GET /v1/analysis/model-fit` returns the fixture
frontier. It is not a model-arena leaderboard. Observation uses polling, or
a one-shot `text/event-stream` replay of the same stub events. Neither
transport contacts LiteLLM.

## Model Pool

The workbench model page is the Model Pool at `/pools`. It starts empty.
添加本地 / Add local and 添加云端 / Add cloud are the only add actions. Scan is
the discovery helper inside Add local, not a third action. There is no separate intake page.
A model enters `GET /v1/catalog/models` only when connectivity or handshake
succeeds and the LiteLLM gateway policy matches. A policy miss is saved as
pending and is not a comparison slot. The connectivity probe is a scoped GET
(`/health/liveliness` or `/v1/models`). It does not call `/v1/chat/completions`
and does not unlock live spend. The harness page is Harness 库 at `/harnesses`.
A detected allowed version can be marked 已预装. Only version-gated registered
harnesses enter plans, and live order stays `HARNESS_ORDER`. See
`docs/model-intake.md` for the fail-closed host rules.

## Cloud LiteLLM

Hosted LiteLLM is not an OHBE gateway. The controlled track talks to an
operator proxy such as `ohbe-gateway`. These registrable names, and any
subdomain of them, are denied before a socket is opened:

- `litellm.ai`
- `litellm.com`
- `litellm.cloud`
- `berri.ai`

The check is a suffix match after the host is normalized, so
`https://API.LiteLLM.ai/v1` and `https://org.proxy.litellm.ai/...` are denied.
A lookalike such as `litellm.ai.example.com` is a different registrable domain
and is not classified as this cloud. Dry-run still does not contact it.

Denial happens in four places, all in process:

1. `NetworkEgressPolicy` refuses to take a cloud host as the gateway.
2. `evaluate` denies a cloud destination even if it were the allowlisted host.
3. `GatewayClient` / `post_json` raises before `build_opener`.
4. `POST /v1/dry-run` returns HTTP 403 `cloud_litellm_denied`.

The 403 is the same when `live` is true and `OHBE_ALLOW_LIVE_SPEND=1`. The
error body contains the hostname, not the raw URL, so a userinfo secret is
not echoed. `spend` is `none` and `network` is `not_contacted`.

Provider hosts such as `api.openai.com` remain denied by the existing egress
policy. Configuring one as the gateway returns `gateway_rejected`.

## Run

```bash
pip install -e ".[dev,api]"
pytest
uvicorn ohbe.api.app:app --host 127.0.0.1 --port 8000
```

`python -m ohbe.api` starts the same uvicorn app. `GET /health` stays `ok`
when the fixture catalog is missing; catalog routes then return 503.
The API listens on `127.0.0.1:8000`. In another shell, from `workbench/`:

```bash
npm install
npm run dev
```

The Vite dev server proxies `/v1` to that API. The workbench UI is Chinese or
English. A header toggle (**中文 | English**) switches labels and stores the
choice in `localStorage` (`ohbe.workbench.locale`). Browser languages starting
with `zh` default to Chinese; every other language defaults to English. API
payloads stay English. The page set is 总览 / Overview, 实验 / Experiments,
模型池 / Model Pool, Harness 库 / Harness Library, 结果 / Results, 方案 / Rounds, and 联调 / Doctor.
`/pools` is the Model Pool. It lists admitted catalog entries and starts empty.
The only add actions on that page are 添加本地 / Add local and 添加云端 / Add cloud.
Scan discovers deployed models from inside Add local. Ollama stays `ollama list`. A vLLM base URL on the request or in `OHBE_VLLM_BASE_URL` also GETs `/v1/models`; the bearer is not stored. An unreachable listing fails closed. There is no separate intake page. That scan does not unlock live spend.
`/harnesses` is only the harness library. Install resolves a pinned pip spec or artifact URL and does not store `latest`. WorkBuddy `5.7.7`, OpenClaw `2026.10.1`, DeepSeek Harness `@deepseek-ai/dsh@0.2.1-alpha.2`, 千问办公 `1.2.5`, and 豆包工作 Windows `2.31.10` are catalog pins. OpenClaw's runnable adapter is an opt-in embedded agent (`docs/openclaw.md`): it does not start the OpenClaw Gateway and does not bridge the turn to DeepSeek Harness. WorkBuddy's runnable adapter is an opt-in bundled CodeBuddy CLI print (`docs/workbuddy.md`): it does not launch the desktop GUI and does not bridge the turn into DeepSeek Harness. A local npm click installs the pinned tarball with `npm install --ignore-scripts` and admits the profile when the bin matches. A desktop click downloads the package for this OS and does not launch it. Linux WorkBuddy is `os_unsupported`. 千问办公 returns `installer_unpinned` and does not download `/release/latest/`. Windows 豆包工作 downloads the pinned installer, does not launch it, and admits the closed profile. A matching `WORKBUDDY_VERSION` plus `WORKBUDDY_BIN`, `OPENCLAW_VERSION` plus `OPENCLAW_BIN`, `DSH_VERSION` plus `DSH_BIN`, `QWEN_WORK_VERSION` plus `QWEN_WORK_BIN`, or `DOUBAO_WORK_VERSION` plus `DOUBAO_WORK_BIN`, still admits that catalog row without fetching. 千问办公 and 豆包工作 stay closed (`docs/qwen-work.md`, `docs/doubao-work.md`): the admitted profile sets `runnable: false`, and `run` does not launch a process. A remote target requires SSH auth. The key or password is a request field or an environment-variable name and is not written to YAML. After auth, remote inventory is `inventory_unsupported` until a safe executor exists. These five stay outside `HARNESS_ORDER`. A live run still requires `HARNESS_ORDER` (`openbot`, `inspect_react`). These routes do not unlock live spend.
方案 configures a round, edits the combinatorial draft, confirms `suite_hash`,
and shows a dry-run dispatcher. Each plan keeps its own comparison slots.
Generating a plan stores the request `model_ids` as that plan's draft snapshot
and binds `pool-admitted` to them. Confirm freezes that copy. `POST
/v1/rounds/comparison-slots` returns `409 slots_unscoped`. A Dry-run job still
uses the fixture pool `pool-pilot`, is labeled `Dry-run`, and does not rank
models. Observation lines the slots up across the five dimensions and can hide
identical rows or highlight the better value. On this page the fixture
model-fit table appears only for `fixture-cross-model`. It compares models
under one fixed harness.

On a private OpenBot host, `make workbench-up` serves the built UI and this
API together at `http://127.0.0.1:8080`. Nginx on `127.0.0.1:8080` proxies
`/v1` to uvicorn on `127.0.0.1:8765`. That stack leaves
`OHBE_ALLOW_LIVE_SPEND` empty. See `docs/private-deploy-checklist.md`
section 5.2.

`OHBE_ROOT` overrides the checkout used to read `pricing/` and
`results/fixture-pilot/`. A missing catalog returns HTTP 503
`catalog_unavailable` and still reports `spend: none`.
