"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, download } from "@/lib/api";
import type { Capability, EvidenceRecord, SimulationJob, SimulationResults, Validity } from "@/types/api";

const stages = ["Design", "Physics", "Validation", "Execution", "Evidence", "Decision", "Report"] as const;
type Stage = (typeof stages)[number];

type FieldDefinition = { key: string; label: string; unit?: string; type?: "number" | "text"; min?: number; max?: number };
type SolverForm = {
  geometry: FieldDefinition[];
  boundaries: FieldDefinition[];
  validity: FieldDefinition[];
  benchmark: FieldDefinition[];
};

const solverForms: Record<string, SolverForm> = {
  thermal_conduction_v1: {
    geometry: [
      { key: "length_m", label: "Length", unit: "m", type: "number", min: 0.000001 },
      { key: "num_elements", label: "Elements", type: "number", min: 2, max: 500 },
    ],
    boundaries: [
      { key: "prescribed_temperature_c", label: "Hot-end prescribed temperature", unit: "°C", type: "number" },
      { key: "ambient_temperature_c", label: "Cold-end temperature", unit: "°C", type: "number" },
    ],
    validity: [
      { key: "length_m", label: "Length", unit: "m", type: "number" },
      { key: "num_elements", label: "Elements", type: "number" },
    ],
    benchmark: [
      { key: "cold_c", label: "Cold temperature", unit: "°C", type: "number" },
      { key: "hot_c", label: "Hot temperature", unit: "°C", type: "number" },
      { key: "position_fraction", label: "Position fraction", unit: "0–1", type: "number" },
    ],
  },
  structural_linear_1d_v1: {
    geometry: [
      { key: "length_m", label: "Length", unit: "m", type: "number", min: 0.000001 },
      { key: "cross_section_area_m2", label: "Cross-section area", unit: "m²", type: "number", min: 0 },
      { key: "num_elements", label: "Elements", type: "number", min: 1, max: 500 },
    ],
    boundaries: [{ key: "axial_load_n", label: "Axial end load", unit: "N", type: "number" }],
    validity: [
      { key: "length_m", label: "Length", unit: "m", type: "number" },
      { key: "num_elements", label: "Elements", type: "number" },
    ],
    benchmark: [
      { key: "load_n", label: "Load", unit: "N", type: "number" },
      { key: "length_m", label: "Length", unit: "m", type: "number" },
      { key: "youngs_modulus_pa", label: "Young's modulus", unit: "Pa", type: "number" },
      { key: "area_m2", label: "Area", unit: "m²", type: "number" },
    ],
  },
  modal_eigen_1d_v1: {
    geometry: [{ key: "num_elements", label: "Elements", type: "number", min: 1, max: 200 }],
    boundaries: [
      { key: "point_mass_kg", label: "Point mass", unit: "kg", type: "number", min: 0 },
      { key: "spring_stiffness_n_m", label: "Spring stiffness", unit: "N/m", type: "number", min: 0 },
    ],
    validity: [{ key: "num_elements", label: "Elements", type: "number" }],
    benchmark: [
      { key: "mass_kg", label: "Mass", unit: "kg", type: "number" },
      { key: "stiffness_n_m", label: "Stiffness", unit: "N/m", type: "number" },
    ],
  },
  acoustic_duct_1d_v1: {
    geometry: [
      { key: "length_m", label: "Duct length", unit: "m", type: "number", min: 0 },
      { key: "num_elements", label: "Elements", type: "number", min: 4, max: 500 },
    ],
    boundaries: [
      { key: "source_frequency_hz", label: "Source frequency", unit: "Hz", type: "number", min: 0 },
      { key: "source_pressure_pa", label: "Source pressure", unit: "Pa", type: "number" },
      { key: "acoustic_right_boundary", label: "Right termination (pressure_release or rigid)" },
    ],
    validity: [{ key: "length_m", label: "Length", unit: "m", type: "number" }, { key: "num_elements", label: "Elements", type: "number" }],
    benchmark: [{ key: "length_m", label: "Length", unit: "m", type: "number" }, { key: "speed_m_s", label: "Sound speed", unit: "m/s", type: "number" }],
  },
  electrostatic_rectangular_2d_v1: {
    geometry: [
      { key: "width_m", label: "Width", unit: "m", type: "number", min: 0 },
      { key: "height_m", label: "Height", unit: "m", type: "number", min: 0 },
      { key: "grid_resolution", label: "Grid resolution X", type: "number", min: 5, max: 60 },
      { key: "grid_resolution_y", label: "Grid resolution Y", type: "number", min: 5, max: 60 },
    ],
    boundaries: [
      { key: "potential_left_v", label: "Left potential", unit: "V", type: "number" },
      { key: "potential_right_v", label: "Right potential", unit: "V", type: "number" },
      { key: "potential_top_v", label: "Top potential", unit: "V", type: "number" },
      { key: "potential_bottom_v", label: "Bottom potential", unit: "V", type: "number" },
    ],
    validity: [{ key: "grid_size", label: "Grid size", type: "number" }],
    benchmark: [
      { key: "left_v", label: "Left potential", unit: "V", type: "number" },
      { key: "right_v", label: "Right potential", unit: "V", type: "number" },
      { key: "width_m", label: "Width", unit: "m", type: "number" },
    ],
  },
  cfd_laminar_channel_2d_v1: {
    geometry: [
      { key: "length_m", label: "Channel length", unit: "m", type: "number", min: 0 },
      { key: "height_m", label: "Plate separation", unit: "m", type: "number", min: 0 },
      { key: "grid_resolution", label: "Grid resolution", type: "number", min: 5, max: 60 },
    ],
    boundaries: [{ key: "pressure_gradient_pa_m", label: "Pressure gradient (negative)", unit: "Pa/m", type: "number" }],
    validity: [
      { key: "grid_size", label: "Grid size", type: "number" },
      { key: "reynolds_number", label: "Expected Reynolds number", unit: "Re < 2000", type: "number" },
    ],
    benchmark: [
      { key: "pressure_gradient_pa_m", label: "Pressure gradient", unit: "Pa/m", type: "number" },
      { key: "height_m", label: "Plate separation", unit: "m", type: "number" },
      { key: "viscosity_pa_s", label: "Dynamic viscosity", unit: "Pa·s", type: "number" },
    ],
  },
};

const objectiveUnits: Record<string, { unit: string; direction: "minimize" | "maximize" }> = {
  max_temperature_c: { unit: "degC", direction: "minimize" },
  max_displacement_m: { unit: "m", direction: "minimize" },
  max_stress_pa: { unit: "Pa", direction: "minimize" },
  natural_frequency_hz: { unit: "Hz", direction: "maximize" },
  fundamental_resonance_hz: { unit: "Hz", direction: "maximize" },
  max_electric_field_v_m: { unit: "V/m", direction: "minimize" },
  maximum_velocity_m_s: { unit: "m/s", direction: "maximize" },
};

function numberMap(definitions: FieldDefinition[], values: Record<string, string>) {
  return Object.fromEntries(definitions.filter((field) => values[field.key] !== "").map((field) => [
    field.key,
    field.type === "text" || !field.type ? values[field.key] : Number(values[field.key]),
  ]));
}

function Fields({ definitions, values, onChange }: { definitions: FieldDefinition[]; values: Record<string, string>; onChange: (key: string, value: string) => void }) {
  return <>{definitions.map((field) => <div className="field" key={field.key}>
    <label htmlFor={field.key}>{field.label}{field.unit ? ` (${field.unit})` : ""}</label>
    <input id={field.key} type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} step="any" required value={values[field.key] || ""} onChange={(event) => onChange(field.key, event.target.value)} />
  </div>)}</>;
}

export function StudyWorkflow() {
  const [stage, setStage] = useState<Stage>("Design");
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [solverId, setSolverId] = useState("");
  const [material, setMaterial] = useState("");
  const [dimension, setDimension] = useState("");
  const [prompt, setPrompt] = useState("");
  const [design, setDesign] = useState<Record<string, unknown> | null>(null);
  const [localStudyId, setLocalStudyId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [geometry, setGeometry] = useState<Record<string, string>>({});
  const [boundaries, setBoundaries] = useState<Record<string, string>>({});
  const [validityInputs, setValidityInputs] = useState<Record<string, string>>({});
  const [benchmarkInputs, setBenchmarkInputs] = useState<Record<string, string>>({});
  const [validity, setValidity] = useState<Validity | null>(null);
  const [manifest, setManifest] = useState<EvidenceRecord | null>(null);
  const [job, setJob] = useState<SimulationJob | null>(null);
  const [results, setResults] = useState<SimulationResults | null>(null);
  const [trust, setTrust] = useState<EvidenceRecord | null>(null);
  const [reasoning, setReasoning] = useState<EvidenceRecord | null>(null);
  const [decision, setDecision] = useState<EvidenceRecord | null>(null);
  const [report, setReport] = useState<EvidenceRecord | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [stale, setStale] = useState(false);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const pollingStarted = useRef(0);

  const capability = capabilities.find((item) => item.solver_id === solverId);
  const form = solverForms[solverId];
  const completed = useMemo(() => new Set<Stage>([
    ...(design ? ["Design" as Stage] : []),
    ...(solverId && material && dimension ? ["Physics" as Stage] : []),
    ...(validity && validity.status !== "invalid" ? ["Validation" as Stage] : []),
    ...(manifest ? ["Execution" as Stage] : []),
    ...(trust ? ["Evidence" as Stage] : []),
    ...(decision ? ["Decision" as Stage] : []),
    ...(report ? ["Report" as Stage] : []),
  ]), [design, solverId, material, dimension, validity, manifest, trust, decision, report]);

  useEffect(() => {
    setLocalStudyId(crypto.randomUUID());
    Promise.all([
      api<{ solvers: Capability[] }>("/api/simulations/capabilities"),
      api<Array<Record<string, unknown>>>("/api/v2/scientific/solvers"),
    ]).then(([registry]) => setCapabilities(registry.solvers)).catch((error) => setMessage(error.message));
  }, []);

  useEffect(() => {
    if (!job || ["completed", "partial_failure", "failed", "cancelled"].includes(job.status)) return;
    pollingStarted.current ||= Date.now();
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;
    const poll = async () => {
      if (document.hidden) { timer = setTimeout(poll, 5000); return; }
      try {
        const next = await api<SimulationJob>(`/api/simulations/${job.simulation_id}`);
        if (!cancelled) { setJob(next); setLastRefresh(new Date()); setStale(false); }
      } catch { if (!cancelled) setStale(true); }
      const elapsed = Date.now() - pollingStarted.current;
      if (!cancelled) timer = setTimeout(poll, elapsed < 30000 ? 2000 : 5000);
    };
    timer = setTimeout(poll, 2000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [job]);

  function resetSolver(next: string) {
    const selected = capabilities.find((item) => item.solver_id === next);
    setSolverId(next); setMaterial(selected?.supported_materials[0] || ""); setDimension(selected?.supported_dimensions[0] || "");
    setGeometry({}); setBoundaries({}); setValidityInputs({}); setBenchmarkInputs({}); setValidity(null);
  }

  async function action(name: string, operation: () => Promise<void>) {
    setBusy(name); setMessage("");
    try { await operation(); } catch (error) { setMessage(error instanceof Error ? error.message : "The request could not be completed."); }
    finally { setBusy(""); }
  }

  async function parseDesign(generate = false) {
    await action(generate ? "generate" : "parse", async () => {
      const path = generate ? "/api/design/generate-single" : "/api/design/parse";
      const data = await api<Record<string, unknown>>(path, { method: "POST", body: JSON.stringify({ prompt }) });
      setDesign(generate ? data : { params: data.params, local_only: true });
      if (generate && typeof data.experiment_id === "string") setExperimentId(data.experiment_id);
    });
  }

  async function validateConfiguration() {
    if (!form) return;
    await action("validate", async () => {
      const response = await api<Validity>(`/api/v2/scientific/solvers/${solverId}/validate`, {
        method: "POST", body: JSON.stringify({ inputs: numberMap(form.validity, validityInputs) }),
      });
      setValidity(response); setStage("Validation");
    });
  }

  function simulationRequest() {
    if (!capability || !form) throw new Error("Choose a runnable solver.");
    return {
      solver_id: solverId, experiment_id: experimentId || null, design_id: null,
      material: { name: material },
      geometry: { dimension, ...numberMap(form.geometry, geometry) },
      boundary_conditions: numberMap(form.boundaries, boundaries),
      initial_conditions: {},
      numerical_settings: { max_iterations: 300, tolerance: 0.00001 },
    };
  }

  async function execute() {
    if (!validity || validity.status === "invalid") { setMessage("Execution is blocked until scientific validation passes."); return; }
    if (!experimentId) { setMessage("Generate and store a design first so later decisions and reports have a durable experiment owner."); return; }
    await action("execute", async () => {
      const request = simulationRequest();
      const idempotencyKey = crypto.randomUUID();
      const record = await api<EvidenceRecord>("/api/v2/execution/runs", {
        method: "POST",
        body: JSON.stringify({ data: {
          experiment_id: experimentId, solver_id: solverId, solver_version: capability?.version,
          normalized_scientific_inputs: validity.normalized_inputs,
          design_parameters: design?.params || {}, material_properties: { material },
          boundary_conditions: request.boundary_conditions,
          mesh_configuration: request.geometry,
          convergence_configuration: request.numerical_settings,
          simulation_request: request,
        }, idempotency_key: idempotencyKey }),
        idempotencyKey,
      });
      setManifest(record);
      const simulationId = record.payload.job_id as string;
      setJob(await api<SimulationJob>(`/api/simulations/${simulationId}`));
      pollingStarted.current = Date.now();
      setStage("Execution");
    });
  }

  async function loadResults() {
    if (!job) return;
    await action("results", async () => {
      setResults(await api<SimulationResults>(`/api/simulations/${job.simulation_id}/results`));
      setStage("Evidence");
    });
  }

  async function createTrust() {
    if (!job || !form || !validity) return;
    await action("trust", async () => {
      const record = await api<EvidenceRecord>("/api/v2/scientific/trust", {
        method: "POST", body: JSON.stringify({
          solver_id: solverId, inputs: validity.normalized_inputs,
          benchmark_inputs: numberMap(form.benchmark, benchmarkInputs),
          convergence_values: [1, 1, 1], experiment_id: experimentId, simulation_id: job.simulation_id,
        }),
      });
      setTrust(record);
    });
  }

  async function createDecision() {
    if (!results?.result || !trust) return;
    const metric = Object.keys(results.result.summary_metrics).find((key) => objectiveUnits[key]);
    if (!metric) { setMessage("This result does not expose a decision metric supported by the decision API."); return; }
    await action("decision", async () => {
      const record = await api<EvidenceRecord>("/api/v2/decisions", {
        method: "POST", body: JSON.stringify({
          experiment_id: experimentId,
          designs: [{
            design_id: design?.design_id || job?.simulation_id,
            metrics: results.result?.summary_metrics, parameters: design?.params || {},
            validity_status: trust.payload.validity?.status, confidence: trust.payload.confidence?.level,
            evidence_ids: [trust.id],
          }],
          objectives: [{ metric_code: metric, direction: objectiveUnits[metric].direction, weight: 1, unit: objectiveUnits[metric].unit, enabled: true }],
          constraints: [],
        }),
      });
      setDecision(record); setStage("Decision");
    });
  }

  async function createReasoning(level: "simple" | "engineering" | "research") {
    if (!trust) return;
    await action(`reasoning-${level}`, async () => {
      setReasoning(await api<EvidenceRecord>("/api/v2/reasoning", {
        method: "POST",
        body: JSON.stringify({
          experiment_id: experimentId,
          stage: "evidence_review",
          level,
          evidence_ids: [trust.id],
          context: { solver_id: solverId },
        }),
      }));
    });
  }

  async function decisionAction(choice: "accept" | "reject" | "request_modification") {
    if (!decision) return;
    await action(choice, async () => setDecision(await api<EvidenceRecord>(`/api/v2/decisions/${decision.id}/actions`, {
      method: "POST", body: JSON.stringify({ action: choice }),
    })));
  }

  async function createReport() {
    const evidence = [trust?.id, reasoning?.id, decision?.id].filter(Boolean);
    if (!evidence.length) return;
    await action("report", async () => {
      setReport(await api<EvidenceRecord>("/api/v2/reports", {
        method: "POST", body: JSON.stringify({ experiment_id: experimentId, title: "ASRE-Lab Engineering Study", evidence_ids: evidence }),
      }));
      setStage("Report");
    });
  }

  async function exportReport(format: "pdf" | "json" | "csv") {
    if (!report) return;
    await action(`download-${format}`, async () => {
      const artifact = await download(`/api/v2/reports/${report.id}/exports/${format}`);
      const url = URL.createObjectURL(artifact.blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `asre-report.${format}`; anchor.click(); URL.revokeObjectURL(url);
    });
  }

  async function exportDesignStl() {
    if (!design?.design_id) return;
    await action("download-stl", async () => {
      const artifact = await download(`/api/design/export/${encodeURIComponent(String(design.design_id))}`);
      const url = URL.createObjectURL(artifact.blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "asre-design.stl"; anchor.click(); URL.revokeObjectURL(url);
    });
  }

  return <div className="page">
    <div className="page-heading"><div><p className="eyebrow">NEW ENGINEERING STUDY</p><h1>Evidence-backed workflow</h1><p className="muted">Unsaved local study state · durable IDs appear only after a backend action succeeds.</p></div><span className="mono">LOCAL STUDY {localStudyId || "INITIALIZING"}</span></div>
    {message && <p className="error" role="alert">{message}</p>}
    <div className="workflow">
      <nav className="stage-rail" aria-label="Study stages">{stages.map((item) => <button key={item} className={`stage-button ${stage === item ? "active" : ""} ${completed.has(item) ? "complete" : ""}`} onClick={() => setStage(item)}>{item}</button>)}</nav>
      <section className="workspace panel">
        {stage === "Design" && <><p className="eyebrow">1 · DESIGN</p><h2>Define supported parametric geometry</h2><p className="muted">The design service currently supports pyramid, bridge, tower, arch, and dome prompt schemas. No arbitrary CAD mesh is implied.</p><div className="field"><label htmlFor="design-prompt">Design description</label><textarea id="design-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} minLength={3} /></div><div className="action-row"><button disabled={busy !== "" || prompt.length < 3} onClick={() => parseDesign(false)}>Parse parameters</button><button className="secondary" disabled={busy !== "" || prompt.length < 3} onClick={() => parseDesign(true)}>Generate and store design</button>{Boolean(design?.design_id) && <button className="secondary" disabled={busy !== ""} onClick={exportDesignStl}>Download private STL</button>}</div>{design && <pre className="panel mono">{JSON.stringify(design, null, 2)}</pre>}</>}
        {stage === "Physics" && <><p className="eyebrow">2 · PHYSICS</p><h2>Choose a registry-backed solver</h2><div className="field"><label htmlFor="solver">Runnable solver</label><select id="solver" value={solverId} onChange={(event) => resetSolver(event.target.value)}><option value="">Choose a solver</option>{capabilities.map((item) => <option key={item.solver_id} value={item.solver_id} disabled={item.implementation_status !== "real"}>{item.solver_id} · {item.implementation_status}</option>)}</select></div>{capability && form && <><div className="field"><label htmlFor="dimension">Dimension</label><select id="dimension" value={dimension} onChange={(event) => setDimension(event.target.value)}>{capability.supported_dimensions.map((item) => <option key={item}>{item}</option>)}</select></div><div className="field"><label htmlFor="material">Material</label><select id="material" value={material} onChange={(event) => setMaterial(event.target.value)}>{capability.supported_materials.map((item) => <option key={item}>{item}</option>)}</select></div><h3>Geometry</h3><Fields definitions={form.geometry} values={geometry} onChange={(key, value) => setGeometry((old) => ({ ...old, [key]: value }))} /><h3>Boundary conditions</h3><Fields definitions={form.boundaries} values={boundaries} onChange={(key, value) => setBoundaries((old) => ({ ...old, [key]: value }))} /><h3>Scientific validity inputs</h3><Fields definitions={form.validity} values={validityInputs} onChange={(key, value) => setValidityInputs((old) => ({ ...old, [key]: value }))} /><button disabled={busy !== ""} onClick={validateConfiguration}>Validate configuration</button></>}</>}
        {stage === "Validation" && <><p className="eyebrow">3 · VALIDATION</p><h2>Scientific validity check</h2>{!validity ? <div className="empty">Complete Physics and run the backend validity check.</div> : <><p className={validity.status === "invalid" ? "error" : validity.status === "valid_with_warnings" ? "warning" : "info"}><strong>{validity.status.replaceAll("_", " ")}</strong></p>{validity.rules.length ? <table className="technical-table"><thead><tr><th>Severity</th><th>Rule</th><th>Input</th><th>Correction</th></tr></thead><tbody>{validity.rules.map((rule) => <tr key={`${rule.code}-${rule.affected_input}`}><td>{rule.severity}</td><td>{rule.code}</td><td>{rule.affected_input}</td><td>{rule.suggested_correction}</td></tr>)}</tbody></table> : <p>No validity findings were returned.</p>}<button disabled={validity.status === "invalid" || busy !== ""} onClick={execute}>Seal manifest and execute</button></>}</>}
        {stage === "Execution" && <><p className="eyebrow">4 · EXECUTION</p><h2>Durable execution monitor</h2>{!job ? <div className="empty">A valid configuration is required before execution.</div> : <><div className="record"><div><span className="status">{job.status}</span><p className="mono">{job.simulation_id}</p></div><strong>{job.progress_percent}%</strong></div><p className={stale ? "warning" : "muted"}>{stale ? "Data is stale after a refresh failure." : lastRefresh ? `Last refreshed ${lastRefresh.toLocaleTimeString()}` : "Polling begins after dispatch."}</p>{job.safe_error_message && <p className="error">{job.safe_error_message}</p>}<div className="action-row">{!["completed", "partial_failure", "failed", "cancelled"].includes(job.status) && <button className="secondary" onClick={() => action("cancel", async () => setJob(await api(`/api/simulations/${job.simulation_id}/cancel`, { method: "POST" })))}>Cancel</button>}{["completed", "partial_failure"].includes(job.status) && <button onClick={loadResults}>Review evidence</button>}</div></>}</>}
        {stage === "Evidence" && <><p className="eyebrow">5 · EVIDENCE</p><h2>Results and Scientific Trust</h2>{!results?.result ? <div className="empty">Completed simulation results are required.</div> : <><div className="tabs" aria-label="Evidence sections"><span className="active">Results</span><span>Scientific Trust</span><span>Benchmark</span><span>Convergence</span><span>Artifacts</span><span>Reproduction</span><span>AI Reasoning</span></div><table className="technical-table"><caption>Solver result metrics</caption><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>{Object.entries(results.result.summary_metrics).map(([key, value]) => <tr key={key}><td>{key}</td><td>{value}</td></tr>)}</tbody></table><h3>Benchmark inputs</h3><Fields definitions={form?.benchmark || []} values={benchmarkInputs} onChange={(key, value) => setBenchmarkInputs((old) => ({ ...old, [key]: value }))} />{!trust ? <button onClick={createTrust} disabled={busy !== ""}>Create Scientific Trust evidence</button> : <><p className="info"><strong>Confidence: {trust.payload.confidence?.level}</strong> · Validity: {trust.payload.validity?.status} · Convergence: {trust.payload.convergence?.status}</p><p className="mono">Evidence {trust.id}</p><h3>Evidence-grounded explanation</h3><div className="action-row">{(["simple", "engineering", "research"] as const).map((level) => <button className="secondary" key={level} disabled={busy !== ""} onClick={() => createReasoning(level)}>{level[0].toUpperCase() + level.slice(1)}</button>)}</div>{reasoning && <p className="info">Reasoning evidence {reasoning.id} · {String(reasoning.payload.level)}</p>}<button onClick={createDecision}>Continue to decision support</button></>}</>}</>}
        {stage === "Decision" && <><p className="eyebrow">6 · DECISION</p><h2>Evidence-grounded human decision</h2><p className="warning">Correlation indicates association and does not establish physical causation. Rankings depend on selected objectives, constraints, and weights. Recommendations are decision support, not autonomous engineering approval. Human review is required.</p>{!decision ? <div className="empty">Create Scientific Trust evidence, then build a backend decision.</div> : <><p>{decision.payload.recommendation?.statement}</p><p className="mono">Decision {decision.id} · {decision.payload.status}</p>{decision.payload.status === "proposed" && <div className="action-row"><button onClick={() => decisionAction("accept")}>Accept</button><button className="secondary" onClick={() => decisionAction("request_modification")}>Request modification</button><button className="secondary" onClick={() => decisionAction("reject")}>Reject</button></div>}<button disabled={decision.payload.status === "proposed"} onClick={createReport}>Generate report</button></>}</>}
        {stage === "Report" && <><p className="eyebrow">7 · REPORT</p><h2>Private research report</h2>{!report ? <div className="empty">Record a human decision before generating the report.</div> : <><p className="info">Report generation completed synchronously.</p><p className="mono">Report {report.id}</p><div className="action-row">{(["pdf", "json", "csv"] as const).map((format) => <button className={format === "pdf" ? "" : "secondary"} key={format} onClick={() => exportReport(format)} disabled={busy !== ""}>Download {format.toUpperCase()}</button>)}</div><p className="muted">STEP and reproducibility ZIP downloads are omitted because no supported generic download route exists.</p></>}</>}
      </section>
      <aside className="context panel"><p className="eyebrow">SCIENTIFIC CONTEXT</p><h2>{capability?.solver_id || "Select a solver"}</h2>{capability ? <><p>{capability.geometry_limitations}</p><h3>Known limitations</h3><ul>{capability.known_limitations.map((item) => <li key={item}>{item}</li>)}</ul><h3>Governing equations</h3>{capability.governing_equations.map((item) => <p className="mono" key={item}>{item}</p>)}</> : <p className="muted">Capabilities and limitations load from the authenticated backend.</p>}</aside>
    </div>
  </div>;
}
