# Frontend environment

## Required frontend variables

```dotenv
NEXT_PUBLIC_FASTAPI_API_URL=http://127.0.0.1:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

These are public browser configuration values. Do not add service-role keys, JWT
secrets, database URLs, Redis URLs, or test-user passwords to the frontend.

## Backend variables

The backend requires its existing Supabase service-role configuration, JWT
verification secret, explicit CORS origins, PostgreSQL-backed Supabase project,
private storage bucket, Redis broker/result backend, and a separate Celery worker.
See `backend/.env.example`, the root `.env.example`, and
[`PRODUCTION_CONFIGURATION.md`](PRODUCTION_CONFIGURATION.md); never commit
`.env`.

## Local development

Install frontend dependencies inside `frontend/`, then run `npm run dev`. Run
FastAPI, Redis, and the Celery worker through the repository's existing Docker
Compose or documented local commands. Apply ordered migrations through
`013_accounts_and_founders.sql` to the disposable Supabase test project before
account validation.

Production builds use `npm run build`. No deployment is performed by this
integration task.
