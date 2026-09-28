# ASRE-Lab documentation

This folder contains two kinds of documentation.

The first group explains the project to a reader who wants to understand what was built and why. The second group contains deeper engineering references used during implementation and validation.

## Start here

| Document | What it covers |
| --- | --- |
| [Project overview](PROJECT_OVERVIEW.md) | Problem, origin, core idea, current product, and project boundaries |
| [Product workflow](PRODUCT_WORKFLOW.md) | Frontend, user journey, study stages, persistence, and downloads |
| [Scientific scope](SCIENTIFIC_SCOPE.md) | Supported physics, model limits, evidence, and Scientific Trust |
| [Architecture](ARCHITECTURE.md) | Frontend, API, workers, storage, deployment, and data flow |
| [Validation and release](VALIDATION_AND_RELEASE.md) | Test strategy, final engineering audit, known gaps, and release state |
| [Research quickstart](../REAL_RESEARCH_QUICKSTART.md) | Short walkthrough of a real controlled pyramid study |

## Product and frontend references

- [Frontend product and integration dossier](FRONTEND_PRODUCT_AND_INTEGRATION_DOSSIER.md)
- [Frontend integration](FRONTEND_INTEGRATION.md)
- [Frontend environment](FRONTEND_ENVIRONMENT.md)
- [Frontend testing](FRONTEND_TESTING.md)
- [Decision, reasoning, and research outputs](DECISION_REASONING_RESEARCH_OUTPUTS.md)

## Scientific and execution references

- [Scientific Trust](SCIENTIFIC_TRUST.md)
- [Scientific capability details](SCIENTIFIC_CAPABILITY_GAPS.md)
- [Capability contracts](CAPABILITY_CONTRACTS.md)
- [Reproducible and reliable execution](REPRODUCIBLE_RELIABLE_EXECUTION.md)
- [Backend API contract](BACKEND_API_CONTRACT.md)

The solver registry in `backend/app/module2_simulation/solver_registry.py` is the code-level authority for supported solver capabilities.

## Authentication and production

- [Authentication and founding users](AUTH_AND_FOUNDING_USERS.md)
- [Production configuration](PRODUCTION_CONFIGURATION.md)
- [VPS staging runbook](VPS_STAGING_RUNBOOK.md)

## Implementation records

Some files in this folder are implementation plans or engineering handoff records created during development. They are kept as development history and are not the best place to understand the current product.

For the current project state, use the documents in **Start here**.
