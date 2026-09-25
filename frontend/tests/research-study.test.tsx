import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ResearchStudy } from "@/components/research-study";
import { EvidenceScatter } from "@/components/evidence-scatter";
import { api } from "@/lib/api";

const push = vi.fn();
let evidenceMode: "existing" | "derive" | "missing-validity" = "existing";
let derived = false;
let cadJobStatus = "completed";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({
  api: vi.fn((path: string) => {
    if (path === "/api/simulations/run-1/evidence") { if (evidenceMode === "derive" && !derived) return Promise.resolve([]); return Promise.resolve([{ id: "trust-1", record_type: "scientific_trust", status: "complete", schema_version: "1.0", simulation_id: "run-1", created_at: "2026-08-05T00:00:00Z", payload: { overall_trust: evidenceMode === "missing-validity" ? "LOW" : "MODERATE", dimensions: { validity: evidenceMode === "missing-validity" ? { state: "NOT_RUN", evidence_ids: [] } : { state: "PASS", evidence_ids: ["validity-1"] }, benchmark: { state: "WARNING", evidence_ids: ["benchmark-1"] }, run_convergence: { state: "PASS", evidence_ids: ["convergence-1"] } }, reason_code: evidenceMode === "missing-validity" ? "VALIDITY_EVIDENCE_NOT_RUN" : "BENCHMARK_WARNING", evidence_ids: ["validity-1", "benchmark-1", "convergence-1"], limitations: ["Evidence-state classification only"], result_hash: "result-hash", trust_hash: "trust-hash" } }]); }
    if (path === "/api/v2/scientific/trust/simulations/run-1") { derived = true; return Promise.resolve({ id: "trust-1" }); }
    if (path === "/api/v2/scientific/trust/trust-1") return Promise.resolve({ id: "trust-1" });
    if (path === "/api/design/parse") return Promise.resolve({ params: { geometry_type: "pyramid", base_length_m: 2, height_m: 4, slope_angle_deg: 45, material: "concrete" } });
    if (path === "/api/design/design-space/preview") return Promise.resolve({ variant_count: 2, variants: [{ variation_index: 0, parameters: { geometry_type: "pyramid", base_length_m: 2, height_m: 2, slope_angle_deg: 45, material: "concrete" }, varied_values: {} }, { variation_index: 1, parameters: { geometry_type: "pyramid", base_length_m: 2, height_m: 4, slope_angle_deg: 45, material: "concrete" }, varied_values: {} }] });
    if (path === "/api/design/generate-batch") return Promise.resolve({ job_id: "cad-job", study_id: "study-1", status: cadJobStatus });
    if (path === "/api/studies/study-1/comparison-plan") return Promise.resolve({ evaluation_class: "comparative", model_disclosure: "controlled", variant_count: 1, varies: ["height_m"], held_constant: {} });
    if (path === "/api/studies/study-1/comparative-runs") return Promise.resolve({ job_id: "simulation-job", study_id: "study-1", status: "queued" });
    if (path.startsWith("/api/studies/")) {
      const decisionStatus = path.endsWith("study-actioned") ? "accepted" : undefined;
      return Promise.resolve({
      id: "study-1", title: "Persisted pyramid study", description: "", research_question: "How does height matter?",
      hypothesis: null, geometry_family: "pyramid", status: "active", designs: [{ id: "design-1", variation_index: 0, parameters: { geometry_type: "pyramid", base_length_m: 2, height_m: 4, slope_angle_deg: 45, material: "concrete" }, generation_status: "completed", files: [] }], generation_jobs: [{ id: "cad-generation-job", status: "completed", progress_percent: 100, completed_count: 2, failed_count: 0 }], simulations: [{ id: "run-1", design_id: "design-1", solver_id: "pyramid_thermal_conduction_v1", status: "completed", input: null, fields: [], result: { solver_version: "1", summary_metrics: { max_temperature_c: 30 }, converged: true, governing_equations: [], assumptions: [], warnings: [], validation_metadata: { convergence_evidence: { resolution_refinement_performed_for_current_run: false } }, reproducibility_hash: "hash" } }],
      analyses: [], decisions: decisionStatus ? [{ id: "decision-actioned", status: decisionStatus, payload: { status: decisionStatus } }] : [], reports: [], updated_at: "2026-08-05T00:00:00Z",
      });
    }
    if (path === "/api/v2/decisions") return Promise.resolve({ id: "decision-1", payload: { status: "proposed", recommendation: { statement: "Review" } } });
    return Promise.resolve({});
  }),
  download: vi.fn(),
}));

describe("durable research study workspace", () => {
  afterEach(() => { evidenceMode = "existing"; derived = false; cadJobStatus = "completed"; vi.mocked(api).mockClear(); });
  it("renders the human-facing study tree and tracks the active stage", async () => {
    render(<ResearchStudy studyId="study-1" />);
    expect(await screen.findByRole("heading", { name: "Persisted pyramid study" })).toBeInTheDocument();
    for (const stage of ["Question", "Design", "Physics", "Validation", "Run", "Evidence", "Decision", "Report"]) expect(screen.getByRole("button", { name: new RegExp(`^${stage}(,|$)`) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Question, complete" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "Run, completed" })).toHaveAccessibleName("Run, completed");
    fireEvent.click(screen.getByRole("button", { name: "Run, completed" }));
    expect(screen.getByRole("button", { name: "Run, completed" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("heading", { name: "Durable batch execution" })).toBeInTheDocument();
  });

  it("keeps existing study actions reachable through their remapped stages", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Design, complete" }));
    expect(screen.getByRole("button", { name: "Parse into editable parameters" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Physics" }));
    expect(screen.getByRole("button", { name: "Build pre-run comparison" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Evidence, persisted result available" }));
    expect(screen.getByRole("button", { name: "Run persisted analysis" })).toBeInTheDocument();
  });

  it("changes the contextual inspector without inventing validation or trust", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    const inspector = screen.getByRole("complementary", { name: "Study inspector" });
    expect(inspector).toHaveTextContent("Study metadata");
    fireEvent.click(screen.getByRole("button", { name: "Run, completed" }));
    expect(inspector).toHaveTextContent("Execution trace");
    expect(inspector).toHaveTextContent("Latest persisted result");
    expect(inspector).toHaveTextContent("Solver version");
    expect(inspector).toHaveTextContent("Governing equations");
    expect(inspector).toHaveTextContent("Validation metadata");
    expect(inspector).toHaveTextContent("Reproducibility hash");
    expect(inspector).not.toHaveTextContent(/high trust|validated/i);
  });

  it("identifies the persisted design and evidence run behind a plotted point", () => {
    render(<EvidenceScatter xLabel="Height" yLabel="Temperature" points={[{ designId: "design-1", simulationId: "run-1", x: 1, y: 22 }, { designId: "design-2", simulationId: "run-2", x: 2, y: 25 }]} />);
    fireEvent.click(screen.getByRole("button", { name: /Design design-2/i }));
    expect(screen.getByText("design-2")).toBeInTheDocument();
    expect(screen.getByText("run-2")).toBeInTheDocument();
  });

  it("shows authoritative iterative solver convergence without claiming spatial convergence", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Run, completed" }));
    expect(screen.getByRole("columnheader", { name: "Iterative convergence" })).toBeInTheDocument();
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.queryByText(/spatial convergence/i)).not.toBeInTheDocument();
  });

  it("uses the registry-declared unit when creating an evidence-linked decision", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Decision" }));
    fireEvent.click(screen.getByRole("button", { name: "Build decision from completed evidence" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/api/v2/decisions", expect.objectContaining({ method: "POST" })));
    const call = vi.mocked(api).mock.calls.find(([path]) => path === "/api/v2/decisions");
    expect(JSON.parse(String(call?.[1]?.body)).objectives[0]).toMatchObject({ metric_code: "max_temperature_c", unit: "degC" });
    expect(JSON.parse(String(call?.[1]?.body)).designs[0]).toMatchObject({ validity_status: "valid", confidence: "moderate", evidence_ids: ["trust-1"] });
    expect(JSON.stringify(JSON.parse(String(call?.[1]?.body)))).not.toContain("run-1\"]");
  });

  it("keeps the canvas selection, evidence ledger, trust panel, and inspector on one persisted run", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Evidence, persisted result available" }));
    expect(await screen.findByLabelText("Evidence ledger")).toHaveTextContent("trust-1");
    expect(screen.getByLabelText("Scientific Trust")).toHaveTextContent("MODERATE");
    expect(screen.getByRole("complementary", { name: "Study inspector" })).toHaveTextContent("Selected evidence context");
  });

  it("derives missing trust through the backend and refreshes the selected run evidence", async () => {
    evidenceMode = "derive";
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Evidence, persisted result available" }));
    fireEvent.click(await screen.findByRole("button", { name: "Derive Scientific Trust" }));
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith("/api/v2/scientific/trust/simulations/run-1", { method: "POST" }));
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/v2/scientific/trust/trust-1");
    expect(await screen.findByLabelText("Scientific Trust")).toHaveTextContent("MODERATE");
  });

  it("blocks a decision when authoritative validity evidence is not run", async () => {
    evidenceMode = "missing-validity";
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Decision" }));
    fireEvent.click(screen.getByRole("button", { name: "Build decision from completed evidence" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Decision support requires authoritative validity evidence for every candidate.");
    expect(vi.mocked(api).mock.calls.some(([path]) => path === "/api/v2/decisions")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Evidence, persisted result available" }));
    expect(await screen.findByLabelText("Scientific Trust")).toHaveTextContent("LOW");
    expect(screen.getByLabelText("Evidence Spine")).toHaveTextContent("completed · run-1");
  });

  it("marks a proposed decision as awaiting human action, not complete", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Decision" }));
    fireEvent.click(screen.getByRole("button", { name: "Build decision from completed evidence" }));
    const decisionStage = await screen.findByRole("button", { name: "Decision, awaiting human action" });
    expect(decisionStage.querySelector(".study-tree-marker")).toHaveClass("available");
    expect(decisionStage.querySelector(".study-tree-marker")).not.toHaveClass("complete");
  });

  it("marks an actioned decision as complete without claiming scientific success", async () => {
    render(<ResearchStudy studyId="study-actioned" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    const decisionStage = screen.getByRole("button", { name: "Decision, human action recorded" });
    expect(decisionStage.querySelector(".study-tree-marker")).toHaveClass("complete");
  });

  it("keeps active CAD generation in Design and labels it as CAD work", async () => {
    cadJobStatus = "queued";
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Design, complete" }));
    fireEvent.click(screen.getByRole("button", { name: "Parse into editable parameters" }));
    fireEvent.click(await screen.findByRole("button", { name: "Define design space" }));
    fireEvent.click(screen.getByRole("button", { name: "Resolve final variants" }));
    fireEvent.click(await screen.findByRole("button", { name: "Generate 2 CAD variants" }));
    expect(await screen.findByRole("heading", { name: "Generating CAD variants..." })).toBeInTheDocument();
    expect(screen.getByText("CAD design artifacts are being generated. Physics simulations have not run yet.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Durable batch execution" })).not.toBeInTheDocument();
  });

  it("shows completed CAD artifacts with an explicit Physics handoff", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Design, complete" }));
    fireEvent.click(screen.getByRole("button", { name: "Parse into editable parameters" }));
    fireEvent.click(await screen.findByRole("button", { name: "Define design space" }));
    fireEvent.click(screen.getByRole("button", { name: "Resolve final variants" }));
    fireEvent.click(await screen.findByRole("button", { name: "Generate 2 CAD variants" }));
    expect(await screen.findByRole("heading", { name: "CAD generation completed" })).toBeInTheDocument();
    expect(screen.getByText("2 CAD design variants are ready. Physics simulations have not run yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue to Physics" }));
    expect(await screen.findByRole("heading", { name: "Comparable physics" })).toBeInTheDocument();
    expect(vi.mocked(api)).not.toHaveBeenCalledWith("/api/studies/study-1/comparative-runs", expect.anything());
  });

  it("uses Run only for comparative simulations and omits CAD job rows", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Run, completed" }));
    expect(screen.queryByText("cad-generation-job")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Physics" }));
    fireEvent.click(screen.getByRole("button", { name: "Build pre-run comparison" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm and execute 1 runs" }));
    expect(await screen.findByRole("heading", { name: "Durable batch execution" })).toBeInTheDocument();
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/studies/study-1/comparative-runs", expect.anything());
  });
});
