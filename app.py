from __future__ import annotations

import json
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from mechcad.api_config import (
    ApiConfig,
    dashscope_base_url_for_region,
    env_value,
    normalize_dashscope_base_url,
    normalize_minimax_base_url,
    normalize_role_base_url,
    save_api_config,
    test_minimax_config,
    test_qwen_config,
)
from mechcad.feedback import save_feedback
from mechcad.pipeline import GenerationSettings, run_generation


load_dotenv()


def generate(
    image,
    description: str,
    part_family: str,
    quality_mode: str,
    operation_mode: str,
    force_real_api: bool,
    dashscope_api_key: str,
    dashscope_base_url: str,
    qwen_model: str,
    vision_protocol: str,
    minimax_api_key: str,
    minimax_protocol: str,
    minimax_base_url: str,
    minimax_model: str,
    temperature: float,
    sandbox_timeout: int,
    clarification_mode: str,
    clarification_answers: str,
):
    if image is None:
        raise gr.Error("请先上传一张 PNG/JPG 草图。")
    if not description or not description.strip():
        raise gr.Error("请用一句话描述这个零件的功能。")

    normalized_operation_mode = "strict" if operation_mode in {"strict", "严格模式"} else "smart"
    settings = GenerationSettings(
        part_family=part_family,
        quality_mode=quality_mode,
        operation_mode=normalized_operation_mode,
        force_real_api=force_real_api,
        api_config=_api_config(
            dashscope_api_key,
            dashscope_base_url,
            qwen_model,
            vision_protocol,
            minimax_api_key,
            minimax_protocol,
            minimax_base_url,
            minimax_model,
        ),
        qwen_model=qwen_model.strip() or None,
        minimax_model=minimax_model.strip() or None,
        temperature=temperature,
        sandbox_timeout=sandbox_timeout,
        clarification_mode=clarification_mode,
        clarification_answers=clarification_answers or "",
    )
    result = run_generation(image, description.strip(), settings)

    qwen_json = json.dumps(result.qwen_json, ensure_ascii=False, indent=2)
    model_plan = json.dumps(result.model_plan, ensure_ascii=False, indent=2)
    return (
        result.report_markdown,
        result.obj_path,
        result.step_path,
        result.stl_path,
        qwen_json,
        model_plan,
        result.build123d_code,
        result.run_id,
        "\n".join(f"{i}. {q}" for i, q in enumerate(result.clarification_questions, 1)),
    )


def feedback(run_id: str, rating: int, issue: str, would_continue: bool, contact: str):
    if not run_id:
        raise gr.Error("请先完成一次生成，再提交反馈。")
    path = save_feedback(
        run_id=run_id,
        rating=rating,
        issue=issue.strip(),
        would_continue=would_continue,
        contact=contact.strip(),
    )
    return f"已保存反馈：{path}"


def save_config(
    dashscope_api_key: str,
    dashscope_base_url: str,
    qwen_model: str,
    vision_protocol: str,
    minimax_api_key: str,
    minimax_protocol: str,
    minimax_base_url: str,
    minimax_model: str,
):
    return save_api_config(
        _api_config(
            dashscope_api_key,
            dashscope_base_url,
            qwen_model,
            vision_protocol,
            minimax_api_key,
            minimax_protocol,
            minimax_base_url,
            minimax_model,
        )
    )


def test_qwen(dashscope_api_key: str, dashscope_base_url: str, qwen_model: str, vision_protocol: str):
    try:
        return test_qwen_config(
            ApiConfig(
                dashscope_api_key=dashscope_api_key.strip(),
                dashscope_base_url=dashscope_base_url.strip(),
                qwen_model=qwen_model.strip(),
                vision_api_key=dashscope_api_key.strip(),
                vision_base_url=dashscope_base_url.strip(),
                vision_model=qwen_model.strip(),
                vision_protocol=vision_protocol.strip().lower(),
            )
        )
    except Exception as exc:
        return f"Qwen/DashScope 测试失败：{exc}"


def test_minimax(minimax_api_key: str, minimax_protocol: str, minimax_base_url: str, minimax_model: str):
    try:
        return test_minimax_config(
            ApiConfig(
                minimax_api_key=minimax_api_key.strip(),
                minimax_protocol=minimax_protocol.strip().lower(),
                minimax_base_url=minimax_base_url.strip(),
                minimax_model=minimax_model.strip(),
            )
        )
    except Exception as exc:
        return f"后端建模模型测试失败：{exc}"


def apply_dashscope_region(region: str):
    return dashscope_base_url_for_region(region)


def apply_vision_provider(provider: str):
    presets = {
        "DashScope / Qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "openai", "qwen-vl-plus"),
        "OpenAI / GPT-4o": ("https://api.openai.com/v1", "openai", "gpt-4o"),
        "Anthropic / Claude": ("https://api.anthropic.com", "anthropic", "claude-3-5-sonnet-latest"),
        "Gemini 兼容端点": ("https://generativelanguage.googleapis.com/v1beta/openai", "openai", "gemini-2.0-flash"),
        "Ollama 本地模型": ("http://127.0.0.1:11434/v1", "openai", "qwen2.5-vl"),
        "自定义视觉模型": ("", "openai", ""),
    }
    return presets.get(provider, presets["自定义视觉模型"])


def apply_minimax_protocol(protocol: str):
    if protocol == "anthropic":
        return "https://api.minimaxi.com/anthropic"
    return "https://api.minimax.io/v1"


def apply_model_profile(
    profile: str,
    vision_base_url: str,
    vision_protocol: str,
    vision_model: str,
    planner_protocol: str,
    planner_base_url: str,
    planner_model: str,
    temperature: float,
):
    profiles = {
        "Qwen VL + MiniMax M3（推荐）": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1", "openai", "qwen-vl-plus",
            "anthropic", "https://api.minimax.io/anthropic", "MiniMax-M3", 0.2,
        ),
        "GPT-4o Vision + Claude": (
            "https://api.openai.com/v1", "openai", "gpt-4o",
            "anthropic", "https://api.anthropic.com", "claude-3-5-sonnet-latest", 0.2,
        ),
        "Gemini Vision + MiniMax": (
            "https://generativelanguage.googleapis.com/v1beta/openai", "openai", "gemini-2.0-flash",
            "anthropic", "https://api.minimax.io/anthropic", "MiniMax-M3", 0.2,
        ),
        "自定义组合": None,
    }
    selected = profiles.get(profile)
    if selected is None:
        return vision_base_url, vision_protocol, vision_model, planner_protocol, planner_base_url, planner_model, temperature
    return selected


def _api_config(
    dashscope_api_key: str,
    dashscope_base_url: str,
    qwen_model: str,
    vision_protocol: str,
    minimax_api_key: str,
    minimax_protocol: str,
    minimax_base_url: str,
    minimax_model: str,
) -> ApiConfig:
    return ApiConfig(
        dashscope_api_key=dashscope_api_key.strip() or None,
        dashscope_base_url=dashscope_base_url.strip() or None,
        qwen_model=qwen_model.strip() or None,
        vision_api_key=dashscope_api_key.strip() or None,
        vision_base_url=dashscope_base_url.strip() or None,
        vision_model=qwen_model.strip() or None,
        vision_protocol=vision_protocol.strip().lower() or None,
        minimax_api_key=minimax_api_key.strip() or None,
        minimax_protocol=minimax_protocol.strip().lower() or None,
        minimax_base_url=minimax_base_url.strip() or None,
        minimax_model=minimax_model.strip() or None,
        planner_api_key=minimax_api_key.strip() or None,
        planner_base_url=minimax_base_url.strip() or None,
        planner_model=minimax_model.strip() or None,
        planner_protocol=minimax_protocol.strip().lower() or None,
    )


CSS = """
:root {
  --paper: #fdfcf8;
  --paper-warm: #f8f6ef;
  --paper-cool: #eef4f0;
  --paper-deep: #e4ece7;
  --ink: #2f383c;
  --ink-soft: #687278;
  --ink-line: rgba(47, 56, 60, 0.16);
  --ink-line-strong: rgba(47, 56, 60, 0.24);
  --jade: #577a72;
  --jade-soft: rgba(87, 122, 114, 0.18);
  --cinnabar: #a42e23;
  --cinnabar-soft: rgba(164, 46, 35, 0.14);
  --mist: rgba(255, 255, 255, 0.52);
}

html,
body {
  margin: 0;
  min-height: 100%;
  color: var(--ink) !important;
  background:
    radial-gradient(ellipse at top, rgba(223, 236, 232, 0.8), transparent 44%),
    linear-gradient(180deg, #fdfcf8 0%, #fbfcfb 32%, #f5f8f6 72%, #edf3f0 100%) !important;
  font-family: "Times New Roman", "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif !important;
}

body {
  overflow-x: hidden;
}

.gradio-container {
  max-width: 1360px !important;
  margin: 0 auto !important;
  padding: 20px 18px 42px !important;
  background:
    linear-gradient(180deg, rgba(255,255,255,0.42), rgba(255,255,255,0.16)),
    repeating-linear-gradient(0deg, rgba(70, 84, 88, 0.018), rgba(70, 84, 88, 0.018) 1px, transparent 1px, transparent 9px) !important;
}

.mechcad-shell {
  position: relative;
  z-index: 1;
}

.mechcad-hero {
  position: relative;
  overflow: hidden;
  min-height: 420px;
  padding: 34px 30px 24px;
  margin: 0 0 22px;
  border-radius: 0;
  background:
    radial-gradient(ellipse at 18% 14%, rgba(255,255,255,0.94), rgba(255,255,255,0.0) 34%),
    linear-gradient(180deg, rgba(255,255,255,0.72), rgba(245,249,247,0.54) 36%, rgba(239,244,242,0.18) 100%);
  border-bottom: 1px solid var(--ink-line);
}

.mechcad-hero::before {
  content: "";
  position: absolute;
  inset: -8% -10% auto -8%;
  height: 32%;
  background:
    linear-gradient(180deg, rgba(205, 226, 219, 0.42), rgba(205, 226, 219, 0.12) 58%, transparent 100%);
  pointer-events: none;
}

.mechcad-hero::after {
  content: "";
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 14px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(47, 56, 60, 0.12), transparent);
}

.mechcad-hero-cloud {
  position: absolute;
  left: -8%;
  right: -8%;
  border-radius: 999px;
  pointer-events: none;
  mix-blend-mode: multiply;
  filter: saturate(0.52) brightness(1.05);
}

.mechcad-hero-cloud.far {
  top: 6%;
  height: 170px;
  opacity: 0.26;
  background:
    radial-gradient(circle at 10% 52%, rgba(255,255,255,0.95) 0 16%, transparent 18%),
    radial-gradient(circle at 24% 42%, rgba(232, 238, 236, 0.92) 0 17%, transparent 19%),
    radial-gradient(circle at 38% 56%, rgba(245, 248, 246, 0.88) 0 21%, transparent 23%),
    radial-gradient(circle at 58% 44%, rgba(230, 235, 233, 0.92) 0 18%, transparent 20%),
    radial-gradient(circle at 76% 53%, rgba(240, 244, 242, 0.84) 0 16%, transparent 18%),
    radial-gradient(circle at 92% 47%, rgba(229, 235, 232, 0.8) 0 13%, transparent 15%);
  animation: cloud-drift-far 220s linear infinite;
}

.mechcad-hero-cloud.mid {
  top: 13%;
  height: 190px;
  opacity: 0.32;
  background:
    radial-gradient(circle at 12% 52%, rgba(255,255,255,0.96) 0 17%, transparent 19%),
    radial-gradient(circle at 28% 44%, rgba(239, 243, 241, 0.96) 0 20%, transparent 22%),
    radial-gradient(circle at 44% 58%, rgba(231, 238, 235, 0.9) 0 18%, transparent 20%),
    radial-gradient(circle at 62% 43%, rgba(242, 246, 244, 0.92) 0 21%, transparent 23%),
    radial-gradient(circle at 80% 55%, rgba(234, 240, 237, 0.92) 0 17%, transparent 19%),
    radial-gradient(circle at 96% 48%, rgba(225, 231, 229, 0.82) 0 14%, transparent 16%);
  animation: cloud-drift-mid 140s linear infinite reverse;
}

.mechcad-hero-cloud.near {
  top: 22%;
  height: 230px;
  opacity: 0.38;
  background:
    radial-gradient(circle at 9% 54%, rgba(255,255,255,0.97) 0 18%, transparent 20%),
    radial-gradient(circle at 22% 39%, rgba(248, 250, 248, 0.98) 0 20%, transparent 22%),
    radial-gradient(circle at 36% 58%, rgba(237, 244, 241, 0.94) 0 21%, transparent 23%),
    radial-gradient(circle at 52% 41%, rgba(241, 246, 243, 0.98) 0 18%, transparent 20%),
    radial-gradient(circle at 67% 57%, rgba(233, 239, 236, 0.94) 0 20%, transparent 22%),
    radial-gradient(circle at 83% 45%, rgba(246, 248, 247, 0.92) 0 18%, transparent 20%),
    radial-gradient(circle at 96% 55%, rgba(230, 236, 232, 0.82) 0 15%, transparent 17%);
  animation: cloud-drift-near 102s linear infinite;
}

.mechcad-hero-copy {
  position: relative;
  z-index: 2;
  max-width: 980px;
  padding: 34px 6px 0;
}

.mechcad-hero-topline {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
  font-size: 0.86rem;
  color: var(--ink-soft);
}

.mechcad-brand {
  color: var(--ink);
  font-weight: 700;
}

.mechcad-version {
  position: relative;
  padding-left: 14px;
}

.mechcad-version::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  width: 8px;
  height: 1px;
  background: var(--ink-line-strong);
}

.mechcad-eyebrow {
  margin: 0 0 10px;
  color: var(--ink-soft);
  font-size: 0.98rem;
}

.mechcad-hero h1 {
  margin: 0;
  color: var(--ink);
  font-size: clamp(3.2rem, 6vw, 5.8rem);
  line-height: 0.95;
  font-weight: 700;
  text-shadow: 0 0 16px rgba(255,255,255,0.66);
}

.mechcad-deck {
  margin: 16px 0 0;
  max-width: 760px;
  color: var(--ink-soft);
  line-height: 1.75;
  font-size: 1.03rem;
}

.mechcad-hero-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 24px;
  max-width: 900px;
}

.mechcad-hero-meta div {
  padding: 12px 14px;
  border-top: 1px solid var(--ink-line);
  background: linear-gradient(180deg, rgba(255,255,255,0.34), rgba(255,255,255,0.12));
}

.mechcad-hero-meta span {
  display: block;
  margin-bottom: 4px;
  color: var(--ink-soft);
  font-size: 0.82rem;
}

.mechcad-hero-meta strong {
  color: var(--ink);
  font-size: 0.95rem;
  font-weight: 600;
}

.mechcad-progress {
  position: relative;
  z-index: 2;
  margin-top: 28px;
  max-width: 760px;
}

.mechcad-progress-line {
  height: 1px;
  background: rgba(47, 56, 60, 0.12);
  overflow: hidden;
}

.mechcad-progress-line span {
  display: block;
  width: 42%;
  height: 100%;
  background: linear-gradient(90deg, rgba(87,122,114,0.05), rgba(87,122,114,0.6), rgba(164,46,35,0.22));
  box-shadow: 0 0 16px rgba(87,122,114,0.26);
  animation: progress-glow 9s ease-in-out infinite;
}

.mechcad-progress-copy {
  margin-top: 10px;
  color: var(--ink-soft);
  font-size: 0.92rem;
}

.mechcad-seal {
  position: absolute;
  right: 30px;
  bottom: 28px;
  padding: 8px 10px 7px;
  color: var(--cinnabar);
  border: 1px solid var(--cinnabar);
  border-radius: 4px;
  background: rgba(255, 244, 240, 0.54);
  font-size: 0.95rem;
  font-weight: 700;
  transform: rotate(-3deg);
  letter-spacing: 0;
}

.block,
.form,
.panel,
.tabs,
.accordion {
  border-color: var(--ink-line) !important;
}

.block {
  border-radius: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  backdrop-filter: none;
}

.gradio-container .block {
  overflow: visible !important;
}

/* Gradio renders dropdown options outside the input; do not clip them in cards. */
.block:has(input[role="listbox"]) {
  overflow: visible !important;
  position: relative;
  z-index: 20;
}

[role="listbox"] + .options,
.options {
  z-index: 1000 !important;
}

.mechcad-workbench {
  gap: 20px;
}

.mechcad-panel {
  position: relative;
}

label,
.label,
.prose,
.markdown,
textarea,
input,
select {
  color: var(--ink) !important;
}

textarea,
input,
.wrap,
.input-container,
.token,
.dropdown,
.gr-box {
  border-radius: 6px !important;
  border-color: rgba(47, 56, 60, 0.2) !important;
  background: rgba(255, 255, 252, 0.84) !important;
  box-shadow: none !important;
}

textarea,
input {
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.55) !important;
}

button.primary,
.primary {
  background: linear-gradient(180deg, #b13a2f, #94261b) !important;
  border-color: #7f1f16 !important;
  color: #fff9f5 !important;
  border-radius: 999px !important;
  box-shadow: 0 10px 24px rgba(127, 31, 22, 0.16) !important;
}

button.secondary,
.secondary {
  border-radius: 999px !important;
  border-color: rgba(87, 122, 114, 0.38) !important;
  color: var(--jade) !important;
  background: rgba(250, 252, 250, 0.76) !important;
}

.accordion > .label-wrap {
  background: rgba(255, 255, 255, 0.38) !important;
  border-radius: 6px 6px 0 0 !important;
  border-bottom: 1px solid var(--ink-line) !important;
}

.accordion {
  background: rgba(255,255,255,0.12) !important;
}

.file-preview,
.model3d-container,
model-viewer,
.empty {
  border-radius: 6px !important;
  background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(237,243,240,0.72)) !important;
}

pre,
code,
.cm-editor {
  font-family: "Cascadia Mono", "Consolas", monospace !important;
  border-radius: 6px !important;
}

@keyframes cloud-drift-far {
  from { transform: translate3d(-3%, 0, 0); }
  to { transform: translate3d(3%, 0, 0); }
}

@keyframes cloud-drift-mid {
  from { transform: translate3d(2%, 0, 0); }
  to { transform: translate3d(-2%, 0, 0); }
}

@keyframes cloud-drift-near {
  from { transform: translate3d(-1.4%, 0, 0); }
  to { transform: translate3d(1.4%, 0, 0); }
}

@keyframes progress-glow {
  0%, 100% { transform: translateX(-12%); opacity: 0.6; }
  50% { transform: translateX(124%); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .mechcad-hero-cloud,
  .mechcad-progress-line span {
    animation: none !important;
  }
}

@media (max-width: 900px) {
  .mechcad-hero {
    min-height: 360px;
    padding: 24px 18px 22px;
  }

  .mechcad-hero-copy {
    padding-top: 12px;
  }

  .mechcad-hero-meta {
    grid-template-columns: 1fr;
  }

  .mechcad-seal {
    right: 18px;
    bottom: 18px;
  }
}

@media (max-width: 760px) {
  .gradio-container {
    padding: 12px 10px 28px !important;
  }

  .mechcad-hero {
    min-height: 320px;
    padding: 18px 14px 18px;
  }

  .mechcad-hero h1 {
    font-size: 2.7rem;
  }

  .mechcad-hero-cloud {
    left: -18%;
    right: -18%;
  }

  .mechcad-progress {
    margin-top: 20px;
  }
}
"""

HERO_HTML = """
<div class="mechcad-hero">
  <div class="mechcad-hero-cloud far"></div>
  <div class="mechcad-hero-cloud mid"></div>
  <div class="mechcad-hero-cloud near"></div>
  <div class="mechcad-hero-copy">
    <div class="mechcad-hero-topline">
      <span class="mechcad-brand">MechCAD · 栖云</span>
      <span class="mechcad-version">paper / ink / roman</span>
    </div>
    <p class="mechcad-eyebrow">Sketch to CAD, with the calm of paper and cloud.</p>
    <h1>栖云</h1>
    <p class="mechcad-deck">
      宣纸上的云缓缓铺开。AI 先读图、提问、规划，再由受控执行器落成特征树，输出可预览模型与 STEP 文件。
    </p>
    <div class="mechcad-hero-meta">
      <div><span>模式</span><strong>严格 / 智能</strong></div>
      <div><span>链路</span><strong>视觉 → FeaturePlan → Executor</strong></div>
      <div><span>输出</span><strong>STEP / STL / OBJ</strong></div>
    </div>
    <div class="mechcad-progress">
      <div class="mechcad-progress-line"><span></span></div>
      <div class="mechcad-progress-copy">研磨代码之墨，先看整体，再落特征。</div>
    </div>
  </div>
  <div class="mechcad-seal">栖云</div>
</div>
"""


with gr.Blocks(
    title="MechCAD MVP",
    css=CSS,
    theme=gr.themes.Soft(primary_hue="red", neutral_hue="stone"),
) as demo:
    run_id_state = gr.State("")

    with gr.Column(elem_classes=["mechcad-shell"]):
        gr.HTML(HERO_HTML)

        with gr.Row(equal_height=False, elem_classes=["mechcad-workbench"]):
            with gr.Column(scale=1, min_width=360, elem_classes=["mechcad-panel"]):
                image = gr.Image(type="pil", label="手绘草图", sources=["upload", "clipboard"], height=260)
                description = gr.Textbox(
                    label="零件功能描述",
                    placeholder="例如：一段带端部环槽的薄壁管，用于定位和装配。",
                    lines=4,
                )
                part_family = gr.Dropdown(
                    ["自动判断", "连接板", "支架", "带孔板", "法兰", "夹具", "轴套"],
                    value="自动判断",
                    label="零件类型",
                )
                quality_mode = gr.Radio(
                    ["快速草模", "更稳妥", "更细致"],
                    value="更稳妥",
                    label="生成策略",
                )
                operation_mode = gr.Radio(
                    ["智能模式", "严格模式"],
                    value="智能模式",
                    label="建模模式",
                    info="smart：主动分析、提问和提出建议；strict：不猜尺寸，不做几何兜底。",
                )

                with gr.Accordion("专业调节", open=False):
                    clarification_mode = gr.Radio(
                        ["interactive", "conservative"],
                        value="interactive",
                        label="不确定项处理",
                        info="interactive 会先提问再继续；conservative 直接跳过无法确认的特征。",
                    )
                    force_real_api = gr.Checkbox(
                        value=False,
                        label="强制使用真实 API",
                        info="未勾选时，如果缺少 API key 或调用失败，会自动使用本地降级链路。",
                    )
                    temperature = gr.Slider(0.0, 1.0, value=0.25, step=0.05, label="MiniMax temperature")
                    sandbox_timeout = gr.Slider(5, 60, value=30, step=1, label="Build123d executor 超时秒数")

                with gr.Accordion("真实 API 配置", open=True):
                    model_profile = gr.Dropdown(
                        [
                            "Qwen VL + MiniMax M3（推荐）",
                            "GPT-4o Vision + Claude",
                            "Gemini Vision + MiniMax",
                            "自定义组合",
                        ],
                        value="自定义组合",
                        label="模型组合预设",
                        info="先选择组合，再点击“应用组合”。选择“自定义组合”不会覆盖当前编辑内容。",
                    )
                    apply_profile_btn = gr.Button("应用模型组合", variant="secondary")
                    gr.Markdown("页面填写会立即用于本次生成；点击保存后会写入 `.env`。API Key 只保存在本机项目目录。")
                    dashscope_api_key = gr.Textbox(
                        value=env_value("MECHCAD_VISION_API_KEY", env_value("DASHSCOPE_API_KEY")),
                        label="视觉模型 API Key",
                        type="password",
                    )
                    vision_provider = gr.Dropdown(
                        [
                            "自定义视觉模型",
                            "DashScope / Qwen",
                            "OpenAI / GPT-4o",
                            "Anthropic / Claude",
                            "Gemini 兼容端点",
                            "Ollama 本地模型",
                        ],
                        value="自定义视觉模型",
                        label="视觉模型供应商预设",
                        info="仅用于填写默认端点和模型名；也可以直接编辑下面三项。",
                    )
                    dashscope_base_url = gr.Textbox(
                        value=normalize_role_base_url(
                            env_value("MECHCAD_VISION_BASE_URL", env_value("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
                        ),
                        label="视觉模型 Base URL",
                    )
                    qwen_model = gr.Textbox(
                        value=env_value("MECHCAD_VISION_MODEL", env_value("QWEN_MODEL", "qwen2.5-vl-32b-instruct")),
                        label="视觉模型名（任意厂商）",
                    )
                    vision_protocol = gr.Dropdown(
                        ["openai", "anthropic"],
                        value=env_value("MECHCAD_VISION_PROTOCOL", "openai"),
                        label="视觉模型协议",
                        info="视觉模型只需提供对应协议的图片输入接口。",
                    )
                    test_qwen_btn = gr.Button("测试视觉模型连接")

                    minimax_api_key = gr.Textbox(
                        value=env_value("MECHCAD_PLANNER_API_KEY", env_value("MINIMAX_API_KEY")),
                        label="后端建模模型 API Key",
                        type="password",
                    )
                    minimax_protocol = gr.Dropdown(
                        ["anthropic", "openai"],
                        value=env_value(
                            "MECHCAD_PLANNER_PROTOCOL",
                            env_value(
                                "MINIMAX_API_PROTOCOL",
                                "anthropic" if "/anthropic" in env_value("MINIMAX_BASE_URL", "") else "openai",
                            ),
                        ),
                        label="后端建模模型协议",
                    )
                    minimax_base_url = gr.Textbox(
                        value=normalize_minimax_base_url(
                            env_value("MECHCAD_PLANNER_BASE_URL", env_value("MINIMAX_BASE_URL", "https://api.minimax.io/v1")),
                            env_value("MECHCAD_PLANNER_PROTOCOL", env_value("MINIMAX_API_PROTOCOL", None)),
                        ),
                        label="后端建模模型 Base URL",
                    )
                    minimax_model = gr.Textbox(
                        value=env_value("MECHCAD_PLANNER_MODEL", env_value("MINIMAX_MODEL", "MiniMax-M3")),
                        label="后端建模模型名（任意厂商）",
                    )
                    test_minimax_btn = gr.Button("测试后端建模模型连接")
                    save_config_btn = gr.Button("保存 API 配置到 .env", variant="secondary")
                    api_status = gr.Markdown()

                generate_btn = gr.Button("生成 3D 模型", variant="primary")
                clarification_questions = gr.Markdown(label="待确认问题")
                clarification_answers = gr.Textbox(
                    label="回答建模问题",
                    placeholder="例如：槽宽 10 mm，距顶部端面 5 mm，沿 Z 轴环形切槽。",
                    lines=3,
                )
                continue_btn = gr.Button("根据回答继续建模", variant="secondary")

            with gr.Column(scale=1, min_width=420, elem_classes=["mechcad-panel"]):
                report = gr.Markdown(label="生成报告")
                preview = gr.Model3D(label="3D 预览", display_mode="solid", camera_position=(35, 35, 2.5))
                step_file = gr.File(label="STEP 下载")
                stl_file = gr.File(label="STL 下载/预览源")

    with gr.Accordion("阶段输出", open=False):
        qwen_json = gr.Code(label="阶段 1: Qwen JSON", language="json")
        model_plan = gr.Code(label="阶段 2: FeaturePlan 特征树", language="json")
        code = gr.Code(label="阶段 3: 受控 Build123d executor 代码", language="python")

    with gr.Accordion("试用反馈", open=False):
        rating = gr.Slider(1, 5, value=3, step=1, label="结果接近期望程度")
        issue = gr.Textbox(label="最大问题或修改建议", lines=3)
        would_continue = gr.Checkbox(label="我愿意继续试用/迭代这个工具", value=True)
        contact = gr.Textbox(label="联系方式，可选")
        feedback_btn = gr.Button("提交反馈")
        feedback_status = gr.Markdown()

    generate_btn.click(
        generate,
        inputs=[
            image,
            description,
            part_family,
            quality_mode,
            operation_mode,
            force_real_api,
            dashscope_api_key,
            dashscope_base_url,
            qwen_model,
            vision_protocol,
            minimax_api_key,
            minimax_protocol,
            minimax_base_url,
            minimax_model,
            temperature,
            sandbox_timeout,
            clarification_mode,
            clarification_answers,
        ],
        outputs=[report, preview, step_file, stl_file, qwen_json, model_plan, code, run_id_state, clarification_questions],
        api_name=False,
        show_api=False,
    )

    continue_btn.click(
        generate,
        inputs=[
            image,
            description,
            part_family,
            quality_mode,
            operation_mode,
            force_real_api,
            dashscope_api_key,
            dashscope_base_url,
            qwen_model,
            vision_protocol,
            minimax_api_key,
            minimax_protocol,
            minimax_base_url,
            minimax_model,
            temperature,
            sandbox_timeout,
            clarification_mode,
            clarification_answers,
        ],
        outputs=[report, preview, step_file, stl_file, qwen_json, model_plan, code, run_id_state, clarification_questions],
        api_name=False,
        show_api=False,
    )

    apply_profile_btn.click(
        apply_model_profile,
        inputs=[
            model_profile,
            dashscope_base_url,
            vision_protocol,
            qwen_model,
            minimax_protocol,
            minimax_base_url,
            minimax_model,
            temperature,
        ],
        outputs=[dashscope_base_url, vision_protocol, qwen_model, minimax_protocol, minimax_base_url, minimax_model, temperature],
        api_name=False,
        show_api=False,
    )

    save_config_btn.click(
        save_config,
        inputs=[
            dashscope_api_key,
            dashscope_base_url,
            qwen_model,
            vision_protocol,
            minimax_api_key,
            minimax_protocol,
            minimax_base_url,
            minimax_model,
        ],
        outputs=[api_status],
        api_name=False,
        show_api=False,
    )

    test_qwen_btn.click(
        test_qwen,
        inputs=[dashscope_api_key, dashscope_base_url, qwen_model, vision_protocol],
        outputs=[api_status],
        api_name=False,
        show_api=False,
    )

    test_minimax_btn.click(
        test_minimax,
        inputs=[minimax_api_key, minimax_protocol, minimax_base_url, minimax_model],
        outputs=[api_status],
        api_name=False,
        show_api=False,
    )

    vision_provider.change(
        apply_vision_provider,
        inputs=[vision_provider],
        outputs=[dashscope_base_url, vision_protocol, qwen_model],
        api_name=False,
        show_api=False,
    )

    minimax_protocol.change(
        apply_minimax_protocol,
        inputs=[minimax_protocol],
        outputs=[minimax_base_url],
        api_name=False,
        show_api=False,
    )

    feedback_btn.click(
        feedback,
        inputs=[run_id_state, rating, issue, would_continue, contact],
        outputs=[feedback_status],
        api_name=False,
        show_api=False,
    )


if __name__ == "__main__":
    Path("outputs").mkdir(exist_ok=True)
    demo.launch(server_name="127.0.0.1", server_port=7860, show_api=False)
