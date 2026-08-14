# General CAD V2 contract

`EngineeringDesignDocumentV2` is ASRE-Lab's feature-based CAD authority. It
describes named parameters, datum planes, typed sketches, immutable named body
outputs, a dependency graph, output bodies, tolerance policy, and semantic
regions. Pyramid, tower, bridge, bracket, and flange are not V2 schema types;
models are compositions of the same bounded operations.

## Unit boundary

- Document quantities always carry a unit. Length parameters support `m`,
  `mm`, `cm`, `um`, and `in`; angles support `deg` and `rad`.
- Scientific design identity normalizes lengths to SI metres and angles to
  radians.
- CadQuery/OpenCascade execution converts lengths once, at the compiler
  boundary, to millimetres. CadQuery angles are supplied in degrees.
- STEP exports declare millimetres.
- STL has no native unit declaration. Its coordinates are intentionally
  millimetres, and artifact metadata states that fact.
- Legacy `DesignParameters` retains its existing behaviour and is not silently
  reinterpreted by V2.

## Determinism and safety

The compiler uses a stable topological order. Duplicate IDs, dangling
dependencies, cycles, missing bodies/sketches/parameters, and multiple
producers for one immutable body are rejected before CAD execution. Scientific
identity includes normalized parameters and bounds, feature inputs and graph,
body/material structure, semantic tags, and tolerance policy. It excludes the
document/request identity, timestamps, worker state, paths, storage keys, and
other operational metadata.

The document cannot contain Python, shell commands, imports, filesystem paths,
or executable expressions. Every operation is selected by a discriminated,
closed Pydantic union. Parameter support is direct-reference only in this
batch; no expression evaluator exists.

## Validation and semantics

Every output body must contain real OpenCascade solids with valid BRep
topology, positive finite volume, finite bounds, and extents above the declared
minimum feature scale. Empty/failed booleans and invalid finishing operations
fail compilation; invalid geometry cannot be exported.

Semantic regions preserve bounded tag and selector intent such as
`mounting_face`, `inlet`, or `fixed_support_region`. This is a foundation for
future simulation coupling, not a claim of persistent face identity across
arbitrary topology-changing edits.

## Current feature vocabulary

- Sketch entities: rectangle, circle, line, polyline, three-point arc
- Construction: XY/XZ/YZ reference planes and custom datum planes
- Solids: extrude, revolve, and multi-profile loft
- Booleans: union, subtract, intersection
- Modification: translate/rotate, fillet, chamfer
- Repetition: linear and circular patterns
- Multi-body: named immutable feature outputs and explicit exported bodies
- Constraint support: fixed, explicit-coordinate entities only; no general
  sketch constraint solver
- Artifacts: STEP and STL through authenticated owner-scoped storage/download
  endpoints

Assemblies/mates, arbitrary surfaces and organic modeling, persistent
topological naming, meshing, FEA/CFD, and arbitrary expressions are explicitly
outside this foundation batch.

## Legacy transition

`legacy_cad_adapter.adapt_legacy_design()` maps each currently executable
pyramid, tower, or bridge request to this generic IR and makes the legacy
`*_m` unit meaning explicit. The established legacy endpoints remain on the
legacy generator for this batch so their historical files and dimensions do
not change silently. V2 is authoritative for V2 documents; the adapter is the
tested migration seam, not a second geometry authority.
