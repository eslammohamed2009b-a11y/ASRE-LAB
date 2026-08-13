"""Reusable claim-to-evidence integrity contract; no prompt-dependent enforcement."""
from __future__ import annotations

import hashlib
import json

from app.module2_simulation.source_resolution import resolve_simulation_source
from app.v2.evidence_integrity import (
    EvidenceIntegrityError, validate_scientific_record, validate_simulation_record,
)


def _authoritative(record: dict, repository, user_id: str) -> bool:
    if record.get("record_type") == "scientific_trust":
        payload = record.get("payload", {})
        ids = payload.get("evidence_ids", [])
        dependencies_valid = (
            payload.get("trust_version") == "2.0" and bool(ids)
            and all(
                (dependency := repository.get(source_id, user_id)) is not None
                and dependency.get("record_type", "").startswith("scientific_")
                and _authoritative(dependency, repository, user_id)
                for source_id in ids
            )
        )
        source_repository = getattr(repository, "source_repository", None)
        if not dependencies_valid or source_repository is None:
            return False
        dimension_ids = sorted({
            item for dimension in payload.get("dimensions", {}).values()
            for item in dimension.get("evidence_ids", [])
        })
        if dimension_ids != sorted(ids):
            return False
        canonical = dict(payload); declared_hash = canonical.pop("trust_hash", None)
        calculated_hash = hashlib.sha256(json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode()).hexdigest()
        return declared_hash == calculated_hash
    if record.get("record_type", "").startswith("scientific_"):
        try:
            model = validate_scientific_record(record)
        except EvidenceIntegrityError:
            return False
        source_repository = getattr(repository, "source_repository", None)
        if record["record_type"] == "scientific_analysis":
            if source_repository is None:
                return False
            analysis = source_repository.get_analysis(model.analysis_id)
            if analysis is None or analysis.user_id != user_id:
                return False
            if (
                analysis.experiment_id != model.experiment_id
                or analysis.analysis_type != model.analysis_type
                or analysis.dataset_hash != model.dataset_hash
                or sorted(analysis.source_simulation_ids) != sorted(model.source_simulation_ids)
                or analysis.reproducibility_hash != model.provenance.get("reproducibility_hash")
            ):
                return False
        if model.simulation_id:
            if source_repository is None:
                return False
            try:
                source = resolve_simulation_source(
                    model.simulation_id, user_id, require_result=True,
                    repository=source_repository,
                )
                validate_simulation_record(record, source)
                if record["record_type"] == "scientific_field_result":
                    fields = source_repository.list_field_results(model.simulation_id)
                    if not any(
                        item.variable_name == model.variable_name
                        and item.checksum_sha256 == model.checksum_sha256
                        for item in fields
                    ):
                        return False
            except Exception:
                return False
        return all(
            (dependency := repository.get(source_id, user_id)) is not None
            and dependency.get("record_type", "").startswith("scientific_")
            and _authoritative(dependency, repository, user_id)
            for source_id in model.source_ids
        )
    return False


def classify_claim(
    repository, user_id: str, statement: str, evidence_ids: list[str],
    *, requested_classification: str = "finding",
) -> dict:
    """A finding is allowed only when every cited record is owner-visible and authoritative."""
    records = [repository.get(item, user_id) for item in evidence_ids]
    supported = bool(evidence_ids) and all(
        record is not None and _authoritative(record, repository, user_id)
        for record in records
    )
    if requested_classification == "hypothesis":
        classification = "hypothesis"
    elif supported:
        classification = "finding"
    else:
        classification = "insufficient_evidence"
    return {
        "statement": statement,
        "classification": classification,
        "evidence_ids": evidence_ids if supported else [],
        "reason_code": "AUTHORITATIVE_EVIDENCE_RESOLVED" if supported else "AUTHORITATIVE_EVIDENCE_REQUIRED",
    }
