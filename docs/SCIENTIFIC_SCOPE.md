# Scientific scope

## Rule

ASRE-Lab uses bounded engineering models.

The platform should refuse or clearly limit a case that is outside the implemented model instead of returning a result with a stronger claim than the code supports.

The code-level source of truth is:

`backend/app/module2_simulation/solver_registry.py`

## Current solver families

| Capability | Current scope | Validation position | Main limits |
| --- | --- | --- | --- |
| `pyramid_thermal_conduction_v1` | Steady heat conduction in a solid square-pyramid Cartesian mask | Partially validated | No CAD mesh, transient heat, convection, radiation, contact, anisotropy, or temperature-dependent properties |
| `thermal_conduction_v1` | Steady 1D rod or slab and structured 3D cubic finite differences | Validated bounded model | No arbitrary CAD mesh and no transient conduction |
| `structural_linear_1d_v1` | Linear axial bar and Euler-Bernoulli cantilever beam | Validated bounded model | No 2D or 3D solids, plasticity, buckling, or general supports |
| `modal_eigen_1d_v1` | SDOF and cantilever-beam eigenmodes | Validated bounded model | No damping or general 3D modal analysis |
| `acoustic_duct_1d_v1` | Linear lossless plane-wave duct acoustics | Validated bounded model | No room acoustics, losses, transverse modes, or nonlinear acoustics |
| `electrostatic_rectangular_2d_v1` | Static rectangular-grid Poisson field | Validated bounded model | Constant permittivity, no magnetics, no waves, no arbitrary geometry |
| `cfd_laminar_channel_2d_v1` | Fully developed plane-Poiseuille flow | Validated bounded model | Laminar internal channel only, no turbulence or general CFD |
| `thermal_fem_3d_v1` | CAD-derived TET4 steady thermal FEM | Partially validated | Bounded mesh size, isotropic steady conduction, no transient model or contact resistance |
| `structural_linear_elasticity_3d_v1` | CAD-derived TET4 small-strain linear elasticity | Partially validated | No contact, plasticity, geometric nonlinearity, or general buckling claims |
| `modal_fem_3d_v1` | CAD-derived TET4 undamped modal FEM | Partially validated | No damping, prestress, frequency response, or participation factors |
| `acoustic_helmholtz_fem_3d_v1` | CAD-derived TET4 frequency-domain Helmholtz acoustics | Partially validated | Fixed homogeneous acoustic volume, no transient, radiation, PML, thermoviscous, or nonlinear model |
| `cfd_openfoam_laminar_internal_3d_v1` | Steady incompressible Newtonian laminar internal flow on certified CAD-derived finite-volume meshes | Partially validated, maximum trust is bounded | No turbulence, transient, compressible, multiphase, non-Newtonian, CHT, FSI, combustion, or external aerodynamics |
| `thermal_structural_one_way_v1` | Sequential steady thermal to linear structural response | Bounded coupling workflow | One-way only, no deformation feedback |

The planned `coupled_multiphysics_v0` entry is not a runnable general multiphysics solver.

## The pyramid thermal model

The main guided product workflow uses `pyramid_thermal_conduction_v1`.

It solves steady conduction in a solid square-pyramid domain using a structured Cartesian mask.

The model uses:

- a parametric square-pyramid geometry
- constant isotropic conductivity
- prescribed base temperature
- prescribed exposed-surface temperature
- optional uniform volumetric heat source
- an odd Cartesian grid inside declared limits
- deterministic iterative convergence criteria

The solver builds its own numerical domain from the persisted dimensions.

It does not read the displayed STEP or STL as a finite-element mesh.

That is why the product explicitly separates CAD from simulation.

## Evidence

ASRE-Lab stores evidence as persisted records rather than treating the latest UI state as the scientific authority.

Evidence can include:

- numerical result evidence
- field result evidence
- input validity evidence
- run convergence evidence
- benchmark evidence
- refinement convergence evidence
- analysis evidence
- provenance links

Not every solver produces every evidence type.

Missing evidence stays missing.

## Convergence

Several different ideas must remain separate.

### Iterative convergence

This asks whether the numerical algorithm reached its stopping condition for a single discretization.

### Spatial refinement

This asks whether the reported result stabilizes as spatial resolution changes.

A single run can pass iterative convergence and still have no spatial-refinement evidence.

ASRE-Lab does not use one as a substitute for the other.

## Benchmarks

Some bounded models have analytical or server-owned reference cases.

A benchmark only supports the declared reference case.

It does not prove general industrial accuracy.

If a benchmark was not run for the selected Study, its state remains `NOT_RUN`.

## Scientific Trust

Scientific Trust summarizes persisted scientific state.

It can reflect:

- model validity
- benchmark state
- convergence state
- warnings
- evidence completeness
- declared limitations

Trust is not a probability and it is not generated from model confidence language.

A low Trust classification does not automatically mean the numerical record is fake or unusable. It means the available evidence is limited, failed, or incomplete relative to the trust rules.

## Analysis limits

The analysis layer supports deterministic methods such as:

- descriptive statistics
- correlations
- first-order standardized regression sensitivity
- ranking
- Pareto analysis
- evidence-linked recommendations

Correlation is association.

It does not establish physical causation.

Sensitivity results depend on the selected method, variables, and available dataset. They are not presented as universal causal sensitivity.

## AI limits

AI can help with supported input interpretation and explanation of persisted evidence.

AI does not:

- replace the physics solver
- create benchmark evidence
- invent refinement studies
- fabricate field results
- approve the final engineering decision

## General exclusions

ASRE-Lab does not claim:

- unrestricted 3D FEA
- unrestricted CFD
- turbulence
- general nonlinear material behavior
- arbitrary contact
- general transient multiphysics
- industrial certification
- safety approval
- causal discovery from correlation
- autonomous engineering approval

For detailed rules and reference cases see [Scientific Trust](SCIENTIFIC_TRUST.md) and [Scientific capability details](SCIENTIFIC_CAPABILITY_GAPS.md).
