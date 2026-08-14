"""Deterministic, read-only measurements of Build123d/OpenCascade BRep shapes.

Measurement reports observable geometry only. In particular, a cylindrical
surface is not identified as a hole, boss, or any other FeaturePlan feature.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from build123d import GeomType
from OCP.BRepAdaptor import BRepAdaptor_Surface

from backend.schemas import (
    BoundingBoxFact,
    CylinderFact,
    GeometryMeasurementReport,
    VolumeFact,
)


_SUCCESS = "MEASUREMENT_SUCCESS"
_UNAVAILABLE = "MEASUREMENT_UNAVAILABLE"
_ERROR = "MEASUREMENT_ERROR"


def measure_shape(shape: Any) -> GeometryMeasurementReport:
    """Measure a final Build123d shape without changing it.

    ``BuildPart`` is accepted as a convenience for the existing worker, but
    measurements are always taken from its ``part`` BRep rather than from a
    plan, exported artifact, or reconstructed STEP file.
    """

    target = _target_shape(shape)
    if target is None:
        return GeometryMeasurementReport(
            status=_UNAVAILABLE,
            errors=["No final BRep shape was supplied for measurement."],
        )

    bounding_box = measure_bounding_box(target)
    volume = measure_volume(target)
    cylinders, cylinder_errors = measure_cylindrical_surfaces(target)

    errors = list(cylinder_errors)
    for fact in (bounding_box, volume):
        if fact.status == _ERROR and fact.error:
            errors.append(fact.error)

    status = _ERROR if errors else _SUCCESS
    return GeometryMeasurementReport(
        status=status,
        bounding_box=bounding_box,
        volume=volume,
        cylinders=cylinders,
        errors=errors,
    )


def measure_bounding_box(shape: Any) -> BoundingBoxFact:
    """Return the final BRep axis-aligned bounding box in model coordinates."""

    target = _target_shape(shape)
    if target is None:
        return BoundingBoxFact(
            status=_UNAVAILABLE,
            error="No final BRep shape was supplied for bounding-box measurement.",
        )
    try:
        box = target.bounding_box()
        minimum = box.min
        maximum = box.max
        size = box.size
        values = [
            minimum.X,
            minimum.Y,
            minimum.Z,
            maximum.X,
            maximum.Y,
            maximum.Z,
            size.X,
            size.Y,
            size.Z,
        ]
        if not all(isfinite(float(value)) for value in values):
            return BoundingBoxFact(
                status=_UNAVAILABLE,
                error="Build123d returned a non-finite bounding-box value.",
            )
        return BoundingBoxFact(
            min_x=float(minimum.X),
            min_y=float(minimum.Y),
            min_z=float(minimum.Z),
            max_x=float(maximum.X),
            max_y=float(maximum.Y),
            max_z=float(maximum.Z),
            size_x=float(size.X),
            size_y=float(size.Y),
            size_z=float(size.Z),
            metadata={"coordinate_system": "final BRep model coordinates"},
        )
    except Exception as exc:
        return BoundingBoxFact(status=_ERROR, error=f"Bounding-box measurement failed: {exc}")


def measure_volume(shape: Any) -> VolumeFact:
    """Return final BRep solid volume in cubic millimetres when available."""

    target = _target_shape(shape)
    if target is None:
        return VolumeFact(
            status=_UNAVAILABLE,
            error="No final BRep shape was supplied for volume measurement.",
        )
    try:
        value = getattr(target, "volume", None)
        value = value() if callable(value) else value
        if value is None or not isfinite(float(value)):
            return VolumeFact(
                status=_UNAVAILABLE,
                error="Build123d did not provide a finite solid volume.",
            )
        return VolumeFact(volume=float(value))
    except Exception as exc:
        return VolumeFact(status=_ERROR, error=f"Volume measurement failed: {exc}")


def measure_cylindrical_surfaces(shape: Any) -> tuple[list[CylinderFact], list[str]]:
    """Enumerate observable cylindrical faces without assigning feature meaning.

    ``measurement_index`` is deterministic for a report after sorting by
    measured values. It is not a persistent topological identity and must not
    be used for feature references.
    """

    target = _target_shape(shape)
    if target is None:
        return [], ["No final BRep shape was supplied for cylindrical-surface measurement."]
    try:
        faces = target.faces()
    except Exception as exc:
        return [], [f"Cylindrical-surface enumeration failed: {exc}"]

    candidates: list[dict[str, Any]] = []
    for face in faces:
        if not _is_cylindrical_face(face):
            continue
        candidates.append(_measure_cylindrical_face(face))

    candidates.sort(key=_cylinder_sort_key)
    facts = []
    for index, candidate in enumerate(candidates):
        facts.append(
            CylinderFact(
                measurement_index=index,
                status=candidate["status"],
                radius=candidate["radius"],
                diameter=candidate["diameter"],
                axis=candidate["axis"],
                center=candidate["center"],
                height=candidate["height"],
                unavailable=candidate["unavailable"],
                error=candidate["error"],
                metadata={
                    "center_definition": "OCCT cylindrical-surface axis location",
                    "measurement_index_scope": "single measurement report only",
                },
            )
        )
    return facts, []


def _target_shape(shape: Any) -> Any | None:
    if shape is None:
        return None
    return getattr(shape, "part", shape)


def _is_cylindrical_face(face: Any) -> bool:
    geometry_type = getattr(face, "geom_type", None)
    return geometry_type == GeomType.CYLINDER or str(geometry_type).endswith("CYLINDER")


def _measure_cylindrical_face(face: Any) -> dict[str, Any]:
    unavailable: list[str] = []
    radius = diameter = height = None
    axis = center = None
    error = None
    try:
        adaptor = BRepAdaptor_Surface(face.wrapped, True)
        cylinder = adaptor.Cylinder()

        raw_radius = float(cylinder.Radius())
        if isfinite(raw_radius):
            radius = raw_radius
            diameter = raw_radius * 2.0
        else:
            unavailable.extend(["radius", "diameter"])

        direction = cylinder.Axis().Direction()
        raw_axis = [float(direction.X()), float(direction.Y()), float(direction.Z())]
        if all(isfinite(value) for value in raw_axis):
            axis = raw_axis
        else:
            unavailable.append("axis")

        location = cylinder.Location()
        raw_center = [float(location.X()), float(location.Y()), float(location.Z())]
        if all(isfinite(value) for value in raw_center):
            center = raw_center
        else:
            unavailable.append("center")

        first_v = float(adaptor.FirstVParameter())
        last_v = float(adaptor.LastVParameter())
        raw_height = abs(last_v - first_v)
        if isfinite(raw_height):
            height = raw_height
        else:
            unavailable.append("height")
    except Exception as exc:
        error = f"Cylindrical-surface measurement failed: {exc}"
        unavailable.extend(name for name in ("radius", "diameter", "axis", "center", "height") if name not in unavailable)

    status = _SUCCESS if radius is not None and axis is not None else _UNAVAILABLE
    return {
        "status": status,
        "radius": radius,
        "diameter": diameter,
        "axis": axis,
        "center": center,
        "height": height,
        "unavailable": unavailable,
        "error": error,
    }


def _cylinder_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    infinity = float("inf")
    return (
        candidate["radius"] if candidate["radius"] is not None else infinity,
        *(candidate["axis"] or [infinity, infinity, infinity]),
        *(candidate["center"] or [infinity, infinity, infinity]),
        candidate["height"] if candidate["height"] is not None else infinity,
        tuple(candidate["unavailable"]),
    )
