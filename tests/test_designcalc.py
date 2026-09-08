"""backend/agent/designcalc.py 测试：工程计算正确性 + 与内核 gear.py 公式对拍。"""

from __future__ import annotations

import math
import os
import unittest
from pathlib import Path

from backend.agent.designcalc import (
    CalcError,
    gear_pair,
    gear_ratio_split,
    housing_wall,
    nearest_standard_module,
    run_builtin,
    shaft_diameter,
)


class RatioSplitTests(unittest.TestCase):
    def test_exact_100_splits_into_three_stages(self) -> None:
        # 1:100：单级 ≤8 → 至少 3 级；3 级有精确解（如 5×5×4）
        out = gear_ratio_split(100.0)
        self.assertTrue(out["schemes"], out["infeasible_stage_notes"])
        self.assertTrue(all(s["stage_count"] >= 3 for s in out["schemes"]))
        best = out["schemes"][0]
        self.assertLessEqual(abs(best["ratio_error_pct"]), 2.0)
        product = 1.0
        for st in best["stages"]:
            self.assertGreaterEqual(st["z1"], 17)          # 根切下限
            self.assertLessEqual(st["z2"], 140)
            product *= st["z2"] / st["z1"]
        self.assertAlmostEqual(product, best["exact_total_ratio"], places=2)

    def test_single_stage_passthrough(self) -> None:
        out = gear_ratio_split(3.2, max_stages=2)
        self.assertEqual(out["schemes"][0]["stage_count"], 1)
        st = out["schemes"][0]["stages"][0]
        self.assertAlmostEqual(st["z2"] / st["z1"], 3.2, places=3)

    def test_two_stage_50(self) -> None:
        out = gear_ratio_split(50.0)
        self.assertEqual(out["schemes"][0]["stage_count"], 2)

    def test_infeasible_reports_notes(self) -> None:
        out = gear_ratio_split(1000.0, max_stages=1, min_stage_ratio=3.0, max_stage_ratio=8.0)
        self.assertEqual(out["schemes"], [])
        self.assertTrue(out["infeasible_stage_notes"])

    def test_invalid_inputs(self) -> None:
        for bad in (
            {"total_ratio": 1.0},          # <=1 无意义
            {"total_ratio": 0},
            {"total_ratio": 100, "max_stages": 9},
            {"total_ratio": 100, "min_stage_ratio": 9.0, "max_stage_ratio": 3.0},  # 倒挂
        ):
            with self.assertRaises(CalcError, msg=str(bad)):
                gear_ratio_split(**bad)


class GearPairTests(unittest.TestCase):
    def test_geometry_values(self) -> None:
        g = gear_pair(module=2.0, z1=20, z2=60)
        self.assertEqual(g["center_distance"], 80.0)
        self.assertEqual(g["pinion"]["pitch_diameter"], 40.0)
        self.assertEqual(g["pinion"]["tip_diameter"], 44.0)
        self.assertEqual(g["pinion"]["root_diameter"], 35.0)
        self.assertEqual(g["gear"]["tip_diameter"], 124.0)
        self.assertAlmostEqual(g["circular_pitch"], math.pi * 2, delta=0.001)  # 输出保留 3 位
        self.assertTrue(g["module_is_standard"])
        self.assertGreater(g["transverse_contact_ratio"], 1.2)  # 大中心距标准副
        self.assertEqual(g["warnings"], [])

    def test_undercut_warning(self) -> None:
        g = gear_pair(module=3.0, z1=14, z2=28)  # z1<17
        self.assertTrue(any("根切" in w for w in g["warnings"]))

    def test_nonstandard_module_flag(self) -> None:
        self.assertFalse(gear_pair(module=2.1, z1=20, z2=40)["module_is_standard"])

    def test_crosscheck_with_kernel_gear_math(self) -> None:
        """与 mechcad-kernel 的 gear_geometry/center_distance 对拍（同源公式锁）。"""
        from backend.kernel_worker import kernel_repo_path

        kernel_repo = kernel_repo_path()
        if not (kernel_repo / "mech_kernel" / "gear.py").exists():
            self.skipTest(f"内核仓不可用: {kernel_repo}")
        import sys

        if str(kernel_repo) not in sys.path:
            sys.path.insert(0, str(kernel_repo))
        try:
            from mech_kernel.gear import center_distance, gear_geometry
        except Exception as exc:  # build123d 未装等
            self.skipTest(f"内核 gear 不可导入: {exc}")
        for m, z1, z2 in [(2.0, 20, 60), (1.5, 24, 72), (4.0, 18, 45)]:
            g = gear_pair(module=m, z1=z1, z2=z2)
            kg1 = gear_geometry(m, z1)
            kg2 = gear_geometry(m, z2)
            # gear_pair 输出统一保留 3 位小数 → 与内核精确值按 0.001 容差对拍
            self.assertAlmostEqual(g["center_distance"], center_distance(m, z1, z2), delta=0.001)
            self.assertAlmostEqual(g["pinion"]["pitch_radius"], kg1["pitch_radius"], delta=0.001)
            self.assertAlmostEqual(g["pinion"]["base_radius"], kg1["base_radius"], delta=0.001)
            self.assertAlmostEqual(g["gear"]["addendum_radius"], kg2["addendum_radius"], delta=0.001)
            self.assertAlmostEqual(g["gear"]["dedendum_radius"], kg2["dedendum_radius"], delta=0.001)

    def test_run_builtin_dispatch(self) -> None:
        out = run_builtin("gear_pair", {"module": 2.0, "z1": 20, "z2": 60})
        self.assertEqual(out["kind"], "gear_pair")
        with self.assertRaises(CalcError):
            run_builtin("nope", {})
        with self.assertRaises(CalcError):
            run_builtin("gear_pair", "not-a-dict")


class ShaftAndHousingTests(unittest.TestCase):
    def test_shaft_from_power_speed(self) -> None:
        # 3 kW / 1450 rpm → T = 9550*3/1450 ≈ 19.759 N·m
        out = shaft_diameter(power_kw=3.0, rpm=1450.0, allowable_shear_mpa=40.0)
        self.assertAlmostEqual(out["torque_nm"], 9550.0 * 3 / 1450, places=3)
        expect_d = (16000.0 * out["torque_nm"] / (math.pi * 40.0)) ** (1 / 3)
        self.assertAlmostEqual(out["d_min_mm"], round(expect_d, 2), places=2)
        self.assertGreater(out["d_with_keyway_mm"], out["d_min_mm"])
        self.assertGreaterEqual(out["recommended_standard_mm"], out["d_with_keyway_mm"])
        self.assertEqual(out["method"], "empirical")

    def test_shaft_torque_direct(self) -> None:
        out = shaft_diameter(torque_nm=100.0)
        self.assertAlmostEqual(out["torque_nm"], 100.0)

    def test_shaft_requires_inputs(self) -> None:
        with self.assertRaises(CalcError):
            shaft_diameter()
        with self.assertRaises(CalcError):
            shaft_diameter(power_kw=3.0)  # 缺 rpm

    def test_housing_wall_clamped(self) -> None:
        small = housing_wall(center_distance_mm=80.0)   # 0.025*80+3=5 → 下限 6
        self.assertEqual(small["wall_thickness_mm"], 6.0)
        big = housing_wall(center_distance_mm=200.0)    # 8.0
        self.assertEqual(big["wall_thickness_mm"], 8.0)
        huge = housing_wall(center_distance_mm=2000.0)  # 上限 14
        self.assertEqual(huge["wall_thickness_mm"], 14.0)
        with self.assertRaises(CalcError):
            housing_wall()

    def test_nearest_standard_module(self) -> None:
        self.assertEqual(nearest_standard_module(1.8)["recommended"], 2.0)
        self.assertEqual(nearest_standard_module(2.2)["recommended"], 2.5)
        self.assertEqual(nearest_standard_module(2.0)["recommended"], 2.0)


if __name__ == "__main__":
    unittest.main()
