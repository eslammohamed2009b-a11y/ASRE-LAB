import Link from "next/link";

const workflow = [
  ["Create a Research Study", "State the question, context, and hypothesis you want to investigate.", "ASRE-Lab creates an owner-scoped study record that keeps later artifacts together.", "A durable study record is ready to reopen.", "Define the design you want to compare."],
  ["Define the research question", "Describe the engineering question and, if useful, a hypothesis.", "ASRE-Lab records this as the study context; it does not infer a scientific claim.", "A stated question that frames the comparison.", "Set the controlled design parameters."],
  ["Set design parameters", "Choose the supported geometry, dimensions, material, and which parameter varies.", "ASRE-Lab resolves the supported parameter relationships before generation.", "A defined design space and its held-constant values.", "Generate the CAD variants."],
  ["Generate CAD variants", "Create the persisted design artifacts for the planned variations.", "ASRE-Lab generates supported CAD artifacts only. This is not a physics run.", "Persisted design variants, each with a human-readable identity.", "Configure the supported physics model."],
  ["Configure supported physics", "Choose a registry-backed model and the bounded inputs it supports.", "ASRE-Lab validates that configuration against the model's declared limits.", "A pre-run comparison plan showing what varies and what is held constant.", "Run the comparative simulations."],
  ["Run comparative simulations", "Confirm the plan and run the selected persisted designs.", "ASRE-Lab creates durable simulation records and retains their result and evidence trail.", "Completed, failed, or partial run records — never an implied result.", "Inspect the persisted results."],
  ["Inspect Results", "Read the reported metrics, units, solver status, warnings, and exportable records.", "ASRE-Lab displays only persisted result values for the selected run.", "Comparable result records; iterative convergence is shown separately from refinement.", "Run or inspect Analysis."],
  ["Run or inspect Analysis", "Choose the allowed comparison objective and inspect the persisted analysis if one exists.", "ASRE-Lab analyzes the completed dataset and stores the result without overwriting its provenance.", "An analysis record, data-quality information, and limitations.", "Review the associated Evidence."],
  ["Review Evidence", "Inspect the authoritative records behind a selected result and its Scientific Trust classification.", "ASRE-Lab links persisted validity, benchmark, convergence, and other available evidence.", "An evidence trail and its declared limitations.", "Understand Scientific Trust before making a decision."],
  ["Understand Scientific Trust", "Read the classification and each evidence dimension in plain language.", "ASRE-Lab reports available evidence; it does not turn trust into a percentage or approval.", "A traceable classification with missing or not-run evidence made explicit.", "Make the human decision."],
  ["Make a Human Decision", "Review the candidate, objective, results, analysis, evidence, trust, and limitations, then accept, reject, or request changes.", "ASRE-Lab records your action but never approves engineering work automatically.", "A human decision record.", "Generate the research report after a recorded decision."],
  ["Generate the Research Report", "Review the in-product report summary and export the recorded report when ready.", "ASRE-Lab packages only available persisted records and their limitations.", "A private research report and its export formats.", "Reopen the study whenever you need its traceability."],
] as const;

const glossary = [
  ["Research Study", "The durable container for one question, its designs, simulations, evidence, decisions, and reports."],
  ["Design Variant", "One persisted version of the supported geometry with its declared parameters."],
  ["Simulation", "A numerical evaluation of one persisted design using a supported model."],
  ["Analysis", "A persisted comparison of completed simulation records; it is distinct from the simulation itself."],
  ["Evidence", "Authoritative records that document inputs, results, validation, and other traceability facts."],
  ["Scientific Trust", "An evidence-state classification. It reflects available evidence, not a generic confidence percentage."],
  ["Benchmark", "An authoritative comparison against a declared reference, when one was performed."],
  ["Refinement", "A comparison across deliberately different spatial grid or resolution levels."],
  ["Run Convergence", "Whether the numerical calculation reached its own iterative stopping condition."],
  ["Human Decision", "A required researcher action to accept, reject, or request a modification; it is never automatic."],
  ["Research Report", "A private, persisted report assembled from the available study records."],
] as const;

export function DocumentationContent({ workspace = false }: { workspace?: boolean }) {
  return <article className={workspace ? "page documentation-workspace documentation-guide" : "section documentation-public documentation-guide"}>
    <header className="documentation-heading"><p className="eyebrow">USER GUIDE</p><h1>From a research question to a traceable engineering record</h1><p className="lede">ASRE-Lab helps you run controlled comparative studies with supported designs and physics models, while keeping results, evidence, human decisions, and reports connected.</p></header>
    <section className="documentation-overview"><h2>What ASRE-Lab does</h2><p>Use ASRE-Lab to define a bounded engineering question, create controlled CAD design variants, evaluate them with supported physics, compare persisted results, and document the evidence behind a human decision. It does not claim to replace general-purpose CAD, FEA, CFD, or multiphysics software.</p></section>
    <section className="documentation-workflow" aria-label="Research workflow"><p className="eyebrow">WORKFLOW</p><h2>How a study moves forward</h2>{workflow.map(([title, researcher, system, output, next], index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><div><h3>{title}</h3><dl><dt>You do</dt><dd>{researcher}</dd><dt>ASRE-Lab does</dt><dd>{system}</dd><dt>Output</dt><dd>{output}</dd><dt>Next</dt><dd>{next}</dd></dl></div></article>)}</section>
    <section className="documentation-distinction"><h2>Important distinctions</h2><dl><div><dt>CAD generation ≠ physics simulation</dt><dd>CAD generation creates design artifacts. A physics simulation is a separate numerical evaluation of a selected persisted design.</dd></div><div><dt>Simulation convergence ≠ spatial refinement</dt><dd>Run or iterative convergence means the numerical calculation reached its iteration stopping condition. Spatial refinement means the solution was compared across deliberately different grid or resolution levels. They are not equivalent.</dd></div><div><dt>Correlation ≠ causation</dt><dd>An association in an analysis may support comparison, but it does not establish a physical cause.</dd></div><div><dt>Trust ≠ percentage confidence</dt><dd>Scientific Trust reflects the available evidence and its limitations. It is not a generic percentage and does not replace human review.</dd></div></dl></section>
    <section className="documentation-glossary"><p className="eyebrow">GLOSSARY</p><h2>Terms used in the workspace</h2><dl>{glossary.map(([term, definition]) => <div key={term}><dt>{term}</dt><dd>{definition}</dd></div>)}</dl></section>
    <section className="documentation-notes"><h2>Technical notes</h2><p>The workspace may expose identifiers, hashes, exports, and implementation details for traceability. Those are secondary to the research workflow. Authentication, artifact access, and records remain owner-scoped.</p></section>
    <p className="hero-actions"><Link className="button secondary" href={workspace ? "/app/dashboard" : "/scientific-scope"}>{workspace ? "Back to Studies" : "Review scientific scope"}</Link></p>
  </article>;
}
