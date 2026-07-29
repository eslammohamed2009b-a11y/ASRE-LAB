# Decisions, AI Reasoning, and research outputs

Backend V2 evaluates only declared metrics with compatible units. Objectives use
minimize/maximize directions and non-negative weights. Constraints use explicit
operators and are evaluated before ranking. Invalid, infeasible, and
insufficient-evidence designs cannot outrank feasible designs.

Seeded Latin Hypercube Sampling produces at most 25 bounded variants. Correlation
sensitivity supports Pearson and Spearman coefficients, missing-value filtering, and
constant-variable warnings. Correlation indicates association and does not prove
physical causation.

Pareto membership is calculated only for feasible designs. Weighted ranking uses
feasible-set min-max normalization; equal objective values receive 0.5 and do not
artificially distinguish designs. Every score includes its raw value, normalized value,
direction, normalized weight, and contribution. Rankings and recommendations depend on
user-selected objectives, constraints, and weights; they are decision support, not
autonomous engineering approval.

Recommendations require an explicit owner action: accept, reject, or request
modification. Acceptance records immutable source/child lineage. Repeated identical
actions are idempotent, and rejected recommendations cannot execute.

## AI Reasoning safety

AI Reasoning means deterministic explanations of persisted engineering evidence. It is
not hidden chain-of-thought and never exposes or claims private model reasoning. Simple,
engineering, and research levels share the same evidence IDs. Missing evidence returns
an explicit insufficient-evidence response. The system works without an external AI
provider; no provider controls facts, feasibility, ranking, confidence, or reports.

## Reports and exports

Reports include only populated evidence-backed sections: experiment identity,
scientific trust, run/reproduction metadata, decision analysis, explanation snapshots,
and safe failure information. Each section identifies its evidence records. Private,
owner-namespaced artifacts provide deterministic JSON, CSV, and compact PDF exports,
each with a SHA-256 checksum. Existing STEP, STL, NPZ, and reproducibility artifacts
remain available through their existing owner-scoped APIs and inventories.

Reports exclude secrets, signed URLs, database/broker configuration, host paths, and
unrelated users. They document bounded research models and are not industrial
certification or academic-paper generation.
