"""Missing usage and cost stay missing. They are not coerced to zero."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ohbe.core import (
    CostAssessment,
    CostStatus,
    InvocationStatus,
    PricingRef,
    TokenUsage,
    UsageStatus,
    select_pricing_ref,
)
from tests.samples import PRICING_TABLE_DIGEST, sample_invocation, sample_pricing_ref


def test_missing_token_usage_is_not_coerced_to_zero() -> None:
    invocation = sample_invocation()
    assert invocation.usage.status is UsageStatus.MISSING
    assert invocation.usage.prompt_tokens is None
    assert invocation.usage.completion_tokens is None
    assert invocation.usage.total_tokens is None
    assert invocation.cost.status is CostStatus.NOT_COMPUTED
    assert invocation.cost.amount is None

    raw = invocation.model_dump(mode="json")
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        assert raw["usage"][name] is None
        assert TokenUsage.model_fields[name].default is None
    assert raw["usage"]["status"] == "missing"
    assert raw["cost"]["amount"] is None
    assert raw["cost"]["status"] == "not_computed"
    assert CostAssessment.model_fields["amount"].default is None


def _rebuild_invocation(*, usage: object) -> object:
    data = sample_invocation().model_dump(mode="python")
    data.pop("invocation_hash")
    if usage is Ellipsis:
        data.pop("usage")
    else:
        data["usage"] = usage
    return type(sample_invocation()).build(**data)


def test_omitted_and_null_usage_reload_as_missing() -> None:
    reloaded = _rebuild_invocation(usage=Ellipsis)
    assert reloaded.usage.status is UsageStatus.MISSING
    assert reloaded.usage.prompt_tokens is None

    from_null = _rebuild_invocation(usage=None)
    assert from_null.usage == TokenUsage.missing()
    assert from_null.usage.prompt_tokens is None


def test_partial_usage_object_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({"prompt_tokens": 5})
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({"status": "present", "prompt_tokens": 5})
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({})


def test_zero_counts_cannot_stand_in_for_missing_or_invalid_usage() -> None:
    for status in (UsageStatus.MISSING, UsageStatus.INVALID):
        with pytest.raises(ValidationError, match="must not carry token counts"):
            TokenUsage(
                status=status,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )


def test_explicit_present_zero_is_preserved() -> None:
    usage = TokenUsage.present(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    assert usage.status is UsageStatus.PRESENT
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0
    revived = TokenUsage.model_validate_json(usage.model_dump_json())
    assert revived == usage


def test_present_usage_rejects_null_counts_and_coercion() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(status=UsageStatus.PRESENT, prompt_tokens=1, completion_tokens=1, total_tokens=None)
    with pytest.raises(ValidationError):
        TokenUsage.model_validate(
            {
                "status": "present",
                "prompt_tokens": "0",
                "completion_tokens": 0,
                "total_tokens": 0,
            }
        )
    with pytest.raises(ValidationError):
        TokenUsage.model_validate(
            {
                "status": "missing",
                "prompt_tokens": True,
                "completion_tokens": None,
                "total_tokens": None,
            }
        )


def test_invalid_usage_keeps_counts_null() -> None:
    usage = TokenUsage.invalid(reason="negative prompt count was dropped")
    assert usage.status is UsageStatus.INVALID
    assert usage.prompt_tokens is None
    assert usage.reason == "negative prompt count was dropped"
    assert usage.model_dump(mode="json")["prompt_tokens"] is None


def test_not_computed_cost_rejects_a_zero_amount() -> None:
    with pytest.raises(ValidationError):
        CostAssessment(status=CostStatus.NOT_COMPUTED, amount="0")
    with pytest.raises(ValidationError):
        CostAssessment(status=CostStatus.INVALID, amount="0", currency="USD")
    with pytest.raises(ValidationError):
        CostAssessment.model_validate({"status": "not_computed", "amount": 0})


def test_computed_zero_must_be_explicit() -> None:
    cost = CostAssessment.computed(
        amount="0",
        currency="USD",
        pricing_ref=sample_pricing_ref(),
    )
    assert cost.status is CostStatus.COMPUTED
    assert cost.amount == "0"
    with pytest.raises(ValidationError):
        CostAssessment.computed(amount="0", currency="USD", pricing_ref=None)  # type: ignore[arg-type]


def test_missing_usage_cannot_carry_a_computed_cost() -> None:
    with pytest.raises(ValidationError, match="cost cannot be computed"):
        sample_invocation(
            usage=TokenUsage.missing(),
            cost=CostAssessment.computed(
                amount="0",
                currency="USD",
                pricing_ref=sample_pricing_ref(),
            ),
        )
    invocation = sample_invocation(
        usage=TokenUsage.present(prompt_tokens=2, completion_tokens=2, total_tokens=4),
    )
    assert invocation.usage.status is UsageStatus.PRESENT
    assert invocation.cost.status is CostStatus.NOT_COMPUTED
    assert invocation.cost.amount is None


def test_failed_invocation_can_record_invalid_usage_without_a_zero() -> None:
    invocation = sample_invocation(
        status=InvocationStatus.FAILED,
        response=None,
        error="provider returned no usage",
        usage=TokenUsage.invalid(reason="field absent"),
        cost=CostAssessment.invalid(reason="usage invalid"),
    )
    assert invocation.usage.prompt_tokens is None
    assert invocation.cost.amount is None
    assert invocation.cost.status is CostStatus.INVALID


def test_pricing_ref_is_versioned_and_does_not_invent_a_price() -> None:
    ref = sample_pricing_ref()
    assert ref.content_hash is None
    assert ref.version == "2026-10-01"
    pinned = sample_pricing_ref(content_hash=PRICING_TABLE_DIGEST)
    assert pinned.content_hash == PRICING_TABLE_DIGEST
    from ohbe.core import canonical_model_hash

    assert canonical_model_hash(pinned) != pinned.content_hash


def test_select_pricing_ref_prefers_the_most_specific_pointer() -> None:
    invocation = PricingRef(pricing_id="call", version="1")
    model = PricingRef(pricing_id="model", version="1")
    experiment = PricingRef(pricing_id="experiment", version="1")
    assert select_pricing_ref(invocation=invocation, model=model, experiment=experiment) == invocation
    assert select_pricing_ref(model=model, experiment=experiment) == model
    assert select_pricing_ref(experiment=experiment) == experiment
    assert select_pricing_ref() is None
