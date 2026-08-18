"""Compile FeatureGeometrySignature from FeatureDefinition + FeatureV3.

Signatures are queries. They never read BRep, measurement, or invented values.
"""

from __future__ import annotations

from typing import Any

from backend.feature_definitions import FEATURE_DEFINITIONS, get_feature_definition
from backend.schemas import (
    ExpectedPrimitive,
    FeatureGeometrySignature,
    FeaturePlanV3,
    FeatureV3,
    SignatureConstraint,
)


_CENTERED_REFERENCES = {
    "main_axis",
    "origin",
    "center",
    "flange_center",
    "model_center",
    "bottom_center",
    "bottom_end_center",
    "base_center",
    "top_center",
}

_PRIMARY_KIND: dict[str, str] = {
    "box_base": "bounding_region",
    "link_plate": "bounding_region",
    "rectangular_pad": "box",
    "rib_box": "box",
    "rectangular_slot": "box",
    "rectangular_pocket": "box",
    "cylinder_base": "cylinder",
    "hollow_cylinder": "cylinder",
    "through_hole": "cylinder",
    "blind_hole": "cylinder",
    "counterbore_hole": "cylinder",
    "boss_cylinder": "cylinder",
    "annular_groove": "cylinder",
    "internal_annular_groove": "cylinder",
    "fillet": "edge",
    "chamfer": "edge",
}

_BINDABLE = {
    "box_base",
    "cylinder_base",
    "hollow_cylinder",
    "through_hole",
    "blind_hole",
    "boss_cylinder",
    "annular_groove",
    "internal_annular_groove",
}

_AXIS_VECTORS = {
    "X": [1.0, 0.0, 0.0],
    "Y": [0.0, 1.0, 0.0],
    "Z": [0.0, 0.0, 1.0],
}


def compile_feature_signature(feature: FeatureV3) -> FeatureGeometrySignature:
    """Build a geometric query for one feature without inspecting BRep."""

    definition = get_feature_definition(feature.type)
    primary_kind = _PRIMARY_KIND.get(feature.type, "unsupported")
    bindable = feature.type in _BINDABLE
    required_properties = list(definition.verification_contract.properties) if definition is not None else []
    primitives, constraints = _primitives_and_constraints(feature, primary_kind, required_properties)
    return FeatureGeometrySignature(
        feature_id=feature.id,
        feature_type=feature.type,
        operation=feature.operation,
        geometry_effect=definition.geometry_effect if definition is not None else feature.operation,
        primary_kind=primary_kind,  # type: ignore[arg-type]
        bindable=bindable,
        primitives=primitives,
        constraints=constraints,
    )


def compile_plan_signatures(plan: FeaturePlanV3) -> list[FeatureGeometrySignature]:
    return [compile_feature_signature(feature) for feature in _plan_features(plan)]


def registered_signature_types() -> list[str]:
    return [definition.feature_type for definition in FEATURE_DEFINITIONS.list()]


def _plan_features(plan: FeaturePlanV3) -> list[FeatureV3]:
    features: list[FeatureV3] = []
    if plan.base_feature is not None:
        features.append(plan.base_feature)
    features.extend(plan.features)
    return features


def _primitives_and_constraints(
    feature: FeatureV3,
    primary_kind: str,
    required_properties: list[str],
) -> tuple[list[ExpectedPrimitive], list[SignatureConstraint]]:
    if feature.type in {"through_hole", "blind_hole", "boss_cylinder"}:
        return _cylinder_child_signature(feature, required_properties, role="bore" if "hole" in feature.type else "outer")
    if feature.type in {"annular_groove", "internal_annular_groove"}:
        return _groove_signature(feature, required_properties)
    if feature.type in {"cylinder_base", "hollow_cylinder"}:
        return _cylinder_base_signature(feature, required_properties)
    if feature.type == "box_base":
        return _box_signature(feature, required_properties)
    return (
        [ExpectedPrimitive(role="region", kind=primary_kind if primary_kind != "unsupported" else "unsupported")],
        [
            _constraint("type", required=True, evaluable=False, expected=primary_kind, reason="Feature type is not bindable in 1D-2.1."),
            _constraint("relationship", required=False, evaluable=False, reason="Relationship evidence is reserved for a later phase."),
        ],
    )


def _cylinder_child_signature(
    feature: FeatureV3,
    required_properties: list[str],
    *,
    role: str,
) -> tuple[list[ExpectedPrimitive], list[SignatureConstraint]]:
    diameter = _dimension(feature, "diameter", "hole_diameter", "outer_diameter")
    axis_name = feature.placement.axis or "Z"
    position_expected, position_evaluable, position_reason = _position_expectation(feature)
    primitives = [
        ExpectedPrimitive(
            role=role,  # type: ignore[arg-type]
            kind="cylinder",
            expected={"diameter": diameter, "axis": axis_name},
        )
    ]
    constraints = [
        _constraint("type", required=True, evaluable=True, expected="cylinder"),
        _constraint(
            "dimension",
            required="diameter" in required_properties,
            evaluable=diameter is not None and diameter > 0,
            property_name="diameter",
            expected=diameter,
            reason="" if diameter is not None and diameter > 0 else "No positive expected diameter was supplied.",
        ),
        _constraint(
            "axis",
            required="axis" in required_properties,
            evaluable=axis_name in _AXIS_VECTORS,
            property_name="axis",
            expected=_AXIS_VECTORS.get(axis_name),
        ),
        _constraint(
            "position",
            required="position" in required_properties,
            evaluable=position_evaluable,
            property_name="position",
            expected=position_expected,
            reason=position_reason,
        ),
        _constraint("region", required=False, evaluable=True, property_name="region"),
        _constraint("host", required=False, evaluable=True, property_name="host"),
        _constraint(
            "relationship",
            required=False,
            evaluable=False,
            property_name="depth",
            reason="Depth and through-ness are not proven in 1D-2.1; cylinder height is a V-span, not hole depth.",
        ),
    ]
    return primitives, constraints


def _groove_signature(
    feature: FeatureV3,
    required_properties: list[str],
) -> tuple[list[ExpectedPrimitive], list[SignatureConstraint]]:
    root = _dimension(feature, "reduced_outer_diameter")
    width = _dimension(feature, "axial_width", "width")
    z_start = _dimension(feature, "z_start")
    axis_name = feature.placement.axis or "Z"
    axial = None if z_start is None else {"z_start": z_start, "width": width}
    primitives = [
        ExpectedPrimitive(
            role="region",
            kind="cylinder",
            expected={"diameter": root, "axis": axis_name, "z_start": z_start, "width": width},
        )
    ]
    constraints = [
        _constraint("type", required=True, evaluable=True, expected="cylinder"),
        _constraint(
            "position",
            required="position" in required_properties or z_start is not None,
            evaluable=z_start is not None,
            property_name="z_start",
            expected=axial,
            reason="" if z_start is not None else "Groove z_start is missing, so axial position cannot be evaluated.",
        ),
        _constraint(
            "axis",
            required="axis" in required_properties,
            evaluable=axis_name in _AXIS_VECTORS,
            property_name="axis",
            expected=_AXIS_VECTORS.get(axis_name),
        ),
        _constraint(
            "dimension",
            required="width" in required_properties or "depth" in required_properties,
            evaluable=root is not None and root > 0,
            property_name="diameter",
            expected=root,
            reason="" if root is not None and root > 0 else "Groove root diameter is not on the feature (internal grooves bind by Z/width).",
        ),
        _constraint("host", required=True, evaluable=True, property_name="host"),
        _constraint("relationship", required=False, evaluable=False, reason="Groove relationships are not resolved in 0.7.3-B."),
    ]
    return primitives, constraints


def _cylinder_base_signature(
    feature: FeatureV3,
    required_properties: list[str],
) -> tuple[list[ExpectedPrimitive], list[SignatureConstraint]]:
    outer = _dimension(feature, "outer_diameter")
    inner = _dimension(feature, "inner_diameter") if feature.type == "hollow_cylinder" else None
    axis_name = feature.placement.axis or "Z"
    primitives = [
        ExpectedPrimitive(role="outer", kind="cylinder", expected={"diameter": outer, "axis": axis_name}),
        ExpectedPrimitive(role="region", kind="bounding_region"),
    ]
    if feature.type == "hollow_cylinder":
        primitives.insert(1, ExpectedPrimitive(role="inner", kind="cylinder", expected={"diameter": inner}))
    constraints = [
        _constraint("type", required=True, evaluable=True, expected="cylinder"),
        _constraint(
            "dimension",
            required=True,
            evaluable=outer is not None and outer > 0,
            property_name="diameter",
            expected=outer,
            reason="" if outer is not None and outer > 0 else "No positive expected outer diameter was supplied.",
        ),
        _constraint(
            "axis",
            required="axis" in required_properties,
            evaluable=axis_name in _AXIS_VECTORS,
            property_name="axis",
            expected=_AXIS_VECTORS.get(axis_name),
        ),
        _constraint("host", required=False, evaluable=True, property_name="host"),
        _constraint("relationship", required=False, evaluable=False, reason="Base relationships are not resolved in 1D-2.1."),
    ]
    return primitives, constraints


def _box_signature(
    feature: FeatureV3,
    required_properties: list[str],
) -> tuple[list[ExpectedPrimitive], list[SignatureConstraint]]:
    sizes = {
        "size_x": _dimension(feature, "length"),
        "size_y": _dimension(feature, "width"),
        "size_z": _dimension(feature, "height"),
    }
    evaluable = all(value is not None and value > 0 for value in sizes.values())
    primitives = [
        ExpectedPrimitive(role="region", kind="bounding_region", expected=sizes),
        ExpectedPrimitive(role="host", kind="box", expected=sizes),
    ]
    constraints = [
        _constraint("type", required=True, evaluable=True, expected="bounding_region"),
        _constraint(
            "dimension",
            required=any(name in required_properties for name in ("volume", "bounding_box", "length", "width", "height")),
            evaluable=evaluable,
            property_name="bounding_box",
            expected=sizes if evaluable else None,
            reason="" if evaluable else "Box length/width/height are incomplete.",
        ),
        _constraint("host", required=False, evaluable=True, property_name="host"),
        _constraint("relationship", required=False, evaluable=False, reason="Base relationships are not resolved in 1D-2.1."),
    ]
    return primitives, constraints


def _position_expectation(feature: FeatureV3) -> tuple[list[float] | None, bool, str]:
    placement = feature.placement
    if placement.x is not None and placement.y is not None:
        return [float(placement.x), float(placement.y), float(placement.z or 0.0)], True, ""
    return (
        None,
        False,
        "Placement X/Y is not explicit, so position is UNAVAILABLE; a Worker origin default is not invented.",
    )


def _dimension(feature: FeatureV3, *names: str) -> float | None:
    for name in names:
        dimension = feature.dimensions.get(name)
        if dimension is None or dimension.value is None:
            continue
        try:
            return float(dimension.value)
        except (TypeError, ValueError):
            continue
    return None


def _constraint(
    kind: str,
    *,
    required: bool,
    evaluable: bool,
    property_name: str = "",
    expected: Any = None,
    reason: str = "",
) -> SignatureConstraint:
    return SignatureConstraint(
        kind=kind,  # type: ignore[arg-type]
        required=required,
        evaluable=evaluable,
        property_name=property_name,
        expected=expected,
        reason=reason,
    )
