# Scientific trust in Backend V2

ASRE-Lab provides bounded research and teaching models, not industrial CAD, CFD, or
multiphysics validation. Scientific confidence is deterministic and never AI-generated.

## Supported bounded models

- `thermal_conduction_v1`: steady 1D/structured-3D conduction with constant isotropic properties.
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
