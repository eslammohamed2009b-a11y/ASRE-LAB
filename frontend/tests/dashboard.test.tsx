import {fireEvent,render,screen,waitFor} from "@testing-library/react";
import {beforeEach,describe,expect,it,vi} from "vitest";
import {Dashboard} from "@/components/dashboard";
import {api} from "@/lib/api";

vi.mock("@/lib/api",()=>({api:vi.fn()}));

const dashboard={attempts:[],decisions:[],reports:[],account:{founding_user:false,founding_user_number:null,usage_access_period:"standard"}};
const studies={items:[{id:"study-1",title:"Persisted study",status:"draft",updated_at:"2026-09-25T00:00:00Z",design_count:0,simulation_count:0,completed_run_count:0,failed_run_count:0,analysis_count:0,report_count:0}]};

describe("research dashboard loading",()=>{
  beforeEach(()=>vi.clearAllMocks());

  it("shows a loading state before the server list resolves",()=>{
    vi.mocked(api).mockImplementation(()=>new Promise(()=>{}));
    render(<Dashboard/>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading server-side studies");
  });

  it("renders the successfully loaded Study list",async()=>{
    vi.mocked(api).mockImplementation((path:string)=>Promise.resolve(path==="/api/studies"?studies:dashboard));
    render(<Dashboard/>);
    expect(await screen.findByText("Persisted study")).toBeInTheDocument();
  });

  it("offers Retry after a real list failure",async()=>{
    let fails=true;
    vi.mocked(api).mockImplementation((path:string)=>fails&&path==="/api/studies"?Promise.reject(new Error("Internal server error")):Promise.resolve(path==="/api/studies"?studies:dashboard));
    render(<Dashboard/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load your studies");
    fails=false;
    fireEvent.click(screen.getByRole("button",{name:"Retry"}));
    await waitFor(()=>expect(screen.getByText("Persisted study")).toBeInTheDocument());
  });
});
