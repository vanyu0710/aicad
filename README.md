# MechCAD MVP

手绘草图转 3D 机械模型的本地 MVP。默认流程兼顾“傻瓜式试用”和“专业调节”：用户上传 PNG/JPG 草图并描述零件功能，系统输出 3D 预览、STEP/STL 文件和可审计的中间结果。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

打开终端显示的本地地址，通常是 `http://127.0.0.1:7860`。

如果在 Codex 沙箱里启动服务，页面里的“测试 Qwen/DashScope”“测试 MiniMax”和真实生成需要联网权限；否则后端请求外部 API 时可能出现 `[WinError 10013]`。在你自己的 PowerShell 里用上面的命令启动，则不会经过 Codex 的网络沙箱。

## 当前建模架构

默认路径已经不是“AI 直接写 Build123d Python 并执行”，而是 SW 风格的受控特征树：

1. OpenCV 本地图像预处理。
2. Qwen/DashScope 识别图纸视图、尺寸、基准、特征和不确定项。
3. MiniMax 输出 `FeaturePlan JSON`，类似 SolidWorks 的“基准 -> 基体 -> 加料/减料特征 -> 阵列 -> 圆角倒角”特征树。
4. MechCAD 用 Pydantic 校验 FeaturePlan。
5. 受控 Build123d executor 固定调用安全 API，生成 STEP/STL。
6. 如果 FeaturePlan/Build123d 失败，OCP fallback 会兜底导出简化模型，并在报告中明确标注。

这样做的目标是减少模型幻觉：AI 负责读图和规划，程序负责真正建模；缺尺寸不猜，缺定位不建。

## API Key

没有 API key 时，应用会自动使用本地演示分析和降级建模，方便先跑通产品链路。

要启用真实两阶段模型调用：

1. 在页面展开“真实 API 配置 / Real API”。
2. 填入 `DASHSCOPE_API_KEY`、`MINIMAX_API_KEY`、base URL 和模型名。
3. 分别点击“测试 Qwen/DashScope”和“测试 MiniMax”。
4. 点击“保存 API 配置到 .env”。
5. 勾选“强制使用真实 API”后生成；任一 API 失败都会直接报错，方便排查。

也可以手动复制 `.env.example` 为 `.env` 后填写：

```text
DASHSCOPE_API_KEY=...
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen2.5-vl-32b-instruct

MINIMAX_API_KEY=...
MINIMAX_API_PROTOCOL=anthropic
MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic
MINIMAX_MODEL=MiniMax-M3
```

MiniMax 支持两种协议：

- `openai`: base URL 形如 `https://api.minimax.io/v1`，调用 `/chat/completions`
- `anthropic`: base URL 形如 `https://api.minimaxi.com/anthropic`，调用 `/v1/messages`

## 当前支持范围

第一版优先覆盖 MVP 高频机械件：

- 管、轴、套筒：外径、内径、长度、端部台阶、环槽
- 板件：长宽厚、通孔、矩形槽/口袋
- 法兰：中心孔、螺栓孔圈
- 简单支架：由相交盒体、孔、槽、凸台组成的零件

复杂螺纹、齿轮、自由曲面、钣金展开等暂不假装建模，会进入未解决项。

## 文件说明

- `app.py`: Gradio 界面入口
- `mechcad/pipeline.py`: 主流程编排
- `mechcad/preprocess.py`: OpenCV 图像预处理
- `mechcad/clients/qwen.py`: Qwen 视觉分析客户端和本地降级
- `mechcad/clients/minimax.py`: MiniMax FeaturePlan 客户端和本地降级
- `mechcad/feature_plan.py`: FeaturePlan 数据模型与旧计划转换
- `mechcad/feature_executor.py`: 受控 Build123d executor
- `mechcad/sandbox/build123d_runner.py`: AST 检查、子进程执行、STEP/STL 产物管理
- `mechcad/sandbox/ocp_fallback.py`: OCP 简化模型兜底
- `mechcad/feedback.py`: 本地反馈保存

## 验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q app.py mechcad tests
.\.venv\Scripts\python.exe -m unittest tests.test_feature_executor
```
