# Validation and release

## Current state

ASRE-Lab v1 has completed its engineering audit and final live release acceptance.

The accepted application-code baseline is:

`f5558ede291810f32040c856ff78eab0efd77c1d`

That patch fixed the final known comparative-job metadata inconsistency found during the code audit.

At release acceptance:

- known P0 defects: 0
- known P1 defects: 0
- known material P2 defects: 0
- final authenticated browser acceptance: **PASS**
- release blockers: **None**

The v1 application code is frozen at this baseline. Documentation-only presentation updates may follow without changing the accepted application behavior. New product features belong in a later version.

## Testing approach

The project is tested at several levels.

### Unit tests

Used for:

- solver behavior
- repository behavior
- scientific rules
- evidence logic
- helper functions
- frontend utilities

### Integration tests

Used for:

- persisted design and simulation workflows
- scientific evidence
- analysis
- decisions and reports
- owner-scoped downloads
- comparative Studies
- CAD and field artifacts
- selected real numerical backends

### Scientific benchmark tests

Used to compare bounded models against declared analytical or server-owned references.

A passing benchmark is evidence for that reference case. It is not treated as proof of general industrial accuracy.

### Frontend behavior tests

Used for:

- Study navigation
- stage behavior
- validation
- state isolation
- download behavior
- readable errors
- persisted workflow presentation

### Build checks

The release process also uses:

- TypeScript type checking
- production Next.js build
- `git diff --check`
- backend test suites
- API health
- worker health

## Final engineering audit

The deep release audit found and fixed five release-relevant issues.

### E-01

The deployed Evidence transport retry fix was not preserved in the canonical main history.

It was restored to main so a later backend deployment could not silently remove it.

### E-02

Comparative batches counted `partial_failure` simulations as completed.

The batch now stays `partial_failure` when any simulation is partial.

### E-03

A late response from Study A could arrive after navigation and overwrite Study B state in the frontend.

The frontend now checks the active Study before applying async Study responses or polling results.

### E-04

Private field download failures in Resource Detail and Scientific Canvas could fail without readable user feedback.

Those paths now show visible errors.

### E-05

Automatic comparative-analysis exceptions did not include server-side traceback logging.

Exception logging was added without changing the scientific gate behavior.

A final follow-up patch also fixed inconsistent `error_code` metadata for partial-only comparative batches.

## Reported test results from the final audit

The deep engineering audit reported:

- frontend test suite: 67 passed
- frontend typecheck: passed
- frontend production build: passed
- backend unit suite: 67 passed
- focused scientific, comparative, Evidence, and API suite: 58 passed
- comparative pyramid integration at that stage: 6 passed

The final metadata patch then reported:

- comparative pyramid integration: 9 passed
- relevant job, repository, Study, analysis, and Evidence tests: 33 passed
- `git diff --check`: passed

These groups overlap and should not be added together as a single project-wide test count.

## Final live release acceptance

The final release acceptance was performed against the live product through the normal researcher workflow. It checked the release gates that matter for closing v1 rather than opening another improvement cycle.

The accepted workflow covered:

1. authenticated workspace access
2. opening an existing completed Study
3. Design stage access
4. Physics stage access and readability
5. inspection of completed simulation results
6. Evidence loading without a material error
7. Scientific Trust visibility without inventing missing benchmark or refinement evidence
8. Human Decision access
9. Research Report access
10. visible feedback from report/export interaction
11. absence of blocker-level UI failure through the normal workflow
12. continuity of the Study → Design → Physics → Results → Evidence → Trust → Decision → Report path

Result:

**FINAL RELEASE ACCEPTANCE: PASS**

**Release blockers: None**

The tested ASRE-Lab v1 workflow was accepted at commit `f5558ede291810f32040c856ff78eab0efd77c1d`.

## Recorded verification limits

The release decision does not turn unexecuted checks into passes. Two narrower engineering-test limitations remain documented from the final code audit:

### External Supabase suite

The external live Supabase test suite was not rerun in the final engineering-audit environment. The production application continued to exercise Supabase authentication, persistence, and private storage during live use.

### Complete long numerical integration suite

The full marked integration suite was started during the engineering audit but stopped after a long CPU-bound run with no observed failure at that point. Focused release-critical numerical and scientific suites passed. The incomplete full run is not reported as a pass.

These are recorded verification limits, not known release blockers.

## Release rule

The v1 release would not have been accepted with:

- a P0 defect
- a P1 defect
- a material P2 defect
- a reproducible core workflow failure
- scientific misrepresentation
- persistence corruption
- silent failure of a core action

No such blocker remained at final release acceptance.

## Freeze rule

The accepted application-code baseline is frozen for v1.

After the v1 tag:

- do not add new v1 features
- do not redesign the accepted workflow as part of v1
- only consider an emergency v1 patch for a newly discovered material defect
- put normal improvements, new solver work, competition-specific additions, and broader UX changes into the next version
