from typing import Any

from app.core.persistence import persistence_service
from app.module1_design.multiprocessing_generator import generate_design_matrix
from app.module1_design.nl_parser import parse_design_request
from app.module1_design.schemas import DesignVariationRequest
from app.module2_simulation.schemas import AnalysisType, SimulationRunRequest
from app.module2_simulation.service import run_simulation_service
from app.module2_simulation.solver_registry import UnsupportedAnalysisError
from app.module3_analysis.clustering import cluster_designs
from app.module3_analysis.correlation import build_correlation_matrix
from app.module3_analysis.synthesis import synthesize_report


def run_pipeline_flow(
    prompt: str,
    variation_count: int,
    analyses: list[AnalysisType],
    user_id: str,
) -> dict[str, Any]:
    base_params = parse_design_request(prompt)
    design_request = DesignVariationRequest(base_params=base_params, variation_count=variation_count)
    generated_designs = generate_design_matrix(design_request)

    experiment_id = persistence_service.create_experiment(
        owner_id=user_id,
        title=f"Pipeline run - {base_params.geometry_type.value}",
        description=prompt,
    )

    design_results: list[dict[str, Any]] = []
    skipped_analyses: set[str] = set()
    for idx, design in enumerate(generated_designs):
        if "error" in design:
            continue

        design_model_id = None
        if experiment_id:
            design_model_id = persistence_service.store_design_model(
                experiment_id=experiment_id,
                variation_index=idx,
                design=design,
            )

        merged_metrics: dict[str, float] = {}
        for analysis in analyses:
            try:
                sim_result = run_simulation_service(
                    SimulationRunRequest(
                        design_id=design["design_id"],
                        geometry_type=base_params.geometry_type.value,
                        analysis_type=analysis,
                        material=(base_params.material.value if base_params.material else "concrete"),
                    )
                )
            except UnsupportedAnalysisError:
                # Do not fabricate a result for an analysis with no validated solver
                # (see app.module2_simulation.solver_registry). Skip it and report the
                # gap honestly in the pipeline response instead of silently pretending
                # it ran or crashing the whole pipeline for the other, supported analyses.
                skipped_analyses.add(analysis.value)
                continue
            merged_metrics.update(sim_result.summary_metrics)
            if design_model_id:
                persistence_service.store_simulation_metrics(
                    design_model_id=design_model_id,
                    analysis_type=sim_result.analysis_type,
                    metrics=sim_result.summary_metrics,
                )

        design_results.append(
            {
                "design_id": design["design_id"],
                "params": design["params"],
                "metrics": merged_metrics,
            }
        )

    cluster_output = cluster_designs(design_results, n_clusters=4)
    correlation_output = build_correlation_matrix(design_results)
    insights = synthesize_report(cluster_output, correlation_output)

    if experiment_id:
        persistence_service.finalize_experiment(experiment_id)

    return {
        "experiment_id": experiment_id,
        "base_params": base_params.model_dump(),
        "generated_count": len(generated_designs),
        "analyzed_count": len(design_results),
        "clusters": cluster_output,
        "correlation": correlation_output,
        "insights": insights,
        "persistence_enabled": persistence_service.enabled,
        "skipped_analyses": sorted(skipped_analyses),
    }
