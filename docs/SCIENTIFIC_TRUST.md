# Scientific trust in Backend V2

ASRE-Lab provides bounded research and teaching models, not industrial CAD, CFD, or
multiphysics validation. Scientific confidence is deterministic and never AI-generated.

## Supported bounded models

- `thermal_conduction_v1`: steady 1D/structured-3D conduction with constant isotropic properties.
- `pyramid_thermal_conduction_v1`: steady conduction on a structured Cartesian mask of a solid square parametric pyramid.
- `structural_linear_1d_v1`: small-deformation linear axial bar or Euler-Bernoulli beam.
- `modal_eigen_1d_v1`: undamped SDOF or cantilever-beam eigenmodes.
- `acoustic_duct_1d_v1`: lossless, linear, plane-wave duct acoustics.
- `electrostatic_rectangular_2d_v1`: static rectangular-grid Poisson field with constant permittivity.
- `cfd_laminar_channel_2d_v1`: steady, incompressible, fully developed plane-Poiseuille flow below Re 2000.
- `thermal_structural_one_way_v1`: approximate sequential coupling using mean nodal temperature.

The planned `coupled_multiphysics_v0` is unsupported and is not presented as evidence.

## Benchmarks and convergence

Each supported model has one locally derivable analytical reference: linear heat profile,
axial displacement, SDOF frequency, half-wave duct frequency, uniform electrostatic
field, plane-Poiseuille maximum velocity, or restrained thermal stress. Tolerances are
declared in solver metadata. Coarse/medium/fine studies require decreasing medium-to-fine
change below the declared threshold. Coupling convergence is explicitly not applicable;
the coupling benchmark is a consistency check, not general validation.

### Geometry-aware pyramid thermal model

`pyramid_thermal_conduction_v1` solves

```text
k * Laplacian(T) + q = 0
```

on a solid square-pyramid domain. The base is Dirichlet at
`prescribed_temperature_c`; the staircase-approximated sides and apex are
Dirichlet at `ambient_temperature_c`; `heat_source_w_m3` is uniform inside the
mask. Conductivity is homogeneous, isotropic, and constant. The anisotropic
seven-point finite-difference stencil accounts for distinct horizontal and
vertical cell spacing, and deterministic Gauss-Seidel iteration stops at the
declared maximum-update tolerance.

The solver accepts height/base ratios from 0.1 through 10 and odd bounding-box
grid resolutions from 9 through 41. It persists the numerical temperature field
and a separate finite domain mask; values outside that mask are storage fill
values, not physical pyramid results.

The per-result benchmark is the analytical zero-source/equal-Dirichlet constant
temperature solution. Iterative convergence and its residual history are saved.
Spatial convergence is not inferred from one run: researchers must repeat the
same scenario at at least three odd grid resolutions and inspect the
coarse/medium/fine metric change. Tests also verify that the estimated mask
volume approaches the analytical pyramid volume under refinement and that a
height change produces a genuinely different geometry-sensitive result.

This model has no transient conduction, convection, radiation, contact,
anisotropy, temperature-dependent properties, arbitrary CAD-mesh import, or
general FEA claim.

## Confidence rules

- **high**: valid inputs, passing benchmark, applicable convergence passed, no warnings.
- **moderate**: passing evidence with a boundary warning, scientific warning, or explicitly
  non-applicable convergence.
- **low**: missing/failed benchmark or poor convergence.
- **invalid**: input outside the supported envelope or required validity evidence missing.

Stable warnings include `MISSING_REQUIRED_INPUT`, `OUTSIDE_VALIDITY_ENVELOPE`,
`NEAR_VALIDITY_BOUNDARY`, and `POOR_CONVERGENCE`. Each finding identifies its affected
input, evidence reference, and suggested correction.

These checks validate only the documented reference cases and bounded envelopes. They do
not establish general industrial accuracy, material nonlinearity, arbitrary geometry,
turbulence, transient behavior, two-way coupling, or probabilistic certainty.
