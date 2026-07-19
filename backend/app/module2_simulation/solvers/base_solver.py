"""
Module 2 — Common FEA solver interface.
Only governing equations and numerical setup differ between analyses.
"""
from abc import ABC, abstractmethod

from pydantic import BaseModel


class Mesh(BaseModel):
    """Minimal FE mesh representation (node coords + element connectivity)."""

    nodes: list[tuple[float, float, float]]
    elements: list[tuple[int, ...]]


class SolverResult(BaseModel):
    analysis_type: str
    design_id: str
    summary_metrics: dict[str, float]
    field_values: list[float]
    hotspot_node_ids: list[int]


class BaseSolver(ABC):
    analysis_type: str = "base"

    @abstractmethod
    def solve(self, mesh: Mesh, material: str, boundary_conditions: dict) -> SolverResult:
        ...
