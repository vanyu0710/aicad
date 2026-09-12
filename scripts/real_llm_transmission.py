"""真实 LLM 验收：5 挡手动变速器（含换挡机构 + 分箱体），v0.17 / kernel v2.18。

任务：常规前置前驱 5 挡手动变速器（5 前进挡 + 1 倒挡）。
- 三轴式：输入轴、输出轴、中间轴（常啮合）
- 5 对常啮合**斜齿**圆柱齿轮（helix_angle_deg 15~20，真渐开线）
- 倒挡惰轮
- 同步器毂 + 接合套（每挡一套，可用齿圈 + 滑块槽近似）
- 换挡拨叉 ×2 + 拨叉轴 ×2
- 输入/输出轴端**花键** + 轴上**键槽**
- 剖分式箱体：壳体 + 端盖，轴承座孔
- 轴承 ×6~8

运行（aicad venv，.env 配好 planner）：
  .\\.venv\\Scripts\\python.exe scripts/real_llm_transmission.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from backend.agent.approvals import ApprovalBroker  # noqa: E402
from backend.agent.loop import build_task_message, run_agent_loop  # noqa: E402
from backend.agent.session import AgentSession  # noqa: E402
from backend.kernel_worker import get_worker_manager  # noqa: E402
from backend.mechcad_ai.client import chat_completion_with_tools, has_configured_model, resolve_role_config  # noqa: E402
from backend.mechcad_ai.prompts import get_prompt  # noqa: E402
from backend.schemas import ModelConfig  # noqa: E402
from backend.storage import ARTIFACT_ROOT  # noqa: E402

TASK = (
    "设计一台 5 挡手动变速器（前置前驱、三轴式，5 个前进挡 + 1 个倒挡），输入扭矩 200 N·m、"
    "输入转速 3000 rpm，目标是完整可装配的变速器总成："
    "①输入轴/输出轴/中间轴三根轴（阶梯轴，输入端与输出端做花键，轴上做键槽）；"
    "②5 对常啮合斜齿圆柱齿轮（helix_angle_deg 取 15~20，真渐开线，模数 2~3）；"
    "③倒挡惰轮与倒挡轴；"
    "④5 套同步器（同步器毂 + 接合套，可用带外齿的齿圈与滑块槽近似）；"
    "⑤2 根换挡拨叉与 2 根拨叉轴；"
    "⑥剖分式箱体：下壳体（含轴承座孔与加强筋）+ 上端盖；"
    "⑦支撑轴承。"
    "先做设计调研（传动比分级、齿轮副几何与中心距、各轴轴径、箱体壁厚），"
    "再出零件清单（BOM）计划等我确认（每个零件给 key_params 参数表与装配位姿 pose），"
    "然后逐件建模归档，全部完成后调用 export_assembly 导出装配交付物。"
)
AUTO_ANSWER = "按常规乘用车手动变速器设计取值即可"


def auto_approve(broker: ApprovalBroker, max_wait: float = 7200.0) -> None:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        with broker._lock:
            if not broker._requests:
                time.sleep(0.5)
                continue
            aid = next(iter(broker._requests))
            req = broker._requests[aid]
        kind = getattr(req, "kind", "")
        args = getattr(req, "args", {}) or {}
        try:
            if kind == "ask_user":
                answers = {}
                for q in args.get("questions") or []:
                    qid, qtype = q.get("id"), q.get("type", "text")
                    opts = q.get("options") or []
                    if qtype == "multi":
                        answers[qid] = [opts[0]["label"]] if opts else [AUTO_ANSWER]
                    elif qtype == "single":
                        answers[qid] = opts[0]["label"] if opts else AUTO_ANSWER
                    else:
                        answers[qid] = AUTO_ANSWER
                print(f"  [auto] 批准 ask_user {aid}", flush=True)
                broker.resolve(aid, "edit", {"answers": answers})
            else:
                print(f"  [auto] 批准 {kind} {aid}", flush=True)
                broker.resolve(aid, "approve")
        except Exception as exc:  # noqa: BLE001
            print(f"  [auto] resolve 失败 {aid}: {exc}", flush=True)
        time.sleep(0.3)


def main() -> int:
    if not has_configured_model(ModelConfig(), "planner"):
        print("未配置 planner 模型（检查 .env）")
        return 1
    cfg = resolve_role_config(ModelConfig(), "planner")
    print(f"planner = {cfg['model']} @ {cfg['base_url']}", flush=True)

    manager = get_worker_manager()
    project_id = f"llm-trans-{int(time.time())}"
    worker = manager.get_or_start(project_id)
    caps = worker.capabilities()
    run_dir = ARTIFACT_ROOT / f"llm_trans_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    def emit(ev, msg, payload):
        if ev == "agent_step":
            op = payload.get("op") or ""
            print(f"  [step {payload.get('step')}] {op}: {str(payload.get('summary') or msg)[:92]}", flush=True)
        elif ev == "plan_updated":
            bom = payload.get("bom") or []
            print(f"  [plan] {str(payload.get('summary',''))[:76]} | 步骤 {len(payload.get('steps') or [])} | BOM {len(bom)}", flush=True)
        elif ev == "artifact_ready":
            print(f"  [artifact] {msg[:66]}", flush=True)

    broker = ApprovalBroker(timeout=7200)
    threading.Thread(target=auto_approve, args=(broker,), daemon=True).start()
    session = AgentSession(project_id=project_id,
                           path=ROOT / "work" / "agent_sessions" / f"{project_id}.json")

    def chat(messages, tools, on_text_delta=None):
        return chat_completion_with_tools(
            ModelConfig(), "planner", messages, tools,
            max_tokens=int(__import__("os").getenv("MECHCAD_PLANNER_MAX_TOKENS", "32768")),
            on_text_delta=on_text_delta)

    t0 = time.time()
    result = run_agent_loop(
        worker=worker,
        chat_with_tools=chat,
        protocol=cfg["protocol"],
        emit=emit,
        run_dir=run_dir,
        system_prompt=get_prompt("agent_modeling", "zh"),
        language="zh",
        max_steps=200,
        approvals=broker,
        session=session,
        initial_user_message=build_task_message(TASK, worker, caps),
        mode="plan",
        project_id=project_id,
    )
    dt = time.time() - t0

    print(f"\n=== ok={result.ok} status={result.status} steps={result.steps} parts={len(result.parts)} "
          f"calc={len(result.design_calculations)} {dt:.0f}s ===", flush=True)
    plan = session.plan_dict()
    print(f"计划: {plan.get('summary','')[:110]}", flush=True)
    print(f"  BOM {len(plan.get('bom') or [])} 项，步骤 {len(plan.get('steps') or [])} 步", flush=True)
    for p in result.parts:
        f = Path(p["step"])
        print(f"  part #{p['index']:02d} {p['part']:<22} via={p.get('built_via'):<6} "
              f"{p['step_file']:<36} {f.stat().st_size if f.exists() else 0}B", flush=True)
    if result.assembly:
        a = result.assembly
        print(f"装配: {a.get('step_file')} | 零件 {a.get('parts_count')} | "
              f"硬碰撞 {a.get('hard_collision_count')} / 干涉 {a.get('interfering_count')}（豁免 {a.get('exempted_count')}）| "
              f"预览 {a.get('render_file')}", flush=True)
    if result.error:
        print("error:", str(result.error)[:300], flush=True)
    print("final_text:", (result.final_text or "")[:400].replace("\n", " "), flush=True)
    manager.stop(project_id)
    ok = bool(result.ok and result.parts)
    print("\nLLM-TRANSMISSION", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
