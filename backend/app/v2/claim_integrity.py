"""Reusable claim-to-evidence integrity contract; no prompt-dependent enforcement."""
from __future__ import annotations

import hashlib
import json
import math

from app.module2_simulation.source_resolution import resolve_simulation_source
from app.module3_analysis.dataset import scientific_dataset_hash
from app.module3_analysis.schemas import ExperimentDataset
from app.v2.evidence_integrity import (
    EvidenceIntegrityError, validate_scientific_record, validate_simulation_record,
)
from app.v2.evidence_models import EvidenceType


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


def _same_value(asserted, authoritative) -> bool:
    if isinstance(asserted, bool) or isinstance(authoritative, bool):
        return asserted is authoritative
    if isinstance(asserted, (int, float)) and isinstance(authoritative, (int, float)):
        return math.isfinite(float(asserted)) and math.isfinite(float(authoritative)) and math.isclose(
            float(asserted), float(authoritative), rel_tol=1e-12, abs_tol=1e-12,
        )
    return asserted == authoritative


def _analysis_rows(record: dict, repository, user_id: str) -> list[dict]:
    model = validate_scientific_record(record, expected_type=EvidenceType.ANALYSIS)
    source_repository = getattr(repository, "source_repository", None)
    if source_repository is None:
        return []
    analysis = source_repository.get_analysis(model.analysis_id)
    if analysis is None or analysis.user_id != user_id:
        return []
    dataset = analysis.result.get("dataset", {}) if isinstance(analysis.result, dict) else {}
    if not isinstance(dataset, dict):
        return []
    try:
        parsed = ExperimentDataset.model_validate(dataset)
    except Exception:
        return []
    if (
        parsed.experiment_id != analysis.experiment_id
        or parsed.dataset_hash != analysis.dataset_hash
        or scientific_dataset_hash(parsed.experiment_id, parsed.rows, parsed.columns, parsed.units) != analysis.dataset_hash
        or sorted(row.simulation_id for row in parsed.rows) != sorted(analysis.source_simulation_ids)
    ):
        return []
    return [row.model_dump(mode="json") for row in parsed.rows]


def _record_supports_metric(
    record: dict, repository, user_id: str, design_id: str, metric_name: str, value,
) -> bool:
    if record.get("record_type") == "scientific_trust":
        return metric_name == "confidence" and str(record["payload"].get("overall_trust","")).lower() == str(value).lower()
    if record.get("record_type") == "scientific_validity":
        return metric_name == "validity_status" and str(record["payload"].get("status")) == str(value)
    if record.get("record_type") == "scientific_benchmark":
        return metric_name == "benchmark_passed" and record["payload"].get("passed") is value
    if record.get("record_type") == "scientific_numerical_result":
        model = validate_scientific_record(record, expected_type=EvidenceType.NUMERICAL_RESULT)
        return metric_name in model.summary_metrics and _same_value(value, model.summary_metrics[metric_name])
    if record.get("record_type") == "scientific_analysis":
        for row in _analysis_rows(record, repository, user_id):
            values = row.get("values", {})
            if row.get("design_id") == design_id:
                for candidate_name in (metric_name, f"metric.{metric_name}"):
                    if candidate_name in values and _same_value(value, values[candidate_name]):
                        return True
    return False


def _semantic_support(repository, user_id: str, records_by_id: dict[str, dict], context: dict | None) -> bool:
    if not isinstance(context, dict):
        return False
    experiment_id = context.get("experiment_id")
    candidates = context.get("candidates")
    if not experiment_id or not isinstance(candidates, list) or not candidates:
        return False
    contextual_ids: set[str] = set()
    for candidate in candidates:
        design_id = candidate.get("design_id")
        evidence_ids = candidate.get("evidence_ids", [])
        assertions = candidate.get("metric_assertions", [])
        if not design_id or not evidence_ids or not assertions:
            return False
        candidate_records = []
        for evidence_id in evidence_ids:
            record = records_by_id.get(evidence_id)
            if record is None:
                return False
            if record.get("record_type") == "scientific_trust":
                payload=record.get("payload",{})
                if payload.get("experiment_id") != experiment_id or payload.get("design_id") != design_id:
                    return False
            else:
                try:
                    model = validate_scientific_record(record)
                except EvidenceIntegrityError:
                    return False
                if model.experiment_id != experiment_id:
                    return False
            if record.get("record_type") == "scientific_analysis":
                if not any(row.get("design_id") == design_id for row in _analysis_rows(record, repository, user_id)):
                    return False
            elif record.get("record_type") != "scientific_trust" and model.design_id != design_id:
                return False
            contextual_ids.add(evidence_id)
            candidate_records.append(record)
        for assertion in assertions:
            metric_name = assertion.get("metric_name")
            if not metric_name or not any(
                _record_supports_metric(
                    record, repository, user_id, design_id, metric_name, assertion.get("value"),
                )
                for record in candidate_records
            ):
                return False
    return contextual_ids == set(records_by_id)


def classify_claim(
    repository, user_id: str, statement: str, evidence_ids: list[str],
    *, requested_classification: str = "finding", semantic_context: dict | None = None,
) -> dict:
    """A finding requires authoritative evidence bound to its scientific assertions."""
    records = [repository.get(item, user_id) for item in evidence_ids]
    authoritative = bool(evidence_ids) and all(
        record is not None and _authoritative(record, repository, user_id)
        for record in records
    )
    records_by_id = {
        evidence_id: record for evidence_id, record in zip(evidence_ids, records) if record is not None
    }
    supported = authoritative and _semantic_support(
        repository, user_id, records_by_id, semantic_context,
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


def is_authoritative_evidence(repository, user_id: str, record: dict) -> bool:
    """Public narrow helper for non-numerical evidence-state summaries such as trust."""
    return _authoritative(record, repository, user_id)
