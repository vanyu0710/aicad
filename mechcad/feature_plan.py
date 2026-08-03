from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


Number = int | float


class Dimension(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: float | None = None
    unit: str = "mm"
    evidence: str = ""
    source: str = ""
    confidence: float | None = None

    @field_validator("unit", "evidence", "source", mode="before")
    @classmethod
    def _text_or_default(cls, value: Any, info) -> str:
        if value is None:
            return "mm" if info.field_name == "unit" else ""
        return str(value)


class Placement(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference: str = "origin"
    x: float | None = None
    y: float | None = None
    z: float | None = None
    axis: Literal["X", "Y", "Z"] = "Z"
    rotation: tuple[float, float, float] | None = None

    @field_validator("reference", mode="before")
    @classmethod
    def _reference_or_default(cls, value: Any) -> str:
        if value is None:
            return "origin"
        return str(value)

    @field_validator("axis", mode="before")
    @classmethod
    def _axis_or_default(cls, value: Any) -> str:
        if value in {"X", "Y", "Z"}:
            return value
        return "Z"


class BaseFeature(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = "base"
    type: str
    operation: Literal["base"] = "base"
    dimensions: dict[str, Dimension] = Field(default_factory=dict)
    placement: Placement = Field(default_factory=Placement)
    evidence: str = ""

    @field_validator("id", "type", "evidence", mode="before")
    @classmethod
    def _text_or_default(cls, value: Any, info) -> str:
        if value is None:
            if info.field_name == "id":
                return "base"
            if info.field_name == "type":
                return "unsupported"
            return ""
        return str(value)


class CadFeature(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    operation: Literal["add", "remove", "modify", "pattern"] = "remove"
    dimensions: dict[str, Dimension] = Field(default_factory=dict)
    placement: Placement = Field(default_factory=Placement)
    extent: str | None = None
    pattern: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    evidence: str = ""
    unresolved: list[str] = Field(default_factory=list)

    @field_validator("id", "type", "evidence", mode="before")
    @classmethod
    def _text_or_default(cls, value: Any, info) -> str:
        if value is None:
            if info.field_name == "id":
                return ""
            if info.field_name == "type":
                return "unsupported"
            return ""
        return str(value)

    @field_validator("depends_on", "unresolved", mode="before")
    @classmethod
    def _list_or_empty(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


class FeaturePlan(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "2.0"
    units: Literal["mm"] = "mm"
    coordinate_system: dict[str, Any] = Field(default_factory=dict)
    part_family: str = "unknown"
    base_feature: BaseFeature | None = None
    features: list[CadFeature] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    self_checks: dict[str, Any] = Field(default_factory=dict)
    source: str = "unknown"

    @field_validator("features")
    @classmethod
    def _require_feature_ids(cls, value: list[CadFeature]) -> list[CadFeature]:
        for index, feature in enumerate(value):
            if not feature.id:
                feature.id = f"feature_{index + 1}"
        return value


def validate_feature_plan(raw_plan: dict[str, Any]) -> tuple[FeaturePlan | None, list[str]]:
    try:
        return FeaturePlan.model_validate(raw_plan), []
    except ValidationError as exc:
        return None, [error["msg"] for error in exc.errors()]


def dimension_value(dimensions: dict[str, Dimension], *names: str) -> float | None:
    for name in names:
        item = dimensions.get(name)
        if item is not None and item.value is not None:
            return float(item.value)
    return None


def dimension_evidence(dimensions: dict[str, Dimension], *names: str) -> str:
    for name in names:
        item = dimensions.get(name)
        if item is not None and item.evidence:
            return item.evidence
    return ""


def dim(value: Any, evidence: str = "", source: str = "") -> Dimension:
    number = _first_number(value)
    return Dimension(value=number, unit="mm", evidence=evidence, source=source)


def normalize_ai_feature_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"schema_version": "2.0", "units": "mm", "base_feature": None, "features": [], "unresolved": []}
    plan = dict(raw)
    plan.setdefault("schema_version", "2.0")
    plan.setdefault("units", "mm")
    plan.setdefault("coordinate_system", {"origin": "model origin", "axes": "right-handed XYZ"})
    plan.setdefault("features", [])
    plan.setdefault("unresolved", [])
    plan.setdefault("self_checks", {})
    return plan


def model_plan_to_feature_plan(model_plan: dict[str, Any], qwen_json: dict[str, Any]) -> dict[str, Any]:
    """Convert the old deterministic tube plan into the new FeaturePlan shape."""
    body = model_plan.get("base_body")
    features: list[dict[str, Any]] = []
    unresolved = list(model_plan.get("unresolved", [])) if isinstance(model_plan.get("unresolved"), list) else []
    base_feature = None
    if isinstance(body, dict):
        base_feature = {
            "id": "base_tube",
            "type": "hollow_cylinder",
            "operation": "base",
            "dimensions": {
                "outer_diameter": _as_dim(body.get("outer_diameter")),
                "inner_diameter": _as_dim(body.get("inner_diameter")),
                "length": _as_dim(body.get("length")),
            },
            "placement": {"reference": "bottom_end_center", "axis": "Z"},
            "evidence": "Converted from deterministic model_plan.base_body.",
        }
    for index, item in enumerate(model_plan.get("features", [])):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "annular_groove":
            features.append(
                {
                    "id": str(item.get("source_feature_id") or f"annular_groove_{index + 1}"),
                    "type": "annular_groove",
                    "operation": "remove",
                    "dimensions": {
                        "z_start": _as_dim(item.get("z_start")),
                        "axial_width": _as_dim(item.get("axial_width")),
                        "reduced_outer_diameter": _as_dim(item.get("reduced_outer_diameter")),
                    },
                    "placement": {"reference": item.get("location", "axis"), "axis": "Z"},
                    "evidence": "Converted from deterministic model_plan.features.",
                }
            )
    return {
        "schema_version": "2.0",
        "units": "mm",
        "coordinate_system": model_plan.get("coordinate_system", {"main_axis": "Z", "units": "mm"}),
        "part_family": model_plan.get("part_family") or _domain_family(qwen_json),
        "base_feature": base_feature,
        "features": features,
        "unresolved": unresolved,
        "self_checks": {
            "dimension_binding": "converted_from_model_plan",
            "cad_ready": bool(base_feature),
        },
        "source": "deterministic_model_plan",
    }


def qwen_to_seed_feature_plan(qwen_json: dict[str, Any], model_plan: dict[str, Any]) -> dict[str, Any]:
    converted = model_plan_to_feature_plan(model_plan, qwen_json)
    if converted.get("base_feature"):
        return converted
    return {
        "schema_version": "2.0",
        "units": "mm",
        "coordinate_system": {"main_axis": "Z", "origin": "model origin", "units": "mm"},
        "part_family": _domain_family(qwen_json),
        "base_feature": None,
        "features": [],
        "unresolved": converted.get("unresolved", []),
        "self_checks": {"cad_ready": False, "reason": "No supported base feature found."},
        "source": "qwen_seed",
    }


def _as_dim(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        result = dict(value)
        result.setdefault("unit", "mm")
        result.setdefault("evidence", result.get("source", ""))
        return result
    return dim(value).model_dump()


def _domain_family(qwen_json: dict[str, Any]) -> str:
    domain = qwen_json.get("domain")
    if isinstance(domain, dict):
        return str(domain.get("part_family") or "unknown")
    return str(domain or "unknown")


def _first_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        if "value" in value:
            return _first_number(value["value"])
        if "numeric_value" in value:
            return _first_number(value["numeric_value"])
        for item in value.values():
            found = _first_number(item)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_number(item)
            if found is not None:
                return found
    return None
