# Production configuration

ASRE-Lab has three independently running application processes: the Next.js
frontend, the FastAPI API, and a Celery worker. They require a managed Redis
service and a Supabase project that provides Auth, PostgreSQL, and the private
`design-files` Storage bucket. Apply the ordered migrations through
`013_accounts_and_founders.sql` before accepting user traffic.

The API refuses to start with local SQLite/filesystem persistence or the
development localhost Redis defaults when `ENV=production`.

## Commands

Run the frontend from `frontend/`:

```text
npm ci
npm run build
npm run start
```

Run the API from `backend/` after installing `requirements.txt`:

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Run the worker from `backend/` using the same backend environment:

```text
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=2
```

## Environment variable names

Frontend (public browser configuration only):

- `NEXT_PUBLIC_FASTAPI_API_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

Backend API and worker (keep all values server-side):

- `ENV`
- `DEBUG`
- `ALLOWED_ORIGINS` — JSON array of exact frontend origins
- `SUPABASE_URL`
- `SUPABASE_KEY` — service-role key used only by the backend/worker
- `SUPABASE_JWT_SECRET` or `JWT_SECRET_KEY` — required only for legacy HS256 tokens; ES256 tokens use the Supabase JWKS endpoint
- `JWT_ALGORITHM`
- `SUPABASE_STORAGE_BUCKET`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `CELERY_BROKER_VISIBILITY_TIMEOUT`
- `PORT` — API host-provided port

Development-only fallback variables (never configure these for production):

- `LOCAL_PERSISTENCE_DB_PATH`
- `LOCAL_STORAGE_ROOT`
- `CELERY_TASK_ALWAYS_EAGER`

The repository does not use `DATABASE_URL`, `APP_ENV`, `APP_DEBUG`,
`CORS_ALLOWED_ORIGINS`, or `ACCESS_TOKEN_EXPIRE_MINUTES`; setting those names
does not configure the running backend.

For the supported single-VPS staging topology (Caddy, Next.js, FastAPI,
separate Celery worker, and persistent Redis), follow
[`VPS_STAGING_RUNBOOK.md`](VPS_STAGING_RUNBOOK.md).
