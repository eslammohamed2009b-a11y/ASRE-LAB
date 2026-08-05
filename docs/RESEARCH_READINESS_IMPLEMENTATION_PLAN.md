# Research-readiness implementation plan

This internal plan is grounded in the `main` branch at `f55af643` and is intentionally ordered by research risk rather than presentation value.

1. Make the existing `experiments` record the durable, owner-scoped Research Study aggregate. Add collection/detail/update/archive APIs that assemble persisted designs, generation jobs, simulations, analyses, evidence, decisions, and reports without creating parallel storage.
2. Replace positional natural-language number parsing with semantic dimension parsing. Return provenance for resolved/defaulted/derived parameters, enforce pyramid base/height/slope consistency, and require the frontend to expose the resolved typed values before generation.
3. Replace uncontrolled random percentage variation with persisted deterministic design-space rules: linear, explicit values, bounded two-parameter grid, and seeded Latin-hypercube sampling. Persist every variation index and full resolved parameter set under the selected study.
4. Add a comparative execution contract that accepts selected persisted design IDs plus one server-validated physics scenario, records identical controlled inputs for every run, preserves partial failures, and produces one experiment dataset/analysis from successful runs.
5. Add one separately registered geometry-aware pyramid thermal model. Keep the existing integrated pipeline explicitly labelled as reduced-order/reference evaluation. Document the equation, mask/discretization, boundary conditions, validation envelope, convergence behavior, benchmark, and limitations.
6. Rebuild the authenticated study UI around server-authoritative study state: setup, inspectable parameters, design space, pre-run varies/held-constant review, batch execution, numerical evidence, persisted analysis, decision, report, and exports. The dashboard must list studies from the server, not `localStorage`.
7. Add unit, integration, owner-isolation, partial-failure, persistence, reproduction, and browser tests. Refresh generated OpenAPI types only from the implemented API.
8. After local gates pass, deploy through the existing Vercel + Hetzner/Celery/Redis/Supabase architecture and run the authenticated five-variant production acceptance study. Record real IDs and outputs; mark any gate without credentials or observable evidence as not verified.

## Audit baseline

- Existing and substantial: parametric CAD/STL/STEP storage, async generation jobs, solver registry and bounded solvers, immutable simulation inputs/results/fields, deterministic Module 3 analyses, evidence manifests/attempts/reproduction, decisions, reports, ownership checks, and private artifact routes.
- Backend-only or disconnected: experiment datasets/analysis, design batches, integrated pipeline, persisted experiment relationships, comparisons, reproduction, and most scientific-trust detail.
- Broken for the stated mission: fallback parsing uses the first metre-valued number as height; random design variation has no seed; the primary single-study UI hides parsed structure in JSON, creates a new experiment implicitly, submits `design_id: null`, and cannot reopen an experiment; dashboard study state is evidence-centric and includes device-local recent IDs.
- Scientifically incomplete: the pipeline maps height to a disclosed 1D reference model; the available 3D thermal mode is a cube, not the generated pyramid; no geometry-aware pyramid solver is registered.
- Operational caveat: production configuration exists for Vercel, Hetzner, Celery, Redis/Valkey, Supabase, Caddy/TLS, but live authenticated acceptance evidence must be regenerated after this release.
