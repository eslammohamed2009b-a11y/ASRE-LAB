# Frontend and full-stack testing

## Frontend checks

From `frontend/`:

```text
npm run typecheck
npm run lint
npm test
npm run build
npm run test:e2e
```

Vitest covers bounded landing claims, authenticated transport behavior, stable
error codes, idempotency headers, and the seven-stage workflow surface. Playwright
covers public navigation, automated accessibility scanning, and the real Supabase
login/session/logout path when disposable E2E credentials are configured.

## Backend checks

From the repository root with `backend/` on `PYTHONPATH`, run the existing unit,
integration, E2E, external Supabase, migration, and contract suites. New account
tests cover:

- concurrent unique ordered allocation;
- idempotent duplicate provisioning;
- deletion without ordinal reuse;
- no ordinal above 1,000;
- exact migration mirror bytes;
- owner-scoped empty collections and pagination;
- deterministic sorting and two-user isolation.

## Real-service gate

The final E2E gate is not mocked. It requires FastAPI, migrated Supabase/PostgreSQL,
Supabase Auth users, private file storage, Redis, and a separate Celery worker.
The Playwright test additionally requires `E2E_USER_EMAIL` and
`E2E_USER_PASSWORD`. A missing credential skips the real-auth case and must be
reported as blocked, never passed.

Disposable records and test accounts must be removed after the gate. Never run
destructive cleanup against a non-test Supabase project.
