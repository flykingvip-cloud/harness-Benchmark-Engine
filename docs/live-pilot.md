# Live pilot

The live pilot is a manual paired run. Confirmed topology:

```text
private OpenBot host  ->  OHBE  ->  LiteLLM on the GPU farm  ->  local vLLM / Ollama models
```

OpenBot runs on the operator's private server. A separately rented GPU server
hosts the open-source models. OHBE, on the private server, sends benchmark
traffic only to LiteLLM on that GPU server. The model pool lists local
backends (`vllm/…`, `ollama/…`). It does not list a cloud provider, and the
OpenBot profile does not carry a cloud API key.

`experiments/live-pilot-smoke.yaml` names one to three objective tasks, both
harnesses, `REPLACE_ME` model ids, repetitions, a timeout, and a token budget.
Those ids stand in for whatever the GPU server actually serves. They
are not observed spend.

This is separate from the fixture pilot.

| | Fixture pilot | Live pilot |
| --- | --- | --- |
| Manifest | Built in `ohbe.audit.fixtures` | `experiments/live-pilot-smoke.yaml` |
| Command | `benchmark audit-pilot` | `benchmark run --experiment ...` |
| Model calls | None | OpenBot and Inspect, only after the checks below pass |
| Results | `results/fixture-pilot/` (checked in) | `results/live/<experiment_id>/` (gitignored except the README) |
| Spend | Not live. The report label is `fixture` | Offline price of token counts the trace actually recorded. The report label is `supplied-input` |
| CI | `.github/workflows/ci.yml` on every pull request and on `main` | `.github/workflows/live-pilot.yml` on `workflow_dispatch` only |

The fixture corpus plants missing usage, an objective miss, and a timeout so
the release gates have those cases. A live run does not plant them and does
not invent the token counts that would make them look real. The same gate ids
still apply to whatever the harnesses returned: shared task, environment,
pool, and protocol; missing usage stays null; invalid runs are not task
failures; versions are recorded; the objective scorer is stable; provider
bypass fails the egress policy; oracle and routing regret stay unset.

OHBE does not claim tool-surface parity, routing optimality, or that one model
is independent of another.

## Pins and credentials

The runner reads the process environment. It exits before starting a harness
when any required value is missing, when the OpenBot commit is not a full git
SHA, when the image is a floating tag, or when the proxy URL is not the
gateway written in the experiment file.

| Variable | Required | What to set |
| --- | --- | --- |
| `OPENBOT_COMMIT` | yes | 40-character lowercase git SHA of the OpenBot revision you are running. `openbot-commit-unavailable` and branch names are rejected. |
| `OPENBOT_BIN` | yes | Executable OpenBot CLI built from that commit. This repository does not clone the private OpenBot monorepo. |
| `OPENBOT_IMAGE` | no | Image reference pinned by that commit SHA (`…:<40-hex>`) or by a repo digest (`…@sha256:<64-hex>`). `latest` and `openbot-image-unavailable` are rejected. The runner records the reference. It does not pull the image. |
| `LITELLM_API_KEY` | yes | Virtual key for the OHBE proxy. It is passed to the adapters and is not written into the manifest, the trace, or the report. |
| `LITELLM_BASE_URL` | yes | Compute-server gateway, for the smoke file `http://gpu-litellm:4000/v1`. Scheme must be `http`. Host and port must match the experiment `egress` block, which is the only allowlisted destination. `api.openai.com`, `api.anthropic.com`, and `huggingface.co` are rejected. |
| `LITELLM_IMAGE_DIGEST` | no | `sha256:<64 lowercase hex>` of the proxy image you actually pulled. Omit it to leave the digest unrecorded. Do not paste a guessed digest. |

`LITELLM_API_KEY` is the virtual key for that GPU LiteLLM. The OpenBot profile
stores it as `${LITELLM_API_KEY}` and sets `baseUrl` to the origin resolved
from `LITELLM_BASE_URL` (`http://gpu-litellm:4000` for the smoke file). The
profile does not contain `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or a
HuggingFace token. The harness child process drops those variables before
OpenBot starts.

The smoke manifest points at `models/pools/pool-gpu-farm.example.yaml`
(`model_pool_file`). The pool id is `pool-gpu-farm`. Its members are
`vllm/REPLACE_ME-instruct` (`hosted_vllm/REPLACE_ME-instruct`) and
`ollama/REPLACE_ME`. Replace every `REPLACE_ME` id with the id the GPU
server serves, and point LiteLLM `model_name` at the same ids.
`docker/litellm/config.gpu-farm.sample.yaml` is a sample of that proxy config.
It has no cloud API key. `docker/litellm/config.sample.yaml` is the older
fixture proxy sample and is not this pilot.

Those `REPLACE_ME` ids are rows in `pricing/ohbe-offline-2026-10-09.yaml`
with meter `normalized_gpu_seconds` and `gpu_type` `rented-placeholder`.
`gpu_cost_per_hour` `"3.60"` is an offline stand-in (`3.60 / 3600 = 0.001`
USD per GPU-second). It is not an invoice and not observed spend. When token
usage is present and the invocation also records `normalized_gpu_seconds`,
`provider_cost` is the computed zero `0` and `normalized_cost` is seconds
times that per-second rate. `cost_amount` on the event is ignored. Missing
token usage, or present tokens without `normalized_gpu_seconds`, stays
invalid with a null amount. That null is not zero. Leave `max_cost` unset
in the smoke file. A cost ceiling cannot be certified for a cell whose
normalized cost was not computed.

## Network

The private OpenBot server must be able to reach LiteLLM on the GPU server
(`gpu-litellm:4000` in the smoke file). That host and port are the egress
allowlist for the harness process. Direct connections from that process to
HuggingFace, OpenAI, or Anthropic fail the policy before a packet is sent.

`docker.network: none` does not cover the host process that calls the proxy,
and it would also block the GPU gateway. Do not attach the harness to a
network that can reach `huggingface.co`, `api.openai.com`, or
`api.anthropic.com`. Do not call `litellm.completion` from the benchmark
process. Only the LiteLLM process on the GPU server talks to the local vLLM
and Ollama ports.

`docker/litellm/compose.yaml` is a local stand-in for a proxy process. It is
not the rented GPU server, and its sample upstream key is not part of this
pilot.

`benchmark check-egress` remains the Docker release gate. The live audit also
repeats the in-process policy check and names that Docker test in the
checklist. A live pilot does not replace the gate.

Install on the private OpenBot host, the GPU LiteLLM URL, the egress
allowlist, the OpenBot pin, and what to record are in
`docs/private-deploy-checklist.md`. That checklist does not start a run.

## Run, audit, and report

From the repository root, with the variables above set and the `inspect`
extra installed (`inspect-ai==0.3.277`):

```bash
pip install -e ".[dev,inspect]"
export OPENBOT_COMMIT=<40-character sha>
export OPENBOT_BIN=/path/to/openbot
export OPENBOT_IMAGE=ghcr.example/openbot@sha256:<64-hex>   # optional
export LITELLM_API_KEY=...
export LITELLM_BASE_URL=http://gpu-litellm:4000/v1
benchmark run \
  --experiment experiments/live-pilot-smoke.yaml \
  --out results/live
benchmark report \
  --input results/live/live-pilot-smoke/report-input.json \
  --out results/live/live-pilot-smoke/report
```

`benchmark run` writes `audit.json` and `report-input.json` only after both
harnesses have returned every cell. It then judges each trace, prices it from
the named table, aggregates the pair, and evaluates the release gates. Exit 0
means every gate passed. Exit 1 means a harness cell failed, in which case no
metrics are published, or the matrix finished and a release gate failed, in
which case the audit and report input are still written. Exit 2 means the
manifest or the prerequisites were refused, and no model call was started.

`benchmark report` renders that input. Read `spend` on `report.json`:

- `as_supplied` — figures come from the executions this live run wrote
- `not_live` — the fixture corpus

A null cost is a missing or unpriceable usage total. It is not zero. The
renderer still lists oracle scoring and routing regret as deferred, and
`routing_optimality` and `model_independence` stay null.

`benchmark audit-pilot` does not read `results/live/`. Keep using it for the
fixture corpus.

The workbench reads a published report only. `GET /v1/results` lists
`results/live/<experiment_id>/report.json` when that file is present, and
otherwise `results/live/<experiment_id>/report/report.json` from the command
above. `source_kind` is `live`. The fixture row stays the default. Opening
the file does not start a run and does not unlock spend. A missing usage
total stays null. Oracle scoring and routing optimality stay off this
response.

## Manual workflow

`.github/workflows/live-pilot.yml` runs only when someone starts it from the
Actions tab (`workflow_dispatch`). It is not triggered by pull requests or by
pushes to `main`.

The first step exits if `LITELLM_API_KEY`, `LITELLM_BASE_URL`,
`OPENBOT_COMMIT`, or `OPENBOT_BIN` is empty, and it exits if
`LITELLM_BASE_URL` names OpenAI, Anthropic, or HuggingFace. That happens
before `benchmark run`, so a missing secret does not spend and a cloud URL is
not used as the gateway. The job installs this repository with the `inspect`
extra. It does not clone the OpenBot monorepo and it does not run on the
default CI workflow. `.github/workflows/ci.yml` has no `workflow_dispatch`
live job and does not call `benchmark run`.
