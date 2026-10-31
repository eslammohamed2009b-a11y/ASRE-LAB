# Authentication

## Authentication

Supabase Auth owns email/password registration, confirmation, login, refresh,
restoration, and logout. The browser uses only
`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`. Service-role and
JWT secrets are backend-only.

Protected application pages render a session-restoration gate before any private
content. Missing or expired sessions return to `/auth/log-in`; a safe same-origin
`returnTo` under `/app/` is restored after login. Every FastAPI `/api/*` request
is authenticated again on the server.

## Provisioning

`GET /api/v2/account/me` provisions the account on its first authenticated backend
request. This is deliberate: the backend never trusts browser-computed identity.
PostgreSQL executes `provision_asre_account` as an owner-scoped, idempotent
transaction.

Browser roles have `SELECT` only on their own row through RLS. They have no table
insert, update, or delete privilege. The provisioning function accepts only the
current authenticated user (or the backend service role) and never accepts
browser-supplied account state.

## Safe account response

The account endpoint returns only user ID, token-backed email, and timestamps. It
never returns password, service-role, JWT, database, or other-user information.
