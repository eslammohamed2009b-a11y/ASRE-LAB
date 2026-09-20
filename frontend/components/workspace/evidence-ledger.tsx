import type { ScientificEvidenceRecord } from "@/lib/scientific-evidence";

export function EvidenceLedger({ records }: { records: ScientificEvidenceRecord[] }) {
  if (!records.length) return <section className="workspace-subsection evidence-ledger" aria-label="Evidence ledger"><h3>Evidence ledger</h3><p className="empty">No authoritative evidence records were returned for the selected simulation.</p></section>;
  return <section className="workspace-subsection evidence-ledger" aria-label="Evidence ledger"><h3>Evidence ledger</h3>{records.map(record => <details key={record.id} className="evidence-ledger-record"><summary><span>{record.record_type}</span><span>{record.status}</span><span className="mono">{record.id}</span></summary><dl><div><dt>Schema</dt><dd>{record.schema_version}</dd></div><div><dt>Created</dt><dd>{record.created_at || "Not returned"}</dd></div></dl><pre className="workspace-raw mono">{JSON.stringify(record.payload, null, 2)}</pre></details>)}</section>;
}
