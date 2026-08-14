# Geometry Measurement Foundation (v0.7 1D-1)

## Scope

Geometry measurement describes observable Build123d/OpenCascade BRep geometry.
It is a deterministic, read-only inspection of the same final BRep used by the
CAD Worker for export. It does not reload STEP and it does not alter the shape.

Measurement is not verification. Measurement can report a cylindrical surface
with a measured diameter; verification would later decide whether that surface
satisfies an intended feature such as a hole. Feature matching, tolerances,
PASS/FAIL results, topology naming, and production readiness are outside 1D-1.

## Current facts

`backend.geometry.measurement` reports:

- axis-aligned bounding box in final BRep model coordinates;
- solid volume in `mm^3` when Build123d provides it;
- cylindrical faces with radius, diameter, OCCT cylinder-axis direction, OCCT
  cylinder-axis location, and the cylindrical face V-parameter span as height,
  when each property is available.

The Worker stores the report as `geometry_measurement` in
`execution_report.json`. It is independent of STEP/STL/OBJ export: a report is
attempted after construction and before export, and neither operation changes
the result of the other.

## Status and limitations

Facts explicitly use one of:

- `MEASUREMENT_SUCCESS`: the fact was observed;
- `MEASUREMENT_UNAVAILABLE`: the requested shape or property was not reliably
  available;
- `MEASUREMENT_ERROR`: the inspection operation failed.

No value is inferred from a FeaturePlan, evidence, an exported mesh, or a
fallback heuristic. The reported cylinder `center` is the OCCT cylindrical
surface axis location, not a claim that it is a feature center or face centroid.
Properties unavailable from the current Build123d/OCCT adapter remain absent
and are listed in `unavailable`.

`measurement_index` is deterministic only within one measurement report. It is
assigned after sorting by observed geometric values and is not a persistent
topology ID, a feature reference, or a solution to topological naming.

There is intentionally no tolerance policy in 1D-1. Measurements expose raw
observations; semantic verification and tolerance comparisons are reserved for
1D-2 after this foundation has been reviewed.
