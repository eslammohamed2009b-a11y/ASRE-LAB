# ASRE-Lab research quickstart

This walkthrough uses the main guided v1 workflow with the geometry-aware square-pyramid thermal model.

It is a bounded study. It is not CAD-mesh FEA and it does not prove physical causation.

## 1. Create a Study

Sign in and choose **New Research Study**.

Use a clear title and research question.

Example question:

```text
How does pyramid height affect the maximum steady-state temperature when the base, material, boundary conditions, and numerical settings are held constant?
```

The Study is persisted before the main workflow begins.

## 2. Resolve the base design

Open **Design**.

A supported convenience input is:

```text
pyramid with a 2 m by 2 m base and 4 m height made of concrete
```

Inspect the resolved:

- base length
- height
- slope
- material
- authoritative dimension pair

The dependent dimension is re-derived by the server.

## 3. Define the design space

Choose a height sweep.

For example:

```text
minimum: 1 m
maximum: 5 m
variants: 5
```

Resolve the final variants before generation.

Confirm that the table contains five distinct heights and the expected controlled values.

Generate the CAD variants.

The Study should now contain persisted STEP and STL artifacts.

## 4. Configure Physics

Open **Physics**.

For the main guided workflow choose:

`pyramid_thermal_conduction_v1`

Declare:

- material
- ambient temperature
- base temperature
- volumetric heat source
- odd grid resolution
- tolerance
- maximum iterations

## 5. Review before running

Open **Validation** and build the pre-run comparison.

Check that the intended variable appears under **varies**.

Check that the following remain controlled:

- material
- boundary conditions
- solver
- numerical settings

Read the model disclosure.

The pyramid thermal solver uses a structured Cartesian mask built from the persisted dimensions. The displayed STL is a design artifact, not the solver mesh.

## 6. Run the comparative batch

Open **Run** and start the batch.

Monitor:

- status
- progress
- completed count
- failed or partial count

A partial simulation must remain visible as a partial result. It must not make the whole batch appear cleanly completed.

Completed simulations are preserved.

## 7. Inspect results

For each completed simulation inspect:

- design identity
- simulation identity
- solver and version
- summary metrics
- units
- iterative convergence
- governing equations
- assumptions
- warnings
- field metadata where available
- reproducibility information

## 8. Review Evidence and analysis

Open **Evidence**.

Inspect the parameter-versus-metric view and the persisted analysis.

The analysis may include:

- descriptive statistics
- correlations
- first-order standardized regression sensitivity
- ranking and Pareto results when objectives are configured

Correlation is association. It is not proof of causation.

Automatic comparative analysis and a later researcher-requested analysis remain separate persisted records.

## 9. Review Scientific Trust

For a selected simulation inspect:

- validity
- benchmark state
- run convergence
- refinement state
- limitations
- linked evidence

Do not treat iterative convergence as spatial refinement.

If a benchmark or refinement study was not run, its state should remain `NOT_RUN`.

## 10. Record the human decision

Create the decision basis.

Review the designs, objective, results, analysis, evidence, Trust, and limitations.

Then explicitly choose:

- Accept
- Reject
- Request modification

The system does not make this action automatically.

## 11. Generate the report

Open **Report**.

Review the in-app summary.

Download the report as:

- PDF
- JSON
- CSV

The file should be generated from the persisted Study and decision state.

## 12. Reopen the Study

Refresh the page or return to the dashboard.

Reopen the Study and verify that the designs, simulations, analysis, decision, and report are still present.

For an actual spatial-refinement study, repeat the same physical scenario at at least three suitable grid resolutions without changing the controlled physics. A single converged run is not spatial-convergence evidence.
