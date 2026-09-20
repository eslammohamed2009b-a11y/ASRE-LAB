"use client";

import { Bounds, Grid, OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useEffect, useMemo, useState } from "react";
import { BufferGeometry, MeshStandardMaterial } from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { download } from "@/lib/api";

export type CanvasDesign = { id: string; variation_index: number; parameters: { base_length_m: number; height_m: number; slope_angle_deg: number; material: string }; files: Array<{ id: string; file_format: string }> };
export type CanvasSimulation = { id: string; design_id: string; solver_id: string; status: string; fields: Array<Record<string, unknown>>; result: { solver_version: string; summary_metrics: Record<string, number>; converged: boolean; warnings: string[] } | null };

type ViewMode = "geometry" | "result";

const knownMetricLabels: Record<string, { label: string; unit?: string }> = {
  max_temperature_c: { label: "Max temperature", unit: "°C" },
  max_displacement_m: { label: "Max displacement", unit: "m" },
  max_stress_pa: { label: "Max stress", unit: "Pa" },
  natural_frequency_hz: { label: "Natural frequency", unit: "Hz" },
};

function CadModel({ geometry, wireframe }: { geometry: BufferGeometry; wireframe: boolean }) {
  const material = useMemo(() => new MeshStandardMaterial({ color: "#aeb4a8", roughness: .78, metalness: .08, wireframe }), [wireframe]);
  useEffect(() => () => material.dispose(), [material]);
  return <mesh geometry={geometry} material={material} castShadow receiveShadow />;
}

function Viewport({ geometry, wireframe, viewportKey }: { geometry: BufferGeometry; wireframe: boolean; viewportKey: number }) {
  return <Canvas key={viewportKey} className="scientific-canvas-webgl" frameloop="demand" dpr={[1, 1.5]} camera={{ position: [3.5, 2.6, 3.5], fov: 38 }} fallback={<div className="scientific-canvas-fallback">3D rendering is unavailable. The design and result context remain available below.</div>}>
    <color attach="background" args={["#151614"]} />
    <ambientLight intensity={.7} />
    <directionalLight position={[4, 6, 4]} intensity={1.1} />
    <directionalLight position={[-4, 2, -3]} intensity={.35} />
    <Bounds fit clip observe margin={1.25}><CadModel geometry={geometry} wireframe={wireframe} /></Bounds>
    <Grid args={[10, 10]} cellSize={.5} cellThickness={.45} cellColor="#343831" sectionSize={2.5} sectionThickness={.8} sectionColor="#505749" fadeDistance={12} fadeStrength={1.4} position={[0, -1.35, 0]} />
    <OrbitControls makeDefault enableDamping dampingFactor={.08} />
  </Canvas>;
}

function statusText(status: string) { return status.replaceAll("_", " "); }

export function ScientificCanvas({ designs, simulations, solverId }: { designs: CanvasDesign[]; simulations: CanvasSimulation[]; solverId: string }) {
  const [designId, setDesignId] = useState("");
  const [runId, setRunId] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("geometry");
  const [wireframe, setWireframe] = useState(false);
  const [geometry, setGeometry] = useState<BufferGeometry | null>(null);
  const [loadState, setLoadState] = useState<"idle" | "loading" | "error">("idle");
  const [loadError, setLoadError] = useState("");
  const [viewportKey, setViewportKey] = useState(0);

  const selectedDesign = designs.find(item => item.id === designId) || designs[0] || null;
  const matchingRuns = selectedDesign ? simulations.filter(item => item.design_id === selectedDesign.id) : [];
  const selectedRun = matchingRuns.find(item => item.id === runId) || matchingRuns[0] || null;
  const stlFile = selectedDesign?.files.find(file => file.file_format.toLowerCase() === "stl") || null;
  const stlFileId = stlFile?.id;
  const activeSolver = selectedRun?.solver_id || solverId;
  const isPyramidThermal = activeSolver === "pyramid_thermal_conduction_v1";

  useEffect(() => { if (selectedDesign && selectedDesign.id !== designId) setDesignId(selectedDesign.id); }, [designId, selectedDesign]);
  useEffect(() => { if (selectedRun && selectedRun.id !== runId) setRunId(selectedRun.id); }, [runId, selectedRun]);

  useEffect(() => {
    let active = true;
    let parsed: BufferGeometry | null = null;
    setGeometry(null);
    setLoadError("");
    if (!stlFileId) { setLoadState("idle"); return () => { active = false; }; }
    setLoadState("loading");
    download(`/api/design/files/${encodeURIComponent(stlFileId)}/download`).then(async artifact => {
      const bytes = await artifact.blob.arrayBuffer();
      parsed = new STLLoader().parse(bytes);
      parsed.computeVertexNormals();
      parsed.center();
      if (active) { setGeometry(parsed); setLoadState("idle"); }
    }).catch(error => { if (active) { setLoadState("error"); setLoadError(error instanceof Error ? error.message : "The persisted STL artifact could not be loaded."); } });
    return () => { active = false; parsed?.dispose(); };
  }, [stlFileId]);

  async function downloadField(field: Record<string, unknown>) {
    const fieldId = typeof field.id === "string" ? field.id : "";
    if (!fieldId) return;
    const artifact = await download(`/api/simulations/${encodeURIComponent(selectedRun?.id || "")}/fields/${encodeURIComponent(fieldId)}/download`);
    const url = URL.createObjectURL(artifact.blob); const link = document.createElement("a");
    link.href = url; link.download = `${fieldId}.npz`; link.click(); URL.revokeObjectURL(url);
  }

  if (!selectedDesign) return <section className="scientific-canvas scientific-canvas-empty" aria-label="Scientific canvas"><p>No persisted design is available for the scientific canvas.</p></section>;

  return <section className="scientific-canvas" aria-label="Scientific canvas">
    <header className="scientific-canvas-toolbar">
      <label>Design<select aria-label="Persisted design" value={selectedDesign.id} onChange={event => { setDesignId(event.target.value); setRunId(""); }}><option value={selectedDesign.id}>Design {String(selectedDesign.variation_index + 1).padStart(2, "0")}</option>{designs.filter(item => item.id !== selectedDesign.id).map(item => <option key={item.id} value={item.id}>Design {String(item.variation_index + 1).padStart(2, "0")}</option>)}</select></label>
      <div className="scientific-canvas-modes" role="group" aria-label="Canvas mode"><button type="button" className={viewMode === "geometry" ? "active" : "secondary"} aria-pressed={viewMode === "geometry"} onClick={() => setViewMode("geometry")}>Geometry</button><button type="button" className={viewMode === "result" ? "active" : "secondary"} aria-pressed={viewMode === "result"} onClick={() => setViewMode("result")}>Result</button></div>
      <div className="scientific-canvas-tools"><button type="button" className="secondary" onClick={() => setWireframe(value => !value)} aria-pressed={wireframe}>{wireframe ? "Solid CAD" : "CAD triangulation"}</button><button type="button" className="secondary" onClick={() => setViewportKey(value => value + 1)}>Fit view</button></div>
    </header>
    <div className="scientific-canvas-viewport">
      {loadState === "loading" && <div className="scientific-canvas-state" role="status">Loading persisted STL artifact…</div>}
      {loadState === "error" && <div className="scientific-canvas-state error" role="alert">{loadError}</div>}
      {!stlFile && <div className="scientific-canvas-state">No persisted STL artifact is available for this design.</div>}
      {geometry && <Viewport geometry={geometry} wireframe={wireframe} viewportKey={viewportKey} />}
      {viewMode === "result" && <div className="scientific-canvas-result-hud" aria-label="Persisted result context">
        {selectedRun?.result ? <><p className="workspace-stage-kicker">PERSISTED RESULT</p><dl><div><dt>Run</dt><dd className="mono">{selectedRun.id}</dd></div><div><dt>Status</dt><dd>{statusText(selectedRun.status)}</dd></div><div><dt>Solver</dt><dd className="mono">{selectedRun.solver_id}</dd></div><div><dt>Solver version</dt><dd className="mono">{selectedRun.result.solver_version}</dd></div><div><dt>Iterative convergence</dt><dd>{String(selectedRun.result.converged)}</dd></div>{Object.entries(selectedRun.result.summary_metrics).map(([metric, value]) => <div key={metric}><dt>{knownMetricLabels[metric]?.label || metric}</dt><dd>{value}{knownMetricLabels[metric]?.unit ? ` ${knownMetricLabels[metric].unit}` : ""}</dd></div>)}</dl>{selectedRun.result.warnings.length > 0 && <p className="scientific-canvas-warning">Warnings: {selectedRun.result.warnings.join("; ")}</p>}<p className="scientific-canvas-field-notice">Spatial field visualization is unavailable from the current browser data contract. Numerical result remains available as persisted evidence.</p></> : <p className="scientific-canvas-field-notice">No persisted result is available for the selected design.</p>}
      </div>}
    </div>
    <footer className="scientific-canvas-footer"><span><b>CAD artifact</b> {stlFile ? "Persisted STL geometry" : "No STL artifact"} · <span className="mono">{selectedDesign.id}</span></span><span><b>Solver model</b> {isPyramidThermal ? "Masked Cartesian finite-difference pyramid model" : activeSolver}</span></footer>
    {isPyramidThermal && <p className="scientific-canvas-disclosure">The solver reconstructs its numerical domain from the persisted dimensions; the displayed STL is a design artifact, not the solver mesh.</p>}
    <div className="scientific-canvas-trace"><label>Persisted simulation<select aria-label="Persisted simulation" value={selectedRun?.id || ""} disabled={!matchingRuns.length} onChange={event => setRunId(event.target.value)}>{!matchingRuns.length && <option value="">No persisted simulation for this design</option>}{matchingRuns.map(run => <option key={run.id} value={run.id}>{run.id} · {statusText(run.status)}</option>)}</select></label>{selectedRun && <span>Design <span className="mono">{selectedDesign.id}</span> → Simulation <span className="mono">{selectedRun.id}</span> → {selectedRun.solver_id} → {statusText(selectedRun.status)}</span>}</div>
    {selectedRun?.fields.length ? <div className="scientific-canvas-fields"><p className="workspace-stage-kicker">FIELD ARTIFACT INVENTORY</p>{selectedRun.fields.map((field, index) => { const fieldId = typeof field.id === "string" ? field.id : `field-${index}`; const name = String(field.field_name || field.name || "field"); return <div key={fieldId}><span>{name}</span><span className="mono">{fieldId}</span>{typeof field.id === "string" && <button type="button" className="secondary" onClick={() => downloadField(field)}>Download NPZ</button>}</div>; })}</div> : null}
  </section>;
}
