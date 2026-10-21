# Live pilot results

This directory is for **live** OpenBot and Inspect paired runs. OpenBot stays
on the private server. The only model gateway is LiteLLM on the GPU farm,
serving local vLLM and Ollama models. It is not `results/fixture-pilot/`.

The fixture report is a checked-in demo. Nothing in that directory was
produced by a model call. Files written here come from `benchmark run` after
the harnesses return. OHBE prices the trace with the offline table named in
the experiment. Token rows use reported token counts. The smoke model ids
use `normalized_gpu_seconds` and the pinned `gpu_cost_per_hour`. A missing
usage object stays null. It is not stored as zero, and a provider
response-cost is not copied into `provider_cost` or `normalized_cost`.

Everything under this directory except this README is gitignored. Do not
commit traces, `report-input.json`, or rendered reports from a live run.

A published run looks like:

```text
results/live/<experiment_id>/
  manifest.json       pins and budget ceilings, no API key and no observed spend
  traces/*.jsonl      adapter traces
  executions.jsonl    judged rows
  paired.jsonl        one aggregate document per task
  audit.json          release gates
  report-input.json   input for `benchmark report`
  error.json          present only when the matrix was not published
```

`GET /v1/results` lists a report from this tree without running a benchmark
and without unlocking spend. A row is included when either file exists:

```text
results/live/<experiment_id>/report.json
results/live/<experiment_id>/report/report.json
```

The second path is what `benchmark report --out results/live/<experiment_id>/report`
writes. If both exist, the directory-level `report.json` is the one opened.
`<experiment_id>` matches `^[a-z0-9][a-z0-9-]{0,63}$` and is not
`fixture-pilot`. `source_kind` is `live`. The checked-in fixture stays
`source_kind: fixture` and remains the default row. Missing token usage in
the file stays null. This read does not mean the live pilot is connected
end to end.

Regenerate a published tree only with the commands in `docs/live-pilot.md`.
That requires `OPENBOT_BIN`, `OPENBOT_COMMIT`, `LITELLM_API_KEY`, and
`LITELLM_BASE_URL` pointed at the GPU LiteLLM host. The default CI job does
not run this command and does not need the private OpenBot repository.
`.github/workflows/live-pilot.yml` is manual (`workflow_dispatch`) only.
