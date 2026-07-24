from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.core.repository import SupabaseRepository

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def test_ordered_migrations_and_supabase_mirrors_are_identical() -> None:
    authoritative = ROOT / "database" / "migrations"
    mirrored = ROOT / "backend" / "supabase" / "migrations"
    expected = [f"{index:03d}" for index in range(1, 11)]
    files = sorted(authoritative.glob("[0-9][0-9][0-9]_*.sql"))
    assert [path.name[:3] for path in files] == expected

    mirror_files = sorted(
        path for path in mirrored.glob("*.sql")
        if "trigger_initial_production_deploy" not in path.name
    )
    assert len(mirror_files) == len(files)
    for source, mirror in zip(files, mirror_files, strict=True):
        assert source.read_bytes() == mirror.read_bytes(), (source.name, mirror.name)


def test_migration_009_matches_analysis_and_provenance_adapters() -> None:
    sql = (ROOT / "database" / "migrations" / "009_experiment_analyses.sql").read_text()
    required_result_columns = {
        "status", "numerical_method", "residual_history", "validation_metadata",
        "elapsed_time_seconds", "reproducibility_hash", "source_design_id",
    }
    required_analysis_columns = {
        "analysis_type", "status", "dataset_hash", "configuration", "result", "warnings",
        "source_design_ids", "source_simulation_ids", "data_quality", "engine_version",
        "reproducibility_hash", "created_at", "updated_at",
    }
    for column in required_result_columns | required_analysis_columns:
        assert column in sql
    for token in (
        "create table if not exists public.experiment_analyses",
        "references public.experiments(id) on delete cascade",
        "references auth.users(id) on delete cascade",
        "create index if not exists idx_experiment_analyses_experiment",
        "create index if not exists idx_experiment_analyses_user",
        "enable row level security",
        "using (user_id = auth.uid()) with check (user_id = auth.uid())",
    ):
        assert token in sql.lower()

    source = inspect.getsource(SupabaseRepository)
    for column in required_result_columns | required_analysis_columns:
        assert f'"{column}"' in source
    assert 'table("experiment_analyses")' in source
    assert 'table("simulation_field_results")' in source


def test_migration_010_feedback_contract() -> None:
    sql = (ROOT / "database" / "migrations" / "010_design_feedback_iterations.sql").read_text().lower()
    for token in (
        "create table if not exists public.design_improvement_proposals",
        "references public.experiment_analyses(id) on delete cascade",
        "'generated','accepted','rejected','superseded','executed','failed'",
        "create table if not exists public.design_iterations",
        "proposal_id uuid not null unique",
        "parent_design_ids jsonb",
        "child_design_ids jsonb",
        "enable row level security",
        "using (user_id=auth.uid()) with check (user_id=auth.uid())",
    ):
        assert token in sql
