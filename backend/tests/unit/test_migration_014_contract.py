import ast
from pathlib import Path
import re

from app import study_router


ROOT = Path(__file__).resolve().parents[3]

EXPECTED_RECORD_TYPES = {
    "scientific_trust",
    "scientific_numerical_result",
    "scientific_validity",
    "scientific_benchmark",
    "scientific_run_convergence",
    "scientific_refinement_convergence",
    "scientific_field_result",
    "scientific_analysis",
    "run_manifest",
    "engineering_decision",
    "job_attempt",
    "reasoning_event",
    "research_report",
    "study_definition",
    "study_links",
    "study_doe",
    "study_next_experiment",
}


def _study_evidence_types_emitted_by_application() -> set[str]:
    source = Path(study_router.__file__).read_text()
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_study_evidence"
    ]
    return {
        f"study_{call.args[4].value}"
        for call in calls
        if len(call.args) >= 5 and isinstance(call.args[4], ast.Constant)
        and isinstance(call.args[4].value, str)
    }


def test_scientific_evidence_record_types_migration_is_additive_and_complete():
    authoritative = ROOT / "database/migrations/016_study_evidence_record_types.sql"
    mirrored = ROOT / "backend/supabase/migrations/20260925000000_study_evidence_record_types.sql"
    migration = authoritative.read_text()

    assert authoritative.read_bytes() == mirrored.read_bytes()
    assert set(re.findall(r"'([^']+)'", migration)) == EXPECTED_RECORD_TYPES
    assert _study_evidence_types_emitted_by_application() == {
        "study_definition", "study_links", "study_doe", "study_next_experiment",
    }
    assert _study_evidence_types_emitted_by_application() <= EXPECTED_RECORD_TYPES
    assert "drop table" not in migration.lower()
    assert "delete from" not in migration.lower()
