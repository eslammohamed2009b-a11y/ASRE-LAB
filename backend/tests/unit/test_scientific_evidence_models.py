import pytest
from app.v2.evidence_models import EVIDENCE_MODELS, EvidenceType

def _base(kind): return {"evidence_type":kind.value,"status":"completed"}

@pytest.mark.parametrize("kind", list(EvidenceType))
def test_every_evidence_type_has_typed_model(kind):
    assert kind in EVIDENCE_MODELS

def test_numerical_result_requires_typed_fields():
    value={**_base(EvidenceType.NUMERICAL_RESULT),"summary_metrics":{"m":1.0},
           "material_snapshot":{},"numerical_method":"direct","convergence":{}}
    assert EVIDENCE_MODELS[EvidenceType.NUMERICAL_RESULT].model_validate(value).summary_metrics["m"] == 1.0
    with pytest.raises(Exception): EVIDENCE_MODELS[EvidenceType.NUMERICAL_RESULT].model_validate(_base(EvidenceType.NUMERICAL_RESULT))

def test_type_payload_mismatch_and_missing_status_are_rejected():
    with pytest.raises(Exception): EVIDENCE_MODELS[EvidenceType.BENCHMARK].model_validate(_base(EvidenceType.FIELD_RESULT))
    with pytest.raises(Exception): EVIDENCE_MODELS[EvidenceType.FIELD_RESULT].model_validate({"evidence_type":"field_result","variable_name":"T"})

@pytest.mark.parametrize(("kind", "payload"), [
    (EvidenceType.NUMERICAL_RESULT, {"summary_metrics": {}, "material_snapshot": {}, "numerical_method": "direct", "convergence": {}}),
    (EvidenceType.FIELD_RESULT, {"variable_name": "T", "unit": "K", "array_shape": [1], "checksum_sha256": "abc", "format": "json", "format_version": "1"}),
    (EvidenceType.VALIDITY, {"evaluated_inputs": {}, "rules": []}),
    (EvidenceType.BENCHMARK, {"benchmark_id": "b", "metric_name": "m", "computed_value": 1, "reference_value": 1, "absolute_error": 0, "relative_error": 0, "tolerance": .1, "passed": True, "source_simulation_id": "sim"}),
    (EvidenceType.RUN_CONVERGENCE, {"metric_type": "residual", "criterion": "bounded"}),
    (EvidenceType.REFINEMENT_CONVERGENCE, {"selected_metric": "m", "levels": [{"level": name, "simulation_id": f"sim-{name}"} for name in ("coarse", "medium", "fine")]}),
    (EvidenceType.ANALYSIS, {"analysis_id": "a", "dataset_hash": "hash"}),
])
def test_authoritative_statuses_reject_arbitrary_strings(kind, payload):
    with pytest.raises(Exception):
        EVIDENCE_MODELS[kind].model_validate({"evidence_type": kind.value, "status": "trusted", **payload})

def test_benchmark_status_must_match_passed_flag():
    payload = {
        "evidence_type": "benchmark", "status": "fail", "benchmark_id": "b", "metric_name": "m",
        "computed_value": 1, "reference_value": 1, "absolute_error": 0, "relative_error": 0,
        "tolerance": .1, "passed": True, "source_simulation_id": "sim",
    }
    with pytest.raises(Exception):
        EVIDENCE_MODELS[EvidenceType.BENCHMARK].model_validate(payload)
