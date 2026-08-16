"""Ephemeral, deterministic correspondence over one measurement report.

These helpers never persist a feature-to-topology relationship. A returned
measurement index is provenance inside the supplied report only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.schemas import CylinderFact, GeometryCorrespondence, VerificationTolerancePolicy


@dataclass(frozen=True)
class CylinderResolution:
    correspondence: GeometryCorrespondence
    candidate: CylinderFact | None = None


def resolve_unique_cylinder(
    cylinders: Iterable[CylinderFact],
    *,
    diameter: float | None,
    policy: VerificationTolerancePolicy,
    measurement_status: str = "MEASUREMENT_SUCCESS",
) -> CylinderResolution:
    """Resolve one cylinder using only a requested diameter.

    This establishes only a measurement candidate. It does not establish that
    a cylinder is a hole, boss, groove, or persistent CAD feature.
    """

    if diameter is None or diameter <= 0:
        return CylinderResolution(
            GeometryCorrespondence(
                status="UNAVAILABLE",
                reason="A positive expected cylinder diameter is required for correspondence.",
            )
        )

    facts = list(cylinders)
    matching = [
        item
        for item in facts
        if item.status == "MEASUREMENT_SUCCESS"
        and item.diameter is not None
        and _within_tolerance(item.diameter, diameter, policy.linear_absolute_mm, policy.linear_relative)
    ]
    matching.sort(key=lambda item: item.measurement_index)
    indices = [item.measurement_index for item in matching]
    if not matching:
        if measurement_status != "MEASUREMENT_SUCCESS" or any(item.status != "MEASUREMENT_SUCCESS" for item in facts):
            return CylinderResolution(
                GeometryCorrespondence(
                    status="UNAVAILABLE",
                    candidate_indices=[],
                    reason="Cylindrical-surface measurement is unavailable or incomplete.",
                )
            )
        return CylinderResolution(
            GeometryCorrespondence(
                status="NONE",
                candidate_indices=[],
                reason="No measured cylindrical surface matches the expected diameter within software tolerance.",
            )
        )
    if len(matching) > 1:
        return CylinderResolution(
            GeometryCorrespondence(
                status="AMBIGUOUS",
                candidate_indices=indices,
                reason="Multiple measured cylindrical surfaces match the expected diameter.",
            )
        )
    return CylinderResolution(
        GeometryCorrespondence(
            status="UNIQUE",
            candidate_indices=indices,
            reason="One measured cylindrical surface matches the expected diameter.",
        ),
        candidate=matching[0],
    )


def _within_tolerance(actual: float, expected: float, absolute: float, relative: float) -> bool:
    return abs(actual - expected) <= max(absolute, abs(expected) * relative)
