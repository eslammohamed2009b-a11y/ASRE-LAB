"use client";

import { useEffect, useState } from "react";
import { api, ApiError, download } from "@/lib/api";

const resourcePaths = {
  simulation: "/api/simulations/",
  job: "/api/jobs/",
  manifest: "/api/v2/execution/manifests/",
  attempt: "/api/v2/execution/attempts/",
  decision: "/api/v2/decisions/",
  reasoning: "/api/v2/reasoning/",
  report: "/api/v2/reports/",
} as const;

export function ResourceDetail({ kind, id }: { kind: keyof typeof resourcePaths; id: string }) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [comparisonId, setComparisonId] = useState("");
  const [fields, setFields] = useState<Array<Record<string, unknown>>>([]);
  useEffect(() => {
    let active = true;
    api<Record<string, unknown>>(resourcePaths[kind] + encodeURIComponent(id))
      .then((result) => { if (active) setData(result); })
      .catch((reason) => { if (active) setError(reason instanceof ApiError && reason.status === 404 ? "This item does not exist or is not available to this account." : reason.message); });
    if (kind === "simulation") {
      api<Array<Record<string, unknown>>>(`/api/simulations/${encodeURIComponent(id)}/fields`)
        .then((result) => { if (active) setFields(result); })
        .catch(() => {});
    }
    return () => { active = false; };
  }, [kind, id]);

  async function mutate(name: string, path: string, body?: unknown) {
    setBusy(name); setError("");
    try { setData(await api<Record<string, unknown>>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The action could not be completed."); }
    finally { setBusy(""); }
  }

  async function reportExport(format: "pdf" | "json" | "csv") {
    setBusy(format);
    try {
      const result = await download(`/api/v2/reports/${id}/exports/${format}`);
      const url = URL.createObjectURL(result.blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `asre-report.${format}`; anchor.click(); URL.revokeObjectURL(url);
    } finally { setBusy(""); }
  }

  async function fieldDownload(fieldId: string) {
    setBusy(fieldId);
    try {
      const result = await download(`/api/simulations/${encodeURIComponent(id)}/fields/${encodeURIComponent(fieldId)}/download`);
      const url = URL.createObjectURL(result.blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${fieldId}.npz`; anchor.click(); URL.revokeObjectURL(url);
    } finally { setBusy(""); }
  }

  const payload = data?.payload && typeof data.payload === "object" ? data.payload as Record<string, unknown> : data;
  const state = String(payload?.stage || payload?.status || data?.status || "");
  return <div className="page">
    <div className="page-heading"><div><p className="eyebrow">{kind.toUpperCase()}</p><h1>Owner-scoped resource</h1><p className="mono">{id}</p></div>{state && <span className="status">{String(state).replaceAll("_", " ")}</span>}</div>
    {error && <p className="error" role="alert">{error}</p>}
    {!data && !error ? <section className="panel">Loading resource…</section> : data && <section className="panel">
      <div className="action-row">
        {kind === "simulation" && !["completed", "partial_failure", "failed", "cancelled"].includes(state) && <button className="secondary" disabled={!!busy} onClick={() => mutate("cancel", `/api/simulations/${id}/cancel`)}>Cancel</button>}
        {kind === "job" && !["completed", "partial_failure", "failed", "cancelled"].includes(state) && <button className="secondary" disabled={!!busy} onClick={() => mutate("cancel", `/api/jobs/${id}/cancel`)}>Cancel</button>}
        {kind === "attempt" && !["completed", "partially_completed", "failed", "cancelled"].includes(state) && <button className="secondary" disabled={!!busy} onClick={() => mutate("cancel", `/api/v2/execution/attempts/${id}/cancel`)}>Cancel</button>}
        {kind === "attempt" && state === "failed" && <button disabled={!!busy} onClick={() => mutate("retry", `/api/v2/execution/attempts/${id}/retry`, { idempotency_key: crypto.randomUUID() })}>Retry</button>}
        {kind === "attempt" && ["checkpointed", "running_solver"].includes(state) && <button disabled={!!busy} onClick={() => mutate("resume", `/api/v2/execution/attempts/${id}/resume`)}>Resume</button>}
        {kind === "manifest" && <button disabled={!!busy} onClick={() => mutate("clone", `/api/v2/execution/manifests/${id}/clone`, { changes: {} })}>Clone</button>}
        {kind === "manifest" && <button disabled={!!busy} onClick={() => mutate("reproduce", `/api/v2/execution/manifests/${id}/reproduce`, { idempotency_key: crypto.randomUUID() })}>Reproduce</button>}
        {kind === "manifest" && <button className="secondary" disabled={!!busy} onClick={() => mutate("bundle", `/api/v2/execution/manifests/${id}/bundle`)}>Build bundle metadata</button>}
        {kind === "report" && (["pdf", "json", "csv"] as const).map((format) => <button className={format === "pdf" ? "" : "secondary"} key={format} disabled={!!busy} onClick={() => reportExport(format)}>Download {format.toUpperCase()}</button>)}
      </div>
      {kind === "manifest" && <div className="field"><label htmlFor="comparison-manifest">Other owner-scoped manifest ID</label><input id="comparison-manifest" value={comparisonId} onChange={(event) => setComparisonId(event.target.value)} /><button className="secondary" disabled={!!busy || !comparisonId} onClick={() => mutate("compare", `/api/v2/execution/manifests/${id}/compare`, { other_manifest_id: comparisonId, tolerances: {} })}>Compare manifests</button></div>}
      {kind === "simulation" && fields.length > 0 && <div><h2>Private field artifacts</h2><div className="action-row">{fields.map((field) => { const fieldId = String(field.id); return <button className="secondary" key={fieldId} disabled={!!busy} onClick={() => fieldDownload(fieldId)}>Download {String(field.field_name || "field")} NPZ</button>; })}</div></div>}
      <pre className="mono" aria-label={`${kind} data`}>{JSON.stringify(data, null, 2)}</pre>
    </section>}
  </div>;
}
