"""backend/agent/calc_sandbox.py 测试：受限纯算术沙箱的放行面与拒绝面。"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.agent import calc_sandbox
from backend.agent.calc_sandbox import SandboxError, run_sandboxed


class SandboxAllowedTests(unittest.TestCase):
    def test_arithmetic_with_variables(self) -> None:
        out = run_sandboxed(
            "d = 2 * sqrt(r ** 2 + h ** 2)\ntotal = d + center\nresult = {\"d\": d, \"total\": total}",
            {"r": 20.0, "h": 15.0, "center": 80.0},
        )
        self.assertAlmostEqual(out["result"]["d"], 50.0, places=6)
        self.assertAlmostEqual(out["result"]["total"], 130.0, places=6)
        self.assertEqual(out["variables_used"], ["center", "h", "r"])

    def test_loop_and_branch(self) -> None:
        out = run_sandboxed(
            "s = 0\nfor i in range(1, 101):\n    s = s + i\nif s > 100:\n    s = s + 1\nresult = s",
        )
        self.assertEqual(out["result"], 5051)

    def test_math_helpers(self) -> None:
        out = run_sandboxed("result = [degrees(pi), round(tan(radians(45)), 6), floor(3.9), gcd(12, 18)]")
        self.assertEqual(out["result"], [180.0, 1.0, 3, 6])


class SandboxDeniedTests(unittest.TestCase):
    def test_static_escapes(self) -> None:
        cases = {
            "import": "import os\nresult = 1",
            "from-import": "from math import pi\nresult = pi",
            "attribute": "result = ().__class__",
            "builtin-call": "result = open('x')",
            "eval-call": "result = eval('1+1')",
            "comprehension": "result = [i for i in range(3)]",
            "lambda": "f = lambda x: x\nresult = f(1)",
            "while": "n = 0\nwhile n < 3:\n    n = n + 1\nresult = n",
            "no-result": "x = 41 + 1",
            "subscript": "t = (1, 2)\nresult = t[0]",
            "huge-range-arg": "result = range(100000000) and 1",  # range 参数 > 1e7
            "huge-constant": "result = 2000000000",               # 数值常量 > 1e9
        }
        for name, code in cases.items():
            with self.subTest(name):
                try:
                    run_sandboxed(code)
                except SandboxError as exc:
                    self.assertTrue(str(exc))
                    continue
                self.fail(f"应拒绝: {name}")


class SandboxGuardTests(unittest.TestCase):
    def test_variable_validation(self) -> None:
        with self.assertRaises(SandboxError):
            run_sandboxed("result = x", {"x": "str"})
        with self.assertRaises(SandboxError):
            run_sandboxed("result = x", {"x": float("nan")})
        with self.assertRaises(SandboxError):
            run_sandboxed("result = 1", {"pi": 3.0})  # 不允许覆盖数学名
        with self.assertRaises(SandboxError):
            run_sandboxed("result = 1", {"import": 1})

    def test_disabled_switch(self) -> None:
        with patch.dict(os.environ, {"MECHCAD_AGENT_CALC_SANDBOX": "off"}):
            with self.assertRaises(SandboxError) as ctx:
                run_sandboxed("result = 1")
            self.assertIn("禁用", str(ctx.exception))

    def test_timeout(self) -> None:
        with self.assertRaises(SandboxError) as ctx:
            run_sandboxed("s = 0\nfor i in range(10 ** 8):\n    s = s + i\nresult = s", timeout=2)
        self.assertIn("超时", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
