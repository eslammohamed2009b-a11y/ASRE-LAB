import {fireEvent,render,screen,waitFor} from "@testing-library/react";
import {vi} from "vitest";
import {ResearchStudy} from "@/components/research-study";
import {EvidenceScatter} from "@/components/evidence-scatter";
import {api} from "@/lib/api";

const push=vi.fn();
vi.mock("next/navigation",()=>({useRouter:()=>({push})}));
vi.mock("@/lib/api",()=>({
  api:vi.fn((path:string)=>{
    if(path==="/api/studies/study-1")return Promise.resolve({
      id:"study-1",title:"Persisted pyramid study",description:"",research_question:"How does height matter?",
      hypothesis:null,geometry_family:"pyramid",status:"active",designs:[],generation_jobs:[],simulations:[{id:"run-1",design_id:"design-1",solver_id:"pyramid_thermal_conduction_v1",status:"completed",input:null,fields:[],result:{solver_version:"1",summary_metrics:{max_temperature_c:30},converged:true,governing_equations:[],assumptions:[],warnings:[],validation_metadata:{convergence_evidence:{resolution_refinement_performed_for_current_run:false}},reproducibility_hash:"hash"}}],
      analyses:[],decisions:[],reports:[],updated_at:"2026-08-05T00:00:00Z",
    });
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
});
