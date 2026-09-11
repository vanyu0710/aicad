"""提示词 A/B：同一任务同一模型，只换系统提示词（旧 HEAD vs 新七模块）。

指标（审查 §六）：API 调用数、失败调用数、自动修复数、run_script 使用、
首次成功率（无修复一次过）、strict 验证/契约结果、耗时。

运行（aicad venv，.env 配好 planner）：
  .\\.venv\\Scripts\\python.exe scripts/ab_prompt_housing.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from backend.agent.loop import build_task_message, run_agent_loop
from backend.kernel_worker import get_worker_manager
from backend.mechcad_ai.client import chat_completion_with_tools, resolve_role_config, has_configured_model
from backend.mechcad_ai.prompts import get_prompt, load_prompts
from backend.schemas import ModelConfig
from backend.storage import ARTIFACT_ROOT

TASK = ("建一个减速器箱体零件并 finish_part 归档（零件名 housing）：200×130 底板厚 12、"
        "四角 Ø9 螺栓孔（边距 15）、两个立置轴承凸台 Ø40 高 40（中心距 100）、"
        "Ø25 通轴承孔、两凸台之间一条三角加强筋。请用 run_build_script 一次完成。")


def run_variant(tag: str, prompts_file: str | None) -> dict:
    os.environ.pop("MECHCAD_PROMPTS_FILE", None)
    if prompts_file:
        os.environ["MECHCAD_PROMPTS_FILE"] = prompts_file
    load_prompts.cache_clear()
    system_prompt = get_prompt("agent_modeling", "zh")
    load_prompts.cache_clear()

    cfg = resolve_role_config(ModelConfig(), "planner")
    manager = get_worker_manager()
    pid = f"ab-{tag}-{int(time.time())}"
    worker = manager.get_or_start(pid)
    caps = worker.capabilities()
    run_dir = ARTIFACT_ROOT / f"ab_{tag}_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    stats = {"failed_calls": 0, "autofix_retries": 0, "run_script_calls": 0,
             "op_calls": 0, "plan_rejects": 0}

    def emit(ev, msg, payload):
        if ev != "agent_step" or not isinstance(payload, dict):
            return
        op = payload.get("op") or ""
        if payload.get("autofix"):
            stats["autofix_retries"] += 1
        if op == "run_build_script":
            stats["run_script_calls"] += 1
        elif op not in ("propose_plan", "update_plan", "ask_user", "design_calculate", "finish_part", "export_assembly"):
            stats["op_calls"] += 1
        if payload.get("success") is False:
            stats["failed_calls"] += 1
            if payload.get("error_kind") == "BOM_MISSING_PARAMS":
                stats["plan_rejects"] += 1
        print(f"  [{tag} step {payload.get('step')}] {op}: {str(payload.get('summary') or msg)[:80]}", flush=True)

    def chat(messages, tools, on_text_delta=None):
        return chat_completion_with_tools(
            ModelConfig(), "planner", messages, tools,
            max_tokens=int(os.getenv("MECHCAD_PLANNER_MAX_TOKENS", "32768")),
            on_text_delta=on_text_delta)

    t0 = time.time()
    result = run_agent_loop(
        worker=worker, chat_with_tools=chat, protocol=cfg["protocol"], emit=emit,
        run_dir=run_dir, system_prompt=system_prompt,
        language="zh", max_steps=25,
        initial_user_message=build_task_message(TASK, worker, caps),
        mode="auto",
    )
    dt = time.time() - t0
    manager.stop(pid)
    report = {}
    rp = run_dir / "execution_report.json"
    if rp.exists():
        report = json.loads(rp.read_text(encoding="utf-8"))
    part = result.parts[0] if result.parts else {}
    return {
        "tag": tag, "model": cfg["model"], "seconds": round(dt, 1),
        "steps": result.steps, "ok": result.ok, "status": result.status,
        "error_kind": result.error_kind,
        "archived": bool(part), "built_via": part.get("built_via"),
        "volume_mm3": part.get("volume_mm3") or report.get("volume"),
        "validation_passed": part.get("validation_passed"),
        "failed_calls": stats["failed_calls"], "autofix_retries": stats["autofix_retries"],
        "run_script_calls": stats["run_script_calls"], "op_calls": stats["op_calls"],
        "plan_rejects": stats["plan_rejects"],
        "run_dir": str(run_dir),
    }


def main() -> int:
    if not has_configured_model(ModelConfig(), "planner"):
        print("未配置 planner 模型")
        return 1
    old_file = str(ROOT / "work_ab" / "prompts_old.yaml")
    print("=== A: 旧提示词 ===", flush=True)
    a = run_variant("A_old", old_file)
    print("=== B: 新七模块提示词 ===", flush=True)
    b = run_variant("B_new", None)
    out = ROOT / "work_ab" / "ab_result.json"
    out.write_text(json.dumps([a, b], ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== A/B 对比 =====")
    keys = ["model", "seconds", "steps", "ok", "status", "error_kind", "archived",
            "built_via", "volume_mm3", "validation_passed", "failed_calls",
            "autofix_retries", "run_script_calls", "op_calls", "plan_rejects"]
    print(f"{'metric':22} {'A(旧)':>14} {'B(新)':>14}")
    for k in keys:
        print(f"{k:22} {str(a.get(k)):>14} {str(b.get(k)):>14}")
    print("saved:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
