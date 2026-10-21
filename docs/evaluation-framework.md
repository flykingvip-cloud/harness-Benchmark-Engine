# OHBE evaluation framework

OHBE evaluates a **harness**, not a model in isolation. A reading is comparable
only when two harnesses — OpenBot and Inspect `react()` — run the same task
against one shared model pool, through one mandatory LiteLLM gateway, inside
the Docker egress policy. The model, the gateway, and the protocol are the
controls. The harness is the object that is allowed to differ.

This note is the design of that system. It follows the benchmark-paper
skeleton: gap, design, pipeline, multi-dimensional evaluation, findings.
v0.1 findings below are the checked-in fixture. They are not a live ranking.
Oracle routing is deferred and is not computed.

## 1. Object of evaluation（评估对象）

The object is the agent harness: the loop that turns a task into tool calls,
model requests, and a final answer.

| Control | What is shared | Where OHBE records it |
| --- | --- | --- |
| Task | Same task id, content hash, and input | `ohbe.core.task`, fairness record |
| Model pool | Same pool id, pool hash, and selected model id | `ohbe.core.model`, `pilot_pool` |
| Gateway | One LiteLLM OSS proxy; the benchmark process does not call `litellm.completion` | `ohbe.gateway` |
| Egress | Default-deny Docker network; only the designated gateway host and port | `ohbe.environment` |
| Protocol | Same timeout, budgets, and repetitions | `PairedProtocol` |

OpenBot and Inspect `react()` are the paired harnesses. A difference that
survives those controls is a harness difference. A difference that appears
because the model, the pool, or the gateway changed is not.

### Modes（两种模式）

| Mode | Role | What is allowed to vary |
| --- | --- | --- |
| `paired_harness` | **Default. This is the evaluation.** | The harness. OpenBot versus Inspect `react()`, on one shared model pool, through mandatory LiteLLM, inside the Docker egress policy. |
| `cross_model` | **Secondary workbench.** Decision support, not a second leaderboard. | The model, under one **fixed** harness (for example OpenBot) and one frozen task suite. The gateway policy does not change. |

`cross_model` answers a selection question: for this harness and this task
class, which models sit on the cost/quality frontier? Those rows are
adaptation signals for a later OpenBot policy. They are not a model-arena
ranking, and they are not a harness ranking. A campaign that needs to claim
a harness difference uses `paired_harness`.

## 2. Evaluation gap（评估缺口）

Three gaps are why a model leaderboard is the wrong instrument here.

1. **Model versus harness confound.** A single score on a single model mixes
   the model with the loop that called it. OHBE refuses that mix: both
   harnesses must record the same task, the same pool, and the same gateway
   before a success rate can be compared (`ohbe.adapters.fairness`).
2. **Cost, egress, and failure are collapsed.** Many reports treat a timeout,
   a blocked network call, a missing token count, and a wrong answer as one
   failure, and they treat a missing dollar amount as zero. OHBE keeps those
   apart. An egress violation is invalid. Infrastructure failure is not a
   task failure. Missing usage is not a cost of zero.
3. **The trace is not recomputable.** A score without an immutable trace, a
   price-table hash, and recorded versions cannot be priced again offline.
   OHBE stores an append-only hash-chained JSONL trace and prices it later
   from `pricing/ohbe-offline-2026-10-09.yaml`.

## 3. Design goals（设计目标）

The engine is built around five goals. Each one is a property the code
enforces, not a claim about a live leaderboard.

| Goal | Statement |
| --- | --- |
| G1 | **Compare harnesses, not models.** Paired runs share the task, the model pool, the selected model, and the LiteLLM gateway. |
| G2 | **Hold the protocol fixed, and fail closed.** Timeout, budgets, and repetitions match. An egress violation or other infrastructure failure is invalid. It is not a task failure and it leaves the success denominator. |
| G3 | **Keep the evidence recomputable.** Traces are append-only and hash-chained. Content hashes and versions (schema, package, harness, pool, pricing, egress policy, image digest) travel with the run. |
| G4 | **Price offline, and never invent a zero.** Token usage is `present`, `missing`, or `invalid`. Offline GPU-seconds and dollars per success are computed only from present usage and the versioned price table. |
| G5 | **Make a miss diagnosable.** The failure taxonomy, the release-gate audit, and the doctor checklist name why a run cannot be compared. They do not spend, and they do not infer routing optimality. |

G1 answers gap 1. G2 and G4 answer gap 2. G3 and G5 answer gap 3.

### Multi-round goals（多轮设计目标）

A campaign is more than one paired snapshot. These goals keep later rounds
comparable to the first. They do not add a model leaderboard.

| Goal | Statement |
| --- | --- |
| G6 | **Freeze the suite before Running.** `DraftPlan` may edit cells. `ConfirmedSuite` pins `suite_hash`. A later round that needs a different mix is a `ForkPlan`, not a silent edit. |
| G7 | **`cross_model` varies models only.** The harness ref, the confirmed suite, the LiteLLM gateway, and the egress policy stay fixed. `paired_harness` still varies the harness and shares the pool. |
| G8 | **A round report states findings and the frontier.** It includes the five readings. When the mode is `cross_model` and usage is present, it also lists per-model success, cost, dollars per success, and steps, plus `ModelFitRecommendation` rows. A Pareto flag is an adaptation signal under that harness. It is not a rank. |
| G9 | **Stop conditions and budget caps are recorded before Running.** Hitting a cap ends the round as a protocol outcome. It does not unlock live spend, and it does not relabel invalid infrastructure as a task failure. |

## 4. Taxonomy（五维分类）

OHBE reads a paired run on five dimensions. A dimension can be missing. Missing
is reported as missing. It is not filled in.

### Validity（有效性）

A run is valid when it stayed inside the shared protocol: same task, same
pool, same gateway, no egress violation, and no infrastructure failure.

Example metrics:

- Valid count and invalid count, with invalid runs kept out of the success denominator.
- Egress violation counts as invalid (`egress_violation`), not as a wrong answer.
- Infrastructure failure (`timeout`, `budget`, `harness_error`, `infra_invalid`, `cancelled`) is not `task_failure_count`.
- Release gates: `same_task_input`, `same_model_pool`, `same_initial_environment`, `same_protocol`, `invalid_not_task_failure`, `gateway_bypass_denied`.

### Effectiveness（效果）

Effectiveness is success on that shared protocol, not success of the model
alone.

Example metrics:

- `success_rate` = successes / valid runs, with a Wilson interval when the aggregator produced one.
- **Δsuccess**: the paired gap between harnesses on the same task, in report order. It is an observed difference. It is not a ranking and not evidence that one model is independent of another.
- Release gates: `same_protocol`, `evaluator_reliability`. Objective checks are `contains`, `match`, and `exact`.

### Process（过程）

Process is how the harness spent the protocol: steps, tool calls, and whether
the trace is complete enough to recompute.

Example metrics:

- Repetition rows marked `complete` when the observed count matches the protocol repetitions.
- Trace completeness in the engine sense: an append-only hash chain whose head matches its records (`ohbe.core.trace`).
- Step and tool-call totals are part of the dimension. The `/v1` fixture surface does not yet aggregate those counts, so the workbench does not invent them.
- Release gate: `versions_recorded` (harness, model, task, schema, pricing).

### Cost（成本）

Cost is the offline price of reported usage. A null amount is missing, not zero.

Example metrics:

- Token usage status: `present`, `missing`, or `invalid` (`missing_usage_not_zero`).
- Offline price from the versioned table, including `normalized_gpu_seconds` for local GPU rows (`gpu_cost_per_hour / 3600`).
- `total_model_cost` and `cost_per_success` only when usage is present and the price status is `computed`. Gateway response-cost is a proxy signal. It is not an OHBE price.

### Diagnosability（可诊断性）

A run is diagnosable when a later reader can say why it counted, why it was
invalid, and how to recompute the table.

Example metrics:

- Failure taxonomy on invalid runs (`invalid_by_category`), separate from task failure (`eval_fail`).
- Recomputable traces: hash chain, `input_sha256` of the report bundle, pricing content hash.
- Doctor checklist (in-process, no socket) and the release-gate audit.
- `no_routing_claim`: `routing_optimality` and `model_independence` stay null.

### Task axes（任务轴）

The five readings above describe a run. The suite is built on different axes:

**capability × difficulty × an optional domain.**

Difficulty is `easy`, `medium`, or `hard`. A weight on the plan is how many
repetitions that difficulty schedules. It does not clone the cell, so the
cell id stays `capability--difficulty` or `capability--difficulty--domain`.
Empty domains omit that axis. Filters on the plan can narrow the mix. They
cannot invent a cell the mix did not name.

A round report still reads the five dimensions. The axes only say which
tasks were admitted. `task_class` on a model-fit row names one cell so the
frontier is per class, under the fixed harness.

## 5. Construction and experiment pipeline（构建与实验流水线）

The pipeline does not start a model call from the workbench or from CI.

1. **Task.** A versioned task document. The execution stores its id and content hash.
2. **Model pool.** Built from the offline price table (`pilot_pool` for the fixture; the GPU-farm template for a private host). Pool ids in the template are placeholders, not secrets.
3. **Egress.** Default-deny policy. Docker places the model-calling process on an internal network whose only allowance is the gateway host and port.
4. **Gateway.** Mandatory LiteLLM OSS proxy (`litellm==1.104.2`). Provider URLs and cloud LiteLLM hosts are denied before any transport.
5. **Paired adapters.** OpenBot, under a LiteLLM-only profile, and Inspect `react()` (`inspect-ai==0.3.277`) run the same task and record the same pool identity.
6. **Trace.** Append-only JSONL. Each record hashes the previous record.
7. **Judge.** Objective score plus a failure category. Invalid protocol runs are not scored as task failures.
8. **Offline cost.** The cost engine prices the trace from the pinned table. It does not read a provider invoice and it does not replace missing usage with zero.
9. **Paired aggregate.** One table per task: repetitions, timeout, budgets, success rate, cost, usage status. Harness order is input order, not a rank.
10. **Audit and report.** Release gates, then `benchmark report` (JSON, JSONL, Markdown, HTML). The fixture bundle is `results/fixture-pilot/`.
11. **Workbench.** The tester path is Overview, Prepare (model pool and harness library), then Evaluation (`/rounds`): fixture catalog, comparison slots, and the observation window. `/experiments` opens Evaluation. `GET /v1/results` is the post-run report, linked from the end of a round. It lists the fixture report and, when a file is already on disk, `results/live/<id>/report.json` (`source_kind: live`). Opening that file does not place a model call. Doctor is opened from Overview. Dry-run validates a gateway URL and does not open a socket. Live spend stays locked unless a separate, explicit path is configured, and this HTTP surface still does not place a model call.

### Workbench forms（工作台表单）

`/pools` is the Model Pool. It lists admitted catalog entries and starts empty. There is no separate intake page.

The Evaluation page (`/rounds`) recommends candidates into the active plan's comparison slots (columns are entities, rows are metrics). A person can add or remove slots on that plan. `GET`/`PUT /v1/rounds/plans/{plan_id}/slots` are that plan's list. Generating a plan stores the request `model_ids` as the draft snapshot and binds `pool-admitted` to those admitted catalog ids. Confirming the suite freezes that plan's copy. `POST /v1/rounds/comparison-slots` is `409 slots_unscoped`; `GET` still returns the residual process list and plan tabs do not draw it. A Dry-run job still uses the fixture pool and does not spend. The page is not a ranking. Observation can hide identical rows or highlight the better value across validity, effectiveness, process, cost, and diagnosability. A missing value is not zero and is not marked best. Switching a 方案 tab rematches mode, slots, suite, observation, and report for that plan, as specified in [plan-session-isolation.md](plan-session-isolation.md). The fixture model-fit block on this page appears only for `fixture-cross-model`.

**添加本地 / Add local** and **添加云端 / Add cloud** are the only add actions. Field rules for both are the canonical spec in [model-pool-intake.md](model-pool-intake.md), shared with OpenBot 平台端 and 企业端. Scan is the discovery helper inside Add local, not a third action. Both share one gate: local runs immediately, and a remote target runs only after a reachable connection. Add cloud stores `placement: gateway` in `models/catalog/*.yaml`. A scan admits a discovered model only when its revision passes the version gate, with `placement: local`. Ollama comes from `ollama list`. A vLLM base URL on the request or in `OHBE_VLLM_BASE_URL` also reads `GET /v1/models`; the bearer stays out of pool YAML. An unreachable listing writes nothing. Identity stays vllm or ollama through LiteLLM. OpenAI and Anthropic hosts stay denied. This scan does not unlock live spend.

The harness library lists registered installs and starts empty. **预装** chooses a catalog entry. **安装** uses one route. A pip row resolves a pinned target and can install it: an exact spec already in the catalog, such as `inspect-ai==0.3.277`, or the newest concrete release and its `files.pythonhosted.org` artifact URL. The profile `version` is that concrete value. The token `latest` is not written. An npm row installs that registry tarball with `npm install --ignore-scripts` and admits the profile when the package version and bin match. A desktop row downloads the catalog file for this OS and does not launch it. **扫描** admits a local discovery only when a version is detected and, for an exact pin, matches it.

| Harness | Pin | Artifact | Local click |
| --- | --- | --- | --- |
| WorkBuddy | `5.7.7` | three official v5.7.7 installers (macOS arm64, macOS x64, Windows x64) | download the matching OS package and do not launch it (`desktop_registered`). Linux is `os_unsupported`. A matching `WORKBUDDY_VERSION` and `WORKBUDDY_BIN` still admits the CLI profile |
| OpenClaw | `2026.10.1` | `https://registry.npmjs.org/openclaw/-/openclaw-2026.10.1.tgz` | `npm install --ignore-scripts`, then admit when the bin matches. No npm on PATH is `npm_missing` |
| DeepSeek Harness | `0.2.1-alpha.2` | `https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-alpha.2.tgz` (git tag `dsh-v0.2.1-alpha.2`) | same npm path as OpenClaw, bin `dsh` |
| 豆包工作 | `2.31.10` | Windows x64 and arm64 on the publisher CDN; macOS has no versioned URL stored. See [doubao-work.md](doubao-work.md) | Windows downloads the matching installer and does not launch it. The closed profile is admitted. Other systems are `os_unsupported`. The adapter still refuses |
| 千问办公 | `1.2.5` | desktop notes name this version; `/release/latest/` is not stored and versioned paths 404. See [qwen-work.md](qwen-work.md) | `installer_unpinned`. Nothing is downloaded. The adapter still refuses |

DeepSeek Harness is one comparison adapter. See [deepseek-harness.md](deepseek-harness.md). OpenClaw is an opt-in adapter. See [openclaw.md](openclaw.md). OpenClaw the product is an orchestration and channel layer. This adapter runs the embedded `agent exec` runtime and refuses a plugin bridge to DeepSeek Harness, Codex, or Claude CLI. It is not an identical peer of OpenBot or Inspect `react()`. WorkBuddy is the next opt-in adapter. See [workbuddy.md](workbuddy.md). WorkBuddy 5.7.7 is a desktop app. The adapter runs the bundled CodeBuddy CLI `--print` path, refuses `CODEBUDDY_BASE_URL`, and does not launch the desktop installer. It is not the GUI and not a DeepSeek Harness model source. All three adapters stay out of `HARNESS_ORDER`. 豆包工作 and 千问办公 have closed adapters ([doubao-work.md](doubao-work.md), [qwen-work.md](qwen-work.md)). `run` fails before a process or a model call. They are not peer harnesses. OpenClaw's rounds whitelist stays `0.1.0` and is separate from this npm pin. WorkBuddy is not on that rounds whitelist. A remote harness target needs a host plus an SSH key or password. The secret is a request field or the name of an environment variable. It is not written to pool YAML, catalog YAML, or git. Denied hosts fail closed before a socket. After auth, remote package inventory and remote install are not executed yet (`inventory_unsupported`). `install_on_remote` is the hook for a later install on that host, using the same pin. Neither action extends `HARNESS_ORDER` (`openbot`, `inspect_react`).

None of these routes unlock live spend. Responses stay `spend: none` and `executed: false`. A local harness target leaves `network: not_contacted`. A local model scan also leaves `network: not_contacted` unless a vLLM base URL is configured, in which case the scan GETs `/v1/models` and `network` is `probed` after that GET. A model remote target is `probed` only after the TCP connection check. A harness remote target is `probed` only after SSH authentication. Denied hosts stay `not_contacted`.

### Round lifecycle（轮次生命周期）

A multi-round campaign moves through five documents. The HTTP surface can
walk the same path as a dry-run. It still does not spend.

```text
DraftPlan → ConfirmedSuite → Running → RoundReport → ForkPlan
```

| Stage | Document | What is true |
| --- | --- | --- |
| DraftPlan | `RoundPlan` (`mode` defaults to `paired_harness`) plus a draft `TaskSuite` | Combinatorial cells are editable. `suite_hash` is provisional. |
| ConfirmedSuite | `TaskSuite.status = confirmed` | `suite_hash` is frozen. Editing the cells requires a fork. |
| Running | `DispatchJob` | Status walks `queued → dispatching → observing → sealed`. Observation events are stubs. Clients poll `GET /v1/rounds/jobs/{id}/events` or replay one SSE response. There is no live model websocket. |
| RoundReport | `RoundReport` | Findings, the five readings, and a cost/quality frontier note. `cross_model` may add per-model metrics and `ModelFitRecommendation`. `paired_harness` does not carry a model-fit table. `routing_optimality` stays null. |
| ForkPlan | A new `RoundPlan` with `forked_from` | The child starts as a draft. The parent suite is not copied. |

`POST /v1/rounds/plans/{id}/start` is a dry-run even when `live` is true and
`OHBE_ALLOW_LIVE_SPEND=1`. The job records `executed: false`, `spend: none`,
and `network: not_contacted`. A sealed dry-run report is `not_measured`:
missing cost stays missing. The checked-in `cross_model` report is
`fixture_demo`. Its dollars reuse amounts already on
`results/fixture-pilot/` (`0.0009`, `0.00045`). That demo is an adaptation
illustration under OpenBot, not a new measurement.

Invalid infrastructure remains outside the task-failure count. A gateway
bypass is still denied. Oracle routing stays deferred.

## 6. Research questions（研究问题）

The framework is shaped to answer three questions. v0.1 does not answer them
with live spend. The fixture only shows that the instruments exist.

- **RQ1.** When the pool, the gateway, and the protocol are shared, what is Δsuccess between harnesses, and does that gap remain after the model is held fixed?
- **RQ2.** Where do validity and cost come apart from task failure: egress violations, timeouts, and usage that is missing or invalid?
- **RQ3.** Can a third party recompute the published table from the trace, the price-table hash, and the recorded versions, without a new model call?

## 7. Findings on the fixture（夹具上的观察）

These are observed rows in `results/fixture-pilot/`. They are not a production
result and not a harness ranking. `routing_optimality` is null.
`model_independence` is null.

| Task | What the pair shows |
| --- | --- |
| `ohbe-pilot-echo` | Both harnesses succeed on both valid runs. Token usage is missing, so model cost and dollars per success stay null. |
| `ohbe-pilot-exact` | Both harnesses: success rate 0.5, one task failure, usage present, model cost `0.0009`, dollars per success `0.0009`. |
| `ohbe-pilot-match` | Each harness has one `timeout` invalid run. That run is not a task failure. The remaining valid run succeeds. Usage on the valid run is present. |

All ten release gates pass on this bundle, including `invalid_not_task_failure`,
`missing_usage_not_zero`, `gateway_bypass_denied`, and `no_routing_claim`.
The workbench draws the five dimensions from these fields. Overview and the
report page chart paired success rates with the Wilson interval already on
this fixture, plus grouped bars of the five readings by harness.
`GET /v1/analysis/model-fit` can add a secondary success chart under the
fixed harness. Neither chart is a ranking, and a null cost stays null.

### Frontier note on the fixture cross-model report（夹具前沿）

`GET /v1/analysis/model-fit` reads a fixture `cross_model` report whose
harness is OpenBot. `echo--easy` has missing usage, so it is not on the
frontier and is not filled in as zero. `exact--medium` puts both pool models
on the frontier (higher success versus lower dollars per success).
`match--hard` leaves `openai/gpt-test-mini` inside the frontier. Findings
state that this is an adaptation signal under that harness. It is not a
model-arena leaderboard. `routing_optimality` and `model_independence` stay
null.

## 8. Deferred（暂缓）

**Oracle routing is deferred.** Oracle scoring and routing regret are stubs.
They are not computed, they are not stored on a paired run, and a cost or
success gap is not evidence of routing optimality. `cross_model` does not
change that: a Pareto flag is not routing optimality and not a model-arena
rank. Also deferred: a claimed OpenBot-versus-Inspect production ranking,
tool-surface parity between the two harnesses, and live spend on pull
requests. The round API does not unlock spend and does not bypass the gateway.

## 9. Paper skeleton（论文骨架）

| Skeleton | This document |
| --- | --- |
| Gap | §2 Evaluation gap |
| Design | §3 Design goals G1–G5, multi-round G6–G9, and §1 Modes |
| Pipeline | §5 Construction and experiment pipeline, including the round lifecycle |
| Multi-dimensional evaluation | §4 metrics and task axes, shown on the workbench Results, Overview, and Rounds pages |
| Findings | §7 Fixture observations, plus the fixture frontier note |

A later paper can replace §7 with a live paired run that used the same
pipeline. It should not replace the gap, the goals, or the rule that oracle
routing stays out until it is actually computed.
