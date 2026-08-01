# Authentication and Founding Users

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
request. This is deliberate: the backend never trusts browser-computed eligibility.
PostgreSQL executes `provision_asre_account` as one transaction under an advisory
transaction lock. The insert is idempotent by primary key.

The sequence is `NO CYCLE` and never decremented. Values after 1,000 are consumed
but not assigned, so deletion cannot recycle a Founding User ordinal. The table
enforces a unique ordinal and a `1..1000` check.

Browser roles have `SELECT` only on their own row through RLS. They have no table
insert, update, or delete privilege and no sequence privilege. The provisioning
function accepts only the current authenticated user (or the backend service role)
and never accepts entitlement fields.

## Product semantics

- Permanent recognition: **Founding User — First 1,000**.
- Current temporary access: **Early Access — Unlimited Usage**.

Unlimited usage is not described as lifetime, free forever, or permanent access.
Non-founding accounts receive `standard` usage state.

## Safe account response

The account endpoint returns only user ID, token-backed email, Founding User status
and ordinal, usage access and period, and timestamps. It never returns password,
service-role, JWT, database, or other-user information.
