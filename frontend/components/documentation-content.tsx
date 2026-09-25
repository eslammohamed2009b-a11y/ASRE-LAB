import Link from "next/link";

export function DocumentationContent({ workspace = false }: { workspace?: boolean }) {
  return <article className={workspace ? "page documentation-workspace" : "section documentation-public"}>
    <p className="eyebrow">DOCUMENTATION</p><h1>Engineering workflow reference</h1><p className="lede">Use ASRE-Lab to build controlled comparative studies while keeping models, evidence, decisions, and reports connected.</p>
    <div className="feature-grid">
      <section className="card"><h2>Authentication</h2><p>Email/password sessions are provided by Supabase. The browser sends the current access token to FastAPI for every private request.</p></section>
      <section className="card"><h2>Scientific validity</h2><p>Invalid inputs block execution. Warnings remain visible and travel with Scientific Trust evidence.</p></section>
      <section className="card"><h2>Execution</h2><p>Runs use durable IDs and idempotency keys. Polling slows after 30 seconds and stops at terminal states.</p></section>
      <section className="card"><h2>Private artifacts</h2><p>STL, NPZ, and report exports use authenticated downloads. Unsupported STEP and ZIP controls are absent.</p></section>
    </div>
    <p className="hero-actions"><Link className="button secondary" href={workspace ? "/app/dashboard" : "/scientific-scope"}>{workspace ? "Back to Studies" : "Review scientific scope"}</Link></p>
  </article>;
}
