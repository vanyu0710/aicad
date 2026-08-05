from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


OperationMode = Literal["strict", "smart"]
SmartFillPolicy = Literal["suggest_only", "limited_fill", "aggressive_fill"]
FeatureOperation = Literal["base", "add", "remove", "modify", "pattern"]
StageEventType = Literal["stage_started", "stage_progress", "stage_done", "question_required", "artifact_ready", "error"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DimensionV3(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: float | None = None
    unit: str = "mm"
    role: str = ""
    evidence: str = ""
    source: Literal["drawing", "user", "assumption", "derived", "unknown"] = "unknown"
    confidence: float | None = None
    confirmed_by_user: bool = False

    @field_validator("unit", "role", "evidence", mode="before")
    @classmethod
    def text_or_default(cls, value: Any) -> str:
        return "" if value is None else str(value)


class PlacementV3(BaseModel):
    model_config = ConfigDict(extra="allow")

    reference: str = "origin"
    x: float | None = None
    y: float | None = None
    z: float | None = None
    axis: Literal["X", "Y", "Z"] = "Z"
    angle_deg: float | None = None

    @field_validator("reference", mode="before")
    @classmethod
    def reference_or_default(cls, value: Any) -> str:
        return "origin" if value is None else str(value)


class FeatureV3(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    operation: FeatureOperation
    dimensions: dict[str, DimensionV3] = Field(default_factory=dict)
    placement: PlacementV3 = Field(default_factory=PlacementV3)
    extent: str | None = None
    pattern: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    evidence: str = ""
    unresolved: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confirmed_by_user: bool = False

    @field_validator("id", "type", "evidence", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> str:
        return "" if value is None else str(value)


class DesignReview(BaseModel):
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    manufacturability: list[str] = Field(default_factory=list)
    standards: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class ClarificationQuestion(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    text: str
    feature_id: str | None = None
    dimension_refs: list[str] = Field(default_factory=list)
    required: bool = True
    options: list[str] = Field(default_factory=list)


class FeaturePlanV3(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "3.0"
    units: Literal["mm"] = "mm"
    coordinate_system: dict[str, Any] = Field(
        default_factory=lambda: {"origin": "model origin", "main_axis": "Z", "handedness": "right"}
    )
    part_family: str = "unknown"
    base_feature: FeatureV3 | None = None
    features: list[FeatureV3] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    design_review: DesignReview = Field(default_factory=DesignReview)
    self_checks: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_engineering_contract(self) -> "FeaturePlanV3":
        seen_ids: set[str] = set()
        ordered_features = [self.base_feature] if self.base_feature else []
        ordered_features.extend(self.features)

        for index, feature in enumerate(ordered_features):
            if feature is None:
                continue
            if not feature.id:
                feature.id = "base" if index == 0 else f"feature_{index}"
            if feature.id in seen_ids:
                self.unresolved.append({"feature": feature.id, "reason": "Duplicate feature id"})
            seen_ids.add(feature.id)

        for feature in ordered_features:
            if feature is None:
                continue
            for dep in feature.depends_on:
                if dep and dep not in seen_ids:
                    self.unresolved.append({"feature": feature.id, "reason": f"Missing dependency: {dep}"})
            for name, dim in feature.dimensions.items():
                if dim.value is not None and dim.value <= 0 and name not in {"x", "y", "z"}:
                    feature.unresolved.append(f"Invalid non-positive dimension: {name}")
                if dim.source == "assumption" and not dim.confirmed_by_user:
                    self.design_review.requires_confirmation = True
                    self.assumptions.append(dim.evidence or f"{feature.id}.{name}")
        return self


class ArtifactSet(BaseModel):
    run_id: str | None = None
    step: str | None = None
    stl: str | None = None
    obj: str | None = None
    report: str | None = None
    execution_report: str | None = None


class DesignSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    created_at: str = Field(default_factory=now_iso)
    feature_plan: FeaturePlanV3 = Field(default_factory=FeaturePlanV3)
    artifacts: ArtifactSet = Field(default_factory=ArtifactSet)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    design_review: DesignReview = Field(default_factory=DesignReview)
    report_markdown: str = ""
    logs: list[str] = Field(default_factory=list)


class ModelConfig(BaseModel):
    vision_provider: str = "custom"
    vision_model: str = ""
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_protocol: str = "openai"
    planner_provider: str = "custom"
    planner_model: str = ""
    planner_base_url: str = ""
    planner_api_key: str = ""
    planner_protocol: str = "openai"
    operation_mode: OperationMode = "strict"
    smart_fill_policy: SmartFillPolicy = "suggest_only"
    force_real_api: bool = False


class ProjectState(BaseModel):
    project_id: str
    name: str = "Untitled MechCAD Project"
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    settings: ModelConfig = Field(default_factory=ModelConfig)
    current: DesignSnapshot = Field(default_factory=DesignSnapshot)
    history: list[DesignSnapshot] = Field(default_factory=list)
    redo_stack: list[DesignSnapshot] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    name: str | None = None


class CreateProjectResponse(BaseModel):
    project_id: str
    project: ProjectState


class GenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str
    operation_mode: OperationMode = "strict"
    smart_fill_policy: SmartFillPolicy = "suggest_only"
    settings: ModelConfig | None = Field(default=None, alias="model_config")
    image_data_url: str | None = None
    image_name: str | None = None
    clarification_answers: str = ""


class ChatEditRequest(BaseModel):
    message: str


class FeaturePatchRequest(BaseModel):
    dimensions: dict[str, DimensionV3] | None = None
    placement: PlacementV3 | None = None
    confirmed_by_user: bool | None = None


class StageEvent(BaseModel):
    type: StageEventType
    project_id: str
    stage: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=now_iso)
