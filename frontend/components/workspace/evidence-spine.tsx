import type { ScientificEvidenceRecord } from "@/lib/scientific-evidence";

type SpineStage = "Question" | "Design" | "Physics" | "Run" | "Evidence" | "Trust" | "Decision" | "Report";

export function EvidenceSpine({ question, designId, solverId, solverVersion, simulationId, simulationStatus, records, trust, decisionStatus, reportId, onStage }: { question: string; designId?: string; solverId: string; solverVersion?: string; simulationId?: string; simulationStatus?: string; records: ScientificEvidenceRecord[]; trust: ScientificEvidenceRecord | null; decisionStatus?: string; reportId?: string; onStage: (stage: SpineStage) => void }) {
  const nodes: Array<[SpineStage, string]> = [
    ["Question", question || "No persisted question"], ["Design", designId || "No selected design"], ["Physics", solverId ? `${solverId}${solverVersion ? ` · ${solverVersion}` : ""}` : "No configured solver"], ["Run", simulationId ? `${simulationStatus || "persisted"} · ${simulationId}` : "No selected simulation"], ["Evidence", `${records.length} authoritative record${records.length === 1 ? "" : "s"}`], ["Trust", trust ? "Persisted Scientific Trust" : "Not derived"], ["Decision", decisionStatus || "No decision record"], ["Report", reportId || "No report record"],
  ];
  return <section className="evidence-spine" aria-label="Evidence Spine"><p className="workspace-stage-kicker">EVIDENCE SPINE</p><div>{nodes.map(([stage, value]) => <button type="button" key={stage} onClick={() => onStage(stage)}><b>{stage}</b><span>{value}</span></button>)}</div></section>;
}
