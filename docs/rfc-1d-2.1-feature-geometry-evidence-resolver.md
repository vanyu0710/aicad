# RFC: v0.7.1 / 1D-2.1 Feature Geometry Evidence Resolver

**Status:** Approved design. Pure resolver + schema + tests land in this phase. No Worker rewrite, no verification verdict change, no FeaturePlan mutation.
**Baseline:** `codex/mechcad-pro-ui` @ `v0.7.0-stable-baseline` (`fa479a3`).
**Next phase:** 1D-2.2 Complete Hole Verification. Do not start it in this pass.

---

## 1. Why this layer exists

1D-1 answers: *what observable BRep facts exist?*
1D-2 answers: *does an isolated primitive match FeaturePlan intent?*

The missing layer is: **which final-BRep evidence belongs to which Feature?**

Today `resolve_unique_cylinder()` only filters by diameter. That is enough for an isolated `cylinder_base`, and is why holes/bosses stay `UNSUPPORTED`. A cylindrical face is not a hole. Two Ø6 holes make diameter correspondence `AMBIGUOUS`. `CylinderFact.center` is the OCCT axis location, not a hole XY.

1D-2.1 builds a deterministic Feature ↔ geometry-evidence relation. It does **not** hunt for “Face #4”, persist topology, or emit PASS/FAIL.

```text
FeaturePlan
      ↓
Semantic intent          FeatureGeometrySignature
      ↓
Reference resolver       ReferenceContext
      ↓
BRep candidate search    GeometryCandidate[]
      ↓
Correspondence           CorrespondenceResult
      ↓
Evidence                 GeometryEvidence
      ↓
Verification             unchanged 1D-2 consumers (1D-2.2 later)
```

---

## 2. Non-goals and hard boundaries

**In scope**

- Five core types and the resolve pipeline.
- Compile signatures from `FeatureDefinition` + `FeatureV3`.
- Resolve only high-reliability references: global origin, global XYZ axes/planes, AABB faces of an axis-aligned box host.
- Search only high-reliability candidates: `cylinder`, `plane`, `box`, `edge`, `bounding_region`.
- Multi-constraint correspondence with `MATCHED | AMBIGUOUS | NOT_FOUND | UNAVAILABLE`.
- **`AMBIGUOUS` never auto-selects.**
- Additive `GeometryEvidenceReport`. Worker dump of `geometry_evidence` is a later micro-step after this suite is green.

**Out of scope**

- Changing `FeaturePlanV3`, Evidence Gate, Worker CAD execution, UI, export, `geometry_valid`, `production_ready`.
- Hole/Boss/Groove property verifiers (that is 1D-2.2 / 1D-2.3).
- Arbitrary-face / angled-axis Reference System (v0.8).
- Persistent topology IDs or writing `measurement_index` onto the plan.
- Inventing plane/edge numbers if OCCT cannot supply them stably.
- Vision, manufacturing checks, Professional UI.

**Purity**

- Resolver is a pure function of `(FeaturePlan, GeometryMeasurementReport)`.
- It must not mutate the plan, measurement, or snapshots.
- `UNKNOWN` / `UNAVAILABLE` / `AMBIGUOUS` are never promoted to `MATCHED`.

---

## 3. Relationship to existing code

| Existing | Role after 1D-2.1 |
|---|---|
| `GeometryMeasurementReport` / `CylinderFact` / AABB / volume | Unchanged 1D-1 facts. Candidate search *reads* them. |
| `GeometryCorrespondence` (`UNIQUE/AMBIGUOUS/NONE/UNAVAILABLE/NOT_REQUESTED`) | Kept for 1D-2 verifiers. New `CorrespondenceResult` is the 1D-2.1 contract. Adapter maps `MATCHED→UNIQUE`, `NOT_FOUND→NONE`. |
| `resolve_unique_cylinder()` | Remains. New resolver is a *superset* (multi-constraint). 1D-2.2 may switch verifiers to consume `GeometryEvidence`. |
| `verify_feature_plan()` | **No verdict change in 1D-2.1.** Holes stay `UNSUPPORTED`. Isolated bases keep current behavior. |
| `cad_worker/freecad_executor.py` | No execution change in this phase. |

---

## 4. The five core types

### 4.1 `FeatureGeometrySignature`

The feature’s geometric *query*, not an answer. Compiled from `FeatureDefinition` + `FeatureV3`. Never inferred from BRep.

```text
FeatureGeometrySignature
├── feature_id
├── feature_type
├── operation                 base | add | remove | modify | pattern
├── geometry_effect
├── primary_kind              cylinder | plane | box | edge | bounding_region | unsupported
├── bindable                  resolver may attempt identity binding
├── primitives[]              expected primitive roles
│     ├── role                bore | outer | inner | host | region | edge | face
│     ├── kind
│     └── expected            diameter, axis, sizes, …
└── constraints[]             type, dimension, axis, position, region, host, relationship
      ├── kind
      ├── required            from verification_contract.properties
      ├── expected
      └── evaluable           false if intent is missing (no invented values)
```

Compilation rules:

- `through_hole` / `blind_hole` → primary `cylinder`, role `bore`; required constraints follow the contract: diameter, position, axis, depth (depth/through stay unevaluable in 1D-2.1).
- `box_base` → `bounding_region` + optional AABB-derived planes.
- `cylinder_base` / `hollow_cylinder` → `cylinder` (outer, and inner if present) + `bounding_region`.
- Known unsupported types still get a signature with `primary_kind=unsupported` so absence is visible.
- Missing dimensions stay `evaluable=false`. Do not fill from measurement.

### 4.2 `ReferenceContext`

Resolved frames used to evaluate position / axis / host / region. Ephemeral. Not the v0.8 Reference System.

```text
ReferenceContext
├── feature_id
├── status                    MATCHED | AMBIGUOUS | NOT_FOUND | UNAVAILABLE
├── frames[]
│     ├── name                origin | world_xy | world_z | host_aabb | left | front | …
│     ├── origin [x,y,z]
│     ├── axes  {x,y,z}
│     └── source              plan.coordinate_system | measurement.bounding_box
├── placement_frame           the frame PlacementV3.x/y/z are expressed in
└── resolved_position         expected model-space point when PlacementV3 is executable
```

**1D-2.1 supported frames only**

- Global origin + XYZ axes / XY, YZ, XZ planes from `FeaturePlan.coordinate_system` (default: model origin, main axis Z, right-handed).
- Host AABB faces of an axis-aligned box measurement: `min_x=left`, `max_x=right`, `min_y=front`, `max_y=back`, `min_z=bottom`, `max_z=top`.
- Centered labels already used by the Worker (`origin`, `center`, `base_center`, `model_center`, …) resolve to the model origin frame. Missing X/Y is **not** invented as `(0, 0)` for evidence; position stays `UNAVAILABLE`.

**Convention**

- Model coordinates are the Worker’s current coordinates.
- `left` / `front` / `bottom` = AABB `min_x` / `min_y` / `min_z`.
- User language “20 from left, 30 from front” is **not** parsed from chat in 1D-2.1. Executable truth remains `PlacementV3.x/y/z` + `reference`.

**Unresolved → `UNAVAILABLE`**, never a guessed origin.

### 4.3 `GeometryCandidate`

A non-semantic measured primitive that *might* satisfy a signature. Report-local only.

```text
GeometryCandidate
├── candidate_id              "cyl:2" | "bbox:global" | "plane:aabb:top"
├── kind                      cylinder | plane | box | edge | bounding_region
├── measurement_ref           path into GeometryMeasurementReport
├── properties                diameter, axis, axis_point, sizes, normal, …
└── status                    MEASUREMENT_SUCCESS | UNAVAILABLE | ERROR
```

**First working adapters (from existing 1D-1 facts)**

- `cylinder` ← `CylinderFact`.
- `bounding_region` / AABB `box` ← `BoundingBoxFact`.
- Derived host planes of an axis-aligned box ← AABB (no new face walk). These are `plane` candidates with `source=aabb_derived`, not OCCT faces.

**Schema-ready, resolver `UNAVAILABLE` until a later measurement spike**

- True OCCT planar faces.
- Edges.

**Position evidence rule**

`CylinderFact.center` is “OCCT cylindrical-surface axis location”, not hole XY and not a face centroid.
Hole position, when evaluable, is **axis ∩ host reference plane** (for Z holes on a box: axis ∩ `z = host max_z`). If axis or host plane is missing, position is `UNAVAILABLE`, not a guessed XY from `center`.

### 4.4 `CorrespondenceResult`

Identity binding after applying evaluable constraints. Never a PASS/FAIL.

```text
CorrespondenceResult
├── feature_id
├── status                    MATCHED | AMBIGUOUS | NOT_FOUND | UNAVAILABLE
├── selected_candidate_ids[]  0 or 1 id. AMBIGUOUS ⇒ always []
├── candidate_ids[]           all survivors / all ambiguous hits
├── constraint_results[]
│     ├── constraint          type | dimension | axis | position | region | host | relationship
│     ├── status              same 4-state vocab
│     ├── expected / observed
│     └── candidate_ids[]
└── reason
```

**Algorithm**

1. Universe = candidates of the signature’s `primary_kind`.
2. For each *evaluable* constraint, filter the universe. Record per-constraint status.
3. Unevaluable required constraints are marked `UNAVAILABLE` and **do not filter**.
4. After all evaluable filters:
   - 0 survivors → `NOT_FOUND` (`selected=[]`)
   - more than 1 survivor → `AMBIGUOUS` (`selected=[]`, list every id)
   - 1 survivor → `MATCHED` (`selected=[that id]`)
   - no evaluable constraint could run → `UNAVAILABLE` (`selected=[]`)

**`AMBIGUOUS` never auto-selects.** No source priority, no “first index”, no diameter-only fallback once another constraint left the set non-unique.

**Identity vs property proof**

`MATCHED` means “exactly one candidate survived evaluable filters.” It does **not** mean every required property was proven. If two Ø6 holes exist and position is unevaluable → `AMBIGUOUS`. If one Ø6 hole exists and position is unevaluable → `MATCHED` identity, and the `position` constraint stays `UNAVAILABLE`. Verification (1D-2.2) must then emit `Position UNKNOWN` / overall `UNVERIFIED`, never `PASS`.

### 4.5 `GeometryEvidence`

What verification is allowed to consume.

```text
GeometryEvidence
├── feature_id / feature_type
├── signature                 FeatureGeometrySignature
├── reference                 ReferenceContext
├── correspondence            CorrespondenceResult
├── bound_candidates[]        only if correspondence.status == MATCHED
├── usable_for_verification   true only for MATCHED identity
└── notes[]
```

```text
GeometryEvidenceReport
├── evidence_version          "1D-2.1"
├── measurement_version
├── features[]                GeometryEvidence
└── errors[]
```

Verification rule (documented now, enforced in 1D-2.2):

- Property may be `PASS` only against `bound_candidates` of a `MATCHED` evidence row.
- `AMBIGUOUS` / `NOT_FOUND` / `UNAVAILABLE` ⇒ that property is `UNKNOWN` or `FAIL` per existing 1D-2 policy, **never a silent pick**.

---

## 5. Constraint kinds (first batch)

| Kind | What it filters | 1D-2.1 evaluable when |
|---|---|---|
| `type` | candidate.kind == signature.primary_kind | Candidates of that kind were collected, or measurement succeeded with an empty set |
| `dimension` | diameter / length / width / height vs FeatureV3 | Dimension present and candidate property measured |
| `axis` | candidate axis vs `PlacementV3.axis` (X/Y/Z) | Axis measured |
| `position` | axis ∩ host-plane vs resolved placement XY | Placement executable and axis+plane available |
| `region` | observed position inside host AABB | Host AABB exists |
| `host` | a bounding_region candidate exists | Host AABB measured |
| `relationship` | depends_on / through vs blind | Usually `UNAVAILABLE` in 1D-2.1 |

Depth / through are **not** claimed as proven in 1D-2.1. Cylinder `height` is V-span, not hole depth.

---

## 6. First supported signatures

| Feature | Signature compiled | Resolver actually binds | Notes |
|---|---|---|---|
| `box_base` | bounding_region + AABB planes | Yes | Isolated size proof stays in 1D-2 |
| `cylinder_base` / `hollow_cylinder` | cylinder + bounding_region | Yes | Outer cylinder identity |
| `through_hole` / `blind_hole` | cylinder bore + position/axis | Yes, evidence only | **No hole verifier yet** |
| `boss_cylinder` | cylinder | Yes if unique | Still no verifier |
| groove / slot / pad / rib / pattern / fillet / chamfer / counterbore | signature stub | `UNAVAILABLE` | No fake correspondence |

---

## 7. Pipeline

```text
resolve_feature_geometry_evidence(plan, measurement) -> GeometryEvidenceReport

for each feature in [base] + features:
    signature  = compile_signature(definition, feature)     # no BRep
    reference  = resolve_references(plan, feature, measurement)
    candidates = collect_candidates(measurement)            # existing facts only
    result     = correspond(signature, reference, candidates)
    evidence   = bind(signature, reference, result)
```

Worker insert point (deferred micro-step, not this phase):

```text
measure_shape(part)
    → geometry_measurement
resolve_feature_geometry_evidence(plan, measurement)
    → geometry_evidence          # additive, later
verify_feature_plan(plan, measurement)
    → geometry_verification      # unchanged in 1D-2.1
```

---

## 8. Approved defaults

1. Position language: evaluate `PlacementV3` only.
2. AABB convention: `left=min_x`, `front=min_y`, `bottom=min_z`.
3. `MATCHED` means unique identity after evaluable filters.
4. True OCCT face/edge search waits for a measurement spike. AABB-derived planes are allowed.
5. No CAD-path Worker edits in this phase.
6. Stop after 1D-2.1. Do not start hole verification.

---

## 9. Success criteria

A box with two Ø6 holes must produce:

```text
hole_a  diameter MATCHED, position MATCHED → correspondence MATCHED → one cyl id
hole_b  diameter MATCHED, position MATCHED → correspondence MATCHED → the other cyl id
```

The same part with positions stripped must produce:

```text
hole_a  diameter MATCHED, position UNAVAILABLE → correspondence AMBIGUOUS → selected []
hole_b  diameter MATCHED, position UNAVAILABLE → correspondence AMBIGUOUS → selected []
```

Verification still must **not** say PASS on those holes. That is 1D-2.2.
