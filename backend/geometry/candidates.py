"""Adapt 1D-1 measurement facts into report-local GeometryCandidate objects."""

from __future__ import annotations

from backend.geometry.references import host_aabb
from backend.schemas import GeometryCandidate, GeometryMeasurementReport


def collect_geometry_candidates(measurement: GeometryMeasurementReport) -> list[GeometryCandidate]:
    """Collect cylinder, AABB, and AABB-derived plane candidates only."""

    candidates: list[GeometryCandidate] = []
    bounds = host_aabb(measurement)
    if bounds is not None:
        candidates.append(
            GeometryCandidate(
                candidate_id="bbox:global",
                kind="bounding_region",
                measurement_ref="geometry_measurement.bounding_box",
                properties=dict(bounds),
            )
        )
        candidates.append(
            GeometryCandidate(
                candidate_id="box:global",
                kind="box",
                measurement_ref="geometry_measurement.bounding_box",
                properties=dict(bounds),
            )
        )
        candidates.extend(_aabb_planes(bounds))

    for cylinder in measurement.cylinders:
        candidates.append(
            GeometryCandidate(
                candidate_id=f"cyl:{cylinder.measurement_index}",
                kind="cylinder",
                measurement_ref=f"geometry_measurement.cylinders[{cylinder.measurement_index}]",
                status=cylinder.status,
                properties={
                    "measurement_index": cylinder.measurement_index,
                    "diameter": cylinder.diameter,
                    "radius": cylinder.radius,
                    "axis": cylinder.axis,
                    "axis_point": cylinder.center,
                    "height": cylinder.height,
                    "unavailable": list(cylinder.unavailable),
                    "center_definition": "OCCT cylindrical-surface axis location",
                },
            )
        )
    return candidates


def _aabb_planes(bounds: dict[str, float]) -> list[GeometryCandidate]:
    faces = (
        ("left", [bounds["min_x"], bounds["cy"], bounds["cz"]], [-1.0, 0.0, 0.0]),
        ("right", [bounds["max_x"], bounds["cy"], bounds["cz"]], [1.0, 0.0, 0.0]),
        ("front", [bounds["cx"], bounds["min_y"], bounds["cz"]], [0.0, -1.0, 0.0]),
        ("back", [bounds["cx"], bounds["max_y"], bounds["cz"]], [0.0, 1.0, 0.0]),
        ("bottom", [bounds["cx"], bounds["cy"], bounds["min_z"]], [0.0, 0.0, -1.0]),
        ("top", [bounds["cx"], bounds["cy"], bounds["max_z"]], [0.0, 0.0, 1.0]),
    )
    return [
        GeometryCandidate(
            candidate_id=f"plane:aabb:{name}",
            kind="plane",
            measurement_ref="geometry_measurement.bounding_box",
            properties={"name": name, "origin": origin, "normal": normal, "source": "aabb_derived"},
        )
        for name, origin, normal in faces
    ]
