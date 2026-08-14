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

Semantic regions resolve geometry by authored meaning rather than raw face or
edge indices. Supported selectors include axis extremes, planar normals,
cylindrical radius, and bounded geometry types. Resolution records stable
geometry signatures and one of `EXACT`, `DERIVED`, `RESELECTED`, or `LOST`;
ambiguous selections fail instead of silently choosing topology. A rebuild
report records changed parameters, invalidated features and bodies, cache
reuse, and semantic-region preservation/reselection/loss.

## Current feature vocabulary

- Sketch entities: rectangle, circle, line, polyline, three-point arc
- Construction: XY/XZ/YZ reference planes and custom datum planes
- Solids: straight/tapered extrude, revolve, multi-profile solid loft, and
  path sweep
- Booleans: union, subtract, intersection, and split into inside/outside result
- Modification: translate/rotate, mirror, fillet, chamfer, shell, and typed
  through/blind/counterbore/countersink holes
- Repetition: linear, circular, and two-axis grid patterns
- Multi-body: named immutable feature outputs and explicit exported bodies
- Constraints: fixed, coincident, horizontal, vertical, parallel,
  perpendicular, equal, distance, length, radius/diameter, and angle for the
  bounded line/arc/circle kernel adapter. Solve states are
  `FULLY_CONSTRAINED`, `UNDERCONSTRAINED`, `OVERCONSTRAINED`, and `INVALID`.
  Tangency and directional-distance solving are parsed but deliberately
  rejected until their kernel mapping is reliable.
- Assemblies: reusable component definitions, hierarchical explicit
  placements, typed relationships, repeated instances, material/interface
  metadata, and real BRep interference checks. Relationship records do not
  claim a general mate solver; explicit placement remains authoritative.
- Design spaces: explicit, linear, integer-range, boolean, and categorical
  variables; deterministic variant identity/order; bounded counts; chunking;
  partial-failure/cancellation results; and deferred or selective artifacts
- Planning: a strict non-authoritative `DesignIntentPlan` boundary. It cannot
  become executable while questions, unsupported intent, or the typed design
  document candidate are unresolved.
- Artifacts: STEP and STL through authenticated owner-scoped storage/download
  endpoints

General mate solving, free-form/bounded surface bodies, organic modeling,
guaranteed identity through arbitrary destructive topology changes, meshing,
FEA/CFD, and arbitrary expressions remain out of scope. Surface bodies remain
deferred because this compiler's validation and artifact contract is
intentionally solid-only; accepting them now would weaken geometry guarantees.

## Legacy transition

`legacy_cad_adapter.adapt_legacy_design()` maps each currently executable
pyramid, tower, or bridge request to this generic IR and makes the legacy
`*_m` unit meaning explicit. The established legacy endpoints remain on the
legacy generator for this batch so their historical files and dimensions do
not change silently. V2 is authoritative for V2 documents; the adapter is the
tested migration seam, not a second geometry authority.
