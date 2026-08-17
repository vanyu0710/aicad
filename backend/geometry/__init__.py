"""Read-only BRep geometry utilities.

Import measurement from ``backend.geometry.measurement`` when Build123d/OCP
are available. The 1D-2.1 resolver only needs FeaturePlan + measurement facts.
"""

from backend.geometry.resolver import resolve_feature_geometry_evidence, to_geometry_correspondence
from backend.geometry.verification import DEFAULT_VERIFICATION_REGISTRY, verify_feature_plan

__all__ = [
    "resolve_feature_geometry_evidence",
    "to_geometry_correspondence",
    "DEFAULT_VERIFICATION_REGISTRY",
    "verify_feature_plan",
]
