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
