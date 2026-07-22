from __future__ import annotations

import pytest

from app.core.repository import LocalSQLiteRepository
from app.core.storage import LocalFileStorage
from app.module2_simulation.coupling import CouplingNotFoundError, ThermalStructuralCouplingRequest, get_coupling, run_thermal_structural_coupling
from app.module2_simulation.materials import properties_as_dict
from app.module2_simulation.tasks import run_simulation_job
from app.module3_analysis.feedback import FeedbackNotFoundError, ProposalRequest, execute_proposal, generate_proposal, get_proposal, transition_proposal
from app.module3_analysis.schemas import AnalysisCreateRequest
from app.module3_analysis.service import run_experiment_analysis

pytestmark=pytest.mark.e2e

def _run(repo,storage,user,experiment,design,solver,material,geometry,boundary):
    sid=repo.create_simulation_job(user_id=user,solver_id=solver,experiment_id=experiment,design_id=design)
    repo.record_simulation_input(sid,material,properties_as_dict(material),{}, {},boundary,{"max_iterations":3000,"tolerance":1e-7})
    outcome=run_simulation_job(simulation_id=sid,solver_id=solver,material_name=material,geometry=geometry,
        boundary_conditions=boundary,initial_conditions={},numerical_settings={"max_iterations":3000,"tolerance":1e-7},
        experiment_id=experiment,design_id=design,repository=repo,storage=storage)
    assert outcome["status"]=="completed"; return sid

def test_complete_backend_vision_is_durable_and_owner_scoped(tmp_path):
    db=tmp_path/"complete.db"; repo=LocalSQLiteRepository(db); storage=LocalFileStorage(tmp_path/"objects"); user="owner-a"
    experiment=repo.create_experiment(user,"complete deterministic workflow")
    designs=[]
    for index,height in enumerate((18.0,20.0,22.0)):
        designs.append(repo.create_design_model(experiment,user,"tower",{"geometry_type":"tower","base_length_m":10.0,
            "height_m":height,"wall_thickness_m":0.5,"slope_angle_deg":0.0,"material":"steel"},{"height_m":"m"},index,"completed"))
    first=designs[0]
    simulation_ids=[
        _run(repo,storage,user,experiment,first,"thermal_conduction_v1","steel",{"dimension":"1d","length_m":1,"num_elements":10},{"ambient_temperature_c":20,"prescribed_temperature_c":80}),
        _run(repo,storage,user,experiment,first,"structural_linear_1d_v1","steel",{"dimension":"1d","length_m":1,"cross_section_area_m2":.01,"num_elements":10},{"axial_load_n":1000}),
        _run(repo,storage,user,experiment,first,"modal_eigen_1d_v1","steel",{"dimension":"1d"},{"point_mass_kg":2,"spring_stiffness_n_m":200}),
        _run(repo,storage,user,experiment,first,"acoustic_duct_1d_v1","air",{"dimension":"1d","length_m":1,"num_elements":40},{"source_frequency_hz":80,"source_pressure_pa":1,"acoustic_left_boundary":"driven","acoustic_right_boundary":"pressure_release"}),
    ]
    assert sum(len(repo.list_field_results(sid)) for sid in simulation_ids)>=6
    coupling=run_thermal_structural_coupling(ThermalStructuralCouplingRequest(experiment_id=experiment,design_id=first,
        material="steel",length_m=1,cross_section_area_m2=.01,hot_end_temperature_c=120),user,repo,storage)
    assert coupling.status=="completed"
    analysis=run_experiment_analysis(experiment,user,AnalysisCreateRequest(),repo)
    proposal=generate_proposal(ProposalRequest(analysis_id=analysis.id,source_design_id=first,parameter_bounds={"height_m":(15,25)}),user,repo)
    transition_proposal(proposal.id,user,"accepted",repo)
    iteration=execute_proposal(proposal.id,user,repo,storage)
    assert iteration.status=="completed" and len(iteration.child_design_ids)==1
    child=iteration.child_design_ids[0]
    next_sim=_run(repo,storage,user,experiment,child,"thermal_conduction_v1","steel",{"dimension":"1d","length_m":1,"num_elements":8},{"ambient_temperature_c":20,"prescribed_temperature_c":30})
    assert repo.get_simulation_result(next_sim).converged
    with pytest.raises(CouplingNotFoundError): get_coupling(coupling.id,"owner-b",repo)
    with pytest.raises(FeedbackNotFoundError): get_proposal(proposal.id,"owner-b",repo)
    restarted=LocalSQLiteRepository(db)
    assert restarted.get_design_iteration(iteration.id).child_design_ids==[child]
    assert restarted.get_simulation_result(next_sim).source_design_id==child
