from __future__ import annotations

import math
import unittest

# 本机的部分 Windows 字体文件损坏，会让 build123d 导入时崩溃（FontManager 扫描字体）。
# 与 cad_worker/freecad_executor.py 的做法一致：先装 guard 再导入 build123d。
import cad_worker.font_guard as _font_guard

_font_guard.install()

from build123d import Box, BuildPart, Cylinder, Locations, Mode

from backend.geometry.measurement import measure_bounding_box, measure_shape, measure_volume

class GeometryMeasurementTests(unittest.TestCase):
    def test_simple_box_measures_bounding_box_and_volume(self) -> None:
        report = measure_shape(Box(100, 50, 10))

        self.assertEqual(report.status, "MEASUREMENT_SUCCESS")
        self.assertIsNotNone(report.bounding_box)
        self.assertIsNotNone(report.volume)
        self.assertAlmostEqual(report.bounding_box.size_x, 100.0, places=8)
        self.assertAlmostEqual(report.bounding_box.size_y, 50.0, places=8)
        self.assertAlmostEqual(report.bounding_box.size_z, 10.0, places=8)
        self.assertAlmostEqual(report.volume.volume, 50000.0, places=7)
        self.assertEqual(report.cylinders, [])

    def test_cylinder_measures_radius_diameter_bounding_box_and_volume(self) -> None:
        report = measure_shape(Cylinder(5, 20))

        self.assertEqual(report.status, "MEASUREMENT_SUCCESS")
        self.assertAlmostEqual(report.bounding_box.size_x, 10.0, places=8)
        self.assertAlmostEqual(report.bounding_box.size_y, 10.0, places=8)
        self.assertAlmostEqual(report.bounding_box.size_z, 20.0, places=8)
        self.assertAlmostEqual(report.volume.volume, math.pi * 5.0**2 * 20.0, places=8)
        self.assertEqual(len(report.cylinders), 1)
        cylinder = report.cylinders[0]
        self.assertEqual(cylinder.measurement_index, 0)
        self.assertAlmostEqual(cylinder.radius, 5.0, places=8)
        self.assertAlmostEqual(cylinder.diameter, 10.0, places=8)
        self.assertEqual(cylinder.axis, [0.0, 0.0, 1.0])
        self.assertAlmostEqual(cylinder.height, 20.0, places=8)

    def test_box_with_cylindrical_cut_changes_volume_and_detects_surface(self) -> None:
        box = Box(100, 50, 10)
        cut_shape = box - Cylinder(5, 12)

        report = measure_shape(cut_shape)

        self.assertEqual(report.status, "MEASUREMENT_SUCCESS")
        self.assertLess(report.volume.volume, measure_volume(box).volume)
        self.assertGreaterEqual(len(report.cylinders), 1)
        self.assertTrue(any(math.isclose(item.diameter or 0.0, 10.0) for item in report.cylinders))
        self.assertTrue(all(item.fact_type == "cylindrical_surface" for item in report.cylinders))

    def test_multiple_cylindrical_surfaces_are_complete_and_deterministic(self) -> None:
        with BuildPart() as part:
            Box(100, 50, 10)
            with Locations((-20, 0, 0), (20, 0, 0)):
                Cylinder(5, 12, mode=Mode.SUBTRACT)

        first = measure_shape(part.part)
        second = measure_shape(part.part)

        self.assertEqual(len(first.cylinders), 2)
        self.assertEqual(first, second)
        self.assertEqual([item.measurement_index for item in first.cylinders], [0, 1])
        self.assertTrue(all(math.isclose(item.diameter or 0.0, 10.0) for item in first.cylinders))

    def test_measurement_is_repeatable_and_read_only(self) -> None:
        shape = Box(100, 50, 10) - Cylinder(5, 12)
        before_bbox = measure_bounding_box(shape).model_dump()
        before_volume = measure_volume(shape).model_dump()
        before_faces = len(shape.faces())

        first = measure_shape(shape)
        second = measure_shape(shape)

        self.assertEqual(first, second)
        self.assertEqual(measure_bounding_box(shape).model_dump(), before_bbox)
        self.assertEqual(measure_volume(shape).model_dump(), before_volume)
        self.assertEqual(len(shape.faces()), before_faces)

    def test_missing_shape_is_explicitly_unavailable(self) -> None:
        report = measure_shape(None)

        self.assertEqual(report.status, "MEASUREMENT_UNAVAILABLE")
        self.assertTrue(report.errors)
        self.assertIsNone(report.bounding_box)
        self.assertIsNone(report.volume)


if __name__ == "__main__":
    unittest.main()
