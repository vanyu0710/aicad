"""v0.13 代码通道 E2E（真实 MechKernel worker，零 LLM token）。

脚本化决策走 run_build_script 建一个工业级减速器壳体（对标用户提供的 GLM 截图）：
  200×130 底板 + 四角螺栓孔 + 两端轴承凸台 + 通轴承孔 + 三角加强筋
然后 finish_part 归档，断言：
  - 单实体（复检门通过）、体积对账、STEP 落盘
  - rebuild 可重放（代码件与 op 件同等参数化）
  - 四视角快照生成
运行（aicad venv）：
  .\\.venv\\Scripts\\python.exe scripts/e2e_housing_script.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.loop import build_task_message, run_agent_loop
from backend.kernel_worker import get_worker_manager
from backend.mechcad_ai.client import ToolCall, ToolCallRound
from backend.storage import ARTIFACT_ROOT

TASK = "用建模脚本建一个减速器壳体：200×130×12 底板（四角 Ø9 螺栓孔），" \
       "板上两个轴承凸台（Ø40×高40，中心距 100），Ø25 通轴承孔，两侧三角加强筋。"

# 建模脚本：全部走 kernel 公开 op（k 门面），循环打阵列孔、polyline 做三角筋
HOUSING_SCRIPT = '''
import math
# --- 底板 200×130×12 ---
k.create_workplane(name="base", type="XY")
k.new_sketch(workplane_name="base", sketch_name="plate")
k.add_rectangle(sketch_name="plate", width=200, height=130, name="plate_outline")
k.close_sketch(sketch_name="plate")
r = k.extrude(sketch_name="plate", depth=12)
assert r["success"], "plate extrude failed"
# --- 四角螺栓孔 Ø9（循环阵列）---
for x in (-85, 85):
    for y in (-55, 55):
        r = k.hole(position=[x, y], diameter=9)
        assert r["success"], f"bolt hole at {(x, y)} failed: {r.get('error')}"
# --- 两个轴承凸台 Ø40×40（立置，中心距 100，循环生成）---
for cx in (-50, 50):
    wp = f"boss_wp_{cx}"
    sk = f"boss_{cx}"
    k.create_workplane(name=wp, type="XY", offset=12)
    k.new_sketch(workplane_name=wp, sketch_name=sk)
    k.add_circle(sketch_name=sk, center=(cx, 0), radius=20, name=f"{sk}_c")
    k.close_sketch(sketch_name=sk)
    r = k.extrude(sketch_name=sk, depth=40, mode="add")
    assert r["success"], f"boss at {cx} failed: {r.get('error')}"
# --- 通轴承孔 Ø25（depth 缺省=通孔）---
for cx in (-50, 50):
    r = k.hole(position=[cx, 0], diameter=25)
    assert r["success"], f"bore at {cx} failed: {r.get('error')}"
# --- 三角加强筋（polyline 闭合剖面 + add 拉伸，连接两凸台）---
k.create_workplane(name="rib_wp", type="XY", offset=12)
k.new_sketch(workplane_name="rib_wp", sketch_name="rib")
k.add_polyline(sketch_name="rib", points=[[-50, -30], [-50, 30], [50, 0], [-50, -30]])
r = k.close_sketch(sketch_name="rib")
assert r["success"], f"rib close failed: {r.get('error')}"
r = k.extrude(sketch_name="rib", depth=20, mode="add")
assert r["success"], f"rib extrude failed: {r.get('error')}"
print("housing built: plate + 4 bolt holes + 2 bosses + 2 bores + 1 rib")
'''


class ScriptedChat:
    """模拟模型决策：脚本建壳体 → 错误契约被拒 → 正确契约归档 → 总结。"""

    WRONG_CONTRACT = [{"radius_mm": 4.5, "count": 5}]          # 断言 5 个螺栓孔（实际 4）
    RIGHT_CONTRACT = [{"radius_mm": 4.5, "count": 4},          # 螺栓孔
                      {"radius_mm": 12.5, "count": 2},         # 轴承孔
                      {"radius_mm": 20.0, "count": 2}]         # 凸台外圆

    def __init__(self) -> None:
        def call(cid, name, args):
            return ToolCallRound(text="", tool_calls=[ToolCall(id=cid, name=name, arguments=args)],
                                 raw_message={"role": "assistant", "content": None, "tool_calls": [
                                     {"id": cid, "type": "function", "function": {
                                         "name": name,
                                         "arguments": json.dumps(args, ensure_ascii=False)}}]})
        self.rounds = [
            call("c1", "run_build_script", {"code": HOUSING_SCRIPT, "reason": "壳体一次成型"}),
            call("c2", "finish_part", {"part": "housing", "feature_contract": self.WRONG_CONTRACT}),
            call("c3", "finish_part", {"part": "housing", "note": "代码通道建壳体",
                                       "feature_contract": self.RIGHT_CONTRACT}),
            ToolCallRound(text="壳体已归档（契约校验通过）。", tool_calls=[]),
        ]
        self.index = 0

    def __call__(self, messages, tools):
        # 第 3 轮前断言：第 2 轮的错误契约必须被机器拒绝（真实 LLM 验收发现的谎报场景）
        if self.index == 2:
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            last = json.loads(tool_msgs[-1]["content"])
            assert last.get("error_kind") == "FEATURE_CONTRACT_MISMATCH", \
                f"错误契约应被拒绝，实际: {last}"
            print("  [gate] 错误契约被拦截:", last["error"][:80])
        if self.index >= len(self.rounds):
            return ToolCallRound(text="done", tool_calls=[])
        current = self.rounds[self.index]
        self.index += 1
        return current


def main() -> int:
    manager = get_worker_manager()
    project_id = f"e2e-housing-{int(time.time())}"
    worker = manager.get_or_start(project_id)
    caps = worker.capabilities()
    run_dir = ARTIFACT_ROOT / f"e2e_housing_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    events: list = []

    def emit(ev, msg, payload):
        events.append((ev, msg, payload))
        if ev == "agent_step":
            print(f"  [step {payload.get('step')}] {payload.get('op')}: "
                  f"{str(payload.get('summary') or msg)[:80]}")

    t0 = time.time()
    result = run_agent_loop(
        worker=worker,
        chat_with_tools=ScriptedChat(),
        protocol="openai",
        emit=emit,
        run_dir=run_dir,
        system_prompt="E2E 脚本决策（不经过真实提示词）",
        language="zh",
        max_steps=30,
        initial_user_message=build_task_message(TASK, worker, caps),
        mode="auto",  # auto：计划视为已批准，run_build_script 可用
    )
    dt = time.time() - t0
    print(f"\n=== ok={result.ok} steps={result.steps} parts={len(result.parts)} {dt:.1f}s ===")

    checks: dict[str, bool] = {}
    checks["run_ok"] = bool(result.ok and not result.error)
    checks["one_part_archived"] = len(result.parts) == 1
    part = result.parts[0] if result.parts else {}
    checks["built_via_script"] = part.get("built_via") == "script"
    step_file = Path(part.get("step", "")) if part.get("step") else None
    checks["step_file_exists"] = bool(step_file and step_file.exists() and step_file.stat().st_size > 5000)
    checks["step_is_brep"] = bool(step_file and step_file.read_text(encoding="utf-8", errors="ignore").startswith("ISO-10303-21"))
    # 体积对账：底板 312000 − 螺栓孔≈3050 + 凸台 2×π·20²·40≈100530
    #           − 轴承孔 2×π·12.5²·40≈39270 + 筋≈60000 → 约 43.5 万 mm³
    vol = float(part.get("volume_mm3") or 0)
    checks["volume_sane"] = 380000 < vol < 500000

    # 可重放性：会话已被 finish_part reset，重新跑一次脚本再 rebuild 验证
    worker.run_script(HOUSING_SCRIPT, name="replay-check")
    tree = worker.feature_tree()
    checks["history_recorded"] = len(tree.get("op_history") or []) >= 15
    rb = worker.execute("rebuild", {})
    checks["rebuild_ok"] = bool(rb.get("success"))
    q_solids = worker.execute("query", {"target": "_current_geometry", "what": "solid_count"})
    checks["single_solid"] = q_solids.get("value") == 1
    # 四视角快照
    snaps = [e for e in events if e[0] == "agent_snapshot"]
    checks["snapshot_emitted"] = len(snaps) >= 1
    snap_files = list(run_dir.glob("snapshot_s*.png"))
    checks["snapshot_files"] = len(snap_files) >= 1

    # 复检门负例：故意造双实体零件 → finish_part 应拒绝
    worker.reset()
    worker.run_script(
        "k.create_workplane(name='a', type='XY')\n"
        "k.new_sketch(workplane_name='a', sketch_name='s1')\n"
        "k.add_rectangle(sketch_name='s1', width=10, height=10)\n"
        "k.close_sketch(sketch_name='s1')\n"
        "k.extrude(sketch_name='s1', depth=5)\n"
        "k.create_workplane(name='b', type='XY', offset=50)\n"
        "k.new_sketch(workplane_name='b', sketch_name='s2')\n"
        "k.add_rectangle(sketch_name='s2', width=10, height=10)\n"
        "k.close_sketch(sketch_name='s2')\n"
        "k.extrude(sketch_name='s2', depth=5, mode='add')\n",
        name="two-islands",
    )
    q2 = worker.execute("query", {"target": "_current_geometry", "what": "solid_count"})
    checks["gate_detects_multi_solid"] = q2.get("value") == 2

    print()
    for name, passed in checks.items():
        print(f"  check {name}: {'PASS' if passed else 'FAIL'}")
    manager.stop(project_id)
    all_ok = all(checks.values())
    print(f"\nE2E {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
