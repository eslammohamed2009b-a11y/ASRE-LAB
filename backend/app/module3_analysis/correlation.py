"""
Module 3 — Correlation Matrix.
Identifies which geometric input parameters have the greatest
influence on simulation output metrics.
"""
import numpy as np
import pandas as pd


def build_correlation_matrix(design_results: list[dict]) -> dict:
    """
    design_results: [{"design_id", "params": {geometry params...}, "metrics": {...}}]
    Returns a full parameter x metric Pearson correlation matrix plus a
    ranked list of the strongest parameter->metric relationships.
    """
    if not design_results:
        return {"matrix": {}, "top_relationships": []}

    rows = []
    for d in design_results:
        row = {**d["params"], **d["metrics"]}
        rows.append(row)

    df = pd.DataFrame(rows).select_dtypes(include=[np.number])
    corr_matrix = df.corr(method="pearson").fillna(0.0)

    param_cols = [c for c in df.columns if c not in design_results[0]["metrics"]]
    metric_cols = list(design_results[0]["metrics"].keys())

    ranked_relationships = []
    for param in param_cols:
        for metric in metric_cols:
            if param in corr_matrix.index and metric in corr_matrix.columns:
                ranked_relationships.append(
                    {
                        "parameter": param,
                        "metric": metric,
                        "correlation": round(float(corr_matrix.loc[param, metric]), 3),
                    }
                )
    ranked_relationships.sort(key=lambda r: abs(r["correlation"]), reverse=True)

    return {
        "matrix": corr_matrix.round(3).to_dict(),
        "top_relationships": ranked_relationships[:10],
    }
