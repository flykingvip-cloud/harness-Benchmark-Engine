"""Append-only execution traces.

`TraceRecord` is one sealed event. `ExecutionTrace` is the in-memory document
form. `TraceLog` is the raw JSONL file: one canonical JSON object per line,
written only by appending.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import (
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ohbe.core.base import AwareDatetime, NonNegativeInt, OHBEModel, build_sealed
from ohbe.core.integrity import (
    GENESIS_HASH,
    TraceIntegrityError,
    canonical_json,
    require_sha256,
    seal_or_verify,
    verify_chain,
)


class TraceEventType:
    """Well-known event names. Other dotted names are valid extension events."""

    EXECUTION_STARTED = "execution.started"
    INVOCATION_COMPLETED = "invocation.completed"
    EXECUTION_FINISHED = "execution.finished"


_EVENT_TYPE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"


class TraceRecord(OHBEModel):
    """One append-only trace event.

    `record_hash` covers every field except itself, including `prev_hash`.
    """

    record_id: str = Field(min_length=1)
    seq: NonNegativeInt
    execution_id: str = Field(min_length=1)
    event_type: str = Field(pattern=_EVENT_TYPE)
    payload: dict[str, Any]
    recorded_at: AwareDatetime
    prev_hash: str
    record_hash: str

    @field_validator("prev_hash", "record_hash")
    @classmethod
    def _hashes(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field=info.field_name or "hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> TraceRecord:
        seal_or_verify(self, info, "record_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> TraceRecord:
        return build_sealed(cls, "record_hash", data)


class ExecutionTrace(OHBEModel):
    """In-memory trace document.

    `head_hash` is the last record's `record_hash`, or the genesis digest when
    the trace is empty. It is the chain head, not a second hash of the file.
    """

    trace_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    records: tuple[TraceRecord, ...] = ()
    head_hash: str

    @field_validator("head_hash")
    @classmethod
    def _head_hash(cls, value: str) -> str:
        return require_sha256(value, field="head_hash")

    @model_validator(mode="after")
    def _check_chain(self) -> ExecutionTrace:
        for index, record in enumerate(self.records):
            if record.execution_id != self.execution_id:
                raise ValueError(f"record {index} execution_id does not match the trace")
        head = verify_chain(self.records)
        if self.head_hash != head:
            raise ValueError("head_hash does not match the trace chain head")
        return self

    @classmethod
    def build(
        cls,
        *,
        trace_id: str,
        execution_id: str,
        records: Sequence[TraceRecord] = (),
    ) -> ExecutionTrace:
        for index, record in enumerate(records):
            if record.execution_id != execution_id:
                raise ValueError(f"record {index} execution_id does not match the trace")
        return cls(
            trace_id=trace_id,
            execution_id=execution_id,
            records=tuple(records),
            head_hash=verify_chain(records),
        )


@dataclass(frozen=True)
class IntegrityReport:
    """Result of verifying a JSONL trace file."""

    ok: bool
    record_count: int
    head_hash: str
    errors: tuple[str, ...] = ()


class TraceLog:
    """Single-writer append-only JSONL log of `TraceRecord` lines.

    Each append checks the existing chain, then writes one canonical line with
    ``O_APPEND``. There is no rewrite API. A torn final line fails verification
    and leaves the prefix in place.

    Truncation of a valid prefix still verifies on its own. Pass `expected_head`
    to detect that once the sealed head has been stored elsewhere.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def append(
        self,
        *,
        execution_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        recorded_at: Any,
        record_id: str,
    ) -> TraceRecord:
        existing = self._read_for_append()
        if existing and existing[0].execution_id != execution_id:
            raise TraceIntegrityError("trace file is bound to a different execution_id")
        previous = existing[-1].record_hash if existing else GENESIS_HASH
        record = TraceRecord.build(
            record_id=record_id,
            seq=len(existing),
            execution_id=execution_id,
            event_type=event_type,
            payload=dict(payload),
            recorded_at=recorded_at,
            prev_hash=previous,
        )
        line = canonical_json(record.model_dump(mode="json"))
        self._append_line(line)
        return record

    def read(self) -> list[TraceRecord]:
        records: list[TraceRecord] = []
        for index, line in enumerate(self._lines()):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceIntegrityError(f"line is not JSON: {exc}", index=index) from exc
            if not isinstance(parsed, dict):
                raise TraceIntegrityError("line is not a JSON object", index=index)
            if canonical_json(parsed) != line:
                raise TraceIntegrityError("line is not canonical JSON", index=index)
            try:
                record = TraceRecord.model_validate(parsed)
            except ValidationError as exc:
                raise TraceIntegrityError(
                    f"line failed schema checks: {exc}",
                    index=index,
                ) from exc
            records.append(record)
        verify_chain(records)
        return records

    def verify(self, *, expected_head: str | None = None) -> IntegrityReport:
        try:
            records = self.read()
            head = records[-1].record_hash if records else GENESIS_HASH
            if expected_head is not None:
                try:
                    require_sha256(expected_head, field="expected_head")
                except ValueError as exc:
                    raise TraceIntegrityError(str(exc)) from exc
                if expected_head != head:
                    raise TraceIntegrityError("head hash does not match the expected anchor")
        except TraceIntegrityError as exc:
            return IntegrityReport(ok=False, record_count=0, head_hash="", errors=(str(exc),))
        return IntegrityReport(
            ok=True,
            record_count=len(records),
            head_hash=head,
            errors=(),
        )

    def _read_for_append(self) -> list[TraceRecord]:
        if not self.path.exists():
            return []
        return self.read()

    def _lines(self) -> list[str]:
        if not self.path.exists():
            raise TraceIntegrityError("trace file does not exist")
        if not self.path.is_file():
            raise TraceIntegrityError("trace path is not a file")
        raw = self.path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise TraceIntegrityError("jsonl must not start with a BOM")
        if b"\r" in raw:
            raise TraceIntegrityError("jsonl must use LF newlines")
        if raw == b"":
            return []
        if not raw.endswith(b"\n"):
            raise TraceIntegrityError("jsonl must end with a newline")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TraceIntegrityError(f"jsonl is not valid UTF-8: {exc}") from exc
        lines = text.split("\n")[:-1]
        for index, line in enumerate(lines):
            if line == "":
                raise TraceIntegrityError("blank line in jsonl", index=index)
        return lines

    def _append_line(self, line: str) -> None:
        if self.path.exists() and not self.path.is_file():
            raise TraceIntegrityError("trace path is not a file")
        data = (line + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)
