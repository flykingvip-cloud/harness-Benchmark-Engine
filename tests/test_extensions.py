"""Oracle and routing regret stay unimplemented stubs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ohbe.core import OracleStub, RoutingRegretStub
from tests.samples import sample_experiment


def test_stubs_have_no_numeric_results() -> None:
    assert OracleStub().status == "not_implemented"
    assert RoutingRegretStub().status == "not_implemented"
    for model in (OracleStub, RoutingRegretStub):
        assert "score" not in model.model_fields
        assert "regret" not in model.model_fields
        assert "value" not in model.model_fields
    with pytest.raises(ValidationError):
        OracleStub.model_validate({"status": "implemented"})
    with pytest.raises(ValidationError):
        RoutingRegretStub.model_validate({"status": "ready"})


def test_experiment_can_carry_deferred_refs_or_leave_them_off() -> None:
    experiment = sample_experiment(
        oracle=OracleStub(ref="dataset://oracle/echo", notes="later"),
        routing_regret=RoutingRegretStub(notes="later"),
    )
    assert experiment.oracle is not None
    assert experiment.oracle.status == "not_implemented"
    assert experiment.routing_regret is not None
    assert experiment.routing_regret.status == "not_implemented"
    bare = sample_experiment()
    assert bare.oracle is None
    assert bare.routing_regret is None
