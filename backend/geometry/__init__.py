"""Read-only BRep geometry utilities.

This package deliberately has no dependency on FeaturePlan, evidence, or UI
layers. It measures geometry that already exists; it does not verify intent.
"""

from backend.geometry.measurement import (
    measure_bounding_box,
    measure_cylindrical_surfaces,
    measure_shape,
    measure_volume,
)

__all__ = [
    "measure_bounding_box",
    "measure_cylindrical_surfaces",
    "measure_shape",
    "measure_volume",
]
