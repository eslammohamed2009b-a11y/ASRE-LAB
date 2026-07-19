from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from supabase import create_client

from app.core.config import settings


class PersistenceService:
    def __init__(self) -> None:
        self.enabled = bool(settings.SUPABASE_URL and settings.SUPABASE_KEY)
        self.client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY) if self.enabled else None

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_experiment(self, owner_id: str, title: str, description: str | None = None) -> str | None:
        if not self.enabled:
            return None

        payload = {
            "owner_id": owner_id,
            "title": title,
            "description": description,
            "status": "running",
            "created_at": self._ts(),
            "updated_at": self._ts(),
        }
        data = self.client.table("experiments").insert(payload).execute().data
        return data[0]["id"] if data else None

    def store_design_model(self, experiment_id: str, variation_index: int, design: dict[str, Any]) -> str | None:
        if not self.enabled:
            return None

        params = design.get("params", {})
        payload = {
            "experiment_id": experiment_id,
            "variation_index": variation_index,
            "base": params.get("base_length_m"),
            "height": params.get("height_m"),
            "angle": params.get("slope_angle_deg"),
            "material": params.get("material"),
            "stl_path": design.get("stl_path"),
            "step_path": design.get("step_path"),
            "metadata": {
                "design_id": design.get("design_id"),
                "params": params,
            },
        }
        data = self.client.table("design_models").insert(payload).execute().data
        return data[0]["id"] if data else None

    def store_simulation_metrics(self, design_model_id: str, analysis_type: str, metrics: dict[str, Any]) -> None:
        if not self.enabled:
            return

        payload = {
            "design_model_id": design_model_id,
            "analysis_type": analysis_type,
            "max_temperature": metrics.get("max_temperature_c"),
            "max_stress": metrics.get("max_stress_mpa"),
            "avg_temperature": metrics.get("avg_temperature_c"),
            "drag_coefficient": metrics.get("drag_coefficient"),
            "raw_metrics": metrics,
        }
        self.client.table("simulation_metrics").insert(payload).execute()

    def finalize_experiment(self, experiment_id: str, status: str = "completed") -> None:
        if not self.enabled:
            return

        self.client.table("experiments").update(
            {"status": status, "updated_at": self._ts()}
        ).eq("id", experiment_id).execute()


persistence_service = PersistenceService()
