"""Registry and protocol for composable semantic geometry verifiers."""

from __future__ import annotations

from typing import Protocol

from backend.feature_definitions import FEATURE_DEFINITIONS
from backend.schemas import (
    FeatureV3,
    FeatureVerificationResult,
    GeometryMeasurementReport,
    GeometryVerificationCapability,
    VerificationContext,
)


class FeatureVerifier(Protocol):
    """Pure verifier contract. Implementations must not mutate their inputs."""

    def verify(
        self,
        feature: FeatureV3,
        measurement: GeometryMeasurementReport,
        context: VerificationContext,
        *,
        plan_has_followup_features: bool,
    ) -> FeatureVerificationResult:
        ...


class GeometryVerificationRegistry:
    """Static mapping between feature types, advertised coverage, and verifiers."""

    def __init__(self) -> None:
        self._capabilities: dict[str, GeometryVerificationCapability] = {}
        self._verifiers: dict[str, FeatureVerifier] = {}

    def register(self, capability: GeometryVerificationCapability, verifier: FeatureVerifier | None = None) -> None:
        feature_type = capability.feature_type
        if feature_type in self._capabilities:
            raise ValueError(f"Geometry verification capability already registered: {feature_type}")
        self._capabilities[feature_type] = capability
        if verifier is not None:
            self._verifiers[feature_type] = verifier

    def get_capability(self, feature_type: str | None) -> GeometryVerificationCapability | None:
        return self._capabilities.get(feature_type or "")

    def get_verifier(self, feature_type: str | None) -> FeatureVerifier | None:
        return self._verifiers.get(feature_type or "")

    def capabilities(self) -> list[GeometryVerificationCapability]:
        return [self._capabilities[key] for key in sorted(self._capabilities)]


def build_default_registry(box_verifier: FeatureVerifier, cylinder_verifier: FeatureVerifier) -> GeometryVerificationRegistry:
    """Register every canonical feature type; only Level-A primitives get code."""

    registry = GeometryVerificationRegistry()
    primitive_verifiers = {
        "box_base": box_verifier,
        "cylinder_base": cylinder_verifier,
        "hollow_cylinder": cylinder_verifier,
    }
    for definition in FEATURE_DEFINITIONS.list():
        verifier = primitive_verifiers.get(definition.feature_type)
        status = "supported" if verifier is not None else "unsupported"
        properties = list(definition.verification_contract.properties)
        if definition.feature_type == "box_base":
            properties = ["size_x", "size_y", "size_z", "volume"]
        elif definition.feature_type in {"cylinder_base", "hollow_cylinder"}:
            properties = ["size_x", "size_y", "size_z", "volume", "cylindrical_geometry", "diameter", "axis"]
        registry.register(
            GeometryVerificationCapability(
                feature_type=definition.feature_type,
                supported_properties=properties,
                required_measurements=["bounding_box", "volume"] + (["cylinders"] if verifier is cylinder_verifier else []),
                implementation_status=status,
                limitations=(
                    []
                    if verifier is not None
                    else ["No deterministic semantic verifier is implemented for this feature type in 1D-2."]
                ),
            ),
            verifier=verifier,
        )
    return registry
