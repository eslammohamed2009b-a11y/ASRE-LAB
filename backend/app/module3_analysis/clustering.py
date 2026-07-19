"""
Module 3 — Pattern Recognition Engine (clustering).
Groups Design Variations with similar engineering performance using
K-Means over the normalized summary_metrics from Module 2.
"""
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler


def cluster_designs(design_results: list[dict], n_clusters: int = 4) -> dict:
    """
    design_results: [{"design_id": str, "metrics": {"max_temperature_c": ..., ...}}, ...]
    Returns cluster assignments plus per-cluster centroid metrics so the
    Unified Output Dashboard can label clusters (e.g. "high-stress / low-cost").
    """
    if not design_results:
        return {"assignments": {}, "clusters": {}, "metric_keys": []}

    design_ids = [d["design_id"] for d in design_results]
    metric_keys = sorted(design_results[0]["metrics"].keys())
    X = np.array([[d["metrics"][k] for k in metric_keys] for d in design_results])

    X_scaled = StandardScaler().fit_transform(X)
    k = min(n_clusters, len(design_results))
    model = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = model.fit_predict(X_scaled)

    clusters: dict[int, dict] = {}
    for cluster_id in range(k):
        members = [design_ids[i] for i, label in enumerate(labels) if label == cluster_id]
        member_metrics = X[[i for i, label in enumerate(labels) if label == cluster_id]]
        clusters[cluster_id] = {
            "design_ids": members,
            "centroid": {k_: float(v) for k_, v in zip(metric_keys, member_metrics.mean(axis=0))},
        }

    return {
        "assignments": dict(zip(design_ids, labels.tolist())),
        "clusters": clusters,
        "metric_keys": metric_keys,
    }
