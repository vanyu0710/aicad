"""真实 LLM 全流程验收：1:100 变速箱（调研→提问→BOM计划→批准→逐件建模→归档）。

用真实 planner 模型 + 真实 MechKernel worker + 计划模式；ask_user / plan_review
由后台线程自动按常规取值批准（无人值守）。打印每步决策与最终零件清单。

运行（aicad venv，需 .env 配好 MECHCAD_PLANNER_*）：
  .\\.venv\\Scripts\\python.exe scripts/real_llm_gearbox.py
"""
from __future__ import annotations

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
    "给我设计个 1:100 的变速箱（三级圆柱齿轮减速器）：输入功率 1.5kW，输入转速 1400rpm，"
    "输出转速约 14rpm，直齿轮模数 m=2，箱体做简化外壳（底板 + 轴承凸台 + 加强筋）。"
    "先做设计调研，再出零件清单（BOM）计划等我确认（每个零件给装配位姿 pose），"
    "然后逐件建模归档，全部完成后调用 export_assembly 导出装配交付物。"
)
AUTO_ANSWER = "按常规机械设计取值即可"


def auto_approve(broker: ApprovalBroker, max_wait: float = 3600.0) -> None:
    """后台线程：自动批准 plan_review / ask_user（常规默认值）。"""
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
    project_id = f"llm-gearbox-{int(time.time())}"
    worker = manager.get_or_start(project_id)
    caps = worker.capabilities()
    run_dir = ARTIFACT_ROOT / f"llm_gearbox_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    events: list = []

    def emit(ev, msg, payload):
        events.append((ev, msg, payload))
        if ev == "agent_step":
            op = payload.get("op") or ""
            print(f"  [step {payload.get('step')}] {op}: {str(payload.get('summary') or msg)[:88]}", flush=True)
        elif ev == "plan_updated":
            steps = payload.get("steps") or []
            bom = payload.get("bom") or []
            print(f"  [plan] {str(payload.get('summary',''))[:70]} | 步骤 {len(steps)} | BOM {len(bom)}", flush=True)
        elif ev == "artifact_ready":
            print(f"  [artifact] {msg[:60]}", flush=True)
        elif ev == "agent_text_delta":
            print(f"  [text] {str(msg)[:200]}", flush=True)

    broker = ApprovalBroker(timeout=3600)
    threading.Thread(target=auto_approve, args=(broker,), daemon=True).start()
    session = AgentSession(project_id=project_id,
                           path=ROOT / "work" / "agent_sessions" / f"{project_id}.json")

    def chat(messages, tools, on_text_delta=None):
        return chat_completion_with_tools(ModelConfig(), "planner", messages, tools,
                                          max_tokens=int(__import__("os").getenv("MECHCAD_PLANNER_MAX_TOKENS","32768")), on_text_delta=on_text_delta)

    t0 = time.time()
    result = run_agent_loop(
        worker=worker,
        chat_with_tools=chat,
        protocol=cfg["protocol"],
        emit=emit,
        run_dir=run_dir,
        system_prompt=get_prompt("agent_modeling", "zh"),
        language="zh",
        max_steps=120,
        approvals=broker,
        session=session,
        initial_user_message=build_task_message(TASK, worker, caps),
        mode="plan",  # 变速箱任务自动升级后的等效模式
        project_id=project_id,
    )
    dt = time.time() - t0

    print(f"\n=== ok={result.ok} steps={result.steps} parts={len(result.parts)} "
          f"calc={len(result.design_calculations)} {dt:.0f}s ===", flush=True)
    plan = session.plan_dict()
    print(f"计划: {plan.get('summary','')[:100]}")
    print(f"  BOM {len(plan.get('bom') or [])} 项，步骤 {len(plan.get('steps') or [])} 步")
    for p in result.parts:
        f = Path(p["step"])
        print(f"  part #{p['index']:02d} {p['part']:<18} via={p.get('built_via'):<6} "
              f"{p['step_file']:<32} {f.stat().st_size if f.exists() else 0}B")
    snaps = sorted(run_dir.glob("snapshot_s*.png"))
    print(f"四视角快照: {len(snaps)} 张 → {run_dir}")
    if result.assembly:
        asm = result.assembly
        print(f"装配: {asm.get('step_file')} | 零件 {asm.get('parts_count')} | "
              f"干涉 {asm.get('interfering_count')}（豁免 {asm.get('exempted_count')}）| "
              f"预览 {asm.get('render_file')} 报告 {asm.get('report_file')}", flush=True)
    print("final_text:", (result.final_text or "")[:500].replace("\n", " "), flush=True)
    manager.stop(project_id)
    ok = bool(result.ok and result.parts)
    print("\nLLM-GEARBOX", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
