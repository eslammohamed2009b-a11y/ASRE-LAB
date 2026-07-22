# ASRE-LAB

> **Proprietary Source-Available Project**
>
> This repository is public for technical inspection, portfolio evaluation,
> university review, and demonstration only. It is not open-source software.
> No permission is granted to use, copy, modify, redistribute, deploy, train
> AI systems on, or create derivative works from this project. All rights are
> reserved. See the [LICENSE](LICENSE) file.

## Repository Status

- **Public for inspection** — the repository is intentionally public so
  universities, reviewers, engineers, and competition judges can read the
  code and history.
- **Proprietary, all rights reserved** — public visibility is not an
  open-source release; see [LICENSE](LICENSE).
- **Active work in progress.**
- Capability status (validated / implemented-but-unvalidated / partial /
  unsupported / planned) is tracked honestly in the status table below and
  in [GO_NO_GO_CHECKLIST.md](GO_NO_GO_CHECKLIST.md) — public visibility must
  never be read as an implied claim that every listed feature is complete.

## Honest capability status

Legend: **Validated** = executed locally/remotely with real evidence and
passing. **Implemented, not externally validated** = code is real (no
stubs/placeholders) but has not been exercised against live third-party
infrastructure. **Partial** = some real functionality, known gaps.
**Unsupported** = explicitly returns an error rather than a fabricated
result. **Planned** = not yet built.

| Area | Status | Evidence |
|---|---|---|
| Module 1 — parametric CAD generation (real CadQuery/OCP kernel) | Validated locally | Real STEP/STL generation is covered by integration tests and real-HTTP E2E tests; files are persisted through `FileStorage`, not exposed as raw server paths. |
| Module 1 — ownership-isolated jobs and files | Validated locally | Persisted job/status/result APIs and file downloads fail closed with 404 across users. Real-HTTP E2E evidence includes checksum/size verification and a process restart against the same SQLite database and storage root. |
| Local backend test suite | Validated | Unit, integration, E2E, and benchmark marker suites pass in the pinned Python 3.11.15 / real-CadQuery environment. Current counts are recorded in `GO_NO_GO_CHECKLIST.md`. |
| Remote CI (GitHub Actions) | **Blocked — unknown cause** | Latest inspected run `29708734759` ended `startup_failure` in the same second it was created, with zero jobs and empty billable timing. GitHub returned no explicit cause. No billing/account-hold theory is treated as confirmed; remote tests have not run. |
| Module 2 — thermal solver | Implemented, not externally validated | Real finite-difference steady-state solver (no fabricated values), executed in benchmark tests against analytical/grid-convergence checks locally. Not run against live production infrastructure. |
| Module 2 — structural solver | Validated locally | Real 1D linear bar and Euler–Bernoulli cantilever finite-element solvers are benchmarked against analytical solutions. This is not 2D/3D arbitrary-CAD FEA. |
| Module 2 — modal solver | Validated locally | Real SDOF frequency and 1D cantilever generalized eigenvalue calculations are benchmarked analytically. SDOF is scalar-only; beam mode shapes are persisted. |
| Module 2 — bounded acoustic | Analytically validated locally | Real 1D frequency-domain Helmholtz duct solve with pressure amplitude/phase fields. It is not arbitrary-room or 3D acoustics. |
| Module 2 — bounded electrostatic | Analytically validated locally | Real 2D rectangular-grid Laplace/Poisson solve with potential and electric-field components. It is not an electromagnetic-wave solver. |
| Module 2 — bounded CFD | Analytically validated locally | Real fully developed laminar channel-flow finite-difference solve, validated against plane Poiseuille flow. It is not turbulence, external aerodynamics, arbitrary-CAD, or industrial CFD. |
| Thermal → structural coupling | Validated locally | One-way sequential steady linear coupling maps the persisted mean 1D temperature to explicit structural thermal strain; compatible 1D models only. |
| Reviewable Module 3 → Module 1 feedback | Validated locally | Persisted evidence-linked proposals require explicit acceptance before Module 1 generation and preserve parent/child iteration lineage. Proposed outcomes are not guarantees. |
| Scientific field results | Validated locally | Genuine thermal, structural, and beam-modal arrays are stored as bounded compressed NPZ artifacts with checksums, reproducibility hashes, safe keys, owner-scoped metadata, and integrity-checked loading. |
| Module 3 — deterministic engineering intelligence | Validated locally | Persisted datasets feed descriptive statistics, Pearson/Spearman association, first-order standardized linear sensitivity estimates, Pareto analysis, transparent ranking, and evidence-linked recommendations. Correlation is not causation and regression is not Sobol/global sensitivity. |
| Integrated Module 1 → 2 → 3 pipeline | Validated locally | Uses authoritative persisted designs, unified real solver jobs/fields, and persisted deterministic analysis. Thermal and structural runs are disclosed 1D comparison scenarios, not arbitrary-CAD mesh simulation or inferred service loading. |
| Persistence — durable ownership (SQLite local adapter) | Validated | Restart-durability and multi-instance-sharing proven with a real on-disk SQLite file (not `:memory:`), by unit tests. |
| Persistence — Supabase (live) | **Blocked** | Ordered migrations and repository/storage adapters exist, but no live credentials are available. External tests skip explicitly and are not counted as passing. |
| Async batch generation (Celery/Redis) | Implemented; queue transport unvalidated | Persisted jobs, progress, partial failure, cancellation, idempotency keys, per-user active-job limits, and result retrieval are tested in Celery eager mode. `docker-compose.yml` provides API + worker + Redis, but a real broker/worker run and load test remain blocked locally because Docker/Redis are unavailable. |
| Licensing | Validated | Proprietary, source-available [LICENSE](LICENSE); public repo, all rights reserved. |

The legacy `/api/simulate/*` and `/api/analyze/full-report` compatibility paths are
deprecated and isolated from the authoritative integrated pipeline. They must not be
used as evidence for unified solver or deterministic-intelligence capability.

## 1) Initialization and GitHub repository

Run these commands locally inside ASRE-LAB:

1. git init
2. git branch -M main
3. git add .
4. git commit -m "chore: initialize ASRE-LAB full-stack structure"
5. gh repo create ASRE-LAB --private --source=. --remote=origin --push

If you do not use GitHub CLI:

1. Create empty repo on GitHub named ASRE-LAB
2. git remote add origin https://github.com/<username>/ASRE-LAB.git
3. git push -u origin main

## 2) Project structure

- frontend: Next.js app
- backend: FastAPI app
- database: Supabase SQL schema
- .github/workflows/deploy.yml: CI/CD deploy pipeline

## 3) Database migrations

Apply every numbered migration in `database/migrations/` in ascending order. The current
authoritative sequence is `001` through `010`; do not stop at Migration 003. Migration 009
adds unified solver-result provenance and owner-scoped analyses; Migration 010 adds reviewable
design proposals and persistent iteration lineage.

See `database/migrations/README.md`. The legacy `database/schema.sql` and
`database/supabase_schema.sql` files are deprecated and must not be applied.

## 4) Local queue stack

`docker-compose.yml` defines the API, Celery worker, Redis broker/result
backend, shared SQLite persistence, and shared design-file storage:

```bash
docker compose up --build
```

Provide production secrets through the environment. Celery eager-mode tests
do not prove this separate-process stack; validate it in a Docker-capable
environment before production use.

## 5) CI/CD behavior

Workflow file:

- .github/workflows/deploy.yml

On push to main:

- frontend changes trigger build and deploy to Vercel
- backend changes trigger deploy hook on Render

Zero-downtime note:

- Render performs rolling deploy using health checks. Keep /health endpoint stable and pass health checks before traffic switch.

## 6) Required secrets in GitHub Actions

Add these in GitHub repository secrets:

- VERCEL_TOKEN
- VERCEL_ORG_ID
- VERCEL_PROJECT_ID
- RENDER_DEPLOY_HOOK_URL

## 7) Environment variables checklist

Vercel (frontend):

- NEXT_PUBLIC_FASTAPI_API_URL
- NEXT_PUBLIC_SUPABASE_URL
- NEXT_PUBLIC_SUPABASE_ANON_KEY

Render (backend):

- APP_ENV
- APP_DEBUG
- SUPABASE_URL
- SUPABASE_KEY
- SUPABASE_JWT_SECRET
- DATABASE_URL
- CORS_ALLOWED_ORIGINS
- JWT_SECRET_KEY
- JWT_ALGORITHM
- ACCESS_TOKEN_EXPIRE_MINUTES

Reference template:

- .env.example
