from pathlib import Path


def test_scientific_evidence_record_types_migration_is_additive_and_complete():
    migration = Path("supabase/migrations/20260813000000_scientific_evidence_record_types.sql").read_text()
    for record_type in (
        "scientific_numerical_result", "scientific_field_result", "scientific_validity",
        "scientific_benchmark", "scientific_run_convergence",
        "scientific_refinement_convergence", "scientific_analysis",
    ):
        assert f"'{record_type}'" in migration
    assert "drop table" not in migration.lower()
    assert "delete from" not in migration.lower()
