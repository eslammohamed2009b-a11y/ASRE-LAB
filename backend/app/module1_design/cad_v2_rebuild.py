"""Structured rebuild reporting and dependency-aware in-process reuse."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.module1_design.cad_v2_compiler import (
    CompiledDesign,
    FeatureCompilationCache,
    compile_design,
    feature_input_hashes,
    normalized_parameter_state,
)
from app.module1_design.cad_v2_schemas import EngineeringDesignDocumentV2, StrictModel


class DesignRebuildReport(StrictModel):
    changed_parameters: list[str]
    rebuilt_features: list[str]
    unchanged_features: list[str]
    changed_bodies: list[str]
    semantic_regions_preserved: list[str]
    semantic_regions_reselected: list[str]
    semantic_regions_lost: list[str]
    validation_status: str
    new_design_hash: str
    cache_hits: list[str] = Field(default_factory=list)
    incremental_execution_claimed: bool = False


def _body_signatures(compiled: CompiledDesign) -> dict[str, Any]:
    return {
        item["body_id"]: item
        for item in compiled.geometry_signature.get("bodies", [])
    }


def rebuild_design(
    previous: CompiledDesign,
    document: EngineeringDesignDocumentV2,
) -> tuple[CompiledDesign, DesignRebuildReport]:
    previous_parameters = previous.normalized_parameters
    next_parameters = normalized_parameter_state(document)
    changed_parameters = sorted(
        name for name in set(previous_parameters) | set(next_parameters)
        if previous_parameters.get(name) != next_parameters.get(name)
    )

    next_hashes = feature_input_hashes(document)
    unchanged = sorted(
        feature_id for feature_id, value in next_hashes.items()
        if previous.feature_hashes.get(feature_id) == value
    )
    rebuilt = sorted(set(next_hashes) - set(unchanged))

    cache = FeatureCompilationCache()
    previous_features = {item.feature_id: item for item in previous.document.features}
    for feature_id in unchanged:
        old_feature = previous_features.get(feature_id)
        if old_feature is not None and old_feature.output_body in previous.bodies:
            cache.put(next_hashes[feature_id], previous.bodies[old_feature.output_body])
    compiled = compile_design(document, cache=cache, strict_semantics=False)

    old_bodies = _body_signatures(previous)
    new_bodies = _body_signatures(compiled)
    changed_bodies = sorted(
        body_id for body_id in set(old_bodies) | set(new_bodies)
        if old_bodies.get(body_id) != new_bodies.get(body_id)
    )
    old_regions = {item["tag"]: item for item in previous.semantic_regions}
    new_regions = {item["tag"]: item for item in compiled.semantic_regions}
    preserved: list[str] = []
    reselected: list[str] = []
    lost: list[str] = []
    for tag in sorted(set(old_regions) | set(new_regions)):
        new = new_regions.get(tag)
        old = old_regions.get(tag)
        if new is None or new.get("status") == "LOST":
            lost.append(tag)
        elif old and old.get("topology_signatures") == new.get("topology_signatures"):
            preserved.append(tag)
        else:
            reselected.append(tag)
    return compiled, DesignRebuildReport(
        changed_parameters=changed_parameters,
        rebuilt_features=rebuilt,
        unchanged_features=unchanged,
        changed_bodies=changed_bodies,
        semantic_regions_preserved=preserved,
        semantic_regions_reselected=reselected,
        semantic_regions_lost=lost,
        validation_status=compiled.validation.status.value,
        new_design_hash=compiled.design_hash,
        cache_hits=compiled.cache_hits,
        incremental_execution_claimed=False,
    )
