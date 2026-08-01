# Frontend integration

The production frontend is the Next.js 14 App Router application in `frontend/`.
It preserves the Stitch light scientific visual system while treating the FastAPI
OpenAPI document and solver registries as the source of truth.

## Routes

- `/` — public product and scientific-scope landing page.
- `/scientific-scope` — explicit supported and unsupported model boundaries.
- `/auth/sign-up`, `/auth/log-in` — Supabase email/password authentication.
- `/app/dashboard` — owner-scoped attempts, decisions, reports, and manifests.
- `/app/studies/new` — Design → Physics → Validation → Execution → Evidence →
  Decision → Report.
- `/app/open` — neutral owner-scoped resource lookup.
- `/app/{simulations,jobs,manifests,attempts,decisions,reasoning,reports}/[id]` —
  durable resource inspection and only the actions supported by each API.
- `/docs` — concise in-product workflow reference.

## API transport

`frontend/lib/api.ts` reads the current Supabase session, adds the bearer token,
normalizes safe API errors, supports `Idempotency-Key`, forwards abort signals
through standard `RequestInit`, and downloads private blobs without persisting
tokens or private URLs. The base URL is always
`NEXT_PUBLIC_FASTAPI_API_URL`.

## Workflow behavior

Solver availability, dimensions, materials, equations, boundary descriptions,
and limitations load from the backend registries. Frontend form definitions map
those capabilities to the current typed simulation schema. Scientific validation
is server authoritative and `invalid` blocks execution.

Execution creates a sealed V2 manifest and dispatches the real simulation with one
retained idempotency key. Polling runs every two seconds for the first 30 seconds,
then every five seconds; hidden pages reduce polling and terminal states stop it.
The last successful refresh and stale state remain visible.

Report exports use authenticated blob downloads for PDF, JSON, and CSV. STL and
NPZ remain available through their existing owner-scoped routes. Generic STEP and
reproducibility-ZIP download controls are omitted because no supported endpoint
exists.

## Deliberate limitations

There is no account-settings, billing, sharing, collaboration, payment, generic
project CRUD, public artifact link, WebSocket, SSE, active STEP download, or
reproducibility-ZIP download feature. `coupled_multiphysics_v0` is visible only as
planned backend metadata and cannot be executed.
