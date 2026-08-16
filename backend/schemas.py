from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


OperationMode = Literal["strict", "smart"]
SmartFillPolicy = Literal["suggest_only", "limited_fill", "aggressive_fill", "full_autonomous"]
FeatureOperation = Literal["base", "add", "remove", "modify", "pattern"]
StageEventType = Literal["stage_started", "stage_progress", "stage_done", "question_required", "artifact_ready", "error", "process_step_started", "process_step_done", "process_step_failed", "process_step_blocked"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ProcessStage = Literal["upload", "vision", "planning", "validation", "chat_edit", "cad", "export"]
ProcessStatus = Literal["pending", "running", "completed", "failed", "skipped", "blocked"]
FeatureEditOp = Literal["add", "update", "delete", "change_type"]


class ProcessStep(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    stage: ProcessStage
    status: ProcessStatus
    label: str
    summary: str = ""
    detail: str = ""
    feature_id: str | None = None
    operation: str | None = None
    changed: dict[str, Any] | None = None
    started_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("label", "summary", "detail", mode="before")
    @classmethod
    def text_or_default(cls, value: Any) -> str:
        return "" if value is None else str(value)


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
    execution_status: Literal["unresolved", "modeled", "skipped", "failed"] = "unresolved"

    @field_validator("id", "type", "evidence", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> str:
        return "" if value is None else str(value)


class DesignReview(BaseModel):
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    manufacturability: list[str] = Field(default_factory=list)
    standards: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False


class DesignAssumption(BaseModel):
    feature_id: str
    dimension: str
    value: float | str | None = None
    reason: str
    source: Literal["drawing", "user", "assumption", "derived", "unknown"] = "assumption"
    confidence: float | None = None
    confirmed_by_user: bool = False


class ClarificationQuestion(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    text: str
    feature_id: str | None = None
    dimension_refs: list[str] = Field(default_factory=list)
    required: bool = True
    options: list[str] = Field(default_factory=list)
    reason: str = ""
    impact: str = ""
    answer_type: Literal["text", "number", "choice"] = "text"
    default_value: str | None = None
    unit: str | None = None
    answer: str | None = None


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    value: float | str | None = None
    unit: str = "mm"
    source: Literal["drawing", "user", "assumption", "derived", "unknown"] = "unknown"
    feature_id: str | None = None
    dimension: str | None = None
    confirmed_by_user: bool = False
    confidence: float | None = None
    conflict_with: list[str] = Field(default_factory=list)


class EvidenceSet(BaseModel):
    model_config = ConfigDict(extra="allow")

    input_kind: Literal["text_only", "image_only", "mixed"] = "text_only"
    items: list[EvidenceItem] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    conflict_details: list["EvidenceConflict"] = Field(default_factory=list)

    def add(self, key: str, value: float | str | None, source: str = "unknown", **extra: Any) -> None:
        self.items.append(EvidenceItem(key=key, value=value, source=source, **extra))

    def get(self, key: str) -> EvidenceItem | None:
        for item in reversed(self.items):
            if item.key == key:
                return item
        return None


class EvidenceConflictSource(BaseModel):
    source: Literal["drawing", "user", "assumption", "derived", "unknown"] = "unknown"
    value: float | str | None = None
    unit: str = "mm"
    confirmed_by_user: bool = False
    detail: str = ""


class EvidenceConflict(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:10])
    key: str
    feature_id: str | None = None
    parameter: str | None = None
    source_a: EvidenceConflictSource
    source_b: EvidenceConflictSource
    severity: Literal["blocking", "warning"] = "blocking"
    status: Literal["unresolved", "resolved"] = "unresolved"
    resolved_value: float | str | None = None
    resolved_by: Literal["user", "system"] | None = None
    resolved_at: str | None = None
    reason: str = ""
    ambiguous: bool = False
    affected_feature_ids: list[str] = Field(default_factory=list)


class EvidenceResolution(BaseModel):
    conflict_id: str | None = None
    key: str | None = None
    feature_id: str | None = None
    selected_value: float | str
    unit: str = "mm"


class EvidenceGateResult(BaseModel):
    status: Literal["ALLOW", "BLOCK", "REQUIRE_RESOLUTION"] = "ALLOW"
    blocking: bool = False
    resolution_required: bool = False
    conflicts: list[EvidenceConflict] = Field(default_factory=list)
    affected_feature_ids: list[str] = Field(default_factory=list)
    affected_parameters: list[str] = Field(default_factory=list)
    reason: str = ""
    warnings: list[str] = Field(default_factory=list)


class DesignIntent(BaseModel):
    model_config = ConfigDict(extra="allow")

    part_family: str = "unknown"
    confidence: float | None = None
    function: str = ""
    main_datum: str = "XY"
    main_axis: str = "Z"
    manufacturing_intent: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)
    summary: str = ""


class FeatureSemantics(BaseModel):
    model_config = ConfigDict(extra="allow")

    feature_type: str
    operation: FeatureOperation = "remove"
    geometry_effect: Literal["base", "add", "remove", "modify", "pattern"] = "remove"
    required_dimensions: list[str] = Field(default_factory=list)
    optional_dimensions: list[str] = Field(default_factory=list)
    parent_required: bool = True
    centered_placements: list[str] = Field(default_factory=list)
    implementation_status: str = "supported"


class TemplateFeatureSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    operation: FeatureOperation = "remove"
    depends_on: list[str] = Field(default_factory=list)
    dimension_map: dict[str, str] = Field(default_factory=dict)
    defaults: dict[str, float] = Field(default_factory=dict)
    placement_rule: str = "center"
    placement: PlacementV3 | None = None
    extent: str | None = None
    required: bool = True
    evidence: str = ""


class FamilyTemplate(BaseModel):
    model_config = ConfigDict(extra="allow")

    family: str
    base_type: str
    base_dimension_map: dict[str, str] = Field(default_factory=dict)
    feature_specs: list[TemplateFeatureSpec] = Field(default_factory=list)
    design_intent: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)


class ExecutionReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    execution_ok: bool = False
    plan_complete: bool = False
    geometry_valid: bool = False
    production_ready: bool = False
    fallback_used: bool = False
    skipped_features: list[str] = Field(default_factory=list)
    failed_features: list[str] = Field(default_factory=list)
    assumption_count: int = 0
    completeness_score: float = 0.0
    engine: str = "build123d"
    details: list[str] = Field(default_factory=list)


MeasurementStatus = Literal[
    "MEASUREMENT_SUCCESS",
    "MEASUREMENT_UNAVAILABLE",
    "MEASUREMENT_ERROR",
]


class GeometryFact(BaseModel):
    """An observed, non-semantic fact about the final BRep geometry."""

    fact_type: str
    source: str = "final_brep"
    unit: str = "mm"
    status: MeasurementStatus = "MEASUREMENT_SUCCESS"
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class BoundingBoxFact(GeometryFact):
    fact_type: Literal["bounding_box"] = "bounding_box"
    min_x: float | None = None
    min_y: float | None = None
    min_z: float | None = None
    max_x: float | None = None
    max_y: float | None = None
    max_z: float | None = None
    size_x: float | None = None
    size_y: float | None = None
    size_z: float | None = None


class VolumeFact(GeometryFact):
    fact_type: Literal["volume"] = "volume"
    unit: str = "mm^3"
    volume: float | None = None


class CylinderFact(GeometryFact):
    """A cylindrical surface observed in the BRep, without feature semantics."""

    fact_type: Literal["cylindrical_surface"] = "cylindrical_surface"
    measurement_index: int
    radius: float | None = None
    diameter: float | None = None
    axis: list[float] | None = None
    center: list[float] | None = None
    height: float | None = None
    unavailable: list[str] = Field(default_factory=list)


class GeometryMeasurementReport(BaseModel):
    """Read-only observations of the final Build123d/OpenCascade BRep."""

    measurement_version: str = "1D-1"
    source: str = "final_brep"
    status: MeasurementStatus = "MEASUREMENT_SUCCESS"
    bounding_box: BoundingBoxFact | None = None
    volume: VolumeFact | None = None
    cylinders: list[CylinderFact] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


VerificationStatus = Literal["PASS", "FAIL", "UNKNOWN", "UNSUPPORTED", "SKIPPED"]
ModelVerificationStatus = Literal[
    "VERIFIED",
    "PARTIALLY_VERIFIED",
    "FAILED",
    "UNKNOWN",
    "UNSUPPORTED",
]


class VerificationTolerancePolicy(BaseModel):
    """Software comparison tolerances, not manufacturing or GD&T tolerances."""

    linear_absolute_mm: float = 0.05
    linear_relative: float = 1e-6
    volume_absolute_mm3: float = 0.001
    volume_relative: float = 1e-6
    angular_degrees: float = 0.1
    axial_axis_equivalence: bool = True


class BoundingBoxExpectation(BaseModel):
    """Optional, explicit global BRep bounding-box expectations in mm."""

    min_x: float | None = None
    min_y: float | None = None
    min_z: float | None = None
    max_x: float | None = None
    max_y: float | None = None
    max_z: float | None = None
    size_x: float | None = None
    size_y: float | None = None
    size_z: float | None = None


class VerificationContext(BaseModel):
    """Explicit optional expectations not represented by an individual feature."""

    expected_bounding_box: BoundingBoxExpectation | None = None
    expected_volume: float | None = None
    tolerance_policy: VerificationTolerancePolicy = Field(default_factory=VerificationTolerancePolicy)


class VerificationPropertyResult(BaseModel):
    property_name: str
    status: VerificationStatus
    expected: Any | None = None
    actual: Any | None = None
    tolerance: dict[str, float | bool] = Field(default_factory=dict)
    deviation: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""


class GeometryCorrespondence(BaseModel):
    """Ephemeral correspondence to facts in one measurement report only."""

    status: Literal["UNIQUE", "AMBIGUOUS", "NONE", "UNAVAILABLE", "NOT_REQUESTED"]
    candidate_indices: list[int] = Field(default_factory=list)
    reason: str = ""


class FeatureVerificationResult(BaseModel):
    feature_id: str
    feature_type: str
    status: VerificationStatus
    properties: list[VerificationPropertyResult] = Field(default_factory=list)
    correspondence: GeometryCorrespondence | None = None
    reasons: list[str] = Field(default_factory=list)


class GeometryVerificationCapability(BaseModel):
    feature_type: str
    supported_properties: list[str] = Field(default_factory=list)
    supported_relations: list[str] = Field(default_factory=list)
    required_measurements: list[str] = Field(default_factory=list)
    implementation_status: Literal["supported", "partial", "unsupported"] = "unsupported"
    limitations: list[str] = Field(default_factory=list)


class ModelVerificationReport(BaseModel):
    """Pure semantic-verification result derived from a plan and BRep facts."""

    verification_version: str = "1D-2"
    status: ModelVerificationStatus = "UNKNOWN"
    measurement_version: str | None = None
    global_properties: list[VerificationPropertyResult] = Field(default_factory=list)
    features: list[FeatureVerificationResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class FeaturePlanV3(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "3.0"
    units: Literal["mm"] = "mm"
    coordinate_system: dict[str, Any] = Field(
        default_factory=lambda: {"origin": "model origin", "main_axis": "Z", "handedness": "right"}
    )
    part_family: str = "unknown"
    autonomy_policy: SmartFillPolicy | None = None
    design_intent: str = ""
    design_intent_details: DesignIntent | None = None
    evidence: EvidenceSet = Field(default_factory=EvidenceSet)
    completeness: dict[str, Any] = Field(default_factory=dict)
    base_feature: FeatureV3 | None = None
    features: list[FeatureV3] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    assumption_details: list[DesignAssumption] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    design_review: DesignReview = Field(default_factory=DesignReview)
    self_checks: dict[str, Any] = Field(default_factory=dict)

class FeatureEditOperation(BaseModel):
    model_config = ConfigDict(extra="allow")

    op: FeatureEditOp
    feature_id: str | None = None
    type: str | None = None
    dimensions: dict[str, DimensionV3] = Field(default_factory=dict)
    placement: PlacementV3 | None = None
    extent: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    evidence: str = ""
    source: Literal["drawing", "user", "assumption", "derived", "unknown"] = "user"
    confirmed_by_user: bool = True
    reason: str = ""
    cascade: bool = False

    @field_validator("feature_id", "type", "evidence", "reason", mode="before")
    @classmethod
    def text_or_none(cls, value: Any) -> Any:
        if value is None:
            return value
        return "" if value == "" else str(value)


class FeatureEditSet(BaseModel):
    model_config = ConfigDict(extra="allow")

    operations: list[FeatureEditOperation] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    message: str = ""


ParameterValueType = Literal["float", "int", "string", "placement", "extent", "boolean"]


class ParameterSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    value_type: ParameterValueType = "float"
    unit: str | None = None
    editable: bool = True
    minimum: float | None = None
    maximum: float | None = None
    required: bool = False
    aliases: list[str] = Field(default_factory=list)


class FeatureCapability(BaseModel):
    model_config = ConfigDict(extra="allow")

    feature_type: str
    editable_parameters: list[ParameterSpec] = Field(default_factory=list)
    allowed_operations: list[FeatureEditOp] = Field(default_factory=list)
    reference_types: list[str] = Field(default_factory=list)
    convert_to_types: list[str] = Field(default_factory=list)
    implementation_status: str = "supported"


class ConstraintSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    message: str
    severity: Literal["info", "warning", "blocking"] = "blocking"


class VerificationContractSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    properties: list[str] = Field(default_factory=list)
    status: Literal["supported", "partial", "unsupported"] = "unsupported"
    notes: str = ""


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    feature_type: str
    operation: FeatureOperation = "remove"
    geometry_effect: Literal["base", "add", "remove", "modify", "pattern"] = "remove"
    parameters: list[ParameterSpec] = Field(default_factory=list)
    required_dimensions: list[str] = Field(default_factory=list)
    optional_dimensions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    centered_placements: list[str] = Field(default_factory=list)
    allowed_operations: list[FeatureEditOp] = Field(default_factory=list)
    convert_to_types: list[str] = Field(default_factory=list)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    verification_contract: VerificationContractSpec = Field(default_factory=VerificationContractSpec)
    parent_required: bool = True
    implementation_status: str = "supported"


class CapabilityIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    error_code: str
    feature_type: str | None = None
    feature_id: str | None = None
    operation: str | None = None
    parameter: str | None = None
    reason: str
    recoverable: bool = False


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
    execution_report: ExecutionReport = Field(default_factory=ExecutionReport)
    report_markdown: str = ""
    logs: list[str] = Field(default_factory=list)
    process: list[ProcessStep] = Field(default_factory=list)


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
    smart_fill_policy: SmartFillPolicy = "limited_fill"
    force_real_api: bool = False


class ModelTestRequest(BaseModel):
    role: Literal["vision", "planner"]
    config: ModelConfig
    language: Literal["zh", "en"] = "zh"


class ModelTestDiagnostics(BaseModel):
    status_code: int | None = None
    content_type: str | None = None
    endpoint: str | None = None
    used_env_fallback: bool = False


class ModelTestResponse(BaseModel):
    ok: bool
    role: str
    provider: str
    protocol: str
    model: str
    message: str
    diagnostics: ModelTestDiagnostics = Field(default_factory=ModelTestDiagnostics)


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


class RenameProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("Project name must not be empty")
        return value

class CreateProjectResponse(BaseModel):
    project_id: str
    project: ProjectState


class GenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str
    operation_mode: OperationMode | None = None
    smart_fill_policy: SmartFillPolicy | None = None
    settings: ModelConfig | None = Field(default=None, alias="model_config")
    image_data_url: str | None = None
    image_name: str | None = None
    clarification_answers: str = ""
    evidence_resolutions: list[EvidenceResolution] = Field(default_factory=list)
    language: Literal["zh", "en"] = "zh"


class ProjectSettingsRequest(ModelConfig):
    """Settings are saved separately so mode changes do not require a model run."""


class ChatEditRequest(BaseModel):
    message: str
    language: Literal["zh", "en"] = "zh"


class FeaturePatchRequest(BaseModel):
    dimensions: dict[str, DimensionV3] | None = None
    placement: PlacementV3 | None = None
    confirmed_by_user: bool | None = None
    language: Literal["zh", "en"] = "zh"


class StageEvent(BaseModel):
    type: StageEventType
    project_id: str
    stage: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=now_iso)
