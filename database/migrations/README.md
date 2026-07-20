# ASRE-LAB database migrations

> **Supabase CLI / GitHub Integration note:** this directory remains the
> human-authored, canonical source of the SQL. For Supabase CLI tooling
> and the Supabase GitHub Integration, the exact same SQL (byte-for-byte,
> no semantic changes) is also mirrored under
> [`backend/supabase/migrations/`](../../backend/supabase/migrations/)
> using Supabase's required chronological timestamp filenames
> (`YYYYMMDDHHMMSS_name.sql`), alongside
> [`backend/supabase/config.toml`](../../backend/supabase/config.toml).
> **`backend/supabase/migrations/` is what the Supabase GitHub Integration
> actually applies** (Working Directory = `backend`); this directory is
> kept as the readable, numbered original and for any tooling still
> pointed at it directly. If a migration is ever added or changed, update
> both locations together so they never diverge.

This is the **single authoritative** schema source for the project. The
old root-level `database/schema.sql` and `database/supabase_schema.sql`
files defined two divergent, contradictory full schemas (different table
sets, different column names for the same concepts) and have been
replaced with a deprecation notice pointing here.

## Order (must be applied in this exact numeric order)

1. `001_initial_schema.sql` — `profiles`, `experiments`, `design_models`,
   shared `set_updated_at()` trigger function, RLS.
2. `002_design_files.sql` — `design_files` (durable file ownership +
   storage location). Depends on 001.
3. `003_job_tracking.sql` — `generation_jobs` (async batch job tracking,
   including idempotency-key duplicate-request protection). Depends on
   001.
4. `004_simulation_jobs.sql` — `simulation_jobs` (Module 2 async simulation
   job tracking, mirrors `generation_jobs`). Depends on 001.
5. `005_simulation_inputs.sql` — `simulation_inputs` (immutable 1:1 request
   snapshot for a simulation job). Depends on 004.
6. `006_simulation_results.sql` — `simulation_results` (immutable 1:1
   persisted result contract: equations, assumptions, convergence,
   metrics, warnings, result-file object keys). Depends on 004.
7. `007_material_library.sql` — `material_library` (global, non-user-owned
   reference material properties with source/valid-range, mirroring
   `app/module2_simulation/materials.py`; seeded via this migration). No
   dependency on the simulation tables.

Each file is idempotent (`create table if not exists`, `create index if
not exists`, `drop policy/constraint if exists` before recreating) and
safe to re-run.

## How to apply

Run each file, in order, in the Supabase SQL editor (or via `psql`/any
Postgres client pointed at the project's connection string):

```
psql "$DATABASE_URL" -f database/migrations/001_initial_schema.sql
psql "$DATABASE_URL" -f database/migrations/002_design_files.sql
psql "$DATABASE_URL" -f database/migrations/003_job_tracking.sql
psql "$DATABASE_URL" -f database/migrations/004_simulation_jobs.sql
psql "$DATABASE_URL" -f database/migrations/005_simulation_inputs.sql
psql "$DATABASE_URL" -f database/migrations/006_simulation_results.sql
psql "$DATABASE_URL" -f database/migrations/007_material_library.sql
```

**These migrations have NOT been applied to any live Supabase project in
this session.** No live Supabase credentials are configured in this
environment (confirmed via repeated secret scans — see
`GO_NO_GO_CHECKLIST.md`). The SQL has been written and is believed
correct, but "written" is not the same claim as "applied and verified
against a real project" — that remains BLOCKED until real credentials are
available; see `backend/tests/external/`.

## Design notes

- `design_variants` was evaluated and judged unnecessary: `design_models`
  already has a unique `(experiment_id, variation_index)` key, which is
  the natural place to store one row per variant.
- `design_files.object_key` is never client-supplied; it is always
  generated server-side following
  `users/{user_id}/experiments/{experiment_id}/designs/{design_id}/{filename}`
  (see `app/core/storage.py`), and is unique.
- RLS policies exist on every table, but are documented as
  defense-in-depth, not the sole enforcement mechanism: the backend
  currently accesses Supabase through a single shared client without
  per-request user auth binding, so ownership is primarily enforced in
  application code (`app/core/repository.py`).
