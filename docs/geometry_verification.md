# Geometry Semantic Verification Engine (v0.7 1D-2)

## Boundary

Measurement answers what observable BRep geometry exists. Verification compares
that observation with explicit FeaturePlan intent. Neither layer creates CAD,
calls an LLM, modifies a FeaturePlan, persists topology references, or changes
execution policy.

```text
FeaturePlan intent ----> semantic verification ----> audit report
                              ^
                              |
                    GeometryEvidence (1D-2.1 binding; consumed by 1D-2.2 holes)
                              ^
                              |
final BRep ----> read-only measurement facts
```

The Worker writes `geometry_verification` beside `geometry_measurement` in its
raw `execution_report.json`. This is evidence only: it does not change
`ExecutionReport.geometry_valid`, `production_ready`, Evidence Gate behavior,
Smart/Strict behavior, exports, snapshots, or the UI.

## Hierarchy

```text
Model verification
|- global properties: explicit bounding-box and volume expectations
`- feature verification
   |- property results: PASS / FAIL / UNKNOWN / UNSUPPORTED / SKIPPED
   |- ephemeral correspondence to one measurement report
   `- model aggregation: VERIFIED / PARTIALLY_VERIFIED / FAILED / UNKNOWN / UNSUPPORTED
```

Every property result contains the expected value, observed value, software
comparison tolerance, deviation, measurement provenance, and explanation.

## Current verified coverage

The registry contains every canonical FeatureDefinition so a missing verifier
is visible as `UNSUPPORTED`, not silently omitted. The currently implemented
Level-A verifiers are deliberately small:

- isolated `box_base`: BRep X/Y/Z bounding dimensions and derived base volume;
- isolated `cylinder_base` and `hollow_cylinder`: BRep bounding dimensions,
  derived base volume, unique outer cylindrical-surface existence and diameter;
- cylinder axis, only when the FeaturePlan explicitly supplies an axis;
- `through_hole` / `blind_hole` (1D-2.2): existence, diameter, position, axis,
  depth, and through-span, only against a MATCHED `GeometryEvidence` row;
- `boss_cylinder` (0.7.3-A): existence, diameter, height, axis, position, host
  on the same evidence types. Host primitives are reserved. One candidate
  cannot verify two features;
- annular grooves (0.7.3-B): existence, width, depth, axial position, axis, host
  on the same evidence types. Position is `z_start` occupancy, not XY;
- optional global bounding-box and volume checks supplied through
  `VerificationContext`.

Hole depth and through-ness compare cylindrical V-span with host AABB size
(or specified blind depth). That is span evidence, not a topological
both-ends-open proof and not a manufacturing tolerance. `AMBIGUOUS`
correspondence never becomes `PASS`.

The default software tolerances are 0.05 mm linear, 0.001 mm^3 volume plus a
small relative component, and 0.1 degrees for axes. They are comparison
tolerances for deterministic software observations, not manufacturing
tolerances or GD&T.

## Correspondence and limitations

`measurement_index` remains report-local provenance. It is never stored on a
FeaturePlan and is not a topology ID. The current resolver only selects a
cylinder when exactly one measured cylinder matches an expected diameter. Zero
candidates is `FAIL` for a requested isolated cylindrical primitive; multiple
candidates is `UNKNOWN`.

A cylindrical face does not by itself prove that it is a hole. 1D-2.2 only
verifies a hole after the evidence resolver uniquely binds it. Bosses, grooves,
patterns, fillets, chamfers, ribs, sketches, and reference relationships remain
`UNSUPPORTED`. When an isolated base has follow-up features, its direct base
check is also `UNKNOWN` rather than guessing how the final BRep relates to the
original primitive.

Future expansion adds a verifier class, registers it with the existing
capability registry, and adds tests. It must not require edits to the Worker
core, FeaturePlan semantics, Evidence Gate, or the frontend.
