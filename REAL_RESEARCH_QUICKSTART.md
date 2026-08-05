# ASRE-Lab real research quickstart

This walkthrough uses the implemented square-pyramid geometry-aware thermal model. It does not claim CAD-mesh FEA or physical causation.

1. Sign in and choose **New Research Study**. Record a bounded research question, optional hypothesis, independent variable, controlled conditions, and outputs. The created study is persisted server-side and appears on the Research Studies dashboard.
2. In **Design**, enter a convenience description such as `pyramid with a 2 m by 2 m base and 4 m height made of concrete`. Inspect the structured base length, height, slope, material, and the selected authoritative dimension pair. The server rejects inconsistent base/height/slope triples.
3. In **Design Space**, choose height, a linear sweep from 1 m to 5 m, and five variants. Resolve the preview before generation. Confirm that the final table contains five distinct height values and full resolved parameter sets, then generate the persisted STL and STEP artifacts.
4. In **Physics**, select all five designs and choose `pyramid_thermal_conduction_v1`. Declare material, base temperature, exposed-surface temperature, volumetric heat source, odd grid resolution, iteration limit, and tolerance.
5. Build the pre-run comparison. Confirm that height appears under **VARIES** and material, boundary conditions, solver, and numerical settings appear under **HELD CONSTANT**. The model disclosure must say that this is a structured pyramid mask, not CAD-mesh FEA.
6. Execute the durable batch. Partial failures remain visible and successful simulations are preserved. For each completed run inspect solver/version, equation, assumptions, warnings, benchmark evidence, convergence, numerical configuration, reproducibility hash, scalar metrics, and persisted field metadata.
7. Open **Analysis**. Inspect dataset row/exclusion counts, missing values, constants, incompatible units, warnings, and dataset hash. Run descriptive statistics. Use correlation only when sample requirements are satisfied; association does not establish causation. Optionally configure first-order standardized regression sensitivity with explicit feature and target columns; it is linear association, not causal or global sensitivity. Add declared objectives before Pareto/ranking/recommendations.
8. Review the parameter-versus-metric plot and select points to reveal their design and simulation evidence IDs. Export simulation CSV/JSON and the analysis dataset CSV/analysis JSON.
9. Create the evidence-grounded decision and explicitly accept, reject, or request modification. Rankings depend on the selected objective direction and weight.
10. Generate the research report and download PDF, JSON, or CSV. Missing evidence is reported as unavailable rather than invented.
11. Sign out, sign back in, open **Research Studies**, and reopen the study by its server ID. Verify the persisted designs, simulations, analysis, decision, report, and counts.

For spatial-convergence evidence, reproduce the identical physical scenario at three odd grid resolutions (for example 9, 17, and 25) without changing material or boundary conditions. Do not call a single-resolution result spatially converged.
