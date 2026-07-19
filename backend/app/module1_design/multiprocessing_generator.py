"""
Module 1 — Parallel generation of Design Variations.
Uses independent CPU processes (not threads — CadQuery/OCCT geometry
kernels release the GIL poorly and are CPU-bound) so 100 variations
generate concurrently instead of sequentially.
"""
import random
from concurrent.futures import ProcessPoolExecutor, as_completed

from app.module1_design.cadquery_engine import generate_model
from app.module1_design.schemas import DesignParameters, DesignVariationRequest


def _build_variation_params(
    base: DesignParameters, vary_fields: list[str], range_pct: float
) -> DesignParameters:
    variation = base.model_copy(deep=True)
    for field_name in vary_fields:
        base_value = getattr(base, field_name)
        if base_value is None:
            continue
        delta = base_value * range_pct
        setattr(variation, field_name, round(random.uniform(base_value - delta, base_value + delta), 3))
    return variation


def _worker_generate(params: DesignParameters) -> dict:
    """Runs in a separate process; must be a top-level picklable function."""
    return generate_model(params)


def generate_design_matrix(request: DesignVariationRequest, max_workers: int = 8) -> list[dict]:
    """
    Generates `request.variation_count` designs in parallel via a
    ProcessPoolExecutor, returning the Design Matrix consumed by the
    Interactive 3D Workbench.
    """
    variation_params = [
        _build_variation_params(request.base_params, request.vary_fields, request.variation_range_pct)
        for _ in range(request.variation_count)
    ]

    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker_generate, params): params for params in variation_params}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"error": str(exc), "params": futures[future].model_dump()})

    return results
