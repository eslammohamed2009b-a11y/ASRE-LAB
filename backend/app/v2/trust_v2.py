"""ScientificTrustRecord V2 derived only from persisted scientific evidence."""
from __future__ import annotations

import hashlib
import json

from app.module2_simulation.source_resolution import resolve_simulation_source
from app.v2.evidence_integrity import records_by_type, validate_scientific_record
from app.v2.evidence_models import EvidenceType
from app.v2.repository import EvidenceRepository
from app.v2.scientific_trust import compatible_benchmarks

TRUST_VERSION = "2.0"


def _latest(items):
    return sorted(items, key=lambda item: (item[0].get("created_at", ""), item[0]["id"]))[-1] if items else None


def _dimension(state: str, item=None, *, warning: str | None = None) -> dict:
    result = {"state": state, "evidence_ids": [item[0]["id"]] if item else []}
    if warning:
        result["warning"] = warning
    return result


def derive_trust_record(user_id: str, simulation_id: str, *, repository=None) -> dict:
    evidence = EvidenceRepository(repository=repository)
    source = resolve_simulation_source(
        simulation_id, user_id, require_completed_result=True, repository=repository,
    )
    grouped = records_by_type(evidence, user_id, source)

    validity_item = _latest(grouped.get(EvidenceType.VALIDITY, []))
    definitions = compatible_benchmarks(source.solver_id)
    benchmark_candidates = [item for item in grouped.get(EvidenceType.BENCHMARK, []) if (
        item[1].benchmark_id in definitions
        and definitions[item[1].benchmark_id] == (item[1].metric_name, item[1].tolerance)
    )]
    benchmark_item = _latest(benchmark_candidates)
    convergence_item = _latest(grouped.get(EvidenceType.RUN_CONVERGENCE, []))
    refinement_item = _latest(grouped.get(EvidenceType.REFINEMENT_CONVERGENCE, []))

    if validity_item:
        validity_state = {"valid": "PASS", "valid_with_warnings": "WARNING", "invalid": "FAIL"}[
            validity_item[1].status.value
        ]
    else:
        validity_state = "FAIL"
    validity = _dimension(
        validity_state, validity_item,
        warning="Authoritative validity evidence is missing." if not validity_item else None,
    )

    if benchmark_candidates:
        benchmark_state = "FAIL" if any(item[1].status.value == "fail" for item in benchmark_candidates) else "PASS"
        benchmark_dimension = _dimension(
            benchmark_state, benchmark_item,
            warning="Benchmark evidence carries a warning state." if benchmark_item[1].status.value == "warning" else None,
        )
    else:
        benchmark_dimension = _dimension("NOT_RUN")

    if convergence_item:
        convergence_state = {
            "completed": "PASS", "not_converged": "FAIL",
            "not_run": "NOT_RUN", "not_applicable": "NOT_APPLICABLE",
        }[convergence_item[1].status.value]
        run_convergence = _dimension(convergence_state, convergence_item)
    else:
        run_convergence = _dimension("NOT_RUN")

    if refinement_item:
        refinement_state = "PASS" if refinement_item[1].status.value == "completed" else "FAIL"
        refinement = _dimension(refinement_state, refinement_item)
    else:
        refinement = _dimension("NOT_RUN")

    dimensions = {
        "validity": validity, "benchmark": benchmark_dimension,
        "run_convergence": run_convergence, "refinement": refinement,
    }
    required = ("validity", "benchmark", "run_convergence")
    if validity_item and validity_state == "FAIL":
        overall, reason = "INVALID", "VALIDITY_FAILED_OR_MISSING"
    elif not validity_item:
        overall, reason = "LOW", "VALIDITY_EVIDENCE_NOT_RUN"
    elif any(dimensions[name]["state"] == "FAIL" for name in required):
        overall, reason = "LOW", "REQUIRED_EVIDENCE_FAILED"
    elif any(dimensions[name]["state"] == "NOT_RUN" for name in required):
        overall, reason = "LOW", "REQUIRED_EVIDENCE_NOT_RUN"
    elif refinement["state"] == "FAIL":
        overall, reason = "LOW", "REFINEMENT_FAILED"
    elif validity_state == "WARNING" or refinement["state"] == "NOT_RUN":
        overall, reason = "MODERATE", "BOUNDED_WARNING_OR_REFINEMENT_NOT_RUN"
    else:
        overall, reason = "HIGH", "ALL_REQUIRED_EVIDENCE_SATISFIED"

    evidence_ids = sorted({item for value in dimensions.values() for item in value["evidence_ids"]})
    for evidence_id in evidence_ids:
        record = evidence.get(evidence_id, user_id)
        if record is None:
            raise ValueError("Trust evidence reference is unavailable")
        validate_scientific_record(record)
    payload = {
        "trust_version": TRUST_VERSION,
        "simulation_id": simulation_id,
        "experiment_id": source.experiment_id,
        "design_id": source.design_id,
        "solver_id": source.solver_id,
        "solver_version": source.solver_version,
        "input_fingerprint": source.result.validation_metadata.get("input_fingerprint"),
        "result_hash": source.result.reproducibility_hash,
        "dimensions": dimensions,
        "overall_trust": overall,
        "reason_code": reason,
        "evidence_ids": evidence_ids,
        "decision_rules": {
            "invalid": "failed or missing authoritative validity evidence",
            "low": "failed or missing required benchmark/run-convergence evidence, or failed refinement",
            "moderate": "required evidence satisfied with validity warning or no refinement study",
            "high": "validity, benchmark, and run convergence satisfied; any present refinement also passed",
        },
        "limitations": ["Trust is an evidence-state classification, not a probability or accuracy percentage."],
    }
    payload["trust_hash"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    return evidence.create(user_id, {
        "record_type": "scientific_trust", "status": overall.lower(),
        "experiment_id": source.experiment_id, "simulation_id": simulation_id,
        "parent_record_id": None, "payload": payload,
    })
