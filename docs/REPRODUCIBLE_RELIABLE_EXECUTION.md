# Reproducible and reliable execution

Backend V2 represents a run as immutable revisions in the existing owner-scoped
engineering-evidence store. A draft may be sealed; sealed scientific inputs cannot be
changed. Changed inputs require clone-and-modify, which records old/new values, parent
and original lineage, recalculates checksums, and reruns bounded validity checks.

## Canonicalization and checksums

Mappings are key-sorted, nested values are normalized, finite floats use 15 significant
digits, and negative zero becomes zero. Secrets, credentials, temporary signed URLs,
and absolute host paths are rejected. Input checksums cover design, material, boundary,
mesh, convergence, normalized scientific input, and seed data. Execution checksums add
solver/model/version and source commit. Geometry and result checksums cover their
respective canonical records. Artifact and bundle checksums are SHA-256 over bytes.
Timestamps do not contribute to scientific input checksums.

Exact reproduction requires equal values. Numerical reproduction uses explicit
per-metric relative tolerances. Incompatible solver versions or physical models are
`not_comparable`; the system does not claim exact reproducibility across them.

## Attempts and lifecycle

Attempts are immutable events linked to the durable V1 job and manifest. Allowed stages
include queued, preparation, validation, solver execution, convergence, persistence,
checkpoint, cancellation, retry, and terminal outcomes. Invalid transitions fail
deterministically. Cancellation is owner-only and idempotent and cooperates with the
existing V1 simulation cancellation. Retry preserves prior attempts and is limited to
classified retryable failures. Resume is supported only from a checksum-valid version
1.0 checkpoint at a declared resumable stage whose artifacts still exist and match
their checksums.

The existing Celery late-acknowledgement and reject-on-worker-loss behavior remains the
redelivery mechanism. V2 adds durable attempt and `worker_lost` evidence; V1 continues
to prevent duplicate final rows and deterministic object keys prevent artifact-key
duplication. User-visible reconstruction reads only persisted records.

## Bundles, privacy, and limits

Reproducibility ZIPs have deterministic entry ordering and timestamps. They contain the
manifest, normalized inputs, geometry/material/boundary data, solver/trust metadata,
metrics, artifact inventory, lineage, instructions, and checksums. They use private,
owner-namespaced storage and never contain signed URLs, credentials, database/broker
configuration, or host paths.

Default limits are exposed by the authenticated policy endpoint: 25 variants, 100
parameters, grid size 60, three convergence levels, 3600 seconds, three attempts, 50
artifacts, 25 MB bundles, and the existing per-owner concurrency limit.

Failure categories include invalid input/geometry, unsupported validity envelope,
non-convergence, instability, resource limits, timeout, worker loss, invalid checkpoint,
artifact integrity, storage, cancellation, and unexpected internal failure. Responses
contain stable codes and corrective actions, never stack traces or infrastructure data.

These controls provide deterministic local and persisted reconstruction. They do not
promise byte-identical floating-point results across incompatible hardware, libraries,
solver versions, or nondeterministic workflows.
