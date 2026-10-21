# Model intake

The workbench model page is the Model Pool at `/pools`. Its add actions are
**添加本地 / Add local** and **添加云端 / Add cloud**. Scan is the discovery
helper inside Add local, not a third action. There is no separate
**模型入库 / Model Intake** page. The harness page is Harness 库 at `/harnesses`.
**Harness 预装 / Harness Preinstall** names the install-inventory rules, not a
second page. Model admission still has two paths and no third:

| Path | Product copy | What the operator does |
| --- | --- | --- |
| Cloud API key | 添加云端 / Add cloud | Bind a remote model with an API key and a LiteLLM gateway URL. |
| Local discovery | 添加本地 / Add local | Scan is the helper inside this action. It lists vLLM and Ollama models already deployed behind LiteLLM（扫描本机/服务器已部署模型）, then confirms a handshake. |

A model enters the intake catalog only when **both** gates succeed:

1. Connectivity or handshake. Cloud intake uses a scoped health check. Server intake uses a successful listing plus an explicit handshake.
2. Gateway policy match. The route is the operator LiteLLM gateway, and the egress policy is fail-closed.

If the gateway policy does not match, catalog entry is rejected and the attempt is saved as `pending`. A failed health check or listing is also `pending`. Pending rows are not catalog members. They cannot fill a round comparison slot.

`models/catalog/*.yaml` and the intake catalog are one admitted set. Startup loads every valid catalog file into the intake service. Saving a catalog file updates that service in the same process. After a restart those entries can fill a comparison slot again. Pending rows are not files, so they do not come back. The load does not open a socket and does not read an API key.

Comparison slots live on ` /v1/rounds/comparison-slots`. That list is the Evaluation page slot board. Admitting a model does not assign a slot. Assigning a slot does not admit a model. Generating a plan while those slots are set binds `pool-admitted` to the admitted catalog members in slot order. A client `pool_id` of `pool-pilot` does not override that. Confirming a suite copies the current slot ids onto the plan and the suite as `comparison_model_ids`. The client `model_ids` must match the current slots or the response is `409 slot_mismatch`. Re-confirm leaves that snapshot unchanged. A dry-run or demo job still dispatches the fixture pool `pool-pilot` and is labeled `Dry-run`. It does not unlock spend.

Harness 预装 / Harness Preinstall is a server install inventory. The host may have no harnesses. The inventory lists the closed registry — OpenBot, Inspect `react()`, and OpenClaw (`openclaw`) — and the operator selects which ids and versions to preinstall. Auto-detect reads an env pin, the installed Inspect version, or `binary --version`. A detected allowed version is marked 已预装 and enters the whitelist without a POST. A detected version outside the gate is `version_rejected` and stays off the whitelist.

Only version-gated registered harnesses enter plans. Live membership is that set filtered by `HARNESS_ORDER` (`openbot`, then `inspect_react`). Selection order cannot reorder live. OpenClaw can enter plans and does not enter live. An unknown id is `403 harness_not_registered`. A version outside the gate is `422 version_not_allowed`. A round plan for a harness that is not on the plans whitelist, or not at `whitelist_version`, is `403 harness_not_whitelisted`. Empty selections clear operator choices and leave a detected 已预装 row whitelisted. This route does not install packages and does not spend.

## Fail-closed gateway policy

The policy runs before any socket. These hosts, and their subdomains, are denied:

- Cloud LiteLLM: `litellm.ai`, `litellm.com`, `litellm.cloud`, `berri.ai`
- Direct model-provider hosts already denied by `NetworkEgressPolicy`, including `api.openai.com` and `api.anthropic.com`

Also denied before a probe:

- Userinfo in the gateway URL. The secret is not echoed.
- A query string or fragment.
- Any path other than an origin, `/`, or `/v1`. A URL that already points at `/v1/chat/completions` is rejected.

The preferred route is the only admitted route: the operator LiteLLM gateway, for example `http://ohbe-gateway:4000`. There is no direct-to-provider intake.

Checks recorded on the attempt:

| Check | Passes when |
| --- | --- |
| `gateway_not_cloud` | The host is not hosted LiteLLM. |
| `provider_bypass_denied` | The host is not a model-provider endpoint. |
| `route_litellm` | The route is that operator gateway. |
| `egress_fail_closed` | The policy denies provider and cloud LiteLLM URLs without calling a transport. |
| `local_backend` | Server handshake only. The discovered id is vLLM (`hosted_vllm/` or `vllm/`) or Ollama (`ollama/`). |

## Scoped connectivity

Intake does not unlock live spend for benchmarking. `live: true` is `403 live_spend_locked` even when `OHBE_ALLOW_LIVE_SPEND=1`. The probe does not run in that case.

Field names, the key policy, and the two admit gates are the canonical standard in [model-pool-intake.md](model-pool-intake.md). OHBE and OpenBot 平台端 / 企业端 share that standard. The fail-closed extras below are OHBE-only.

When the policy matches, connectivity is one GET. It never calls `/v1/chat/completions`.

| Path | When | URL |
| --- | --- | --- |
| 添加云端, and any intake that has `api_key` | key present | `{gateway-origin}/v1/models` with `Authorization: Bearer` |
| 添加本地里的扫描 | no key on the legacy listing | `{gateway-origin}/v1/models` |
| unauthenticated liveness | no key, and the caller did not need the model list | `{gateway-origin}/health/liveliness` |

A present API key is not proved by an unauthenticated `/health/liveliness` GET. The key is shape-checked, reduced to a short fingerprint, and sent only as that Bearer header. It is not stored, not returned, and not written into pool YAML. The probe client has no chat method and refuses `/v1/chat/completions` before opening a socket. Redirects are refused. `spend` stays `none`, `executed` stays false, and `benchmark_unlocked` stays false.

`POST /v1/model-library/scan` is the 添加本地 helper. It keeps `ollama list`. It also GETs `{origin}/v1/models` when `vllm_base_url` or `OHBE_VLLM_BASE_URL` is set. A bearer, when present, is the request `api_key` or `OHBE_VLLM_API_KEY`, and is sent only on that GET. An unreachable listing fails closed and writes no catalog file. That route does not unlock live spend.

A non-2xx health response, an unreachable gateway, or a listing body without `data` is `connectivity_failed`. The row stays pending.

## Routes

| Action | Result |
| --- | --- |
| Cloud admit | Admit, or save pending. |
| Server scan | Discovered list. Nothing is admitted yet. |
| Handshake | Admit selected vLLM/Ollama rows, or save the others pending. |
| `GET /v1/catalog/models` | Admitted models only. |
| Pending list | Pending rows only. |
| `GET /v1/catalog/harnesses` | Install inventory, plans whitelist, and live whitelist. |
| `POST /v1/catalog/harnesses` | Replace operator selections. Registered ids and allowed versions only. |
| `GET /v1/rounds/comparison-slots` | Residual process list. Not a plan tab and not a second catalog. |
| `POST /v1/rounds/comparison-slots` | `409 slots_unscoped`. Plan slots use `PUT /v1/rounds/plans/{plan_id}/slots`. |
| `PUT /v1/rounds/plans/{plan_id}/slots` | Replace that plan's draft copy. Catalog members only. |
| `POST /v1/rounds/plans` | Request `model_ids` become that plan's draft slots and bind `pool-admitted`. An empty list uses `pool-pilot`. |
| `POST /v1/rounds/plans/{plan_id}/confirm` | Freeze `suite_hash` and that plan's stored slot ids. |

`accepted` is true only when `connectivity_or_handshake` and `policy_match` are both true. The response schema rejects any other combination.
