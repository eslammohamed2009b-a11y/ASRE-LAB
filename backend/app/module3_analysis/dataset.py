"""Deterministic, evidence-linked datasets built only from persisted records."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from app.core.repository import PersistenceRepository
from app.module3_analysis.schemas import DatasetQualityReport, DatasetRow, ExperimentDataset

MAX_ROWS = 5000
MAX_COLUMNS = 256
VALID_RESULT_STATUSES = {"completed", "partial_failure"}


class DatasetBuildError(ValueError):
    pass


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _metric_unit(name: str) -> str:
    """Best-effort unit from an explicit metric suffix; never invent units."""
    suffixes = {
        "_c": "degC", "_k": "K", "_pa": "Pa", "_hz": "Hz", "_m": "m",
        "_m2": "m^2", "_m3": "m^3", "_n": "N", "_kg": "kg", "_s": "s",
        "_w": "W", "_w_m2": "W/m^2", "_w_m3": "W/m^3", "_m_s": "m/s",
    }
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.lower().endswith(suffix):
            return suffixes[suffix]
    return "unspecified"


def _add_numeric(
    target: dict[str, float], units_seen: dict[str, set[str]], non_numeric: set[str],
    prefix: str, values: dict, declared_units: dict | None = None,
) -> None:
    declared_units = declared_units or {}
    for name in sorted(values):
        column = f"{prefix}.{name}"
        number = _number(values[name])
        if number is None:
            non_numeric.add(column)
            continue
        target[column] = number
        unit = str(declared_units.get(name) or (_metric_unit(name) if prefix == "metric" else "unspecified"))
        units_seen[column].add(unit)


def build_experiment_dataset(
    repository: PersistenceRepository,
    experiment_id: str,
    user_id: str,
    *,
    include_nonconverged: bool = False,
) -> ExperimentDataset:
    experiment = repository.get_experiment(experiment_id)
    if experiment is None or experiment.user_id != user_id:
        raise DatasetBuildError("Experiment not found")

    designs = {item.id: item for item in repository.list_design_models_for_experiment(experiment_id)}
    jobs = repository.list_simulation_jobs_for_experiment(experiment_id)
    if len(jobs) > MAX_ROWS:
        raise DatasetBuildError(f"Experiment exceeds the {MAX_ROWS}-simulation analysis limit")

    rows: list[DatasetRow] = []
    excluded = 0
    non_numeric: set[str] = set()
    units_seen: dict[str, set[str]] = defaultdict(set)
    seen_simulations: set[str] = set()
    duplicates: list[str] = []
    warnings: list[str] = []

    for job in sorted(jobs, key=lambda item: (item.created_at, item.id)):
        if job.id in seen_simulations:
            duplicates.append(job.id)
            excluded += 1
            continue
        seen_simulations.add(job.id)
        result = repository.get_simulation_result(job.id)
        simulation_input = repository.get_simulation_input(job.id)
        if job.status not in VALID_RESULT_STATUSES or result is None or simulation_input is None:
            excluded += 1
            continue
        if not result.converged and not include_nonconverged:
            excluded += 1
            continue

        values: dict[str, float] = {}
        design = designs.get(job.design_id or "")
        if design is not None:
            _add_numeric(values, units_seen, non_numeric, "design", design.parameters, design.units)
        _add_numeric(
            values, units_seen, non_numeric, "material",
            simulation_input.material_properties, simulation_input.units,
        )
        _add_numeric(values, units_seen, non_numeric, "solver_input", simulation_input.numerical_settings)
        _add_numeric(values, units_seen, non_numeric, "boundary", simulation_input.boundary_conditions)
        _add_numeric(values, units_seen, non_numeric, "metric", result.summary_metrics)
        values["quality.converged"] = 1.0 if result.converged else 0.0
        units_seen["quality.converged"].add("dimensionless")
        values["quality.warning_count"] = float(len(result.warnings))
        values["quality.iteration_count"] = float(result.iteration_count)
        units_seen["quality.warning_count"].add("count")
        units_seen["quality.iteration_count"].add("count")
        if result.residual is not None and math.isfinite(result.residual):
            values["quality.residual"] = float(result.residual)
            units_seen["quality.residual"].add("solver_defined")

        field_records = sorted(
            repository.list_field_results(job.id), key=lambda item: (item.variable_name, item.id)
        )
        variable_counts: dict[str, int] = defaultdict(int)
        for field_result in field_records:
            variable_counts[field_result.variable_name] += 1
            suffix = (
                "" if variable_counts[field_result.variable_name] == 1
                else f"_{variable_counts[field_result.variable_name]}"
            )
            base = f"field.{field_result.variable_name}{suffix}"
            for statistic, value in (
                ("minimum", field_result.minimum), ("maximum", field_result.maximum),
                ("mean", field_result.mean),
            ):
                column = f"{base}.{statistic}"
                values[column] = float(value)
                units_seen[column].add(field_result.unit)

        rows.append(DatasetRow(
            design_id=job.design_id,
            simulation_id=job.id,
            solver_id=job.solver_id,
            solver_version=result.solver_version,
            values=dict(sorted(values.items())),
            converged=result.converged,
            simulation_status=job.status,
            evidence_ids=(
                [job.id] + ([job.design_id] if job.design_id else [])
                + [field_result.id for field_result in field_records]
            ),
        ))

    all_columns = sorted({name for row in rows for name in row.values})
    if len(all_columns) > MAX_COLUMNS:
        raise DatasetBuildError(f"Dataset exceeds the {MAX_COLUMNS}-column analysis limit")

    missing = {
        column: sum(1 for row in rows if column not in row.values)
        for column in all_columns
        if any(column not in row.values for row in rows)
    }
    constants = [
        column for column in all_columns
        if len({row.values[column] for row in rows if column in row.values}) <= 1
    ]
    incompatible = {
        column: sorted(values) for column, values in units_seen.items() if len(values) > 1
    }
    if incompatible:
        warnings.append("Columns with incompatible units must be excluded from numerical analysis.")
    if len(rows) < 3:
        warnings.append("Fewer than three valid simulations: inferential analysis is not reliable.")
    unspecified = sorted(column for column in all_columns if units_seen.get(column) == {"unspecified"})
    if unspecified:
        warnings.append("Some persisted values have no explicit unit; they remain labelled 'unspecified'.")
    if not rows:
        warnings.append("No valid persisted simulation results matched the dataset policy.")

    units = {
        column: next(iter(units_seen[column])) if len(units_seen[column]) == 1 else "incompatible"
        for column in all_columns
    }
    canonical = {
        "experiment_id": experiment_id,
        "version": "1.0",
        "rows": [row.model_dump() for row in rows],
        "columns": all_columns,
        "units": units,
    }
    dataset_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()

    return ExperimentDataset(
        experiment_id=experiment_id,
        rows=rows,
        columns=all_columns,
        units=units,
        quality=DatasetQualityReport(
            source_simulation_count=len(jobs), valid_row_count=len(rows), excluded_row_count=excluded,
            duplicate_simulation_ids=sorted(duplicates), missing_value_counts=missing,
            constant_columns=constants, non_numeric_fields=sorted(non_numeric),
            incompatible_units=incompatible, warnings=warnings,
        ),
        dataset_hash=dataset_hash,
    )
