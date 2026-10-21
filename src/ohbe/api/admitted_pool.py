"""Build the round plan pool from admitted catalog members.

Comparison-slot ids are the membership. Disk catalog files supply name,
revision, and the LiteLLM alias when they exist. A cloud row that was
admitted only in this process still counts. Nothing here writes a pool file
or reads an API key.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ohbe.api.intake import IntakeService, list_catalog_entries
from ohbe.api.validate import ValidationRejected
from ohbe.core.model import ModelDefinition, ModelPool

ADMITTED_POOL_ID = "pool-admitted"
FIXTURE_POOL_ID = "pool-pilot"
DRY_RUN_LABEL = "Dry-run"


def build_admitted_pool(root: Path, intake: IntakeService, model_ids: Sequence[str]) -> ModelPool:
    """Seal one pool whose models are the current comparison slots, in order."""

    if not model_ids:
        raise ValidationRejected(
            409,
            "slots_required",
            "Set comparison slots from the admitted catalog before generating a plan.",
        )
    files = {
        entry.model_id: entry
        for entry in list_catalog_entries(root, {}).entries
        if entry.ok and entry.model_id
    }
    records = {row.model_id: row for row in intake.catalog().models}
    models: list[ModelDefinition] = []
    for model_id in model_ids:
        row = records.get(model_id)
        if row is None or not row.accepted:
            raise ValidationRejected(404, "not_in_catalog", f"Model is not in the catalog: {model_id}")
        entry = files.get(model_id)
        if entry is not None:
            metadata = {"litellm_model": entry.litellm_model} if entry.litellm_model else {}
            models.append(
                ModelDefinition(
                    model_id=model_id,
                    provider=entry.provider or row.provider,
                    name=entry.name or row.display_name or model_id,
                    revision=entry.revision or None,
                    metadata=metadata,
                )
            )
            continue
        models.append(
            ModelDefinition(
                model_id=model_id,
                provider=row.provider,
                name=row.display_name or model_id,
            )
        )
    return ModelPool.build(pool_id=ADMITTED_POOL_ID, models=tuple(models))
