# Fixture pilot report

This directory is a **fixture/demo** of the OHBE v0.1 report. The executions are
synthetic. They are not live OpenBot or Inspect production spend, and they are
not a comparison of production harnesses.

`report-input.json` is an `ohbe.report_input` document (`kind`
`ohbe.report_input`). `benchmark report` reads that file and writes:

- `report.json` — `ohbe.report`
- `metrics.jsonl` — one observed harness row per line
- `report.md` — the same figures in Markdown
- `report.html` — the same figures in a single HTML file

The renderer does not stamp a generation time. The same input bytes produce
the same report bytes. `routing_optimality` and `model_independence` stay
null. A null cost is a missing total, not zero.

Regenerate from the repository root:

```bash
benchmark audit-pilot \
  --pricing pricing/ohbe-offline-2026-10-09.yaml \
  --output /tmp/ohbe-audit.json \
  --report-input results/fixture-pilot/report-input.json
benchmark report \
  --input results/fixture-pilot/report-input.json \
  --out results/fixture-pilot
```

The pricing URI recorded on the fixture pool is the filename
`pricing/ohbe-offline-2026-10-09.yaml`, not a machine path.
