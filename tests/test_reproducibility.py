"""Content hashes, seeds, and document binding."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ohbe import __version__
from ohbe.core import OracleStub, Task, current_runtime_fingerprint, hash_task_set
from ohbe.core.integrity import HASH_PLACEHOLDER
from tests.samples import (
    sample_execution,
    sample_experiment,
    sample_harness,
    sample_model,
    sample_pool,
    sample_task,
)


def test_task_hash_is_stable_and_input_sensitive() -> None:
    first = sample_task()
    second = sample_task()
    assert first.content_hash == second.content_hash
    assert first.content_hash != HASH_PLACEHOLDER
    changed = sample_task(input={"prompt": "Say bye"})
    assert changed.content_hash != first.content_hash
    fields = {
        "task_id": "task-001",
        "name": "echo",
        "version": "1",
        "input": {"prompt": "x"},
    }
    assert Task.build(**fields).content_hash == Task.build(**fields, metadata={}).content_hash


def test_tampered_task_hash_is_rejected() -> None:
    payload = sample_task().model_dump(mode="json")
    payload["input"] = {"prompt": "tampered"}
    with pytest.raises(ValidationError, match="content_hash"):
        type(sample_task()).model_validate(payload)


def test_derived_hash_cannot_be_supplied_to_build() -> None:
    with pytest.raises(TypeError, match="content_hash"):
        sample_task(content_hash=HASH_PLACEHOLDER)


def test_task_set_hash_is_order_sensitive() -> None:
    alpha = sample_task()
    beta = sample_task(task_id="task-002", name="other")
    forward = hash_task_set([alpha, beta])
    backward = hash_task_set([beta, alpha])
    assert forward != backward
    experiment = sample_experiment(task_set_hash=forward)
    experiment.check_task_set([alpha, beta])
    with pytest.raises(ValueError, match="task_set_hash"):
        experiment.check_task_set([beta, alpha])


def test_harness_ref_matches_config_hash() -> None:
    config = sample_harness()
    ref = config.to_ref()
    assert ref.matches(config)
    assert ref.config_hash == config.config_hash
    other = sample_harness(parameters={"max_steps": 3})
    assert not ref.matches(other)
    assert other.config_hash != config.config_hash


def test_pool_hash_changes_with_membership() -> None:
    pool = sample_pool()
    revised = sample_pool(models=(sample_model(revision="2026-10-02"),))
    assert revised.content_hash != pool.content_hash


def test_pool_rejects_duplicates_and_empty_membership() -> None:
    model = sample_model()
    with pytest.raises(ValidationError, match="duplicate"):
        sample_pool(models=(model, model))
    with pytest.raises(ValidationError):
        sample_pool(models=())


def test_execution_binding_and_seed() -> None:
    execution = sample_execution()
    execution.check_binding(
        task=sample_task(),
        harness=sample_harness(),
        pool=sample_pool(),
    )
    assert execution.seed == 7
    with pytest.raises(ValueError, match="task"):
        execution.check_binding(
            task=sample_task(task_id="task-other"),
            harness=sample_harness(),
            pool=sample_pool(),
        )


def test_experiment_hash_covers_seed_and_extensions() -> None:
    base = sample_experiment()
    reseeded = sample_experiment(seed=8)
    assert reseeded.experiment_hash != base.experiment_hash
    with_oracle = sample_experiment(oracle=OracleStub(ref="dataset://oracle/echo"))
    assert with_oracle.experiment_hash != base.experiment_hash
    assert with_oracle.fingerprint.ohbe_version == __version__


def test_runtime_fingerprint_records_versions_without_guessing_git() -> None:
    fingerprint = current_runtime_fingerprint()
    assert fingerprint.ohbe_version == __version__
    assert fingerprint.python_version
    assert fingerprint.platform
    assert fingerprint.git_commit is None
    recorded = current_runtime_fingerprint(git_commit="c0ffee", git_dirty=False)
    assert recorded.git_commit == "c0ffee"
    assert recorded.git_dirty is False


def test_seed_is_not_coerced_from_a_string() -> None:
    with pytest.raises(ValidationError):
        sample_experiment(seed="7")
