# MechCAD Feature Support Matrix

This matrix is generated from the actual codebase state, not from a wish list.
Legend: `Yes` = implemented and exercised, `Partial` = simplified implementation, `No` = unavailable, `Compat` = legacy compatibility entry only.

| Feature type | Role | Normalizer aliases | Capability edits | Worker | Validation | Geometry evidence | Semantic verification | Notes |
|---|---|---|---|---|---|---|---|---|
| `box_base` | base | Yes | update | Yes | Yes | Yes | Yes | Isolated box base verification only |
| `cylinder_base` | base | Yes | update | Yes | Yes | Yes | Yes | Isolated solid cylinder |
| `hollow_cylinder` | base | Yes | update | Yes | Yes | Yes | Yes | Tube base with inner bore |
| `link_plate` | base | Yes | update | Yes | Yes | No | No | Worker builds plate plus end cylinders |
| `through_hole` | remove | Yes | add/update/delete/change_type | Yes | Yes | Yes | Partial | 1D-2.2: existence/diameter/position/axis/depth/through-span; through is not a topological proof |
| `blind_hole` | remove | Yes | add/update/delete/change_type | Yes | Yes | Yes | Partial | 1D-2.2: same as through-hole except through is SKIPPED |
| `counterbore_hole` | remove | Yes | add/update/delete/change_type | Partial | Yes | No | No | Worker currently cuts a single cylindrical pocket, not a true shoulder |
| `rectangular_slot` | remove | Yes | add/update/delete/change_type | Partial | Yes | No | No | Worker uses a box subtract; no open-slot semantics |
| `rectangular_pocket` | remove | Yes | add/update/delete/change_type | Partial | Yes | No | No | Same simplified box cut as slot |
| `annular_groove` | remove | Yes | add/update/delete | Yes | Yes | Yes | Partial | 0.7.3-B: same evidence pipeline; axial z_start + root cylinder |
| `internal_annular_groove` | remove | Yes | add/update/delete | Yes | Yes | Yes | Partial | 0.7.3-B: same pipeline; depth from host inner vs root |
| `boss_cylinder` | add | Yes | add/update/delete | Yes | Yes | Yes | Partial | 0.7.3-A: same evidence pipeline as holes; height is V-span |
| `rectangular_pad` | add | Yes | add/update/delete | Yes | Yes | No | No | Box add |
| `rib_box` | add | Yes | add/update/delete | Partial | Yes | No | No | Worker treats it as a box; no rib-specific shape |
| `linear_pattern` | pattern | Yes | add/update/delete | Partial | Yes | No | No | Worker creates linear through-hole patterns only |
| `circular_pattern` | pattern | Yes | add/update/delete | Partial | Yes | No | No | Worker creates circular through-hole patterns only |
| `fillet` | modify | No | No | No | No | No | No | Known unsupported feature |
| `chamfer` | modify | No | No | No | No | No | No | Known unsupported feature |
| `spur_gear` | unsupported family | N/A | Compat | No | No | No | No | Gear teeth are not modeled; template emits a gear blank and marks teeth unsupported |
| `helical_gear` | unsupported | No | No | No | No | No | No | Known unsupported feature |
| `thread` | unsupported | No | No | No | No | No | No | Known unsupported feature |
| `sheet_metal` | unsupported | No | No | No | No | No | No | Known unsupported feature |

## Mode Semantics

- Strict mode: missing dimensions, unconfirmed assumptions, unsupported features, and unresolved material evidence conflicts block CAD.
- Smart mode: audited assumptions may run as concept preview, but `production_ready=false` until confirmed.
- Material evidence conflicts are never resolved by implicit source priority; the user must choose a value.
- Worker geometry change checks apply to all modeled features as a coarse volume guard; they are not semantic verification.

## Verification Scope

- 1D-2.1 can bind `box_base`, cylindrical bases, `through_hole`, `blind_hole`, and `boss_cylinder` to report-local geometry evidence. `AMBIGUOUS` never auto-selects.
- 1D-2.2 verifies `through_hole` / `blind_hole` only from MATCHED evidence. Isolated `box_base`, `cylinder_base`, and `hollow_cylinder` verification is unchanged.
- 0.7.3-A verifies `boss_cylinder` with the same evidence types. Additive vs subtractive cylindrical faces are not distinguished by 1D-1 measurement.
- 0.7.3-B verifies annular grooves with axial position on the same evidence types.
- All other feature types remain registered with an explicit `UNSUPPORTED` verification capability.
- `UNKNOWN` is never promoted to `PASS`; missing or unreliable measurements are reported as not proven.
