import { trustPayload, type ScientificEvidenceRecord } from "@/lib/scientific-evidence";

const dimensionExplanations: Record<string, string> = {
  validity: "Whether authoritative validity evidence is available for this run.",
  benchmark: "Whether an authoritative benchmark comparison was performed. NOT_RUN means no such comparison was performed.",
  run_convergence: "Whether the numerical calculation itself reached its iterative stopping condition. This is not a spatial refinement study.",
  refinement: "Whether the solution was compared across deliberately different grid or resolution levels. NOT_RUN means no spatial refinement study was performed.",
};

function readableDimension(name: string) { return name === "run_convergence" ? "Run / iterative convergence" : name.replaceAll("_", " "); }

export function ScientificTrustPanel({ trust, disabled, deriving, onDerive }: { trust: ScientificEvidenceRecord | null; disabled?: boolean; deriving?: boolean; onDerive: () => void }) {
  const payload = trustPayload(trust);
  if (!payload) return <section className="workspace-subsection scientific-trust-panel" aria-label="Scientific Trust"><h3>Scientific Trust</h3><p>No persisted Scientific Trust record is available for the selected simulation. Scientific Trust is an evidence-state classification, not a generic confidence percentage.</p><button disabled={disabled || deriving} onClick={onDerive}>{deriving ? "Deriving Scientific Trust…" : "Derive Scientific Trust"}</button></section>;
  return <section className="workspace-subsection scientific-trust-panel" aria-label="Scientific Trust"><h3>Scientific Trust</h3><p><b>Classification</b> {payload.overall_trust}</p><p className="trust-explainer">This classification reflects the authoritative evidence available for this simulation. It is not a percentage and does not replace a human decision.</p><p className="mono">{trust?.id}</p><dl>{Object.entries(payload.dimensions).map(([name, dimension]) => <div key={name}><dt>{readableDimension(name)}</dt><dd><strong>{dimension.state}</strong>{dimension.warning ? ` · ${dimension.warning}` : ""}<br /><span>{dimensionExplanations[name] || "Evidence-state information for this dimension."}</span><br /><span className="mono">{dimension.evidence_ids.join(", ") || "No linked evidence"}</span></dd></div>)}</dl><p><b>Reason code</b> {payload.reason_code || "Not returned"}</p><p><b>Linked records</b> {payload.evidence_ids.length}</p>{payload.limitations.length > 0 && <p><b>Limitations</b> {payload.limitations.join("; ")}</p>}<p className="mono">Result hash: {payload.result_hash || "Not returned"}<br />Trust hash: {payload.trust_hash || "Not returned"}</p></section>;
}
