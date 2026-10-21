"""Offline prices from the versioned table and a trace."""

from __future__ import annotations

from pathlib import Path

import pytest

from decimal import Decimal

from ohbe.core.pricing import PricingRef
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog
from ohbe.cost.engine import price_trace
from ohbe.cost.table import GpuSecondModelPrice, PricingError, load_pricing_table
from tests.samples import NOW

TABLE = Path(__file__).resolve().parents[1] / "pricing" / "ohbe-offline-2026-10-09.yaml"
# sha256 of the committed pricing file bytes.
TABLE_HASH = "67391b6b0bc78d14f802c5cdc7f2a300878ca5070de28f485ef57f382b1ed92b"


def _trace(tmp_path: Path, events: list[tuple[str, dict]], *, name: str = "trace.jsonl") -> ExecutionTrace:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    log = TraceLog(path)
    for index, (event_type, payload) in enumerate(events):
        log.append(
            execution_id="exec-cost",
            event_type=event_type,
            payload=payload,
            recorded_at=NOW,
            record_id=f"rec-{index}",
        )
    return ExecutionTrace.build(
        trace_id="trace-cost",
        execution_id="exec-cost",
        records=tuple(log.read()),
    )


def _usage(prompt: int, completion: int, **extra: object) -> dict:
    total = prompt + completion
    return {
        "completion_tokens": completion,
        "prompt_tokens": prompt,
        "total_tokens": total,
        "usage_status": "present",
        **extra,
    }


def _ref(table) -> PricingRef:
    return PricingRef(
        pricing_id=table.pricing_id,
        version=table.version,
        uri=Path(table.source).as_uri(),
        content_hash=table.content_hash,
    )


def test_golden_price_for_gpt_test(tmp_path: Path) -> None:
    table = load_pricing_table(TABLE)
    assert table.pricing_id == "ohbe-offline"
    assert table.version == "2026-10-09"
    assert table.currency == "USD"
    assert table.content_hash == TABLE_HASH
    trace = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "openai/gpt-test"}),
            (
                TraceEventType.INVOCATION_COMPLETED,
                _usage(1000, 500, cost_amount="0"),
            ),
        ],
    )
    priced = price_trace(trace, table=table, pricing_ref=_ref(table))
    # 1000 * 2.50 / 1_000_000 + 500 * 10.00 / 1_000_000 = 0.0025 + 0.005
    assert priced.invocations[0].cost.amount == "0.0075"
    assert priced.invocations[0].cost.status.value == "computed"
    assert priced.total.amount == "0.0075"
    assert priced.total.currency == "USD"
    assert priced.total.pricing_ref == _ref(table)
    again = price_trace(trace, table=table, pricing_ref=_ref(table))
    assert again.total.model_dump(mode="json") == priced.total.model_dump(mode="json")
    assert priced.invocations[0].provider_cost.amount == "0.0075"
    assert priced.invocations[0].normalized_cost.amount == "0.0075"
    assert priced.provider_cost.amount == "0.0075"
    assert priced.normalized_cost.amount == "0.0075"


def test_two_invocations_sum_and_a_gap_does_not(tmp_path: Path) -> None:
    table = load_pricing_table(TABLE)
    ref = _ref(table)
    both = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "openai/gpt-test"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(1000, 500)),
            (TraceEventType.INVOCATION_COMPLETED, _usage(200, 100)),
        ],
    )
    # 0.0075 + (200 * 2.50 + 100 * 10.00) / 1_000_000 = 0.0075 + 0.0015
    assert price_trace(both, table=table, pricing_ref=ref).total.amount == "0.009"
    gap = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "openai/gpt-test"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(1000, 500)),
            (
                TraceEventType.INVOCATION_COMPLETED,
                {"usage_status": "missing", "prompt_tokens": None, "cost_amount": "0"},
            ),
        ],
        name="gap.jsonl",
    )
    priced = price_trace(gap, table=table, pricing_ref=ref)
    assert priced.invocations[0].cost.amount == "0.0075"
    assert priced.invocations[1].cost.amount is None
    assert priced.total.amount is None
    assert priced.total.status.value == "invalid"
    assert priced.total.amount != "0"


def test_explicit_zero_usage_is_a_computed_zero(tmp_path: Path) -> None:
    table = load_pricing_table(TABLE)
    trace = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "openai/gpt-test"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(0, 0)),
        ],
    )
    priced = price_trace(trace, table=table, pricing_ref=_ref(table))
    assert priced.total.status.value == "computed"
    assert priced.total.amount == "0"


def test_unknown_model_and_unpinned_table_do_not_invent_a_price(tmp_path: Path) -> None:
    table = load_pricing_table(TABLE)
    trace = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "missing-model"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(10, 1)),
        ],
    )
    priced = price_trace(trace, table=table, pricing_ref=_ref(table))
    assert priced.total.amount is None
    assert "not in the pricing table" in (priced.invocations[0].cost.reason or "")
    with pytest.raises(PricingError, match="content_hash"):
        price_trace(
            trace,
            table=table,
            pricing_ref=PricingRef(
                pricing_id=table.pricing_id,
                version=table.version,
                content_hash="ab" * 32,
            ),
        )


def test_yaml_floats_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rates.yaml"
    path.write_text(
        "\n".join(
            [
                "pricing_id: ohbe-offline",
                'version: "2026-10-09"',
                "currency: USD",
                "gpus: []",
                "models:",
                "  - model_id: openai/gpt-test",
                "    meter: tokens",
                "    prompt_per_million: 2.50",
                '    completion_per_million: "10.00"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(PricingError, match="decimal string"):
        load_pricing_table(path)


def test_smoke_placeholders_use_normalized_gpu_seconds() -> None:
    table = load_pricing_table(TABLE)
    rate = table.gpu_for("rented-placeholder")
    assert rate is not None
    assert rate.gpu_cost_per_hour == Decimal("3.60")
    assert rate.usd_per_second == Decimal("0.001")
    for model_id in ("vllm/REPLACE_ME-instruct", "ollama/REPLACE_ME"):
        price = table.price_for(model_id)
        assert isinstance(price, GpuSecondModelPrice)
        assert price.meter == "normalized_gpu_seconds"
        assert price.gpu_type == "rented-placeholder"


def test_golden_normalized_gpu_seconds_ignores_response_cost(tmp_path: Path) -> None:
    table = load_pricing_table(TABLE)
    ref = _ref(table)
    trace = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "vllm/REPLACE_ME-instruct"}),
            (
                TraceEventType.INVOCATION_COMPLETED,
                _usage(1000, 500, cost_amount="999", normalized_gpu_seconds="2"),
            ),
            (
                TraceEventType.INVOCATION_COMPLETED,
                _usage(10, 1, model_id="ollama/REPLACE_ME", normalized_gpu_seconds="1"),
            ),
        ],
    )
    priced = price_trace(trace, table=table, pricing_ref=ref)
    # 3.60 / 3600 = 0.001 USD per GPU-second. 2s + 1s.
    assert priced.invocations[0].provider_cost.amount == "0"
    assert priced.invocations[0].provider_cost.status.value == "computed"
    assert priced.invocations[0].normalized_cost.amount == "0.002"
    assert priced.invocations[0].cost.amount == "0.002"
    assert priced.invocations[1].model_id == "ollama/REPLACE_ME"
    assert priced.invocations[1].normalized_cost.amount == "0.001"
    assert priced.invocations[1].provider_cost.amount == "0"
    assert priced.provider_cost.amount == "0"
    assert priced.normalized_cost.amount == "0.003"
    assert priced.total.amount == "0.003"
    assert priced.total.currency == "USD"
    assert "999" not in (priced.total.amount or "")
    again = price_trace(trace, table=table, pricing_ref=ref)
    assert again.total.model_dump(mode="json") == priced.total.model_dump(mode="json")
    assert again.provider_cost.model_dump(mode="json") == priced.provider_cost.model_dump(mode="json")


def test_gpu_seconds_zero_is_computed_and_a_gap_is_not(tmp_path: Path) -> None:
    table = load_pricing_table(TABLE)
    ref = _ref(table)
    zero = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "ollama/REPLACE_ME"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(0, 0, normalized_gpu_seconds="0")),
        ],
    )
    priced = price_trace(zero, table=table, pricing_ref=ref)
    assert priced.total.status.value == "computed"
    assert priced.total.amount == "0"
    assert priced.provider_cost.amount == "0"
    assert priced.normalized_cost.amount == "0"
    half = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "vllm/REPLACE_ME-instruct"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(4, 1, normalized_gpu_seconds="0.5")),
        ],
        name="half.jsonl",
    )
    # 0.5 * 0.001 = 0.0005. Token counts do not enter the GPU formula.
    assert price_trace(half, table=table, pricing_ref=ref).total.amount == "0.0005"
    missing_seconds = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "vllm/REPLACE_ME-instruct"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(1000, 500, cost_amount="0")),
        ],
        name="no-seconds.jsonl",
    )
    gap = price_trace(missing_seconds, table=table, pricing_ref=ref)
    assert gap.invocations[0].cost.amount is None
    assert gap.invocations[0].provider_cost.amount is None
    assert gap.invocations[0].normalized_cost.amount is None
    assert "normalized_gpu_seconds" in (gap.invocations[0].cost.reason or "")
    assert gap.total.amount is None
    assert gap.provider_cost.amount is None
    whole = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "vllm/REPLACE_ME-instruct"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(1, 1, normalized_gpu_seconds=3)),
        ],
        name="int-seconds.jsonl",
    )
    assert price_trace(whole, table=table, pricing_ref=ref).total.amount == "0.003"
    floated = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "vllm/REPLACE_ME-instruct"}),
            (TraceEventType.INVOCATION_COMPLETED, _usage(1, 1, normalized_gpu_seconds=1.5)),
        ],
        name="float-seconds.jsonl",
    )
    rejected = price_trace(floated, table=table, pricing_ref=ref)
    assert rejected.total.amount is None
    assert rejected.provider_cost.amount is None
    omitted = _trace(
        tmp_path,
        [
            (TraceEventType.EXECUTION_STARTED, {"model_id": "vllm/REPLACE_ME-instruct"}),
            (
                TraceEventType.INVOCATION_COMPLETED,
                {
                    "usage_status": "missing",
                    "normalized_gpu_seconds": "9",
                    "cost_amount": "0",
                },
            ),
        ],
        name="missing-usage.jsonl",
    )
    missing = price_trace(omitted, table=table, pricing_ref=ref)
    assert missing.total.amount is None
    assert missing.provider_cost.amount is None
    assert missing.total.amount != "0"


def test_gpu_catalog_rejects_floats_unknown_types_and_duplicates(tmp_path: Path) -> None:
    def _write(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    header = "\n".join(
        [
            "pricing_id: ohbe-offline",
            'version: "2026-10-09"',
            "currency: USD",
        ]
    )
    with pytest.raises(PricingError, match="decimal string"):
        load_pricing_table(
            _write(
                "float.yaml",
                header
                + "\n"
                + "\n".join(
                    [
                        "gpus:",
                        "  - gpu_type: rented-placeholder",
                        "    gpu_cost_per_hour: 3.60",
                        "models:",
                        "  - model_id: vllm/REPLACE_ME-instruct",
                        "    meter: normalized_gpu_seconds",
                        "    gpu_type: rented-placeholder",
                        "",
                    ]
                ),
            )
        )
    with pytest.raises(PricingError, match="not in gpus"):
        load_pricing_table(
            _write(
                "missing-gpu.yaml",
                header
                + "\n"
                + "\n".join(
                    [
                        "gpus: []",
                        "models:",
                        "  - model_id: vllm/REPLACE_ME-instruct",
                        "    meter: normalized_gpu_seconds",
                        "    gpu_type: rented-placeholder",
                        "",
                    ]
                ),
            )
        )
    with pytest.raises(PricingError, match="duplicate gpu_type"):
        load_pricing_table(
            _write(
                "dup.yaml",
                header
                + "\n"
                + "\n".join(
                    [
                        "gpus:",
                        "  - gpu_type: rented-placeholder",
                        '    gpu_cost_per_hour: "3.60"',
                        "  - gpu_type: rented-placeholder",
                        '    gpu_cost_per_hour: "1.00"',
                        "models:",
                        "  - model_id: openai/gpt-test",
                        "    meter: tokens",
                        '    prompt_per_million: "1"',
                        '    completion_per_million: "1"',
                        "",
                    ]
                ),
            )
        )
