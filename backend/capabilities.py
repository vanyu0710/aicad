from __future__ import annotations

from typing import Any

from backend.schemas import (
    CapabilityIssue,
    DimensionV3,
    FeatureCapability,
    FeatureEditOperation,
    FeatureEditSet,
    FeaturePlanV3,
    ParameterSpec,
    PlacementV3,
)

CAPABILITY_UNKNOWN_FEATURE = "CAPABILITY_UNKNOWN_FEATURE"
CAPABILITY_OPERATION_NOT_ALLOWED = "CAPABILITY_OPERATION_NOT_ALLOWED"
CAPABILITY_PARAMETER_UNKNOWN = "CAPABILITY_PARAMETER_UNKNOWN"
CAPABILITY_PARAMETER_NOT_EDITABLE = "CAPABILITY_PARAMETER_NOT_EDITABLE"
CAPABILITY_PARAMETER_INVALID_VALUE = "CAPABILITY_PARAMETER_INVALID_VALUE"

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


def _issue(
    error_code: str,
    reason: str,
    *,
    feature_type: str | None = None,
    feature_id: str | None = None,
    operation: str | None = None,
    parameter: str | None = None,
    recoverable: bool = False,
) -> CapabilityIssue:
    return CapabilityIssue(
        error_code=error_code,
        feature_type=feature_type,
        feature_id=feature_id,
        operation=operation,
        parameter=parameter,
        reason=reason,
        recoverable=recoverable,
    )


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


def _capability(
    feature_type: str,
    params: list[ParameterSpec],
    operations: list[str],
    *,
    convert_to_types: tuple[str, ...] = (),
) -> FeatureCapability:
    return FeatureCapability(
        feature_type=feature_type,
        editable_parameters=params,
        allowed_operations=list(operations),
        reference_types=list(_CENTERED_REFERENCES),
        convert_to_types=list(convert_to_types),
        implementation_status="supported",
    )


class CapabilityRegistry:
    """Static metadata registry for editable feature operations and parameters.

    This layer only answers "is this edit structurally allowed"; geometry,
    dependencies, strict/smart policy, and manufacturability are validated by
    the existing FeaturePlan validation layer.
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, FeatureCapability] = {}

    def register(self, capability: FeatureCapability) -> None:
        if capability.feature_type in self._capabilities:
            raise ValueError(f"Capability already registered: {capability.feature_type}")
        self._capabilities[capability.feature_type] = capability

    def get(self, feature_type: str | None) -> FeatureCapability | None:
        if not feature_type:
            return None
        return self._capabilities.get(feature_type)

    def supports_operation(self, feature_type: str | None, operation: str) -> bool:
        capability = self.get(feature_type)
        return capability is not None and operation in capability.allowed_operations

    def is_parameter_editable(self, feature_type: str | None, parameter: str) -> bool:
        capability = self.get(feature_type)
        if capability is None:
            return False
        return any(spec.name == parameter and spec.editable for spec in capability.editable_parameters)

    def validate_parameter(
        self,
        feature_type: str | None,
        parameter: str,
        value: Any,
        *,
        feature_id: str | None = None,
        operation: str | None = None,
    ) -> list[CapabilityIssue]:
        capability = self.get(feature_type)
        if capability is None:
            return [
                _issue(
                    CAPABILITY_UNKNOWN_FEATURE,
                    f"feature type {feature_type or 'unknown'} has no registered capability",
                    feature_type=feature_type,
                    feature_id=feature_id,
                    operation=operation,
                    parameter=parameter,
                )
            ]
        spec = next((item for item in capability.editable_parameters if item.name == parameter), None)
        if spec is None:
            return [
                _issue(
                    CAPABILITY_PARAMETER_UNKNOWN,
                    f"parameter {parameter} is not declared for {feature_type}",
                    feature_type=feature_type,
                    feature_id=feature_id,
                    operation=operation,
                    parameter=parameter,
                )
            ]
        if not spec.editable:
            return [
                _issue(
                    CAPABILITY_PARAMETER_NOT_EDITABLE,
                    f"parameter {parameter} is read-only for {feature_type}",
                    feature_type=feature_type,
                    feature_id=feature_id,
                    operation=operation,
                    parameter=parameter,
                )
            ]
        return _validate_value(
            spec,
            value,
            feature_type=feature_type,
            feature_id=feature_id,
            operation=operation,
        )


def _validate_value(
    spec: ParameterSpec,
    value: Any,
    *,
    feature_type: str,
    feature_id: str | None,
    operation: str | None,
) -> list[CapabilityIssue]:
    if value is None:
        return []

    valid = True
    if spec.value_type == "float":
        valid = not isinstance(value, bool) and isinstance(value, (int, float))
        if valid:
            numeric = float(value)
            if spec.minimum is not None and numeric < spec.minimum:
                return [
                    _issue(
                        CAPABILITY_PARAMETER_INVALID_VALUE,
                        f"parameter {spec.name} must be >= {spec.minimum}",
                        feature_type=feature_type,
                        feature_id=feature_id,
                        operation=operation,
                        parameter=spec.name,
                        recoverable=True,
                    )
                ]
            if spec.maximum is not None and numeric > spec.maximum:
                return [
                    _issue(
                        CAPABILITY_PARAMETER_INVALID_VALUE,
                        f"parameter {spec.name} must be <= {spec.maximum}",
                        feature_type=feature_type,
                        feature_id=feature_id,
                        operation=operation,
                        parameter=spec.name,
                        recoverable=True,
                    )
                ]
    elif spec.value_type == "int":
        valid = not isinstance(value, bool) and isinstance(value, int)
    elif spec.value_type == "string":
        valid = isinstance(value, str)
    elif spec.value_type == "placement":
        valid = isinstance(value, (PlacementV3, dict))
    elif spec.value_type == "extent":
        valid = isinstance(value, str)
    elif spec.value_type == "boolean":
        valid = isinstance(value, bool)

    if not valid:
        return [
            _issue(
                CAPABILITY_PARAMETER_INVALID_VALUE,
                f"parameter {spec.name} expects value_type {spec.value_type}",
                feature_type=feature_type,
                feature_id=feature_id,
                operation=operation,
                parameter=spec.name,
                recoverable=True,
            )
        ]
    return []


def _find_feature(plan: FeaturePlanV3, feature_id: str | None) -> Any:
    if not feature_id:
        return None
    candidates = [plan.base_feature] if plan.base_feature else []
    candidates.extend(plan.features)
    return next((feature for feature in candidates if feature is not None and feature.id == feature_id), None)


def validate_feature_operation(operation: FeatureEditOperation, plan: FeaturePlanV3) -> list[CapabilityIssue]:
    """Validate one feature edit operation against the static capability registry."""
    issues: list[CapabilityIssue] = []
    if operation.op == "add":
        feature_type = operation.type
        feature_id = operation.feature_id
        if not feature_type:
            return [
                _issue(
                    CAPABILITY_UNKNOWN_FEATURE,
                    "add operation requires a feature type",
                    feature_id=feature_id,
                    operation="add",
                )
            ]
    else:
        feature = _find_feature(plan, operation.feature_id)
        if feature is None:
            return [
                _issue(
                    CAPABILITY_UNKNOWN_FEATURE,
                    f"feature {operation.feature_id} not found in plan",
                    feature_id=operation.feature_id,
                    operation=operation.op,
                )
            ]
        feature_type = feature.type
        feature_id = feature.id

    capability = CAPABILITIES.get(feature_type)
    if capability is None:
        return [
            _issue(
                CAPABILITY_UNKNOWN_FEATURE,
                f"feature type {feature_type} has no registered capability",
                feature_type=feature_type,
                feature_id=feature_id,
                operation=operation.op,
            )
        ]

    if operation.op not in capability.allowed_operations:
        issues.append(
            _issue(
                CAPABILITY_OPERATION_NOT_ALLOWED,
                f"operation {operation.op} is not allowed for {feature_type}",
                feature_type=feature_type,
                feature_id=feature_id,
                operation=operation.op,
            )
        )

    if operation.op == "change_type":
        target_type = operation.type
        if not target_type:
            issues.append(
                _issue(
                    CAPABILITY_OPERATION_NOT_ALLOWED,
                    "change_type requires a target feature type",
                    feature_type=feature_type,
                    feature_id=feature_id,
                    operation=operation.op,
                )
            )
        elif target_type not in capability.convert_to_types:
            issues.append(
                _issue(
                    CAPABILITY_OPERATION_NOT_ALLOWED,
                    f"cannot convert {feature_type} to {target_type}",
                    feature_type=feature_type,
                    feature_id=feature_id,
                    operation=operation.op,
                )
            )
        elif CAPABILITIES.get(target_type) is None:
            issues.append(
                _issue(
                    CAPABILITY_UNKNOWN_FEATURE,
                    f"target feature type {target_type} has no registered capability",
                    feature_type=target_type,
                    feature_id=feature_id,
                    operation=operation.op,
                )
            )

    edit_feature_type = operation.type if operation.op == "change_type" and operation.type else feature_type
    for name, dim in operation.dimensions.items():
        value = dim.value if isinstance(dim, DimensionV3) else (dim.get("value") if isinstance(dim, dict) else None)
        issues.extend(
            CAPABILITIES.validate_parameter(
                edit_feature_type,
                name,
                value,
                feature_id=feature_id,
                operation=operation.op,
            )
        )
    if operation.placement is not None:
        issues.extend(
            CAPABILITIES.validate_parameter(
                edit_feature_type,
                "position",
                operation.placement,
                feature_id=feature_id,
                operation=operation.op,
            )
        )
    if operation.extent is not None:
        issues.extend(
            CAPABILITIES.validate_parameter(
                edit_feature_type,
                "extent",
                operation.extent,
                feature_id=feature_id,
                operation=operation.op,
            )
        )
    return issues


def validate_feature_edit_set(edit_set: FeatureEditSet, plan: FeaturePlanV3) -> list[CapabilityIssue]:
    """Validate every operation without mutating the plan."""
    issues: list[CapabilityIssue] = []
    for operation in edit_set.operations:
        issues.extend(validate_feature_operation(operation, plan))
    return issues


def validate_feature_patch(
    feature_type: str | None,
    dimensions: dict[str, Any] | None = None,
    placement: PlacementV3 | dict[str, Any] | None = None,
) -> list[CapabilityIssue]:
    """Validate a property-panel patch against the registered capability."""
    capability = CAPABILITIES.get(feature_type)
    if capability is None:
        return [
            _issue(
                CAPABILITY_UNKNOWN_FEATURE,
                f"feature type {feature_type or 'unknown'} has no registered capability",
                feature_type=feature_type,
                operation="update",
            )
        ]
    issues: list[CapabilityIssue] = []
    if "update" not in capability.allowed_operations:
        issues.append(
            _issue(
                CAPABILITY_OPERATION_NOT_ALLOWED,
                f"operation update is not allowed for {feature_type}",
                feature_type=feature_type,
                operation="update",
            )
        )
    for name, dim in (dimensions or {}).items():
        value = dim.value if isinstance(dim, DimensionV3) else (dim.get("value") if isinstance(dim, dict) else None)
        issues.extend(CAPABILITIES.validate_parameter(feature_type, name, value, operation="update"))
    if placement is not None:
        issues.extend(CAPABILITIES.validate_parameter(feature_type, "position", placement, operation="update"))
    return issues


class CapabilityValidationError(Exception):
    """Raised when a property-panel edit fails capability validation."""

    def __init__(self, issues: list[CapabilityIssue] | list[dict[str, Any]]) -> None:
        self.issues = [
            issue if isinstance(issue, CapabilityIssue) else CapabilityIssue.model_validate(issue)
            for issue in issues
        ]
        super().__init__("; ".join(issue.reason for issue in self.issues))


def _build_default_capabilities() -> list[FeatureCapability]:
    return [
        _capability(
            "box_base",
            [_param("length"), _param("width"), _param("height"), _id_param()],
            ["update"],
        ),
        _capability(
            "cylinder_base",
            [_param("outer_diameter"), _param("length"), _id_param()],
            ["update"],
        ),
        _capability(
            "hollow_cylinder",
            [_param("outer_diameter"), _param("inner_diameter"), _param("length"), _id_param()],
            ["update"],
        ),
        _capability(
            "through_hole",
            [_param("diameter"), _placement_param(), _extent_param(), _id_param()],
            ["add", "update", "delete", "change_type"],
            convert_to_types=("blind_hole", "counterbore_hole"),
        ),
        _capability(
            "blind_hole",
            [_param("diameter"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            ["add", "update", "delete", "change_type"],
            convert_to_types=("through_hole", "counterbore_hole"),
        ),
        _capability(
            "counterbore_hole",
            [_param("diameter"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            ["add", "update", "delete", "change_type"],
            convert_to_types=("through_hole", "blind_hole"),
        ),
        _capability(
            "rectangular_slot",
            [_param("length"), _param("width"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            ["add", "update", "delete", "change_type"],
            convert_to_types=("rectangular_pocket",),
        ),
        _capability(
            "rectangular_pocket",
            [_param("length"), _param("width"), _param("depth"), _placement_param(), _extent_param(), _id_param()],
            ["add", "update", "delete", "change_type"],
            convert_to_types=("rectangular_slot",),
        ),
        _capability(
            "annular_groove",
            [_param("reduced_outer_diameter"), _param("axial_width"), _param("z_start"), _id_param()],
            ["add", "update", "delete"],
        ),
        _capability(
            "boss_cylinder",
            [_param("diameter"), _param("height"), _placement_param(), _id_param()],
            ["add", "update", "delete"],
        ),
        _capability(
            "rectangular_pad",
            [_param("length"), _param("width"), _param("height"), _placement_param(), _id_param()],
            ["add", "update", "delete"],
        ),
        _capability(
            "rib_box",
            [_param("length"), _param("width"), _param("height"), _placement_param(), _id_param()],
            ["add", "update", "delete"],
        ),
        _capability(
            "linear_pattern",
            [_param("count"), _param("spacing"), _param("diameter"), _placement_param(), _id_param()],
            ["add", "update", "delete"],
        ),
        _capability(
            "circular_pattern",
            [_param("count"), _param("pitch_radius"), _param("diameter"), _placement_param(), _id_param()],
            ["add", "update", "delete"],
        ),
    ]


CAPABILITIES = CapabilityRegistry()
for _cap in _build_default_capabilities():
    CAPABILITIES.register(_cap)
