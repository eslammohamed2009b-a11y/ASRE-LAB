from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable

from app.module2_simulation.solver_registry import SOLVER_REGISTRY

LEVELS = ("high", "moderate", "low", "invalid")


@dataclass(frozen=True)
class TrustCapability:
    solver_id: str
    physical_model: str
    assumptions: tuple[str, ...]
    units: dict[str, str]
    limits: dict[str, tuple[float, float]]
    benchmark_id: str
    benchmark_title: str
    benchmark_metric: str
    benchmark_tolerance: float
    benchmark_formula: Callable[[dict[str, float]], float]
    convergence_applicable: bool = True
    solver_benchmark_reference: str = ""


def _thermal(x): return x["cold_c"] + (x["hot_c"] - x["cold_c"]) * x.get("position_fraction", .5)
def _pyramid_constant_temperature(x): return x["boundary_temperature_c"]
def _structural(x): return x["load_n"] * x["length_m"] / (x["youngs_modulus_pa"] * x["area_m2"])
def _modal(x): return math.sqrt(x["stiffness_n_m"] / x["mass_kg"]) / (2 * math.pi)
def _acoustic(x): return x.get("speed_m_s", 343.0) / (2 * x["length_m"])
def _electrostatic(x): return abs(x["right_v"] - x["left_v"]) / x["width_m"]
def _channel(x): return -(x["pressure_gradient_pa_m"]) * x["height_m"] ** 2 / (8 * x["viscosity_pa_s"])
def _coupling(x): return x["youngs_modulus_pa"] * x["alpha_1_k"] * x["delta_temperature_k"]


_COMMON_ASSUMPTIONS = {
    "pyramid_thermal_conduction_v1": (
        "steady state", "constant isotropic conductivity", "solid square-pyramid Cartesian mask",
        "isothermal base and staircase sides/apex",
    ),
    "thermal_conduction_v1": ("steady state", "constant isotropic conductivity", "bounded uniform discretization"),
    "structural_linear_1d_v1": ("small deformation", "linear elastic homogeneous material", "straight prismatic 1D member"),
    "modal_eigen_1d_v1": ("undamped free vibration", "linear system", "1D SDOF or Euler-Bernoulli beam"),
    "acoustic_duct_1d_v1": ("lossless plane wave", "uniform straight duct", "linear acoustics"),
    "electrostatic_rectangular_2d_v1": ("static field", "constant permittivity", "rectangular uniform grid"),
    "cfd_laminar_channel_2d_v1": ("steady incompressible laminar flow", "fully developed parallel-plate flow", "Newtonian fluid"),
}

_DEFINITIONS = [
    TrustCapability(
        "pyramid_thermal_conduction_v1", "Masked-grid steady heat conduction in a square pyramid",
        _COMMON_ASSUMPTIONS["pyramid_thermal_conduction_v1"],
        {"base_length":"m","height":"m","temperature":"degC","conductivity":"W/(m K)"},
        {"base_length_m":(1e-3,1e3),"height_m":(1e-3,1e3),"grid_resolution":(9,41)},
        "pyramid_constant_dirichlet", "Zero-source equal-boundary analytical constant solution",
        "max_temperature_c", 1e-10, _pyramid_constant_temperature,
    ),
    TrustCapability("thermal_conduction_v1","Steady heat conduction",_COMMON_ASSUMPTIONS["thermal_conduction_v1"],
        {"length":"m","temperature":"degC","conductivity":"W/(m K)"},{"length_m":(1e-6,1e3),"num_elements":(2,500)},
        "thermal_linear_1d","Analytical 1D linear temperature profile","temperature_c",1e-6,_thermal),
    TrustCapability("structural_linear_1d_v1","Linear-elastic axial bar or Euler-Bernoulli beam",_COMMON_ASSUMPTIONS["structural_linear_1d_v1"],
        {"length":"m","load":"N","displacement":"m","stress":"Pa"},{"length_m":(1e-6,1e3),"num_elements":(1,500)},
        "structural_axial_bar","Analytical axial-bar displacement","displacement_m",1e-8,_structural),
    TrustCapability("modal_eigen_1d_v1","Undamped 1D eigenvalue analysis",_COMMON_ASSUMPTIONS["modal_eigen_1d_v1"],
        {"frequency":"Hz","mass":"kg","stiffness":"N/m"},{"num_elements":(1,200)},
        "modal_sdof","Analytical SDOF natural frequency","frequency_hz",1e-10,_modal),
    TrustCapability("acoustic_duct_1d_v1","One-dimensional Helmholtz duct model",_COMMON_ASSUMPTIONS["acoustic_duct_1d_v1"],
        {"length":"m","frequency":"Hz","pressure":"Pa"},{"length_m":(1e-6,1e3),"num_elements":(4,500)},
        "acoustic_half_wave","Analytical open-open duct fundamental","frequency_hz",1e-8,_acoustic),
    TrustCapability("electrostatic_rectangular_2d_v1","Two-dimensional electrostatic Poisson model",_COMMON_ASSUMPTIONS["electrostatic_rectangular_2d_v1"],
        {"width":"m","potential":"V","electric_field":"V/m"},{"grid_size":(5,60)},
        "electrostatic_parallel_plate","Analytical uniform electric field","electric_field_v_m",1e-6,_electrostatic),
    TrustCapability("cfd_laminar_channel_2d_v1","Fully developed plane-Poiseuille flow",_COMMON_ASSUMPTIONS["cfd_laminar_channel_2d_v1"],
        {"height":"m","velocity":"m/s","pressure_gradient":"Pa/m"},{"grid_size":(5,60),"reynolds_number":(0,2000)},
        "channel_poiseuille","Analytical channel maximum velocity","maximum_velocity_m_s",2e-2,_channel),
    TrustCapability("thermal_structural_one_way_v1","Sequential steady thermal-to-linear-structural coupling",
        ("one-way coupling only","mean nodal temperature mapping","fully restrained linear thermal strain"),
        {"temperature":"degC","stress":"Pa"},{"num_elements":(2,200)},
        "coupling_restrained_expansion","Analytical restrained thermal stress","thermal_stress_pa",1e-8,_coupling,False),
]

# Exact, machine-verifiable association to the solver registry evidence.  The
# trust benchmark remains its own bounded analytical check; this field links it
# to the specific declared implementation benchmark without fuzzy matching.
_SOLVER_BENCHMARK_ASSOCIATIONS = {
    "pyramid_thermal_conduction_v1": "tests/unit/test_pyramid_thermal_solver.py::test_zero_source_equal_boundaries_matches_constant_analytical_solution",
    "thermal_conduction_v1": "tests/integration/test_thermal_solver_v2_benchmark.py::test_1d_slab_matches_linear_analytical_profile (1d Dirichlet-Dirichlet analytical linear profile)",
    "structural_linear_1d_v1": "tests/integration/test_structural_solver_benchmark.py::test_axial_bar_matches_analytical_solution",
    "modal_eigen_1d_v1": "tests/integration/test_modal_solver_benchmark.py::test_sdof_matches_analytical_frequency",
    "acoustic_duct_1d_v1": "tests/integration/test_acoustic_solver.py (analytical sine-profile benchmark)",
    "electrostatic_rectangular_2d_v1": "tests/integration/test_electrostatic_solver.py (parallel-plate linear-potential benchmark)",
    "cfd_laminar_channel_2d_v1": "tests/integration/test_channel_flow_solver.py (analytical plane-Poiseuille profile and refinement)",
}


class TrustRegistry:
    def __init__(self):
        self._items: dict[str, TrustCapability] = {}
        for item in _DEFINITIONS:
            reference = _SOLVER_BENCHMARK_ASSOCIATIONS.get(item.solver_id, "")
            self.register(replace(item, solver_benchmark_reference=reference))
    def register(self, item: TrustCapability):
        if item.solver_id in self._items: raise ValueError(f"Duplicate scientific capability: {item.solver_id}")
        self._items[item.solver_id] = item
    def get(self, solver_id: str) -> TrustCapability:
        if solver_id not in self._items: raise KeyError(solver_id)
        return self._items[solver_id]
    def list(self): return list(self._items.values())


REGISTRY = TrustRegistry()


def metadata(item: TrustCapability) -> dict[str, Any]:
    base = SOLVER_REGISTRY.get(item.solver_id)
    limitations = list(base.known_limitations) if base else [
        "One-way sequential coupling only; temperature does not respond to structural deformation."
    ]
    return {"solver_id":item.solver_id,"implementation_version":base.version if base else "1.0.0",
            "physical_model":item.physical_model,"assumptions":list(item.assumptions),"supported_units":item.units,
            "governing_equations":list(base.governing_equations) if base else ["sigma = E * alpha * delta_T"],
            "required_inputs":list(base.required_inputs) if base else ["length_m","cross_section_area_m2","temperature range","restraint"],
            "supported_boundary_conditions":list(base.supported_boundary_conditions) if base else ["one-way mean-temperature mapping","fully restrained thermal strain"],
            "geometry_limitations":base.geometry_limitations if base else "Uniform compatible 1D thermal and structural discretizations only.",
            "validity_limits":{k:{"minimum":v[0],"maximum":v[1]} for k,v in item.limits.items()},
            "benchmark":{"id":item.benchmark_id,"title":item.benchmark_title,"metric":item.benchmark_metric,
                         "relative_tolerance":item.benchmark_tolerance},
            "convergence_applicable":item.convergence_applicable,"limitations":limitations}


def validate(item: TrustCapability, inputs: dict[str, Any]) -> dict[str, Any]:
    findings=[]
    for name,(low,high) in item.limits.items():
        value=inputs.get(name)
        if value is None:
            findings.append({"code":"MISSING_REQUIRED_INPUT","severity":"error","affected_input":name,
                "message":f"{name} is required for validity assessment.","technical_detail":f"Expected [{low}, {high}].",
                "suggested_correction":f"Provide {name} within the supported range.","evidence_reference":"normalized_inputs"})
        elif not low <= float(value) <= high:
            findings.append({"code":"OUTSIDE_VALIDITY_ENVELOPE","severity":"error","affected_input":name,
                "message":f"{name} is outside the supported range.","technical_detail":f"{value} not in [{low}, {high}].",
                "suggested_correction":f"Use a value from {low} through {high}.","evidence_reference":"normalized_inputs"})
        elif math.isclose(float(value), low) or math.isclose(float(value), high):
            findings.append({"code":"NEAR_VALIDITY_BOUNDARY","severity":"warning","affected_input":name,
                "message":f"{name} is near a validity boundary.","technical_detail":f"Value {value}; supported [{low}, {high}].",
                "suggested_correction":"Review sensitivity before relying on this result.","evidence_reference":"normalized_inputs"})
    status="invalid" if any(x["severity"]=="error" for x in findings) else ("valid_with_warnings" if findings else "valid")
    return {"status":status,"rules":findings,"normalized_inputs":inputs}


def benchmark(item: TrustCapability, inputs: dict[str,float], computed: float|None=None,
              source_simulation_id: str | None = None) -> dict[str,Any]:
    if computed is None:
        raise ValueError("Benchmark evaluation requires an actual computed result; use reference_only for reference calculations.")
    if not source_simulation_id:
        raise ValueError("Benchmark evidence requires source_simulation_id from a real computation.")
    reference=float(item.benchmark_formula(inputs)); actual=float(computed)
    absolute=abs(actual-reference); relative=absolute/max(abs(reference),1e-15)
    return {"benchmark_id":item.benchmark_id,"solver_id":item.solver_id,"title":item.benchmark_title,
        "reference_type":"documented analytical formula","physical_assumptions":list(item.assumptions),
        "reference_inputs":inputs,"reference_result":reference,"computed_result":actual,
        "selected_metric":item.benchmark_metric,"absolute_error":absolute,"relative_error":relative,
        "declared_tolerance":item.benchmark_tolerance,"passed":relative<=item.benchmark_tolerance,
        "source_simulation_id":source_simulation_id,"created_from_real_computation":True,
        "limitations":["Valid only for the stated bounded analytical case."],"evidence_links":["solver_metadata",source_simulation_id]}


def reference_only(item: TrustCapability, inputs: dict[str, float]) -> dict[str, Any]:
    """Return a reference value without making a scientific validation claim."""
    return {"benchmark_id": item.benchmark_id, "solver_id": item.solver_id,
            "reference_result": float(item.benchmark_formula(inputs)), "reference_inputs": inputs,
            "status": "reference_only", "passed": None, "created_from_real_computation": False}


def convergence(item: TrustCapability, values: list[float], configurations: list[dict]|None=None, threshold=.02):
    if not item.convergence_applicable:
        return {"applicable":False,"status":"not_applicable","reason":"No independent resolution refinement is meaningful for this sequential consistency check.","warnings":[]}
    if len(values)!=3: raise ValueError("Exactly coarse, medium, and fine values are required")
    changes=[abs(values[i]-values[i-1])/max(abs(values[i]),1e-15) for i in (1,2)]
    ok=changes[1] <= threshold and changes[1] <= changes[0]
    return {"applicable":True,"status":"converged" if ok else "not_converged","selected_metric":item.benchmark_metric,
        "levels":[{"level":n,"configuration":(configurations or [{},{},{}])[i],"value":values[i],
                   "relative_change":None if i==0 else changes[i-1],"completion_state":"completed","warnings":[]}
                  for i,n in enumerate(("coarse","medium","fine"))],
        "coarse_to_medium_change":changes[0],"medium_to_fine_change":changes[1],"convergence_threshold":threshold,
        "converged":ok,"recommended_level":"medium" if ok else "fine","estimated_numerical_variation":changes[1],
        "warnings":[] if ok else [{"code":"POOR_CONVERGENCE","severity":"warning"}],"evidence_references":["convergence_levels"]}


def confidence(validity,bench,study,warnings=()):
    reasons=[]; blocking=[]; warning_codes=[w.get("code",str(w)) if isinstance(w,dict) else str(w) for w in warnings]
    if validity["status"]=="invalid": level="invalid";reasons.append("INVALID_INPUT");blocking.extend(r["code"] for r in validity["rules"] if r["severity"]=="error")
    elif not bench: level="low";reasons.append("MISSING_BENCHMARK_EVIDENCE")
    elif not bench["passed"]: level="low";reasons.append("BENCHMARK_TOLERANCE_EXCEEDED")
    elif study.get("applicable") and not study.get("converged"): level="low";reasons.append("POOR_CONVERGENCE")
    elif validity["status"]=="valid_with_warnings" or not study.get("applicable"): level="moderate";reasons.append("BOUNDED_WARNING_OR_NON_APPLICABLE_CONVERGENCE")
    elif warning_codes: level="moderate";reasons.append("SCIENTIFIC_WARNINGS_PRESENT")
    else: level="high";reasons.append("VALID_BENCHMARKED_AND_CONVERGED")
    return {"level":level,"reason_codes":reasons,"contributing_evidence_ids":["validity","benchmark","convergence"],
        "blocking_issues":blocking,"warning_issues":warning_codes,
        "explanation":{"high":"Inputs are valid and benchmark and convergence evidence pass.",
        "moderate":"Evidence supports bounded use with stated warnings or limitations.",
        "low":"Evidence is weak or a numerical check failed.","invalid":"Inputs are outside the supported validity envelope."}[level]}
