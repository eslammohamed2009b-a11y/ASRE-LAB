# ASRE-Lab v1 release checklist

This file records the final v1 release state.

## Engineering gate

- [x] Canonical main contains the Evidence transport reliability fix
- [x] Comparative partial failures remain partial
- [x] Comparative job metadata is internally consistent
- [x] Study switching guards against late stale responses
- [x] Report downloads use the safe authenticated artifact path
- [x] Private field download failures show readable errors
- [x] Study title validation is explicit and accessible
- [x] Automatic comparative-analysis exceptions are logged
- [x] Frontend typecheck passed in the final engineering audit
- [x] Frontend production build passed in the final engineering audit
- [x] Focused release-critical backend suites passed
- [x] API health verified after the final backend patch
- [x] Worker health verified after the final backend patch
- [x] No database migration was required by the final release fixes

## Final live acceptance

- [x] Authenticated workspace usable
- [x] Existing completed Study opens
- [x] Design stage accessible
- [x] Physics stage accessible and understandable
- [x] Completed results inspectable
- [x] Evidence loads without a material error
- [x] Scientific Trust visible without invented benchmark/refinement evidence
- [x] Human Decision accessible
- [x] Research Report accessible
- [x] Report/export interaction gives visible feedback
- [x] No blocker-level UI failure in the normal researcher workflow
- [x] Final release acceptance returned **PASS**

## Final known severity

- Known P0: **0**
- Known P1: **0**
- Known material P2: **0**
- Release blockers: **None**

## Scientific gate

- [x] CAD and simulation are kept separate
- [x] The pyramid thermal solver does not claim to use the STL as its numerical mesh
- [x] Correlation is described as association, not causation
- [x] Iterative convergence is separate from spatial refinement
- [x] Missing benchmark or refinement evidence is not invented
- [x] Scientific Trust is an evidence-state classification, not an AI probability
- [x] Human action is required for the final decision
- [x] Reports are generated from persisted Study context

## Release decision

**PASS — ASRE-Lab v1 accepted for tag and freeze.**

Accepted application-code baseline:

`f5558ede291810f32040c856ff78eab0efd77c1d`

Normal feature development should move to the next version after `v1.0.0` is tagged.
