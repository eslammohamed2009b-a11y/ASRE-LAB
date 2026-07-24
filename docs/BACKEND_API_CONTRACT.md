# Frozen Backend API Contract

The authoritative backend contract is frozen at version `1.0.0`. The machine-readable
contract fingerprint and required-path manifest are in `backend/openapi-contract.json`.
`backend/tests/contract/test_openapi_contract.py` rejects unreviewed changes to the generated
OpenAPI document, authentication requirements, legacy-route deprecation, or core response types.

All `/api/*` endpoints require `OAuth2PasswordBearer`. `/health` and `/version` are public.
Owner-scoped resources deliberately return not-found behavior for non-owners. Scientific field
artifacts are retrieved only through authenticated backend routes; storage object keys are metadata,
not public URLs or raw filesystem paths.

Stable workflow surfaces:

- `/api/design/*` and `/api/jobs/*`: parametric generation, durable jobs, and protected exports.
- `/api/simulations/*`: typed solver jobs, results, capability metadata, and NPZ field retrieval.
- `/api/analyze/*`: deterministic persisted engineering intelligence.
- `/api/couplings/*`: one-way sequential thermal-to-structural coupling.
- `/api/design-feedback/*`: reviewable proposals, explicit acceptance/rejection/execution, and lineage.
- `/api/pipeline/*`: authoritative Module 1 -> 2 -> 3 forward orchestration.

Proposal states are `generated`, `accepted`, `rejected`, `superseded`, `executed`, and `failed`.
Iteration states are `planned`, `completed`, and `failed`. Simulation and job state definitions remain
in their generated OpenAPI schemas. The deprecated `/api/simulate/*` compatibility surface must not
be used as evidence for the authoritative pipeline.

Any intentional contract change must update implementation, tests, this document, and the snapshot
hash in one reviewed commit. Frontend implementation is outside this backend release batch.
