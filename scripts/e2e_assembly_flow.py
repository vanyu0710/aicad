"""v0.14 F2a 装配全流程 E2E（真实 MechKernel worker，零 LLM token）。

脚本化决策走完整链路：调研 → BOM（带位姿）计划 → 批准 → 逐件建模 finish_part
（写项目零件库 + manifest）→ export_assembly（装配 STEP + 干涉 + 预览 + 报告）。
故意制造一对重叠零件，验证干涉报告命中。

运行（aicad venv，仓库根）：
  .\\.venv\\Scripts\\python.exe scripts/e2e_assembly_flow.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.approvals import ApprovalBroker  # noqa: E402
from backend.agent.loop import build_task_message, run_agent_loop  # noqa: E402
from backend.agent.session import AgentSession  # noqa: E402
from backend.kernel_worker import get_worker_manager  # noqa: E402
from backend.mechcad_ai.client import ToolCall, ToolCallRound  # noqa: E402
from backend.mechcad_ai.prompts import get_prompt  # noqa: E402
from backend.storage import ARTIFACT_ROOT, PROJECT_PARTS_ROOT, read_manifest  # noqa: E402

TASK = "装配 E2E：底板 + 两个凸台（其中一个故意插入底板），按位姿导出装配并做干涉检查。"

GEAR_SCRIPT = """
k.create_workplane(name='b', type='XY')
k.new_sketch(workplane_name='b', sketch_name='plate')
k.add_rectangle(sketch_name='plate', width=120, height=80)
k.close_sketch(sketch_name='plate')
r = k.extrude(sketch_name='plate', depth=10)
assert r['success']
"""

PEG_SCRIPT = """
k.create_workplane(name='b', type='XY')
k.new_sketch(workplane_name='b', sketch_name='peg')
k.add_circle(sketch_name='peg', center=[0, 0], radius=10)
k.close_sketch(sketch_name='peg')
r = k.extrude(sketch_name='peg', depth=30)
assert r['success']
"""

BOM = [
    {"part": "底板", "role": "基座", "key_params": {"尺寸": "120x80x10"},
     "pose": {"position": [0, 0, 0]}},
    {"part": "凸台A", "role": "正常装配", "key_params": {"Ø": "20x30"},
     "pose": {"position": [60, 0, 10]}},          # 坐在板面上：不重叠
    {"part": "凸台B", "role": "故意插入", "key_params": {"Ø": "20x30"},
     "pose": {"position": [-60, 0, 0]}},          # 与板重叠 10mm
]
STEPS = [
    {"id": "s1", "title": "建底板", "op": "run_build_script", "part": "底板"},
    {"id": "s2", "title": "归档底板", "op": "finish_part", "part": "底板"},
    {"id": "s3", "title": "建凸台A", "op": "run_build_script", "part": "凸台A"},
    {"id": "s4", "title": "归档凸台A", "op": "finish_part", "part": "凸台A"},
    {"id": "s5", "title": "建凸台B", "op": "run_build_script", "part": "凸台B"},
    {"id": "s6", "title": "归档凸台B", "op": "finish_part", "part": "凸台B"},
    {"id": "s7", "title": "装配导出与干涉", "op": "export_assembly", "part": "底板"},
]


def _call(cid: str, name: str, args: dict) -> ToolCallRound:
    return ToolCallRound(
        text="",
        tool_calls=[ToolCall(id=cid, name=name, arguments=args)],
        raw_message={"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]},
    )


class ScriptedChat:
    def __init__(self) -> None:
        self.rounds = [
            _call("p", "propose_plan", {"summary": "底板+两凸台（B 故意插入）验证装配链路",
                                        "bom": BOM, "steps": STEPS}),
            _call("g1", "run_build_script", {"code": GEAR_SCRIPT, "reason": "底板"}),
            _call("f1", "finish_part", {"part": "底板",
                                        "feature_contract": [{"radius_mm": 40.0, "count": 0}]}),
            _call("g2", "run_build_script", {"code": PEG_SCRIPT, "reason": "凸台A"}),
            _call("f2", "finish_part", {"part": "凸台A"}),
            _call("g3", "run_build_script", {"code": PEG_SCRIPT, "reason": "凸台B"}),
            _call("f3", "finish_part", {"part": "凸台B"}),
            _call("a", "export_assembly", {
                "note": "F2a E2E 装配交付",
                # v2.17 P1-8：未豁免硬碰撞会阻断导出——凸台B×底板 10mm 重叠按"演示配合"豁免
                "expected_overlaps": [{"a": "凸台B", "b": "底板", "max_volume_mm3": 5000,
                                       "category": "fit", "reason": "E2E 演示设计内重叠"}]}),
            ToolCallRound(text="装配交付完成：3 件按位姿组装，干涉报告命中凸台B×底板。", tool_calls=[]),
        ]
        self.index = 0

    def __call__(self, messages, tools):
        # 断言 finish_part 底板时 pose 已进 part_rec（来自 BOM）
        if self.index == 2:
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            plan_msg = json.loads(tool_msgs[0]["content"])
            assert plan_msg.get("approved"), plan_msg
        if self.index >= len(self.rounds):
            return ToolCallRound(text="done", tool_calls=[])
        current = self.rounds[self.index]
        self.index += 1
        return current


def main() -> int:
    manager = get_worker_manager()
    project_id = f"e2e-asm-{int(time.time())}"
    worker = manager.get_or_start(project_id)
    caps = worker.capabilities()
    run_dir = ARTIFACT_ROOT / f"e2e_assembly_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    events: list = []

    def emit(ev, msg, payload):
        events.append((ev, msg, payload))
        if ev == "agent_step":
            print(f"  [step {payload.get('step')}] {payload.get('op')}: "
                  f"{str(payload.get('summary') or msg)[:80]}")
        elif ev == "artifact_ready":
            print(f"  [artifact] {msg[:70]}")

    broker = ApprovalBroker(timeout=60)
    import threading

    def auto():
        time.sleep(0.3)
        with broker._lock:
            ids = list(broker._requests)
        for aid in ids:
            broker.resolve(aid, "approve")

    threading.Thread(target=auto, daemon=True).start()
    session = AgentSession(project_id=project_id,
                           path=ROOT / "work" / "agent_sessions" / f"{project_id}.json")
    result = run_agent_loop(
        worker=worker,
        chat_with_tools=ScriptedChat(),
        protocol="openai",
        emit=emit,
        run_dir=run_dir,
        system_prompt=get_prompt("agent_modeling", "zh"),
        language="zh",
        max_steps=60,
        approvals=broker,
        session=session,
        initial_user_message=build_task_message(TASK, worker, caps),
        mode="plan",
        project_id=project_id,
    )

    checks: dict[str, bool] = {}
    checks["run_ok"] = bool(result.ok and not result.error)
    checks["3_parts"] = len(result.parts) == 3
    checks["all_in_library"] = all(p.get("library_step_file") and p.get("library_version") == 1
                                   for p in result.parts)
    checks["poses_from_bom"] = [p.get("pose") for p in result.parts] == [
        b["pose"] for b in BOM]
    checks["assembly_summary"] = bool(result.assembly and result.assembly.get("step_file"))
    checks["interference_hit"] = bool(result.assembly and result.assembly.get("interfering_count", 0) >= 1)
    checks["interference_exempted"] = bool(result.assembly and result.assembly.get("expected_fit_count", 0) >= 1
                                           and result.assembly.get("hard_collision_count", 1) == 0)
    checks["total_pairs_3"] = bool(result.assembly and result.assembly.get("total_pairs") == 3)
    lib = PROJECT_PARTS_ROOT / project_id
    manifest = read_manifest(project_id)
    checks["manifest_parts_3"] = len(manifest.get("parts") or []) == 3
    asm = manifest.get("assembly") or {}
    checks["manifest_assembly"] = bool(asm.get("step_file"))
    # 装配 STEP 应含 3 个具名产品（用回读验证 XCAF 结构）
    asm_step = lib / asm["step_file"] if asm.get("step_file") else None
    checks["asm_step_named"] = bool(asm_step and asm_step.exists() and asm_step.stat().st_size > 2000)
    checks["asm_render"] = bool(asm.get("render_file")) and (lib / asm["render_file"]).exists()
    checks["asm_report"] = bool(asm.get("report_file")) and (lib / asm["report_file"]).exists()
    # 装配 STEP 应含 3 个具名产品（用回读验证 XCAF 结构）
    try:
        sys.path.insert(0, str(Path(r"G:\lfy design\ai cad\mechcad-kernel")))
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDataStd import TDataStd_Name
        from OCP.TDF import TDF_Label, TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDoc import XCAFDoc_DocumentTool

        doc = TDocStd_Document(TCollection_ExtendedString("XCAF"))
        XCAFApp_Application.GetApplication_s().NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)
        reader = STEPCAFControl_Reader()
        reader.SetNameMode(True)
        tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        names: list[str] = []

        def walk(label):
            std = TDataStd_Name()
            target = label
            if tool.IsReference_s(label):
                ref = TDF_Label()
                if tool.GetReferredShape_s(label, ref):
                    target = ref
            if target.FindAttribute(TDataStd_Name.GetID_s(), std):
                names.append(str(std.Get().ToWideString()))
            subs = TDF_LabelSequence()
            tool.GetComponents_s(target, subs)
            for k in range(1, subs.Length() + 1):
                walk(subs.Value(k))

        if reader.ReadFile(str(asm_step)) and reader.Transfer(doc):
            roots = TDF_LabelSequence()
            tool.GetFreeShapes(roots)
            for i in range(1, roots.Length() + 1):
                walk(roots.Value(i))
        checks["xcaf_named_parts"] = all(n in " ".join(names) for n in ("底板", "凸台A", "凸台B"))
    except Exception as exc:
        print(f"  XCAF 回读异常: {exc}")
        checks["xcaf_named_parts"] = False

    print("\n=== E2E 结果 ===")
    for name, passed in checks.items():
        print(f"  {name}: {'PASS' if passed else 'FAIL'}")
    manager.stop(project_id)
    ok = all(checks.values())
    print("\nE2E-ASSEMBLY", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
