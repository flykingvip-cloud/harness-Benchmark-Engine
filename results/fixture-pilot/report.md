# OHBE v0.1 report (fixture)

This file is a v0.1 scaffolding report. It is a fixture/demo. It is not live OpenBot or Inspect production spend.

Figures are observed results from the supplied executions, with uncertainty intervals when the aggregator produced them. A difference in cost or success rate is not evidence of routing optimality, and it is not evidence that one model is independent of another.

Harness rows follow the input order. That order is not a ranking.

Audit passed: yes.
`routing_optimality` is null. `model_independence` is null.

## Reproducibility

- schema_version: `0.1`
- ohbe_version: `0.1.0`
- ohbe_versions_on_executions: `["0.1.0"]`
- git_commits: `["c0ffee"]`
- policy_versions: `["ohbe-egress-0.1"]`
- gateway: `["ohbe-gateway:8080"]`
- image_digests: `["sha256:abababababababababababababababababababababababababababababababab"]`
- harness_versions: `["openbot@0123456789abcdef0123456789abcdef01234567", "inspect-react@0.3.277"]`
- pricing: `[{"content_hash": "67391b6b0bc78d14f802c5cdc7f2a300878ca5070de28f485ef57f382b1ed92b", "pricing_id": "ohbe-offline", "version": "2026-10-09"}]`
- model_pool_ids: `["pool-pilot"]`
- model_pool_hashes: `["ebcdfbe3b9718dc83336475f3436adcdac978877149ebab77222ff1b25e3541b"]`
- timeouts_seconds: `[30]`
- repetitions: `[2]`
- max_total_tokens: `[null]`
- max_cost: `[null]`

- input_sha256: `31eddc2ba0521761177205adaacc1e7102ee5dcdb55081b1475f08681b5277de`

## Release-gate checklist

| Gate | Passed | Summary |
| --- | --- | --- |
| `same_task_input` | yes | Both harnesses record the task id and input from the shared task document. |
| `same_initial_environment` | yes | Controlled-track executions share one egress environment and gateway. |
| `same_model_pool` | yes | Every fixture call uses the same model pool and selected model id. |
| `same_protocol` | yes | Timeout, budgets, and repetitions are the same on every pilot task. |
| `missing_usage_not_zero` | yes | Missing token usage stays null. It is not added as zero and it does not create a partial cost. |
| `invalid_not_task_failure` | yes | Timeout runs stay invalid. They are not task failures and they stay out of the success denominator. |
| `versions_recorded` | yes | Harness, model, task, schema, and pricing versions are on the fixture records. |
| `evaluator_reliability` | yes | Objective checks are stable and match the fixture roles for contains, exact, and match. |
| `gateway_bypass_denied` | yes | Provider hosts and literal addresses fail the egress policy before the transport. The Docker probe is the release gate named in the checklist. |
| `no_routing_claim` | yes | The audit records observed results only. Oracle and routing regret stay unset. |

Evidence pointers stay in `report.json` under `release_gates`.

## Observed metrics

| Task | Harness | Valid | Invalid | Successes | Task failures | Success rate | 95% interval | Total model cost | Cost per success | Usage | Prompt tokens | Latency mean ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ohbe-pilot-echo` | `openbot` | 2 | 0 | 2 | 0 | 1.000000 | [0.342372, 1.000000] wilson95 | null | null | missing | null | 12.500000 |
| `ohbe-pilot-echo` | `inspect-react` | 2 | 0 | 2 | 0 | 1.000000 | [0.342372, 1.000000] wilson95 | null | null | missing | null | 12.500000 |
| `ohbe-pilot-exact` | `openbot` | 2 | 0 | 1 | 1 | 0.500000 | [0.094529, 0.905471] wilson95 | 0.0009 | 0.0009 | present | 200 | 15.000000 |
| `ohbe-pilot-exact` | `inspect-react` | 2 | 0 | 1 | 1 | 0.500000 | [0.094529, 0.905471] wilson95 | 0.0009 | 0.0009 | present | 200 | 15.000000 |
| `ohbe-pilot-match` | `openbot` | 1 | 1 | 1 | 0 | 1.000000 | [0.206543, 1.000000] wilson95 | 0.00045 | 0.00045 | present | 100 | 10.000000 |
| `ohbe-pilot-match` | `inspect-react` | 1 | 1 | 1 | 0 | 1.000000 | [0.206543, 1.000000] wilson95 | 0.00045 | 0.00045 | present | 100 | 10.000000 |

A null cost or token total was not reported. It is not zero.

## Deferred

- Oracle scoring is a stub and is not computed.
- Routing regret is a stub and is not computed.
- A multi-harness production matrix beyond this fixture pair is deferred.
- A dashboard is deferred.
- Tool-surface parity between harnesses is not claimed.
- Live production spend is not in this report.

## Limitations from the audit

- Tool-surface parity between OpenBot and Inspect is not claimed.
- Oracle scoring and routing-regret metrics are deferred stubs and are not computed.
- Pilot audit executions are fixtures. They are not live model spend.
- Gateway bypass in this audit is the in-process policy check. The Docker probe is tests/test_docker_egress.py::test_docker_blocks_provider_egress_and_allows_gateway and benchmark check-egress.
