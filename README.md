# ASRE-LAB

New standalone project scaffold prepared separately from HydroSentinel.

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

## 3) Supabase SQL schema

Use this file in Supabase SQL Editor:

- database/supabase_schema.sql

It creates:

- users
- experiments
- simulation_results

## 4) CI/CD behavior

Workflow file:

- .github/workflows/deploy.yml

On push to main:

- frontend changes trigger build and deploy to Vercel
- backend changes trigger deploy hook on Render

Zero-downtime note:

- Render performs rolling deploy using health checks. Keep /health endpoint stable and pass health checks before traffic switch.

## 5) Required secrets in GitHub Actions

Add these in GitHub repository secrets:

- VERCEL_TOKEN
- VERCEL_ORG_ID
- VERCEL_PROJECT_ID
- RENDER_DEPLOY_HOOK_URL

## 6) Environment variables checklist

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
