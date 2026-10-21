# CI local fallback (Actions runner unavailable)

`.github/workflows/ci.yml` is the intended gate on every pull request and on
`main`. When GitHub Actions fails at the **scheduler / runner** layer — the
check goes red in about two seconds, the job log has no steps, or the org
has no available runners / billing — that failure is **not** a product
regression in the PR diff.

## What to run locally instead

From the repository root (same commands README already documents):

```bash
uv run pytest
# or, after `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev,api]"`:
.venv/bin/pytest
```

Workbench (when the PR touches `workbench/`):

```bash
cd workbench
npm ci
npm test
```

Targeted suites used in recent acceptances:

```bash
uv run pytest tests/test_model_intake.py
uv run pytest tests/test_catalog_hydrate.py
cd workbench && npm test
```

Record the local pass in the PR / acceptance note. Do **not** unlock live
spend, expand `HARNESS_ORDER`, or change CI triggers just to get a green
cloud check while runners are down.

## What this does not replace

- Fixing org runner / billing / Actions quota so `ci.yml` can schedule again.
- The live egress job (`egress`) when a Docker daemon is available locally;
  without Docker, that probe stays skipped locally and stays a cloud gate
  once runners recover.

