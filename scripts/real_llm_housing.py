"""真实 LLM 验收：Qwen3.8-Flash 用 run_build_script 建减速器壳体（四视角渲染对比）。

运行（aicad venv，需 .env 配好 MECHCAD_PLANNER_*）：
  .\.venv\Scripts\python.exe scripts/real_llm_housing.py
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")

from backend.agent.loop import build_task_message, run_agent_loop
from backend.kernel_worker import get_worker_manager
from backend.mechcad_ai.client import chat_completion_with_tools, resolve_role_config, has_configured_model
from backend.mechcad_ai.prompts import get_prompt
from backend.schemas import ModelConfig
from backend.storage import ARTIFACT_ROOT

TASK = ("建一个减速器箱体零件并 finish_part 归档（零件名 housing）：200×130 底板厚 12、"
        "四角 Ø9 螺栓孔（边距 15）、两个立置轴承凸台 Ø40 高 40（中心距 100）、"
        "Ø25 通轴承孔、两凸台之间一条三角加强筋。请用 run_build_script 一次完成。")

def main() -> int:
    if not has_configured_model(ModelConfig(), "planner"):
        print("未配置 planner 模型"); return 1
    cfg = resolve_role_config(ModelConfig(), "planner")
    print(f"planner = {cfg['model']}", flush=True)
    manager = get_worker_manager()
    pid = f"llm-housing-{int(time.time())}"
    worker = manager.get_or_start(pid)
    caps = worker.capabilities()
    run_dir = ARTIFACT_ROOT / f"llm_housing_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    events = []
    def emit(ev, msg, payload):
        events.append((ev, msg, payload))
        if ev == "agent_step":
            print(f"  [step {payload.get('step')}] {payload.get('op')}: {str(payload.get('summary') or msg)[:90]}", flush=True)
        elif ev == "agent_text_delta":
            pass
    def chat(messages, tools, on_text_delta=None):
        return chat_completion_with_tools(ModelConfig(), "planner", messages, tools,
                                          max_tokens=int(__import__("os").getenv("MECHCAD_PLANNER_MAX_TOKENS","32768")), on_text_delta=on_text_delta)
    t0 = time.time()
    result = run_agent_loop(
        worker=worker, chat_with_tools=chat, protocol=cfg["protocol"], emit=emit,
        run_dir=run_dir, system_prompt=get_prompt("agent_modeling", "zh"),
        language="zh", max_steps=25,
        initial_user_message=build_task_message(TASK, worker, caps),
        mode="auto",
    )
    dt = time.time() - t0
    print(f"\n=== ok={result.ok} steps={result.steps} parts={len(result.parts)} {dt:.0f}s ===", flush=True)
    print("final_text:", (result.final_text or "")[:400], flush=True)
    for p in result.parts:
        print(f"  part {p['part']} via={p.get('built_via')} {p['step_file']} vol={p.get('volume_mm3')}", flush=True)
    snaps = sorted(run_dir.glob("snapshot_s*.png"))
    print("snapshots:", [s.name for s in snaps], flush=True)
    manager.stop(pid)
    ok = bool(result.ok and result.parts)
    print("LLM-HOUSING", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
