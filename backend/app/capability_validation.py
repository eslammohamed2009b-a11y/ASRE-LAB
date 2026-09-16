"""Fail-fast validation of public scientific capability contracts."""
from __future__ import annotations

import importlib

from app.module1_design.capability_registry import DESIGN_CAPABILITY_REGISTRY
from app.module1_design.cadquery_engine import GEOMETRY_BUILDERS
from app.module1_design.schemas import DesignParameters
from app.module2_simulation.schemas import ImplementationStatus, ValidationStatus
from app.module2_simulation.solver_registry import SOLVER_REGISTRY
from app.module2_simulation.service import SOLVER_CLASSES
from app.module2_simulation.cad_fem_solvers import CAD_FEM_SOLVERS
from app.module2_simulation.solver_orchestrator import FIXED_CAD_CFD_ADAPTERS, FIXED_SOLVER_ADAPTERS
from app.module3_analysis.capability_registry import ANALYSIS_CAPABILITY_REGISTRY
from app.v2.scientific_trust import REGISTRY as TRUST_REGISTRY


def capability_consistency_errors() -> list[str]:
    errors: list[str] = []
    for geometry_id, item in DESIGN_CAPABILITY_REGISTRY.items():
        if item["implementation_status"] == "supported" and geometry_id not in {key.value for key in GEOMETRY_BUILDERS}:
            errors.append(f"supported geometry without CAD builder: {geometry_id}")
        if item["implementation_status"] == "supported":
            schema_fields = set(DesignParameters.model_fields)
            declared = set(item.get("supported_parameters", []))
            required = set(item.get("required_parameters", []))
            derived = set(item.get("derived_parameters", []))
            if not declared <= schema_fields or not required <= declared or not derived <= declared:
                errors.append(f"design capability parameters inconsistent with schema: {geometry_id}")
            if geometry_id == "tower" and "height_m" not in required:
                errors.append("tower capability must declare height_m as required")
    for solver_id, entry in SOLVER_REGISTRY.items():
        if not entry.solver_id or entry.solver_id != solver_id:
            errors.append(f"invalid solver id: {solver_id}")
        if entry.implementation_status == ImplementationStatus.REAL:
            if solver_id not in SOLVER_CLASSES and solver_id not in CAD_FEM_SOLVERS and solver_id not in FIXED_SOLVER_ADAPTERS and solver_id not in FIXED_CAD_CFD_ADAPTERS:
                errors.append(f"real solver missing implementation: {solver_id}")
            if not entry.version:
                errors.append(f"real solver missing version: {solver_id}")
            if not entry.governing_equations:
                errors.append(f"real solver missing governing equations: {solver_id}")
            for field_name in ("numerical_method", "discretization", "geometry_dependency_description",
                               "convergence_requirements", "implementation_reference"):
                if getattr(entry, field_name) in {"", "not_available", "not_applicable"}:
                    errors.append(f"real solver missing {field_name}: {solver_id}")
            if not entry.supported_geometry:
                errors.append(f"real solver missing supported geometry: {solver_id}")
            if not isinstance(entry.consumes_cad_geometry, bool):
                errors.append(f"real solver missing CAD consumption flag: {solver_id}")
            if not entry.validity_envelope:
                errors.append(f"real solver missing validity envelope: {solver_id}")
            if not entry.known_limitations:
                errors.append(f"real solver missing limitations: {solver_id}")
            try:
                module_name, object_name = entry.implementation_reference.rsplit(".", 1)
                getattr(importlib.import_module(module_name), object_name)
            except (ImportError, AttributeError, ValueError):
                errors.append(f"real solver implementation reference does not resolve: {solver_id}")
            if entry.validation_status == ValidationStatus.VALIDATED and not entry.benchmark_references:
                errors.append(f"validated solver missing benchmark metadata: {solver_id}")
            if solver_id not in {item.solver_id for item in TRUST_REGISTRY.list()}:
                errors.append(f"real solver missing scientific trust mapping: {solver_id}")
    for trust in TRUST_REGISTRY.list():
        if trust.solver_id not in SOLVER_REGISTRY and trust.solver_id != "thermal_structural_one_way_v1":
            errors.append(f"scientific trust references nonexistent solver/workflow: {trust.solver_id}")
        if trust.solver_id in SOLVER_REGISTRY:
            solver = SOLVER_REGISTRY[trust.solver_id]
            if not trust.benchmark_id or not trust.benchmark_title:
                errors.append(f"scientific trust benchmark is incomplete: {trust.solver_id}")
            if trust.solver_benchmark_reference not in solver.benchmark_references:
                errors.append(f"scientific trust benchmark lacks exact solver association: {trust.solver_id}")
    seen: set[str] = set()
    for method_id, item in ANALYSIS_CAPABILITY_REGISTRY.items():
        if method_id in seen or item.get("method_id") != method_id:
            errors.append(f"duplicate or invalid analysis capability id: {method_id}")
        seen.add(method_id)
        if not item.get("known_limitations"):
            errors.append(f"analysis method missing limitations: {method_id}")
        if not item.get("minimum_sample_rules") or not item.get("outputs"):
            errors.append(f"analysis capability malformed: {method_id}")
    return errors


def validate_capability_consistency() -> None:
    errors = capability_consistency_errors()
    if errors:
        raise RuntimeError("Capability consistency validation failed: " + "; ".join(errors))
