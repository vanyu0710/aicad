from __future__ import annotations

"""Canonical, stateless semantic definitions for supported CAD feature types.

This registry is the preferred source for feature metadata. The legacy tables in
validation, AI planning, and normalization remain for compatibility and are
migrated one consumer at a time.
"""

from backend.schemas import (
    ConstraintSpec,
    FeatureCapability,
    FeatureDefinition,
    FeatureSemantics,
    ParameterSpec,
    VerificationContractSpec,
)


_CENTERED_REFERENCES = [
    "main_axis",
    "origin",
    "center",
    "flange_center",
    "model_center",
    "bottom_center",
    "bottom_end_center",
    "base_center",
    "top_center",
]


def _param(
    name: str,
    *,
    value_type: str = "float",
    unit: str | None = "mm",
    editable: bool = True,
    required: bool = False,
    minimum: float | None = 0.0,
    maximum: float | None = None,
    aliases: tuple[str, ...] = (),
) -> ParameterSpec:
    return ParameterSpec(
        name=name,
        value_type=value_type,
        unit=unit,
        editable=editable,
        minimum=minimum,
        maximum=maximum,
        required=required,
        aliases=list(aliases),
    )


def _id_param() -> ParameterSpec:
    return _param("id", value_type="string", unit=None, editable=False, required=True, minimum=None)


def _placement_param() -> ParameterSpec:
    return _param("position", value_type="placement", unit=None, editable=True, required=False, minimum=None)


def _extent_param() -> ParameterSpec:
    return _param("extent", value_type="extent", unit=None, editable=True, required=False, minimum=None)


def _constraint(cid: str, message: str, severity: str = "blocking") -> ConstraintSpec:
    return ConstraintSpec(id=cid, message=message, severity=severity)


def _verify(properties: list[str], status: str = "partial", notes: str = "") -> VerificationContractSpec:
    return VerificationContractSpec(properties=list(properties), status=status, notes=notes)


def _definition(
    feature_type: str,
    *,
    operation: str,
    geometry_effect: str,
    params: list[ParameterSpec],
    required_dimensions: list[str],
    optional_dimensions: list[str] | None = None,
    operations: list[str],
    convert_to_types: tuple[str, ...] = (),
    constraints: list[ConstraintSpec] | None = None,
    verification: VerificationContractSpec | None = None,
    parent_required: bool = True,
    implementation_status: str = "supported",
    centered_placements: list[str] | None = None,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_type=feature_type,
        operation=operation,
        geometry_effect=geometry_effect,
        parameters=params,
        required_dimensions=required_dimensions,
        optional_dimensions=optional_dimensions or [],
        references=list(_CENTERED_REFERENCES),
        centered_placements=centered_placements if centered_placements is not None else list(_CENTERED_REFERENCES),
        allowed_operations=operations,
        convert_to_types=list(convert_to_types),
        constraints=constraints or [],
        verification_contract=verification or _verify([], "unsupported"),
        parent_required=parent_required,
        implementation_status=implementation_status,
    )


def _unsupported_definition(feature_type: str) -> FeatureDefinition:
    return FeatureDefinition(
        feature_type=feature_type,
        operation="modify",
        geometry_effect="modify",
        parameters=[],
        required_dimensions=[],
        optional_dimensions=[],
        references=[],
        centered_placements=[],
        allowed_operations=[],
        convert_to_types=[],
        constraints=[],
        verification_contract=_verify([], "unsupported"),
        parent_required=False,
        implementation_status="unsupported",
    )


def to_capability(definition: FeatureDefinition) -> FeatureCapability:
    return FeatureCapability(
        feature_type=definition.feature_type,
        editable_parameters=list(definition.parameters),
        allowed_operations=list(definition.allowed_operations),
        reference_types=list(definition.references),
        convert_to_types=list(definition.convert_to_types),
        implementation_status=definition.implementation_status,
    )


def to_semantics(definition: FeatureDefinition) -> FeatureSemantics:
    return FeatureSemantics(
        feature_type=definition.feature_type,
        operation=definition.operation,
        geometry_effect=definition.geometry_effect,
        required_dimensions=list(definition.required_dimensions),
        optional_dimensions=list(definition.optional_dimensions),
        parent_required=definition.parent_required,
        centered_placements=list(definition.centered_placements or definition.references),
        implementation_status=definition.implementation_status,
    )


class FeatureDefinitionRegistry:
    """Static metadata registry for canonical feature semantics."""

    def __init__(self) -> None:
        self._definitions: dict[str, FeatureDefinition] = {}

    def register(self, definition: FeatureDefinition) -> None:
        if definition.feature_type in self._definitions:
            raise ValueError(f"Feature definition already registered: {definition.feature_type}")
        self._definitions[definition.feature_type] = definition

    def get(self, feature_type: str | None) -> FeatureDefinition | None:
        if not feature_type:
            return None
        return self._definitions.get(feature_type)

    def list(self) -> list[FeatureDefinition]:
        return list(self._definitions.values())

    def supported(self) -> list[FeatureDefinition]:
        return [definition for definition in self._definitions.values() if definition.implementation_status == "supported"]


def _build_default_definitions() -> list[FeatureDefinition]:
    positive = _constraint("positive_dimensions", "All dimensions must be positive.")
    return [
        _definition(
            "box_base",
            operation="base",
            geometry_effect="base",
            params=[_param("length"), _param("width"), _param("height"), _id_param()],
            required_dimensions=["length", "width", "height"],
            operations=["update"],
            constraints=[positive],
            verification=_verify(["volume", "bounding_box"], "partial"),
            parent_required=False,
            centered_placements=["origin", "bottom_center"],
        ),
        _definition(
            "cylinder_base",
            operation="base",
            geometry_effect="base",
            params=[_param("outer_diameter"), _param("length"), _id_param()],
            required_dimensions=["outer_diameter", "length"],
            operations=["update"],
            constraints=[positive],
            verification=_verify(["volume", "bounding_box"], "partial"),
            parent_required=False,
            centered_placements=["origin", "center"],
        ),
        _definition(
            "hollow_cylinder",
            operation="base",
            geometry_effect="base",
            params=[_param("outer_diameter"), _param("inner_diameter"), _param("length"), _id_param()],
            required_dimensions=["outer_diameter", "inner_diameter", "length"],
            operations=["update"],
            constraints=[
                positive,
                _constraint("inner_less_than_outer", "Inner diameter must be smaller than outer diameter."),
            ],
            verification=_verify(["volume", "bounding_box"], "partial"),
            parent_required=False,
            centered_placements=["origin", "bottom_end_center"],
        ),
        _definition(
            "through_hole",
            operation="remove",
            geometry_effect="remove",
            params=[_param("diameter"), _placement_param(), _extent_param(), _id_param()],
            required_dimensions=["diameter"],
            optional_dimensions=["depth"],
            operations=["add", "update", "delete", "change_type"],
            convert_to_types=("blind_hole", "counterbore_hole"),
            constraints=[positive],
            verification=_verify(["existence", "diameter", "position", "axis", "depth", "through"], "partial"),
            centered_placements=["model_center", "flange_center", "main_axis", "base_center"],
        ),
        _definition(
            "blind_hole",
            operation="remove",
            geometry_effect="remove",
            params=[_param("diameter"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            required_dimensions=["diameter", "depth"],
            operations=["add", "update", "delete", "change_type"],
            convert_to_types=("through_hole", "counterbore_hole"),
            constraints=[positive],
            verification=_verify(["existence", "diameter", "position", "axis", "depth"], "partial"),
            centered_placements=["model_center", "base_center", "origin"],
        ),
        _definition(
            "counterbore_hole",
            operation="remove",
            geometry_effect="remove",
            params=[_param("diameter"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            required_dimensions=["diameter", "depth"],
            operations=["add", "update", "delete", "change_type"],
            convert_to_types=("through_hole", "blind_hole"),
            constraints=[positive],
            verification=_verify(["diameter", "position", "axis", "depth"], "partial"),
            centered_placements=["model_center", "base_center", "origin"],
        ),
        _definition(
            "rectangular_slot",
            operation="remove",
            geometry_effect="remove",
            params=[_param("length"), _param("width"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            required_dimensions=["length", "width", "depth"],
            operations=["add", "update", "delete", "change_type"],
            convert_to_types=("rectangular_pocket",),
            constraints=[positive],
            verification=_verify(["length", "width", "depth", "position"], "unsupported"),
            centered_placements=["base_center", "top_center", "origin"],
        ),
        _definition(
            "rectangular_pocket",
            operation="remove",
            geometry_effect="remove",
            params=[_param("length"), _param("width"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            required_dimensions=["length", "width", "depth"],
            operations=["add", "update", "delete", "change_type"],
            convert_to_types=("rectangular_slot",),
            constraints=[positive],
            verification=_verify(["length", "width", "depth", "position"], "unsupported"),
            centered_placements=["base_center", "top_center", "origin"],
        ),
        _definition(
            "annular_groove",
            operation="remove",
            geometry_effect="remove",
            params=[_param("reduced_outer_diameter"), _param("axial_width"), _param("z_start"), _id_param()],
            required_dimensions=["reduced_outer_diameter", "axial_width", "z_start"],
            operations=["add", "update", "delete"],
            constraints=[
                positive,
                _constraint("groove_root_above_inner", "Groove root diameter must be greater than inner diameter."),
                _constraint("groove_within_length", "Groove position plus width must fit within the body length."),
            ],
            verification=_verify(["existence", "width", "depth", "position", "axis", "host"], "partial"),
            centered_placements=["main_axis", "model_center"],
        ),
        _definition(
            "internal_annular_groove",
            operation="remove",
            geometry_effect="remove",
            params=[_param("axial_width"), _param("groove_depth"), _param("z_start"), _id_param()],
            required_dimensions=["axial_width", "groove_depth", "z_start"],
            operations=["add", "update", "delete"],
            constraints=[
                positive,
                _constraint("internal_groove_root_under_outer", "Groove root diameter must be smaller than outer diameter."),
                _constraint("internal_groove_within_length", "Groove position plus width must fit within the body length."),
            ],
            verification=_verify(["existence", "width", "depth", "position", "axis", "host"], "partial"),
            centered_placements=["main_axis", "model_center"],
        ),
        _definition(
            "link_plate",
            operation="base",
            geometry_effect="base",
            params=[
                _param("length"),
                _param("width"),
                _param("height"),
                _param("end_diameter_1"),
                _param("end_diameter_2"),
                _id_param(),
            ],
            required_dimensions=["length", "width", "height", "end_diameter_1", "end_diameter_2"],
            operations=["update"],
            constraints=[positive],
            verification=_verify(["volume", "bounding_box"], "partial"),
            parent_required=False,
            centered_placements=["origin"],
        ),
        _unsupported_definition("spur_gear"),
        _definition(
            "boss_cylinder",
            operation="add",
            geometry_effect="add",
            params=[_param("diameter"), _param("height"), _placement_param(), _id_param()],
            required_dimensions=["diameter", "height"],
            operations=["add", "update", "delete"],
            constraints=[positive],
            verification=_verify(["existence", "diameter", "height", "axis", "position", "host"], "partial"),
            centered_placements=["base_center", "origin"],
        ),
        _definition(
            "rectangular_pad",
            operation="add",
            geometry_effect="add",
            params=[_param("length"), _param("width"), _param("height"), _placement_param(), _id_param()],
            required_dimensions=["length", "width", "height"],
            operations=["add", "update", "delete"],
            constraints=[positive],
            verification=_verify(["length", "width", "height", "position"], "unsupported"),
            centered_placements=["base_center", "origin"],
        ),
        _definition(
            "rib_box",
            operation="add",
            geometry_effect="add",
            params=[_param("length"), _param("width"), _param("height"), _placement_param(), _id_param()],
            required_dimensions=["length", "width", "height"],
            operations=["add", "update", "delete"],
            constraints=[positive],
            verification=_verify(["length", "width", "height", "position"], "unsupported"),
            centered_placements=["base_center", "origin"],
        ),
        _definition(
            "linear_pattern",
            operation="pattern",
            geometry_effect="pattern",
            params=[_param("count"), _param("spacing"), _param("diameter"), _placement_param(), _id_param()],
            required_dimensions=["count", "spacing", "diameter"],
            operations=["add", "update", "delete"],
            constraints=[_constraint("pattern_count_range", "Pattern count must be between 2 and 100.")],
            verification=_verify(["count", "spacing", "diameter", "position"], "partial"),
            centered_placements=["base_center", "origin"],
        ),
        _definition(
            "circular_pattern",
            operation="pattern",
            geometry_effect="pattern",
            params=[_param("count"), _param("pitch_radius"), _param("diameter"), _placement_param(), _id_param()],
            required_dimensions=["count", "pitch_radius", "diameter"],
            operations=["add", "update", "delete"],
            constraints=[_constraint("pattern_count_range", "Pattern count must be between 2 and 100.")],
            verification=_verify(["count", "pitch_radius", "diameter", "position"], "partial"),
            centered_placements=["model_center", "flange_center", "main_axis"],
        ),
        _unsupported_definition("fillet"),
        _unsupported_definition("chamfer"),
        _unsupported_definition("helical_gear"),
        _unsupported_definition("thread"),
        _unsupported_definition("sheet_metal"),
    ]


FEATURE_DEFINITIONS = FeatureDefinitionRegistry()
for _definition_value in _build_default_definitions():
    FEATURE_DEFINITIONS.register(_definition_value)


def get_feature_definition(feature_type: str | None) -> FeatureDefinition | None:
    """Canonical lookup for feature semantic metadata."""
    return FEATURE_DEFINITIONS.get(feature_type)
