import {fireEvent,render,screen,waitFor} from "@testing-library/react";
import {vi} from "vitest";
import {ResearchStudy} from "@/components/research-study";
import {EvidenceScatter} from "@/components/evidence-scatter";
import {api} from "@/lib/api";

const push=vi.fn();
const cadJob=vi.hoisted(()=>({status:"completed"}));
vi.mock("next/navigation",()=>({useRouter:()=>({push})}));
vi.mock("@/lib/api",()=>({
  api:vi.fn((path:string)=>{
    if(path==="/api/studies/study-1")return Promise.resolve({
      id:"study-1",title:"Persisted pyramid study",description:"",research_question:"How does height matter?",
      hypothesis:null,geometry_family:"pyramid",status:"active",designs:[{id:"design-1",variation_index:0,generation_status:"completed",parameters:{geometry_type:"pyramid",base_length_m:2,height_m:4,slope_angle_deg:45,material:"concrete"},files:[]}],generation_jobs:[{id:"cad-generation-job",status:"completed",progress_percent:100,completed_count:2,failed_count:0}],simulations:[{id:"run-1",design_id:"design-1",solver_id:"pyramid_thermal_conduction_v1",status:"completed",input:null,fields:[],result:{solver_version:"1",summary_metrics:{max_temperature_c:30},converged:true,governing_equations:[],assumptions:[],warnings:[],validation_metadata:{convergence_evidence:{resolution_refinement_performed_for_current_run:false}},reproducibility_hash:"hash"}}],
      analyses:[],decisions:[],reports:[],updated_at:"2026-08-05T00:00:00Z",
    });
    if(path==="/api/design/parse")return Promise.resolve({params:{geometry_type:"pyramid",base_length_m:2,height_m:4,slope_angle_deg:45,material:"concrete"}});
    if(path==="/api/design/design-space/preview")return Promise.resolve({variant_count:2,variants:[{variation_index:0,parameters:{geometry_type:"pyramid",base_length_m:2,height_m:2,slope_angle_deg:45,material:"concrete"},varied_values:{}},{variation_index:1,parameters:{geometry_type:"pyramid",base_length_m:2,height_m:4,slope_angle_deg:45,material:"concrete"},varied_values:{}}]});
    if(path==="/api/design/generate-batch")return Promise.resolve({job_id:"cad-job",study_id:"study-1",status:cadJob.status});
    if(path==="/api/studies/study-1/comparison-plan")return Promise.resolve({evaluation_class:"comparative",model_disclosure:"controlled",variant_count:1,varies:["height_m"],held_constant:{}});
    if(path==="/api/studies/study-1/comparative-runs")return Promise.resolve({job_id:"simulation-job",study_id:"study-1",status:"queued"});
    if(path==="/api/v2/decisions")return Promise.resolve({id:"decision-1",payload:{status:"proposed",recommendation:{statement:"Review"}}});
    return Promise.resolve({});
  }),
  download:vi.fn(),
}));

describe("durable research study",()=>{
  it("loads server study state and exposes the comparative research stages",async()=>{
    render(<ResearchStudy studyId="study-1"/>);
    expect(await screen.findByRole("heading",{name:"Persisted pyramid study"})).toBeInTheDocument();
    for(const stage of ["Setup","Design","Design Space","Physics","Execution","Analysis","Decision","Report"]){
      expect(screen.getByRole("button",{name:stage})).toBeInTheDocument();
    }
    expect(screen.getByText(/updated/i)).toHaveTextContent("study-1");
  });

  it("identifies the persisted design and evidence run behind a plotted point",()=>{
    render(<EvidenceScatter xLabel="Height" yLabel="Temperature" points={[
      {designId:"design-1",simulationId:"run-1",x:1,y:22},
      {designId:"design-2",simulationId:"run-2",x:2,y:25},
    ]}/>);
    fireEvent.click(screen.getByRole("button",{name:/Design design-2/i}));
    expect(screen.getByText("design-2")).toBeInTheDocument();
    expect(screen.getByText("run-2")).toBeInTheDocument();
  });

  it("shows authoritative iterative solver convergence without claiming spatial convergence",async()=>{
    render(<ResearchStudy studyId="study-1"/>);
    await screen.findByRole("heading",{name:"Persisted pyramid study"});
    fireEvent.click(screen.getByRole("button",{name:"Execution"}));
    expect(screen.getByRole("columnheader",{name:"Iterative convergence"})).toBeInTheDocument();
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.queryByText(/spatial convergence/i)).not.toBeInTheDocument();
  });

  it("uses the registry-declared unit when creating an evidence-linked decision",async()=>{
    render(<ResearchStudy studyId="study-1"/>);
    await screen.findByRole("heading",{name:"Persisted pyramid study"});
    fireEvent.click(screen.getByRole("button",{name:"Decision"}));
    fireEvent.click(screen.getByRole("button",{name:"Build decision from completed evidence"}));
    await waitFor(()=>expect(vi.mocked(api)).toHaveBeenCalledWith("/api/v2/decisions",expect.objectContaining({method:"POST"})));
    const call=vi.mocked(api).mock.calls.find(([path])=>path==="/api/v2/decisions");
    expect(JSON.parse(String(call?.[1]?.body)).objectives[0]).toMatchObject({metric_code:"max_temperature_c",unit:"degC"});
  });

  it("keeps CAD generation in Design Space with an explicit Physics handoff",async()=>{
    render(<ResearchStudy studyId="study-1"/>);
    await screen.findByRole("heading",{name:"Persisted pyramid study"});
    fireEvent.click(screen.getByRole("button",{name:"Design"}));
    fireEvent.click(screen.getByRole("button",{name:"Parse into editable parameters"}));
    await screen.findByRole("button",{name:"Define design space"});
    fireEvent.click(screen.getByRole("button",{name:"Define design space"}));
    fireEvent.click(screen.getByRole("button",{name:"Resolve final variants"}));
    expect(await screen.findByRole("button",{name:"Generate 2 CAD variants"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"Generate 2 CAD variants"}));
    await waitFor(()=>expect(vi.mocked(api)).toHaveBeenCalledWith("/api/design/generate-batch",expect.anything()));
    expect(screen.getByRole("heading",{name:"Deterministic design space"})).toBeInTheDocument();
    expect(await screen.findByRole("heading",{name:"CAD generation completed"})).toBeInTheDocument();
    expect(screen.getByText("2 CAD design variants are ready. Physics simulations have not run yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"Continue to Physics"}));
    expect(await screen.findByRole("heading",{name:"Comparable physics"})).toBeInTheDocument();
    expect(screen.queryByRole("heading",{name:/Durable simulation execution/i})).not.toBeInTheDocument();
    expect(vi.mocked(api)).not.toHaveBeenCalledWith("/api/studies/study-1/comparative-runs",expect.anything());
  });

  it("labels an active CAD job as generation rather than simulation execution",async()=>{
    cadJob.status="queued";
    render(<ResearchStudy studyId="study-1"/>);
    await screen.findByRole("heading",{name:"Persisted pyramid study"});
    fireEvent.click(screen.getByRole("button",{name:"Design"}));
    fireEvent.click(screen.getByRole("button",{name:"Parse into editable parameters"}));
    fireEvent.click(await screen.findByRole("button",{name:"Define design space"}));
    fireEvent.click(screen.getByRole("button",{name:"Resolve final variants"}));
    fireEvent.click(await screen.findByRole("button",{name:"Generate 2 CAD variants"}));
    expect(await screen.findByRole("heading",{name:"Generating CAD variants..."})).toBeInTheDocument();
    expect(screen.getByText("CAD design artifacts are being generated. Physics simulations have not run yet.")).toBeInTheDocument();
    cadJob.status="completed";
  });

  it("uses Execution only after comparative simulation execution starts",async()=>{
    render(<ResearchStudy studyId="study-1"/>);
    await screen.findByRole("heading",{name:"Persisted pyramid study"});
    fireEvent.click(screen.getByRole("button",{name:"Physics"}));
    fireEvent.click(screen.getByRole("button",{name:"Build pre-run comparison"}));
    expect(await screen.findByRole("button",{name:"Confirm and execute 1 runs"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"Confirm and execute 1 runs"}));
    expect(await screen.findByRole("heading",{name:/Durable simulation execution/i})).toBeInTheDocument();
    expect(screen.queryByText("cad-generation-job")).not.toBeInTheDocument();
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/studies/study-1/comparative-runs",expect.anything());
  });
});
