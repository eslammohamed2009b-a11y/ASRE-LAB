# Frontend product and integration dossier

## Status

This document describes the implemented v1 frontend.

Earlier planning versions of this file described missing APIs and proposed screens that have since been replaced by the current Study workspace. The current product state is the source of truth for this document.

## Product role

The frontend gives a researcher one place to create, run, review, and reopen a controlled engineering Study.

It is built with Next.js and React and deployed on Vercel.

The primary product workflow is:

```text
Question
-> Design
-> Physics
-> Validation
-> Run
-> Evidence
-> Decision
-> Report
```

## Public pages

### Landing

Explains:

- what ASRE-Lab is
- who it is for
- the main study workflow
- scientific limits
- where to start

### Scientific scope

Explains the difference between supported bounded models and unsupported general claims.

### Documentation

Provides a researcher-facing guide before or after login.

### Authentication

Uses Supabase Auth in the browser.

The frontend does not create its own FastAPI password system.

## Dashboard

The authenticated dashboard reads persisted Study data from the backend.

It allows the user to:

- create a Study
- reopen a Study
- see current persisted counts and status
- return to scope and documentation

## Study creation

A Study contains the research context and becomes the parent record for designs, simulations, analyses, decisions, and reports.

The title is required.

Empty or whitespace-only titles are blocked in the UI with:

```text
Study title is required.
```

The frontend does not send the create request until the required title is valid.

A successful create requires a returned Study ID before navigation.

## Design experience

The main guided workflow uses a square pyramid.

The user can:

- enter a supported convenience description
- inspect structured parameters
- choose the authoritative dimension pair
- define a single-parameter sweep
- optionally define a two-parameter Cartesian design space
- preview resolved variants
- generate persisted CAD artifacts

Human labels such as `Design 01` are used in the main UI.

UUIDs remain visible where traceability matters.

## Physics experience

The current guided Study defaults to the geometry-aware pyramid thermal model.

The user declares:

- solver
- material
- boundary conditions
- grid resolution
- tolerance
- maximum iterations

Before execution the product builds a readable comparison plan.

It separates:

- what varies
- what is controlled
- model disclosure
- numerical settings
- boundary conditions

## Run experience

Comparative simulations run asynchronously.

The frontend polls durable job state and shows:

- status
- progress
- completed count
- failed or partial count

Terminal states include:

- completed
- partial_failure
- failed
- cancelled

Partial simulation outcomes do not count as clean completed simulations.

## Results and Evidence

Persisted simulations keep:

- design ID
- solver ID
- status
- result
- fields

The frontend presents metrics with units and keeps the technical simulation identity secondary to the design context.

Evidence views are scoped to the selected simulation.

The product can display numerical, field, validity, convergence, benchmark, refinement, and analysis evidence where those records exist.

## Analysis

The Study can contain automatic comparative analysis and later researcher-requested analyses.

The frontend distinguishes their provenance.

Analysis can show:

- data quality
- dataset hash
- descriptive statistics
- correlations
- first-order sensitivity
- Pareto output
- ranking
- warnings

Raw JSON is secondary to the readable summary.

## Scientific Trust

Trust is shown as a classification based on persisted scientific evidence.

The UI keeps these ideas separate:

- validity
- iterative convergence
- benchmark
- spatial refinement
- limitations

`LOW` Trust is not presented as a probability.

## Decision

The researcher reviews the decision basis and then explicitly records one action:

- accept
- reject
- request modification

The product does not auto-approve a design.

## Report

Reports are persisted records.

The Report stage shows a readable summary and supports authenticated:

- PDF export
- JSON export
- CSV export

The shared artifact helper:

- rejects empty blobs
- reads safe filenames from `Content-Disposition` when available
- attaches and clicks a temporary download element
- removes the element
- revokes the object URL after the click lifecycle
- surfaces a readable failure

## Persistence and state isolation

The Study page restores persisted state after reload.

When the active Study changes, Study-specific UI state is cleared.

Late responses and job polling are checked against the active Study before state is applied.

This prevents Study A from overwriting Study B after fast navigation.

## API integration

Protected requests use the current Supabase browser session.

The access token is added as:

```text
Authorization: Bearer <token>
```

The frontend calls the FastAPI service through the `/_asre-api` Next.js rewrite.

Public browser configuration contains only browser-safe public coordinates.

Backend service credentials remain server-side.

## Error behavior

API errors are normalized into readable text.

The frontend avoids exposing raw validation arrays, stack traces, storage paths, or `[object Object]`.

Core failures remain visible to the researcher.

## Current verification position

The final code-level engineering audit reported no known P0, P1, or material P2 defect after the latest fixes.

The final authenticated browser acceptance pass completed with **PASS** and no release blockers. The accepted v1 application-code baseline is `f5558ede291810f32040c856ff78eab0efd77c1d`.

See [Validation and release](VALIDATION_AND_RELEASE.md).
