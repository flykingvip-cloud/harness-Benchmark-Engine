# Model-pool intake fields (OHBE v0.1)

This note is the canonical intake field standard for a model pool. **OHBE** and
**OpenBot** both follow it. OpenBot 平台端 and OpenBot 企业端 use the same
fields, the same key policy, and the same admit gates. Neither end invents a
second shape for “local” or “cloud”.

OHBE is stricter in a few fail-closed places. Those extras are listed at the
end and are not required of every OpenBot surface. They do not change the
shared fields.

## Shared fields

Every admitted model names one triad:

| Member | What it is |
| --- | --- |
| `model_id` | OHBE / OpenBot identity. This is also the **pricing key**. |
| `litellm_model` | LiteLLM route alias for that same model. It is not a price key. |
| pricing row | The offline price table addresses the model by `model_id`. A missing row leaves cost missing. It does not become zero. |

`model_id`, `litellm_model`, and the pricing key are one model. The pricing
key equals `model_id`. Putting the price on `litellm_model` instead is not
this standard.

### 添加本地 / local

A local model is a vLLM or Ollama alias already deployed behind the operator
LiteLLM. Placement is `local`.

| Field | Required | Rule |
| --- | --- | --- |
| `provider` | yes | `vllm` or `ollama` |
| `name` | yes | Concrete token. No spaces, no URL, no secret. |
| `revision` | yes | Concrete revision. `latest`, `unpinned`, `main`, and `master` are rejected. |
| `model_id` | yes | `{provider}/{name}` |
| `litellm_model` | yes | `hosted_vllm/{name}` for vLLM, `ollama/{name}` for Ollama |
| pricing key | yes | `model_id` |

Scan is the discovery helper inside 添加本地. It is not a third add action.
A discovered row is admitted only when those fields hold.

Ollama discovery stays `ollama list`. When the scan request sets `vllm_base_url`,
or the process has `OHBE_VLLM_BASE_URL`, the same local scan also GETs
`{origin}/v1/models` on that vLLM or operator-gateway origin. An optional
bearer is the request `api_key`, or `OHBE_VLLM_API_KEY` when the request
omits one. The token is sent only as `Authorization: Bearer` on that GET.
It is not written to pool YAML, catalog YAML, or the response. If that URL
is set and the listing cannot be read, the scan fails closed and writes
nothing. No base URL means that GET is not opened. The listing does not
call `/v1/chat/completions` and does not unlock live spend.

### 添加云端 / cloud and remote

Cloud here means “reached through the operator LiteLLM gateway”, including a
remote machine that is already reachable. It does not mean a direct call to a
provider API.

For a vLLM or Ollama alias placed at the gateway (`placement: gateway`), the
local triad above is required, plus:

| Field | Required | Rule |
| --- | --- | --- |
| `api_key` | yes | Token used only for the connectivity probe. Never written into pool YAML. |
| `gateway_url` | yes | Operator LiteLLM origin (`/`, empty, or `/v1` only). |

A cloud-provider id on the legacy intake path (`openai/…`, `anthropic/…`, and
the other provider prefixes) uses a tighter alias rule: `litellm_model` equals
`model_id`, and that string is the pricing key. The key policy and the admit
gates are the same. The id still does not go into pool YAML as a secret, and
OHBE pool files do not list those providers (see the OHBE-only section).

## Key policy

Pool YAML never contains an API key, a bearer token, or a gateway URL.

The file names environment variables only:

- `LITELLM_BASE_URL` — gateway origin
- `LITELLM_API_KEY` — virtual key

Both OpenBot ends and OHBE read those names from the process environment.
Values are not copied into `models/pools/*.yaml`, catalog YAML, or compose
files. A fingerprint may be stored. The token itself is not returned by the
API.

## Admit gates

A model is admitted only when **both** gates pass:

1. **Connectivity.** When `api_key` is present, the probe is `GET {origin}/v1/models` with `Authorization: Bearer`. An unauthenticated `GET /health/liveliness` does not prove that key. When no key is present, local discovery or an unauthenticated `GET {origin}/v1/models` is the connectivity check.
2. **Policy.** The route is the operator LiteLLM gateway, and the egress policy matches.

Either gate failing keeps the attempt out of the admitted set. Admitting a
model does not assign a round comparison slot. Assigning a slot does not
admit a model. Generating a plan uses the current slots as `pool-admitted`.
That pool is not written to `models/pools`, and it does not contain a key.

The probe never calls `/v1/chat/completions`. Sending the key does not unlock
benchmark spend.

## One success path

添加本地 and 添加云端 each have one success path.

- 添加本地 writes `models/catalog/*.yaml` with `placement: local` and records
  the same id on the intake catalog. Both happen only after the local field
  standard and the discovery gate pass. A declared identity that breaks the
  triad writes nothing.
- 添加云端 writes `models/catalog/*.yaml` with `placement: gateway` and records
  the same id on the intake catalog. Both happen only after the field standard,
  connectivity, and gateway policy pass. A policy miss or a failed probe writes
  no catalog file.
- Legacy `POST /v1/intake/cloud` is the path for a cloud-provider id
  (`litellm_model == model_id`). It uses the same key probe and the same two
  gates. It does not write pool YAML or a catalog file. A vLLM or Ollama alias
  does not use this route; 添加云端 does. A restart keeps catalog files and
  drops a cloud-provider row that was never written to `models/catalog`.

A payload that fails its path is not repaired by the other path.

A process restart reads `models/catalog/*.yaml` back into the intake catalog.
The file is the durable copy. The rebuilt row does not contain the API key.
Confirming a suite records the current comparison-slot ids on the plan and
the suite. It does not write a pool file and does not spend.

## OHBE-only fail-closed extras

OpenBot 平台端 and 企业端 do not have to copy these. OHBE v0.1 does:

- Hosted LiteLLM is denied before any socket: `litellm.ai`, `litellm.com`, `litellm.cloud`, `berri.ai`, and their subdomains.
- Direct provider hosts are denied as the gateway, including `api.openai.com` and `api.anthropic.com`.
- Userinfo, a query string, a fragment, or a path other than an origin, `/`, or `/v1` is rejected. A URL that already points at `/v1/chat/completions` is rejected.
- Redirects are refused.
- Intake `live: true` is `403 live_spend_locked` even when `OHBE_ALLOW_LIVE_SPEND=1`. The probe does not run.
- Pool YAML is fail-closed: `constraints.fail_closed: true`, and `forbid_hosts` includes `api.openai.com` and `api.anthropic.com`. Providers in that file are `vllm` and `ollama` only.
- Responses keep `spend: none`, `executed: false`, and `benchmark_unlocked: false`.

## Desktop compose

The private-host workbench compose (`docker/workbench/compose.yaml`) mounts
`models/` read-write so catalog and pool YAML can be saved. The image root
stays read-only. `LITELLM_BASE_URL` and `LITELLM_API_KEY` come from the host
environment. The file does not contain key values, and it does not set
`OHBE_ALLOW_LIVE_SPEND=1`.
