# Feature Geometry Evidence Resolver (v0.7 1D-2.1)

## Boundary

Measurement answers what observable BRep geometry exists.
Evidence resolution binds a FeaturePlan feature to those observations.
Verification later compares bound evidence with intent.

None of these layers create CAD, call an LLM, modify a FeaturePlan, persist
topology references, or change execution policy.

```text
FeaturePlan
      ↓
FeatureGeometrySignature
      ↓
ReferenceContext
      ↓
GeometryCandidate search
      ↓
CorrespondenceResult
      ↓
GeometryEvidence
      ↓
Semantic verification (unchanged in 1D-2.1)
```

`GeometryEvidenceReport` is additive. The Worker writes it as
`geometry_evidence` beside measurement and verification. It must not change
`ExecutionReport.geometry_valid`, `production_ready`, Evidence Gate behavior,
Smart/Strict behavior, exports, snapshots, or the UI.

1D-2.2 hole verification may `PASS` a property only against `bound_candidates`
of a `MATCHED` evidence row.

## Correspondence vocabulary

Every constraint and the overall identity binding uses exactly:

- `MATCHED` — exactly one candidate survived the evaluable filters
- `AMBIGUOUS` — more than one candidate survived; **never auto-selected**
- `NOT_FOUND` — evaluable filters left the set empty
- `UNAVAILABLE` — a required filter could not be evaluated, or the feature is not bindable

`AMBIGUOUS` always has an empty `selected_candidate_ids` list.

`MATCHED` is identity, not proof. A unique Ø6 cylinder with no executable
placement is `MATCHED` with `position=UNAVAILABLE`. 1D-2.2 must treat that
position as `UNKNOWN`, never `PASS`.

## High-reliability geometry only

Working adapters read existing 1D-1 facts:

- cylindrical surfaces
- global AABB / bounding region
- AABB-derived planes of an axis-aligned box (`left=min_x`, `front=min_y`, `bottom=min_z`)

True OCCT planar faces and edges are schema-ready but stay `UNAVAILABLE`
until a later measurement spike. Do not invent them.

Position of a cylinder is **axis ∩ host plane**, never the raw
`CylinderFact.center` interpreted as a feature XY.

## Bindable features in 1D-2.1

- `box_base`
- `cylinder_base` / `hollow_cylinder`
- `through_hole` / `blind_hole` (evidence only; no hole verifier)
- `boss_cylinder` (evidence only; no boss verifier)

Every other registered type still receives a signature so absence is visible,
but correspondence is `UNAVAILABLE`.

## Worker

After construction the Worker writes `geometry_measurement`, then
`geometry_evidence`, then `geometry_verification`. CAD execution is unchanged.
