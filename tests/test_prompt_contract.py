"""v0.15 提示词工程测试：七模块结构契约 + 程序侧阶段硬门（key_params / 三态状态）。

提示词是行为契约的一部分，结构本身要被测试锁住——重构/翻译时丢模块要能被发现。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.mechcad_ai.prompts import get_prompt
from backend.mechcad_ai.client import ToolCallRound

from tests.test_agent_loop import FakeWorker, _round_with_call, _run


class PromptStructureTests(unittest.TestCase):
    """agent_modeling 必须保持七模块结构与关键契约短语（中英双语）。"""

    MODULES_ZH = ["【一、建模原则】", "【二、工作流程", "【三、API 使用规范】",
                  "【四、验证规范】", "【五、错误修复规范】", "【六、输出格式】"]
    MODULES_EN = ["[1. Modeling principles]", "[2. Workflow", "[3. API usage rules]",
                  "[4. Validation rules]", "[5. Repair rules]", "[6. Output format]"]
    CONTRACTS_ZH = ["三条铁律", "参数表纪律", "默认特征顺序", "feature_contract",
                    "SCRIPT_OP_FAILED", "SUCCESS/PARTIAL/FAILED", "由程序判定",
                    "禁止凭记忆谎报", "调研纪律", "export_assembly"]
    CONTRACTS_EN = ["Three iron rules", "Parameter-table discipline", "Default feature order",
                    "feature_contract", "SCRIPT_OP_FAILED", "SUCCESS/PARTIAL/FAILED",
                    "decided by the program", "never report from memory"]

    def test_zh_modules_present(self) -> None:
        text = get_prompt("agent_modeling", "zh")
        for module in self.MODULES_ZH:
            self.assertIn(module, text, f"中文提示词缺模块: {module}")

    def test_en_modules_present(self) -> None:
        text = get_prompt("agent_modeling", "en")
        for module in self.MODULES_EN:
            self.assertIn(module, text, f"英文提示词缺模块: {module}")

    def test_zh_contracts_present(self) -> None:
        text = get_prompt("agent_modeling", "zh")
        for phrase in self.CONTRACTS_ZH:
            self.assertIn(phrase, text, f"中文提示词丢契约: {phrase}")

    def test_en_contracts_present(self) -> None:
        text = get_prompt("agent_modeling", "en")
        for phrase in self.CONTRACTS_EN:
            self.assertIn(phrase, text, f"英文提示词丢契约: {phrase}")

    def test_language_override_env_roundtrip(self) -> None:
        """MECHCAD_PROMPTS_FILE 覆盖入口（A/B 基准用）。"""
        import os
        from backend.mechcad_ai import prompts as prompts_mod

        with tempfile.TemporaryDirectory() as td:
            custom = Path(td) / "custom.yaml"
            custom.write_text("agent_modeling:\n  role: x\n  prompt: CUSTOM-PROMPT\n", encoding="utf-8")
            os.environ["MECHCAD_PROMPTS_FILE"] = str(custom)
            prompts_mod.load_prompts.cache_clear()
            try:
                self.assertIn("CUSTOM-PROMPT", prompts_mod.get_prompt("agent_modeling", "zh"))
            finally:
                del os.environ["MECHCAD_PROMPTS_FILE"]
                prompts_mod.load_prompts.cache_clear()


class BomParamsGateTests(unittest.TestCase):
    """§2 参数表程序化：多零件 BOM 缺 key_params 直接打回，不进审批。"""

    def _events_chat(self, bom):
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {"summary": "s", "bom": bom, "steps": []})
            return ToolCallRound(text="结束", tool_calls=[])
        return chat

    def test_missing_key_params_rejected_before_approval(self) -> None:
        worker = FakeWorker()
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {
                    "summary": "两件",
                    "bom": [{"part": "a", "key_params": {"d": "8"}}, {"part": "b"}],
                    "steps": []})
            seen.append(json.loads([m for m in messages if m.get("role") == "tool"][-1]["content"]))
            return ToolCallRound(text="结束", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            _run(worker, chat, run_dir=Path(td), mode="plan")
        self.assertEqual(seen[0]["error_kind"], "BOM_MISSING_PARAMS")
        self.assertIn("b", seen[0]["error"])

    def test_single_part_bom_exempt(self) -> None:
        """单零件不强制参数表（简单任务直建路径）。"""
        worker = FakeWorker()
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {
                    "summary": "一件", "bom": [{"part": "a"}], "steps": []})
            seen.append(json.loads([m for m in messages if m.get("role") == "tool"][-1]["content"]))
            return ToolCallRound(text="结束", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            _run(worker, chat, run_dir=Path(td), mode="plan")
        # 单件不被 BOM_MISSING_PARAMS 拦截（走到审批：无 broker 时自动批准）
        self.assertNotEqual(seen[0].get("error_kind"), "BOM_MISSING_PARAMS")


class StatusTriStateTests(unittest.TestCase):
    """§8 程序判态：SUCCESS / PARTIAL / FAILED 由验证结果算出并写进报告。"""

    def test_success_status(self) -> None:
        worker = FakeWorker([{"success": True, "geometry_summary": {"volume": 1000.0}}])
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("create_workplane", {"name": "base"})
            return ToolCallRound(text="完成", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            result = _run(worker, chat, run_dir=Path(td))
            report = json.loads((Path(td) / "execution_report.json").read_text(encoding="utf-8"))
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(report["status"], "SUCCESS")

    def test_partial_status_when_gate_fails_with_artifact(self) -> None:
        worker = FakeWorker([{"success": True, "geometry_summary": {"volume": 1000.0}}])
        worker.validation_result = {"success": True, "geometry_validation": {
            "valid": False, "status": "invalid", "reason_codes": ["INVALID_SHAPE"]}}
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("create_workplane", {"name": "base"})
            return ToolCallRound(text="完成", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            result = _run(worker, chat, run_dir=Path(td))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "PARTIAL")  # 有产物但验证不过

    def test_failed_status_without_artifact(self) -> None:
        worker = FakeWorker()
        chat = lambda *a, **k: ToolCallRound(text="完成", tool_calls=[])  # noqa: E731
        result = _run(worker, chat)
        self.assertEqual(result.status, "FAILED")


if __name__ == "__main__":
    unittest.main()
