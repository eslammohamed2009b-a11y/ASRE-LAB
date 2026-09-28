# Product workflow and frontend

## Product goal

The frontend is not only a control panel for backend routes.

Its job is to help a researcher understand the current study, see what has already been persisted, know what the next valid action is, and keep scientific limits visible.

The current frontend is built with Next.js and React and is deployed on Vercel.

## Public product layer

The public side includes:

- landing page
- scientific scope
- product documentation
- authentication

The landing page explains the product, intended user, main workflow, and limits before login.

The scientific-scope page separates supported models from unsupported claims.

## Authenticated workspace

After login the user reaches a server-backed research dashboard.

The dashboard is used to:

- create a new Study
- reopen existing Studies
- see persisted study counts and status
- return to product documentation and scientific scope
- continue work after refresh or a later sign-in

Study data is not treated as temporary browser state.

## Study workspace

The main study workspace uses eight stages.

### 1. Question

The researcher records the study title, research question, optional hypothesis, and description.

The Study is persisted before the main workflow begins.

Empty or whitespace-only titles are rejected in the UI before an API request is sent.

### 2. Design

The user can start from a supported natural-language description such as:

```text
pyramid with a 2 m by 2 m base and 4 m height made of concrete
```

The server resolves that into structured parameters.

For the square-pyramid workflow the user can inspect:

- base length
- height
- slope
- material
- authoritative dimension pair

The product makes dependent values explicit instead of pretending every displayed field is independent.

The researcher can then define a design space using a linear sweep or explicit values.

The resolved design table is shown before generation.

Generated designs are presented with readable labels such as `Design 01` while the underlying UUID remains available for traceability.

Supported design artifacts include STEP and STL.

### 3. Physics

The researcher selects the supported solver and defines the physical case.

For the guided pyramid study this includes:

- material
- ambient temperature
- prescribed base temperature
- volumetric heat source
- grid resolution
- convergence tolerance
- maximum iterations

The UI keeps units visible.

### 4. Validation

Before execution the product builds a pre-run comparison.

It shows:

- selected designs
- what varies
- what is held constant
- solver
- material
- boundary conditions
- numerical settings
- model disclosure
- warnings and limits

For `pyramid_thermal_conduction_v1` the interface states that the solver reconstructs a structured Cartesian pyramid mask. The displayed STL is a design artifact and is not the solver mesh.

### 5. Run

The comparative simulation batch is executed asynchronously.

The interface shows:

- current status
- progress
- completed count
- failed or partial count
- persisted simulations
- result availability

A partial simulation does not make the full batch look successfully completed.

Completed results remain available if another simulation fails.

The browser can be refreshed without erasing the authoritative server state.

### 6. Evidence

This stage contains the result and analysis work.

The user can inspect:

- persisted metrics
- design to simulation mapping
- solver version
- iterative convergence
- governing equations and assumptions
- warnings
- field metadata
- analysis provenance
- dataset identity
- descriptive statistics
- associations
- first-order sensitivity where configured
- Pareto and ranking outputs where objectives are supplied
- persisted scientific evidence
- Scientific Trust

Correlation is shown as association, not causation.

Automatic comparative analysis and researcher-requested analysis are kept as separate persisted records.

Evidence follows the selected simulation and must not leak from another Study or simulation.

### 7. Decision

The system builds a decision basis from the persisted Study.

The researcher can review:

- candidate designs
- objectives
- numerical results
- analysis
- evidence
- Scientific Trust
- limitations

The product does not auto-approve a design.

The human explicitly chooses:

- accept
- reject
- request modification

The action is persisted.

### 8. Report

A report is generated after the human decision exists.

The in-app summary is visible before download.

The report can be exported as:

- PDF
- JSON
- CSV

Downloads use authenticated blob requests and readable failure messages.

The UI confirms that a download was started. It does not claim that the browser saved a file to disk.

## Persistence and recovery

The frontend is designed around server-authoritative state.

Important recovery behavior includes:

- refresh and reopen
- dashboard to Study navigation
- sign-out and later sign-in
- study A to study B switching
- protection against late responses from a previous Study
- async job polling that stops when the active Study changes

Study-specific state such as selected designs, evidence, trust, analysis, decision, report, and canvas selection is reset when the Study changes.

## Error handling

The product normalizes backend validation errors into readable text.

The interface avoids showing raw FastAPI validation objects, stack traces, or `[object Object]`.

Core actions expose visible errors rather than silently failing.

This includes:

- Study creation
- simulation and analysis actions
- report exports
- field artifact downloads

## Scientific canvas

The research workspace includes a visual engineering panel for persisted design and simulation context.

For the pyramid workflow it keeps two ideas separate:

- the CAD artifact shown to the researcher
- the numerical domain used by the solver

That distinction matters because the pyramid thermal solver does not consume the STL as a finite-element mesh.

## Frontend integration

Authenticated API traffic is sent through the Next.js proxy path to the production FastAPI service.

The browser obtains the Supabase session and adds the bearer token to protected requests.

Backend credentials and service-role keys are not exposed in browser configuration.

For lower-level integration details see:

- [Frontend integration](FRONTEND_INTEGRATION.md)
- [Frontend environment](FRONTEND_ENVIRONMENT.md)
- [Frontend testing](FRONTEND_TESTING.md)
