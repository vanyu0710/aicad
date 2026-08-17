"""Resolve high-reliability ReferenceContext frames from plan + AABB facts."""

from __future__ import annotations

from backend.geometry.signatures import _CENTERED_REFERENCES, _position_expectation
from backend.schemas import (
    BoundingBoxFact,
    FeaturePlanV3,
    FeatureV3,
    GeometryMeasurementReport,
    ReferenceContext,
    ReferenceFrame,
)


_WORLD_AXES = {
    "x": [1.0, 0.0, 0.0],
    "y": [0.0, 1.0, 0.0],
    "z": [0.0, 0.0, 1.0],
}

_AABB_FACES = (
    ("left", "min_x", [-1.0, 0.0, 0.0]),
    ("right", "max_x", [1.0, 0.0, 0.0]),
    ("front", "min_y", [0.0, -1.0, 0.0]),
    ("back", "max_y", [0.0, 1.0, 0.0]),
    ("bottom", "min_z", [0.0, 0.0, -1.0]),
    ("top", "max_z", [0.0, 0.0, 1.0]),
)


def resolve_reference_context(
    plan: FeaturePlanV3,
    feature: FeatureV3,
    measurement: GeometryMeasurementReport,
) -> ReferenceContext:
    """Resolve origin, world planes, and AABB faces. Never guess a frame."""

    frames = [_origin_frame(plan), _world_plane("world_xy", [0.0, 0.0, 1.0]), _world_axis("world_z")]
    box = measurement.bounding_box
    has_aabb = box is not None and box.status == "MEASUREMENT_SUCCESS" and _finite_box(box)
    if has_aabb:
        frames.extend(_aabb_frames(box))

    placement_name = feature.placement.reference or "origin"
    known = placement_name in _CENTERED_REFERENCES or placement_name in {item[0] for item in _AABB_FACES}
    if not known:
        return ReferenceContext(
            feature_id=feature.id,
            status="UNAVAILABLE",
            frames=frames,
            placement_frame=None,
            reason=f"Placement reference '{placement_name}' is not a 1D-2.1 high-reliability frame.",
        )

    if placement_name in {item[0] for item in _AABB_FACES} and not has_aabb:
        return ReferenceContext(
            feature_id=feature.id,
            status="UNAVAILABLE",
            frames=frames,
            placement_frame=None,
            reason="AABB face references require a successful bounding-box measurement.",
        )

    position, evaluable, reason = _position_expectation(feature)
    placement_frame = "origin" if placement_name in _CENTERED_REFERENCES else placement_name
    return ReferenceContext(
        feature_id=feature.id,
        status="MATCHED",
        frames=frames,
        placement_frame=placement_frame,
        resolved_position=position if evaluable else None,
        reason=reason,
    )


def host_aabb(measurement: GeometryMeasurementReport) -> dict[str, float] | None:
    box = measurement.bounding_box
    if box is None or box.status != "MEASUREMENT_SUCCESS" or not _finite_box(box):
        return None
    return {
        "min_x": float(box.min_x),
        "min_y": float(box.min_y),
        "min_z": float(box.min_z),
        "max_x": float(box.max_x),
        "max_y": float(box.max_y),
        "max_z": float(box.max_z),
        "size_x": float(box.size_x),
        "size_y": float(box.size_y),
        "size_z": float(box.size_z),
        "cx": (float(box.min_x) + float(box.max_x)) / 2.0,
        "cy": (float(box.min_y) + float(box.max_y)) / 2.0,
        "cz": (float(box.min_z) + float(box.max_z)) / 2.0,
    }


def frame_by_name(context: ReferenceContext, name: str) -> ReferenceFrame | None:
    for frame in context.frames:
        if frame.name == name:
            return frame
    return None


def _origin_frame(plan: FeaturePlanV3) -> ReferenceFrame:
    source = "plan.coordinate_system"
    _ = plan.coordinate_system
    return ReferenceFrame(name="origin", origin=[0.0, 0.0, 0.0], axes=dict(_WORLD_AXES), source=source)


def _world_plane(name: str, normal: list[float]) -> ReferenceFrame:
    return ReferenceFrame(name=name, origin=[0.0, 0.0, 0.0], axes=dict(_WORLD_AXES), normal=normal, source="plan.coordinate_system")


def _world_axis(name: str) -> ReferenceFrame:
    return ReferenceFrame(name=name, origin=[0.0, 0.0, 0.0], axes=dict(_WORLD_AXES), normal=_WORLD_AXES["z"], source="plan.coordinate_system")


def _aabb_frames(box: BoundingBoxFact) -> list[ReferenceFrame]:
    values = host_aabb_from_box(box)
    frames = [
        ReferenceFrame(
            name="host_aabb",
            origin=[values["cx"], values["cy"], values["min_z"]],
            axes=dict(_WORLD_AXES),
            source="geometry_measurement.bounding_box",
        )
    ]
    for name, key, normal in _AABB_FACES:
        origin = [values["cx"], values["cy"], values["cz"]]
        if key.endswith("x"):
            origin[0] = values[key]
        elif key.endswith("y"):
            origin[1] = values[key]
        else:
            origin[2] = values[key]
        frames.append(
            ReferenceFrame(
                name=name,
                origin=origin,
                axes=dict(_WORLD_AXES),
                normal=list(normal),
                source="geometry_measurement.bounding_box",
            )
        )
    return frames


def host_aabb_from_box(box: BoundingBoxFact) -> dict[str, float]:
    return {
        "min_x": float(box.min_x),
        "min_y": float(box.min_y),
        "min_z": float(box.min_z),
        "max_x": float(box.max_x),
        "max_y": float(box.max_y),
        "max_z": float(box.max_z),
        "size_x": float(box.size_x),
        "size_y": float(box.size_y),
        "size_z": float(box.size_z),
        "cx": (float(box.min_x) + float(box.max_x)) / 2.0,
        "cy": (float(box.min_y) + float(box.max_y)) / 2.0,
        "cz": (float(box.min_z) + float(box.max_z)) / 2.0,
    }


def _finite_box(box: BoundingBoxFact) -> bool:
    required = (box.min_x, box.min_y, box.min_z, box.max_x, box.max_y, box.max_z, box.size_x, box.size_y, box.size_z)
    return all(value is not None for value in required)
