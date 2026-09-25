type ScientificScopeContentProps = { workspace?: boolean };

export function ScientificScopeContent({ workspace = false }: ScientificScopeContentProps) {
  return <article className={`scientific-scope-content${workspace ? " scientific-scope-workspace" : ""}`}>
    <header className="scientific-scope-heading"><p className="eyebrow">SCIENTIFIC SCOPE</p><h1>Boundaries before buttons.</h1><p className="lede">Every runnable solver has a declared physical model, validity envelope, benchmark, assumptions, and exclusions. Capabilities are loaded from the service after sign-in.</p></header>
    <div className="scientific-scope-ledger">
      <section><p className="scientific-scope-label">SUPPORTED</p><p>Steady bounded conduction; straight 1D structural and modal models; straight lossless acoustic duct; rectangular electrostatics; laminar channel flow below Re 2000; one-way thermal-structural coupling.</p></section>
      <section><p className="scientific-scope-label">NOT SUPPORTED</p><p>Transient thermal models, radiation, turbulence, compressibility, general 3D FEA/CFD, plasticity, fatigue, buckling, arbitrary meshes, industrial certification, and autonomous approval.</p></section>
      <section><p className="scientific-scope-label">PLANNED ONLY</p><p><span className="status">UNAVAILABLE</span> coupled_multiphysics_v0 is registry metadata and cannot be executed.</p></section>
      <section><p className="scientific-scope-label">HUMAN RESPONSIBILITY</p><p>Rankings depend on chosen objectives, constraints, and weights. Recommendations remain decision support and require human review.</p></section>
    </div>
  </article>;
}
