"""Deterministic validation of persisted scientific evidence relationships."""
from __future__ import annotations

from app.v2.evidence_models import EVIDENCE_MODELS, EvidenceType


class EvidenceIntegrityError(ValueError):
    pass


def validate_scientific_record(record: dict, *, expected_type: EvidenceType | None = None):
    record_type = str(record.get("record_type", ""))
    if not record_type.startswith("scientific_"):
        raise EvidenceIntegrityError("Record is not authoritative scientific evidence")
    try:
        evidence_type = EvidenceType(record_type.removeprefix("scientific_"))
        model = EVIDENCE_MODELS[evidence_type].model_validate(record.get("payload"))
    except Exception as exc:
        raise EvidenceIntegrityError("Scientific evidence is not schema-valid") from exc
    if expected_type is not None and evidence_type != expected_type:
        raise EvidenceIntegrityError("Scientific evidence has the wrong type")
    if model.schema_version != "2.0":
        raise EvidenceIntegrityError("Scientific evidence schema is unsupported")
    return model


def validate_simulation_record(record: dict, source, *, expected_type: EvidenceType | None = None):
    model = validate_scientific_record(record, expected_type=expected_type)
    actual_type = EvidenceType(record["record_type"].removeprefix("scientific_"))
    result = source.result
    if model.simulation_id != source.simulation_id:
        raise EvidenceIntegrityError("Evidence references a different simulation")
    if model.solver_id != source.solver_id or model.solver_version != source.solver_version:
        raise EvidenceIntegrityError("Evidence solver identity contradicts the simulation")
    if model.experiment_id != source.experiment_id or model.design_id != source.design_id:
        raise EvidenceIntegrityError("Evidence experiment/design identity contradicts the simulation")
    if result is not None:
        fingerprint = result.validation_metadata.get("input_fingerprint")
        if model.input_fingerprint != fingerprint or model.result_hash != result.reproducibility_hash:
            raise EvidenceIntegrityError("Evidence provenance hashes contradict the persisted result")
        if actual_type == EvidenceType.NUMERICAL_RESULT and model.summary_metrics != result.summary_metrics:
            raise EvidenceIntegrityError("Numerical evidence metrics contradict the persisted result")
        if actual_type == EvidenceType.BENCHMARK and (
            model.metric_name not in result.summary_metrics
            or float(model.computed_value) != float(result.summary_metrics[model.metric_name])
        ):
            raise EvidenceIntegrityError("Benchmark evidence contradicts the persisted result metric")
    return model


def records_by_type(repository, user_id: str, source) -> dict[EvidenceType, list[tuple[dict, object]]]:
    output: dict[EvidenceType, list[tuple[dict, object]]] = {}
    for record in repository.list_scientific_for_simulation(user_id, source.simulation_id):
        try:
            model = validate_simulation_record(record, source)
            evidence_type = EvidenceType(record["record_type"].removeprefix("scientific_"))
        except EvidenceIntegrityError:
            continue
        output.setdefault(evidence_type, []).append((record, model))
    return output
