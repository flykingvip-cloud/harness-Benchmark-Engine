"""Append-only JSONL traces and hash-chain verification."""

from __future__ import annotations

import json
from datetime import timedelta, timezone

import pytest
from pydantic import ValidationError

from ohbe.core import (
    GENESIS_HASH,
    ExecutionTrace,
    TraceEventType,
    TraceIntegrityError,
    TraceLog,
    TraceRecord,
    canonical_json,
)
from tests.samples import NOW, sample_invocation, sample_records

EXECUTION_ID = "exec-1"


def _append_pair(log: TraceLog) -> tuple[TraceRecord, TraceRecord]:
    first = log.append(
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"marker": "alpha", "note": "模型"},
        recorded_at=NOW,
        record_id="rec-0",
    )
    second = log.append(
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_FINISHED,
        payload={"status": "succeeded"},
        recorded_at=NOW,
        record_id="rec-1",
    )
    return first, second


def test_append_and_verify_hash_chain(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    first, second = _append_pair(log)
    report = log.verify()
    assert report.ok
    assert report.record_count == 2
    assert report.head_hash == second.record_hash
    assert report.errors == ()

    records = log.read()
    assert records == [first, second]
    assert records[0].prev_hash == GENESIS_HASH
    assert records[0].seq == 0
    assert records[1].prev_hash == records[0].record_hash
    assert records[1].seq == 1
    assert records[0].payload["note"] == "模型"

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    for line in text.splitlines():
        assert " " not in line
        assert canonical_json(json.loads(line)) == line


def test_append_does_not_rewrite_earlier_bytes(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    log.append(
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"marker": "alpha"},
        recorded_at=NOW,
        record_id="rec-0",
    )
    before = path.read_bytes()
    log.append(
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_FINISHED,
        payload={"status": "succeeded"},
        recorded_at=NOW,
        record_id="rec-1",
    )
    assert path.read_bytes().startswith(before)
    assert path.read_bytes() != before


def test_same_instant_in_another_offset_continues_the_chain(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    plus_one = timezone(timedelta(hours=1))
    first = log.append(
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"marker": "alpha"},
        recorded_at=NOW.astimezone(plus_one),
        record_id="rec-0",
    )
    direct = TraceRecord.build(
        record_id="rec-0",
        seq=0,
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"marker": "alpha"},
        recorded_at=NOW,
        prev_hash=GENESIS_HASH,
    )
    assert first.record_hash == direct.record_hash
    assert first.model_dump(mode="json")["recorded_at"].endswith("Z")


def test_tamper_reorder_and_non_canonical_lines_fail(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    original_head = _append_pair(log)[1].record_hash
    original = path.read_bytes()

    path.write_bytes(original.replace(b"alpha", b"beta", 1))
    tampered = log.verify()
    assert not tampered.ok
    assert tampered.errors

    path.write_bytes(original)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    assert not log.verify().ok

    path.write_bytes(original)
    spaced = path.read_text(encoding="utf-8").replace(":", ": ", 1)
    path.write_text(spaced, encoding="utf-8")
    assert not log.verify().ok

    path.write_bytes(original)
    assert log.verify(expected_head=original_head).ok
    assert not log.verify(expected_head=GENESIS_HASH).ok


def test_partial_rewrite_breaks_the_following_link(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    first, second = _append_pair(log)
    forged = TraceRecord.build(
        record_id=first.record_id,
        seq=first.seq,
        execution_id=first.execution_id,
        event_type=first.event_type,
        payload={"marker": "forged"},
        recorded_at=first.recorded_at,
        prev_hash=first.prev_hash,
    )
    lines = [
        canonical_json(forged.model_dump(mode="json")),
        canonical_json(second.model_dump(mode="json")),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = log.verify()
    assert not report.ok
    assert "prev_hash" in report.errors[0]


def test_rewritten_suffix_needs_an_external_head_pin(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    _, original_second = _append_pair(log)
    forged_first = TraceRecord.build(
        record_id="rec-0",
        seq=0,
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"marker": "forged"},
        recorded_at=NOW,
        prev_hash=GENESIS_HASH,
    )
    forged_second = TraceRecord.build(
        record_id="rec-1",
        seq=1,
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.EXECUTION_FINISHED,
        payload={"status": "succeeded"},
        recorded_at=NOW,
        prev_hash=forged_first.record_hash,
    )
    lines = [
        canonical_json(record.model_dump(mode="json"))
        for record in (forged_first, forged_second)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify().ok
    assert not log.verify(expected_head=original_second.record_hash).ok
    assert log.verify(expected_head=forged_second.record_hash).ok


def test_tail_truncation_is_caught_when_the_head_is_pinned(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    first, second = _append_pair(log)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n", encoding="utf-8")
    assert log.verify().ok
    assert log.verify().head_hash == first.record_hash
    assert not log.verify(expected_head=second.record_hash).ok


def test_append_refuses_a_broken_log_and_a_foreign_execution(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    _append_pair(log)
    before = path.read_bytes()
    path.write_bytes(before.replace(b"alpha", b"beta", 1))
    broken = path.read_bytes()
    with pytest.raises(TraceIntegrityError):
        log.append(
            execution_id=EXECUTION_ID,
            event_type=TraceEventType.EXECUTION_FINISHED,
            payload={"status": "again"},
            recorded_at=NOW,
            record_id="rec-2",
        )
    assert path.read_bytes() == broken

    path.write_bytes(before)
    with pytest.raises(TraceIntegrityError, match="execution_id"):
        log.append(
            execution_id="exec-other",
            event_type=TraceEventType.EXECUTION_FINISHED,
            payload={"status": "nope"},
            recorded_at=NOW,
            record_id="rec-2",
        )
    assert path.read_bytes() == before


def test_empty_missing_and_malformed_files(tmp_path) -> None:
    missing = TraceLog(tmp_path / "missing.jsonl")
    assert not missing.verify().ok

    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    report = TraceLog(empty).verify()
    assert report.ok
    assert report.record_count == 0
    assert report.head_hash == GENESIS_HASH

    torn = tmp_path / "torn.jsonl"
    torn.write_text('{"schema_version":"0.1"}', encoding="utf-8")
    assert not TraceLog(torn).verify().ok

    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf")
    assert not TraceLog(bom).verify().ok

    crlf = tmp_path / "crlf.jsonl"
    crlf.write_bytes(b"\r\n")
    assert not TraceLog(crlf).verify().ok


def test_in_memory_trace_matches_the_jsonl_chain(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    log = TraceLog(path)
    first, second = _append_pair(log)
    trace = ExecutionTrace.build(trace_id="trace-1", execution_id=EXECUTION_ID, records=(first, second))
    assert trace.head_hash == log.verify().head_hash
    revived = ExecutionTrace.model_validate_json(trace.model_dump_json())
    assert revived == trace

    broken = sample_records()
    with pytest.raises(ValidationError):
        ExecutionTrace(
            trace_id="trace-1",
            execution_id=EXECUTION_ID,
            records=broken,
            head_hash=GENESIS_HASH,
        )


def test_invocation_with_missing_usage_round_trips_through_the_log(tmp_path) -> None:
    invocation = sample_invocation()
    assert invocation.usage.prompt_tokens is None
    log = TraceLog(tmp_path / "trace.jsonl")
    log.append(
        execution_id=EXECUTION_ID,
        event_type=TraceEventType.INVOCATION_COMPLETED,
        payload={"invocation": invocation.model_dump(mode="json")},
        recorded_at=NOW,
        record_id="rec-0",
    )
    restored = type(invocation).model_validate(log.read()[0].payload["invocation"])
    assert restored == invocation
    assert restored.usage.status.value == "missing"
    assert restored.usage.prompt_tokens is None
    assert restored.cost.amount is None
