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
      const hasReport = path.endsWith("study-report");
      const hasAnalysis = path.endsWith("study-analysis") || path.endsWith("study-multiple-analysis");
      const multipleAnalyses = path.endsWith("study-multiple-analysis");
      const decisionStatus = path.endsWith("study-actioned") || hasReport ? "accepted" : undefined;
      return Promise.resolve({
      id: "study-1", title: "Persisted pyramid study", description: "", research_question: "How does height matter?",
      hypothesis: null, geometry_family: "pyramid", status: "active", designs: [{ id: "design-1", variation_index: 0, parameters: { geometry_type: "pyramid", base_length_m: 2, height_m: 4, slope_angle_deg: 45, material: "concrete" }, generation_status: "completed", files: [] }], generation_jobs: [{ id: "cad-generation-job", status: "completed", progress_percent: 100, completed_count: 2, failed_count: 0 }], simulations: [{ id: "run-1", design_id: "design-1", solver_id: "pyramid_thermal_conduction_v1", status: "completed", input: null, fields: [], result: { solver_version: "1", summary_metrics: { max_temperature_c: 30 }, converged: true, governing_equations: [], assumptions: [], warnings: [], validation_metadata: { convergence_evidence: { resolution_refinement_performed_for_current_run: false } }, reproducibility_hash: "hash" } }],
      analyses: hasAnalysis ? [{ id: "analysis-automatic", dataset_hash: "dataset-hash", data_quality: { valid_row_count: 1, dropped_row_count: 0 }, result: { descriptive_statistics: { "metric.max_temperature_c": { count: 1, mean: 30, min: 30, max: 30 } }, correlations: { relationships: [{ variables: ["design.height_m", "metric.max_temperature_c"], coefficient: 0.8 }] }, sensitivity: { influences: [{ feature: "design.height_m", standardized_coefficient: 0.4 }] }, pareto: { pareto_optimal: [{ design_id: "design-1", objective_values: { "metric.max_temperature_c": 30 } }] }, ranking: { ranking: [{ rank: 1, design_id: "design-1", score: 1 }] } }, warnings: [], configuration: {}, source_simulation_ids: ["run-1"], created_at: "2026-08-05T00:00:00Z" }, ...(multipleAnalyses ? [{ id: "analysis-manual", dataset_hash: "dataset-hash", data_quality: { valid_row_count: 1 }, result: {}, warnings: ["User-selected objective"], configuration: { objectives: [{ column: "metric.max_temperature_c" }] }, source_simulation_ids: ["run-1"], created_at: "2026-08-05T01:00:00Z" }] : [])] : [], decisions: decisionStatus ? [{ id: "decision-actioned", status: decisionStatus, payload: { status: decisionStatus } }] : [], reports: hasReport ? [{ id: "report-1", status: "complete", payload: { status: "complete" } }] : [], updated_at: "2026-08-05T00:00:00Z",
      });
    }
    if (path === "/api/v2/decisions") return Promise.resolve({ id: "decision-1", payload: { status: "proposed", recommendation: { statement: "Review" } } });
    return Promise.resolve({});
  }),
  download: vi.fn(),
}));

describe("durable research study workspace", () => {
  afterEach(() => { evidenceMode = "existing"; derived = false; cadJobStatus = "completed"; push.mockClear(); vi.mocked(api).mockClear(); });
  it("renders the human-facing study tree and tracks the active stage", async () => {
    render(<ResearchStudy studyId="study-1" />);
    expect(await screen.findByRole("heading", { name: "Persisted pyramid study" })).toBeInTheDocument();
    for (const stage of ["Question", "Design", "Physics", "Validation", "Run", "Evidence", "Decision", "Report"]) expect(screen.getByRole("button", { name: new RegExp(`^${stage}(,|$)`) })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evidence, persisted result available" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText("Study progress")).toHaveTextContent("Continue from Evidence");
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
    expect(inspector).toHaveTextContent("Selected evidence context");
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
    expect(await screen.findByLabelText("Decision basis")).toHaveTextContent("Human decision basis");
    expect(screen.getByLabelText("Decision basis")).toHaveTextContent("Design 01");
    expect(screen.getByLabelText("Decision basis")).toHaveTextContent("Analysis not yet generated");
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

  it("creates a Study only after receiving an identifier and navigates to it", async () => {
    vi.mocked(api).mockResolvedValueOnce({ id: "new-study" });
    render(<ResearchStudy />);
    fireEvent.click(screen.getByRole("button", { name: "Create persisted study" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/app/studies/new-study"));
  });

  it("keeps the create form available with a readable failure", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("research_question: Input should have at least 3 characters"));
    render(<ResearchStudy />);
    fireEvent.click(screen.getByRole("button", { name: "Create persisted study" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("research_question: Input should have at least 3 characters");
    expect(screen.getByRole("button", { name: "Create persisted study" })).toBeEnabled();
  });

  it("does not silently navigate when study creation returns no identifier", async () => {
    vi.mocked(api).mockResolvedValueOnce({});
    render(<ResearchStudy />);
    fireEvent.click(screen.getByRole("button", { name: "Create persisted study" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Study creation did not return a study identifier");
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Create persisted study" })).toBeEnabled();
  });

  it("renders a readable pre-run comparison instead of raw configuration", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Physics" }));
    fireEvent.click(screen.getByRole("button", { name: "Build pre-run comparison" }));
    const review = await screen.findByLabelText("Comparison controls");
    expect(review).toHaveTextContent("Pre-run comparison review");
    expect(review).toHaveTextContent("Boundary conditions");
    expect(review).toHaveTextContent("Ambient temperature");
    expect(review).toHaveTextContent("Numerical settings");
    expect(review).toHaveTextContent("Maximum iterations");
    expect(review).not.toHaveTextContent("[object Object]");
  });

  it("keeps human-readable design and result identities primary while retaining traceability IDs", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Run, completed" }));
    const results = screen.getByLabelText("Persisted simulations");
    expect(results).toHaveTextContent("Design 01");
    expect(results).toHaveTextContent("Maximum temperature");
    expect(results).toHaveTextContent("Simulation ID run-1");
  });

  it("shows an in-product summary for an available report", async () => {
    render(<ResearchStudy studyId="study-report" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    expect(screen.getByLabelText("Research report summary")).toHaveTextContent("Persisted pyramid study");
    expect(screen.getByLabelText("Research report summary")).toHaveTextContent("Design 01");
    expect(screen.getByLabelText("Research report summary")).toHaveTextContent("accepted");
  });

  it("labels rerunning analysis as a new persisted analysis", async () => {
    render(<ResearchStudy studyId="study-analysis" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    expect(screen.getByLabelText("Analysis state")).toHaveTextContent("Analysis available");
    expect(screen.getByRole("button", { name: "Run a new persisted analysis" })).toBeInTheDocument();
  });

  it("distinguishes automatic batch analysis from a later researcher-requested analysis", async () => {
    render(<ResearchStudy studyId="study-multiple-analysis" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    const provenance = screen.getByLabelText("Analysis state");
    expect(provenance).toHaveTextContent("Automatic comparative-batch analysis");
    expect(provenance).toHaveTextContent("Researcher-requested persisted analysis");
    expect(provenance).toHaveTextContent("Analysis ID analysis-automatic");
    expect(provenance).toHaveTextContent("Analysis ID analysis-manual");
  });

  it("puts persisted analysis into structured summary sections with raw data secondary", async () => {
    render(<ResearchStudy studyId="study-analysis" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    const summary = screen.getByLabelText("Analysis summary");
    expect(summary).toHaveTextContent("Dataset quality");
    expect(summary).toHaveTextContent("Valid Row Count");
    expect(summary).toHaveTextContent("Descriptive statistics");
    expect(summary).toHaveTextContent("Maximum temperature");
    expect(summary).toHaveTextContent("Association does not establish causation.");
    expect(summary).toHaveTextContent("Ranking");
    expect(summary).not.toHaveTextContent("[object Object]");
    expect(screen.getByText("Technical / raw analysis data").closest("details")).not.toHaveAttribute("open");
  });

  it("keeps decision result values in readable block structure", async () => {
    render(<ResearchStudy studyId="study-1" />);
    await screen.findByRole("heading", { name: "Persisted pyramid study" });
    fireEvent.click(screen.getByRole("button", { name: "Decision" }));
    const basis = screen.getByLabelText("Decision basis");
    expect(basis.querySelector(".readable-values")).not.toBeNull();
    expect(basis).toHaveTextContent("Maximum temperature");
    expect(basis.querySelector(".readable-values dd")).not.toBeNull();
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
