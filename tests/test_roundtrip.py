"""Serialization round-trips for every core schema."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from ohbe.core import SCHEMA_VERSION, OHBEModel, Task
from tests.samples import NOW, roundtrip_cases, sample_task

CASES = roundtrip_cases()


@pytest.mark.parametrize(("name", "document"), CASES, ids=[name for name, _ in CASES])
def test_schema_round_trip(name: str, document: OHBEModel) -> None:
    del name
    raw = document.model_dump_json()
    revived = type(document).model_validate_json(raw)
    assert revived == document
    again = type(document).model_validate(document.model_dump(mode="json"))
    assert again == document
    payload = json.loads(raw)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert document.schema_version == SCHEMA_VERSION
    assert "schema_version" in type(document).model_json_schema()["properties"]


def test_rejected_schema_version_and_unknown_field() -> None:
    task = sample_task()
    payload = task.model_dump(mode="json")
    payload["schema_version"] = "9.9"
    with pytest.raises(ValidationError):
        Task.model_validate(payload)
    payload = task.model_dump(mode="json")
    payload["extra"] = True
    with pytest.raises(ValidationError):
        Task.model_validate(payload)


def test_documents_are_frozen() -> None:
    task = sample_task()
    with pytest.raises(ValidationError):
        task.name = "renamed"  # type: ignore[misc]


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        sample_experiment_at(datetime(2026, 10, 9, 11, 0, 0))


def test_timestamps_normalize_to_utc() -> None:
    plus_one = timezone(timedelta(hours=1))
    local = sample_experiment_at(datetime(2026, 10, 9, 12, 2, 3, 456789, tzinfo=plus_one))
    utc = sample_experiment_at(NOW)
    assert local.created_at == utc.created_at
    assert local.experiment_hash == utc.experiment_hash
    assert local.model_dump(mode="json")["created_at"] == "2026-10-09T11:02:03.456789Z"


def sample_experiment_at(created_at: datetime):
    from tests.samples import sample_experiment

    return sample_experiment(created_at=created_at)
