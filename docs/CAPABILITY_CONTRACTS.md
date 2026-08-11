# Capability contracts

ASRE-Lab exposes one machine-readable, authenticated capability inventory at
`GET /api/capabilities`. It is derived directly from the implementation
registries; it is not a separately maintained product matrix.

- Design execution is governed by `module1_design.capability_registry`.
  `arch` and `dome` are recognised language concepts but are explicitly
  `understood_but_unsupported`; they cannot reach a CAD generator.
- Physics execution is governed by `module2_simulation.SOLVER_REGISTRY`.
  The older `/api/simulate` endpoints are deprecated compatibility routes and
  derive their executable status from that registry.
- The authoritative research-analysis path is the persisted deterministic
  `/api/analyze/experiments/{experiment_id}` workflow. The stateless
  `/api/analyze/full-report` clustering/correlation/LLM synthesis endpoint is
  legacy and non-authoritative.
- Scientific trust entries are cross-checked against real solvers or the
  explicitly bounded one-way thermal-structural workflow.

`capability_validation.validate_capability_consistency()` is run at API startup
and unit tested. It rejects registry drift such as a real solver without an
implementation, benchmark metadata for validated status, scientific-trust
mapping, limitations, version, or governing equations.
