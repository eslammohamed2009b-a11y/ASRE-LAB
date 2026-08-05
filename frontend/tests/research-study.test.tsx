import {fireEvent,render,screen} from "@testing-library/react";
import {vi} from "vitest";
import {ResearchStudy} from "@/components/research-study";
import {EvidenceScatter} from "@/components/evidence-scatter";

const push=vi.fn();
vi.mock("next/navigation",()=>({useRouter:()=>({push})}));
vi.mock("@/lib/api",()=>({
  api:vi.fn((path:string)=>{
    if(path==="/api/studies/study-1")return Promise.resolve({
      id:"study-1",title:"Persisted pyramid study",description:"",research_question:"How does height matter?",
      hypothesis:null,geometry_family:"pyramid",status:"active",designs:[],generation_jobs:[],simulations:[],
      analyses:[],decisions:[],reports:[],updated_at:"2026-08-05T00:00:00Z",
    });
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
});
