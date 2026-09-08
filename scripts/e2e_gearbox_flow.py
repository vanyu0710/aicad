"""F1 变速箱全流程 E2E（真实 MechKernel worker + 脚本化决策序列）。

验证 v0.12 新链路的**全部真实部分**（不含 LLM）：
  design_calculate 调研 → propose_plan(BOM) 批准门控 → make_gear 真齿轮
  → finish_part 逐件归档 STEP/STL + 内核 reset → execution_report 汇总。

决策序列是脚本化的（模拟模型按 prompts 协议的走法）；LLM 真实驱动的验收
请用产品模式在聊天栏输入"给我设计个 1:100 的变速箱"（需配置 MECHCAD_PLANNER_*）。

运行（aicad venv，仓库根）：
  .\\.venv\\Scripts\\python.exe scripts/e2e_gearbox_flow.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.loop import build_task_message, run_agent_loop  # noqa: E402
from backend.kernel_worker import get_worker_manager  # noqa: E402
from backend.mechcad_ai.client import ToolCall, ToolCallRound  # noqa: E402

TASK = "给我设计个 1:100 的变速箱"
MODULE = 2.0
# 1:100 = 5 × 5 × 4（gear_ratio_split 的真实解：17/85, 17/85, 17/68）
GEARS = [
    ("小齿轮17", {"module": MODULE, "teeth": 17, "width": 16, "bore": 14}),
    ("大齿轮85A", {"module": MODULE, "teeth": 85, "width": 16}),
    ("大齿轮85B", {"module": MODULE, "teeth": 85, "width": 14}),
    ("中间小齿轮17B", {"module": MODULE, "teeth": 17, "width": 16}),
    ("大齿轮68", {"module": MODULE, "teeth": 68, "width": 14}),
    ("输出小齿轮17C", {"module": MODULE, "teeth": 17, "width": 14, "bore": 14}),
]
BOM = [
    {"part": name, "role": role, "quantity": 1, "key_params": params}
    for name, role, params in [
        ("小齿轮17", "高速级主动轮", "m=2 z=17 b=16 孔Ø14"),
        ("大齿轮85A", "高速级从动/中轴", "m=2 z=85 b=16"),
        ("大齿轮85B", "中级从动/低速轴", "m=2 z=85 b=14"),
        ("中间小齿轮17B", "中级主动", "m=2 z=17 b=16"),
        ("大齿轮68", "低速级从动", "m=2 z=68 b=14"),
        ("输出小齿轮17C", "低速级主动/输出", "m=2 z=17 b=14 孔Ø14"),
        ("输入轴", "Ø14×120", "d=14 L=120"),
        ("箱底板", "简化箱体（演示）", "220×160×12"),
    ]
]


def _round(name: str, arguments: dict) -> ToolCallRound:
    return ToolCallRound(
        text="",
        tool_calls=[ToolCall(id=f"e2e-{name}-{time.time_ns()}", name=name, arguments=arguments)],
        raw_message={"role": "assistant", "content": None,
                     "tool_calls": [{"id": "e2e", "type": "function",
                                     "function": {"name": name,
                                                  "arguments": json.dumps(arguments, ensure_ascii=False)}}]},
    )


def script() -> list[ToolCallRound]:
    rounds: list[ToolCallRound] = []
    # 调研
    rounds.append(_round("design_calculate", {"kind": "gear_ratio_split",
                                              "params": {"total_ratio": 100.0},
                                              "reason": "1:100 需要几级"}))
    rounds.append(_round("design_calculate", {"kind": "gear_pair",
                                              "params": {"module": MODULE, "z1": 17, "z2": 85},
                                              "reason": "级中心距"}))
    rounds.append(_round("design_calculate", {"kind": "gear_pair",
                                              "params": {"module": MODULE, "z1": 17, "z2": 68},
                                              "reason": "低速级中心距"}))
    rounds.append(_round("design_calculate", {"kind": "shaft_diameter",
                                              "params": {"power_kw": 1.5, "rpm": 1400},
                                              "reason": "输入轴径初估"}))
    rounds.append(_round("design_calculate", {"kind": "housing_wall",
                                              "params": {"center_distance_mm": 102.0},
                                              "reason": "箱体壁厚"}))
    # custom 沙箱调研：校核总传动比乘积
    rounds.append(_round("design_calculate", {"kind": "custom",
                                              "code": "result = r1 * r2 * r3",
                                              "variables": {"r1": 5.0, "r2": 5.0, "r3": 4.0},
                                              "reason": "5*5*4 验算"}))
    # BOM 计划（脚本环境 approvals=None → 计划自动批准；真实产品模式走审批卡）
    steps = []
    sid = 0
    for name, args in GEARS:
        sid += 1
        steps.append({"id": f"s{sid}", "title": f"make_gear {name}", "op": "make_gear", "part": name})
        sid += 1
        steps.append({"id": f"s{sid}", "title": f"归档 {name}", "op": "finish_part", "part": name})
    sid += 1
    steps.append({"id": f"s{sid}", "title": "输入轴草图+拉伸", "op": "extrude", "part": "输入轴"})
    sid += 1
    steps.append({"id": f"s{sid}", "title": "归档输入轴", "op": "finish_part", "part": "输入轴"})
    sid += 1
    steps.append({"id": f"s{sid}", "title": "箱底板拉伸", "op": "extrude", "part": "箱底板"})
    sid += 1
    steps.append({"id": f"s{sid}", "title": "归档箱底板", "op": "finish_part", "part": "箱底板"})
    rounds.append(_round("propose_plan", {"summary": "1:100 = 5×5×4 三级齿轮箱，8 类零件逐件交付",
                                          "bom": BOM, "steps": steps}))
    # 逐件执行
    for name, gear_args in GEARS:
        rounds.append(_round("make_gear", dict(gear_args)))
        rounds.append(_round("finish_part", {"part": name, "note": f"{name} 渐开线/梯形齿归档"}))
    # 输入轴
    rounds.append(_round("create_workplane", {"name": "base", "type": "XY"}))
    rounds.append(_round("new_sketch", {"workplane_name": "base", "sketch_name": "sk_shaft"}))
    rounds.append(_round("add_circle", {"sketch_name": "sk_shaft", "center": [0, 0], "radius": 7}))
    rounds.append(_round("close_sketch", {"sketch_name": "sk_shaft"}))
    rounds.append(_round("extrude", {"sketch_name": "sk_shaft", "depth": 120}))
    rounds.append(_round("finish_part", {"part": "输入轴"}))
    # 箱底板（上一件 finish_part 已 reset，需要重新建基准面）
    rounds.append(_round("create_workplane", {"name": "base", "type": "XY"}))
    rounds.append(_round("new_sketch", {"workplane_name": "base", "sketch_name": "sk_plate"}))
    rounds.append(_round("add_rectangle", {"sketch_name": "sk_plate", "width": 220, "height": 160}))
    rounds.append(_round("close_sketch", {"sketch_name": "sk_plate"}))
    rounds.append(_round("extrude", {"sketch_name": "sk_plate", "depth": 12}))
    rounds.append(_round("finish_part", {"part": "箱底板", "note": "简化演示件"}))
    rounds.append(ToolCallRound(
        text="变速箱 8 类零件全部逐件建模完成并归档（STEP/STL）。关键尺寸来自 gear_ratio_split/gear_pair 调研："
             "m=2，三级 17/85×17/85×17/68=100.0 精确。假设与需复核：未做强度校核、装配位姿与箱体完整特征待 F2。",
        tool_calls=[]))
    return rounds


class ScriptedChat:
    def __init__(self, rounds: list[ToolCallRound]) -> None:
        self.rounds = rounds
        self.index = 0

    def __call__(self, messages, tools) -> ToolCallRound:  # noqa: ANN001
        if self.index >= len(self.rounds):
            return ToolCallRound(text="（脚本耗尽）", tool_calls=[])
        current = self.rounds[self.index]
        self.index += 1
        return current


def main() -> int:
    manager = get_worker_manager()
    project_id = f"e2e-gearbox-{int(time.time())}"
    worker = manager.get_or_start(project_id)
    caps = worker.capabilities()
    print(f"worker started; public ops = {caps['public_count']} "
          f"(make_gear: {'make_gear' in [c['name'] for c in caps['public']]})")

    run_dir = ROOT / "work" / "e2e_gearbox"
    run_dir.mkdir(parents=True, exist_ok=True)
    events: list = []

    def emit(ev: str, message: str, payload: dict) -> None:
        events.append((ev, message, payload))
        if ev in ("agent_step", "plan_updated", "artifact_ready", "agent_done"):
            label = payload.get("op") or payload.get("kind") or ""
            print(f"  [{ev}] {label} {message[:70]}")

    task_message = build_task_message(TASK, worker, caps)
    t0 = time.time()
    result = run_agent_loop(
        worker=worker,
        chat_with_tools=ScriptedChat(script()),
        protocol="openai",
        emit=emit,
        run_dir=run_dir,
        system_prompt="E2E 脚本决策（不经过真实提示词）",
        language="zh",
        max_steps=200,
        initial_user_message=task_message,
        mode="plan",  # 变速箱任务自动升级后的等效模式
    )
    elapsed = time.time() - t0
    print(f"\n=== E2E result: ok={result.ok} steps={result.steps} parts={len(result.parts)} "
          f"calc={len(result.design_calculations)} {elapsed:.1f}s ===")
    for p in result.parts:
        print(f"  part #{p['index']:02d} {p['part']:<14} step={Path(p['step']).name} "
              f"({Path(p['step']).stat().st_size if Path(p['step']).exists() else 'MISSING'} B) "
              f"stl={Path(p['stl']).stat().st_size if Path(p['stl']).exists() else 'MISSING'} B")

    report_path = Path(result.artifacts["execution_report"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = {
        "ok": result.ok and not result.error,
        "8_parts_archived": len(result.parts) == 8,
        "all_step_files_exist": all(Path(p["step"]).exists() and Path(p["stl"]).exists()
                                    for p in result.parts),
        "step_is_real_brep": all(
            Path(p["step"]).read_text(encoding="utf-8", errors="ignore").startswith("ISO-10303-21")
            and Path(p["step"]).stat().st_size > 1000 for p in result.parts),
        "research_recorded": len(result.design_calculations) >= 6
            and all(c["ok"] for c in result.design_calculations),
        "report_has_parts_and_calcs": len(report.get("parts", [])) == 8
            and len(report.get("design_calculations", [])) >= 6,
        "report_ok": bool(report.get("ok")),
        "session_reset_to_empty": (worker.feature_tree() or {}).get("node_count", -1) == 0,
    }
    plan_events = [e for e in events if e[0] == "plan_updated"]
    checks["plan_all_ticked"] = bool(plan_events) and all(
        s.get("status") == "completed"
        for s in (plan_events[-1][2].get("steps") or []))
    for name, passed in checks.items():
        print(f"  check {name}: {'PASS' if passed else 'FAIL'}")
    manager.stop(project_id)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
