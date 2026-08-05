# MechCAD IDE — 交接文档（2026-08-05）

> 本文件供接手 AI / 开发者阅读。项目处于「新架构主线」阶段，旧 Gradio MVP 已迁入 `legacy/gradio/` 只作参考，不再演进。

## 1. 项目定位

**目标**：把原 Gradio MVP 升级为专业 AI CAD IDE。

**核心链路**：

```text
用户上传手绘草图 + 文字描述
→ React 前端 IDE (Vite + Three.js)
→ FastAPI /generate
→ AI 视觉读图 (vision) → AI 特征规划 (planner) → Pydantic 校验 FeaturePlanV3
→ CAD Worker 子进程 (受控 build123d，边界保留给 FreeCAD)
→ STEP / STL / OBJ / execution_report
→ 前端 3D 预览 + 特征树 + 表单化属性面板 + 聊天修改 + 撤销/重做
```

**核心设计原则（建模契约）**：AI 不写任意 Python。AI 只输出 `FeaturePlanV3` 结构化特征树，Pydantic 校验后由受控 CAD 执行器映射为安全建模操作。

- **严格模式 (strict)**：尺寸只来自图纸标注或用户确认；未确认的推断只能进 `assumptions` / `design_review` / `unresolved`，不能成为可执行尺寸。
- **智能模式 (smart)**：可生成建议，但未确认推断同样不直接写入可执行尺寸。

## 2. 项目路径与 Git 状态

- 路径：`C:\Users\fanlu\Documents\Codex\2026-07-30\devops-mechcad-mvp-hugging-face-space`
- 分支：`main`，工作区干净

```text
adfd6ad Initial commit
317b80b Initial MechCAD MVP          （旧 Gradio 时代）
ed5c71b feat: MechCAD IDE 新架构骨架 + 真实 AI 接入     （①提交 + ②真实 AI）
70f3c9b feat(frontend): 表单化属性面板替代 JSON 编辑     （③属性面板）
e580065 test: 新架构测试体系 + 修复三个真实缺陷          （⑤测试体系）
```

原交接报告的「建议下一步」5 项中，**①②③⑤ 已完成，④（真正接 FreeCAD）未做**——本机未安装 FreeCAD（无 `FreeCADCmd`、无安装目录），无法本地验证。

## 3. 目录结构

```text
backend/                  FastAPI 新主线后端
  main.py                 全部 REST 路由 + WebSocket + 事件流
  schemas.py              核心数据模型 (Pydantic v2)
  session.py              SessionStore：快照历史 / undo / redo
  events.py               EventBus：asyncio.Queue 按 project_id 分发
  storage.py              工件目录 work/new_arch_runs/{run_id}/
  cad.py                  调用 CAD Worker 子进程（超时/失败报告）
  ai.py                   AI 编排：真实模型链 + 本地确定性 stub 回退
  mechcad_ai/             真实模型层（替代 legacy clients）
    client.py             OpenAI/Anthropic 兼容 HTTP 客户端
    prompts.py            从 prompts/prompts.yaml 加载提示词
    vision.py             视觉读图
    planner.py            特征规划 + 聊天修改
    normalize.py          自由格式模型输出 → 严格 FeaturePlanV3
cad_worker/               CAD 子进程（当前用 build123d，文件名保留 freecad_executor.py）
  freecad_executor.py     --plan JSON --out DIR，写 execution_report.json
frontend/                 React 18 + TS + Vite 5 + Three.js
  src/App.tsx             IDE 壳：上传/聊天/特征树/3D/属性面板
  src/FeatureForm.tsx     表单化属性面板（尺寸/位置/确认）
  src/Viewport.tsx        Three.js 视口（OBJ/STL）
  src/api.ts              REST + WS 客户端
prompts/prompts.yaml      集中式提示词（vision/plan/chat/review）
mechcad/                  Pydantic 核心 + 旧兼容层（让旧测试继续通过，非新主线）
legacy/gradio/            旧 Gradio MVP（参考用）
tests/                    49 个后端测试（unittest）
frontend/src/*.test.tsx   7 个前端测试（Vitest）
```

## 4. 核心数据模型（backend/schemas.py）

| 模型 | 说明 |
|---|---|
| `FeaturePlanV3` | schema_version 3.0；`base_feature` + `features[]` + `unresolved` + `design_review` |
| `FeatureV3` | id / type / operation(base\|add\|remove\|modify\|pattern) / `dimensions{}` / placement / pattern / depends_on / unresolved / assumptions |
| `DimensionV3` | **value \| None**、unit、evidence、source(drawing\|user\|assumption\|derived\|unknown)、confidence、confirmed_by_user |
| `PlacementV3` | reference / x / y / z / axis(X\|Y\|Z) / angle_deg |
| `DesignSnapshot` | 一次设计状态的不可变快照（含 artifacts/questions/report/logs） |
| `ProjectState` | current + history[] + redo_stack[] + settings |
| `ModelConfig` | vision/planner 双角色配置 + operation_mode + smart_fill_policy + force_real_api |

⚠️ 命名坑：Pydantic v2 保留字段 `model_config`，项目配置字段必须叫 `settings`。`GenerateRequest` 通过 alias 接收前端传来的 `model_config`，内部一律是 `settings`。

## 5. AI 接入层（backend/mechcad_ai/）

- **配置解析**：`resolve_role_config(settings, role)` 先读 `ModelConfig`，缺什么回退读环境变量 `MECHCAD_VISION_*` / `MECHCAD_PLANNER_*`（`.env` 已配好，含 API key）。
- **协议**：OpenAI 兼容 或 Anthropic 兼容（`/anthropic` 出现在 base_url 时自动识别）；`/v1` 路径自动重试。
- **回退链**：vision/planner 未配置或调用失败 → `backend/ai.py` 的确定性本地 stub 接管，保证 IDE 与 CAD 链路永远可用。stub 支持 plate/flange/tube/unknown 四类基体 + 尺寸正则提取。
- 提示词全部集中在 `prompts/prompts.yaml`，代码不散落提示词。

## 6. API 清单（backend/main.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| POST | `/api/projects` | 建项目 |
| GET | `/api/projects/{id}` | 读当前状态 |
| POST | `/api/projects/{id}/generate` | 生成 FeaturePlan + 跑 CAD Worker |
| POST | `/api/projects/{id}/chat` | 自然语言修改 |
| PATCH | `/api/projects/{id}/features/{fid}` | 改单个特征（属性面板保存走这里） |
| POST | `/api/projects/{id}/undo` / `redo` | 快照撤销/重做 |
| GET | `/api/artifacts/{run_id}/{kind}` | step/stl/obj/report/execution_report |
| WS | `/ws/projects/{id}` | stage_started/progress/done、question_required、artifact_ready、error |

每次 generate/chat/patch 都会提交一个新 `DesignSnapshot`（旧快照进 history，redo 栈清空）。

## 7. CAD Worker 现状（第④步缺口）

`cad_worker/freecad_executor.py` **当前实际用 build123d**，但进程边界已按 FreeCAD 子进程设计（`--plan` 传 JSON、`--out` 收产物、写 `execution_report.json`），FastAPI 永不 import CAD 库。

支持特征：box_base、cylinder_base、hollow_cylinder、through_hole、blind_hole、counterbore_hole、rectangular_slot、rectangular_pocket、annular_groove、boss_cylinder、rectangular_pad、rib_box、linear_pattern、circular_pattern。

**第④步要做**：把 worker 内部 build123d 操作替换为真 FreeCAD（Part WB 或 PartDesign），接口不变。前置条件：本机安装 FreeCAD，或部署到 Hugging Face Space（可装 FreeCAD）验证。受控执行器（`mechcad/feature_executor.py`）目前也基于 build123d，同样需要同步。

## 8. 测试体系

后端（unittest，49 个，~55s，其中真实 CAD 建模约占 25s）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

| 文件 | 覆盖 |
|---|---|
| test_session.py | 快照历史 / undo/redo / 深拷贝 |
| test_api.py | 全 API + 真实 worker 端到端 + WS 连接 |
| test_cad_worker.py | worker 成功 / 超时 / 失败报告 |
| test_events.py | EventBus 隔离 |
| test_ai_integration.py | 客户端协议 / 提示词 / 归一化 |
| test_feature_executor.py / test_clarification.py | 旧兼容层（保护旧行为） |

前端（Vitest + Testing Library，7 个）：

```powershell
cd frontend && npm test
```

**测试经验 / 坑**：
1. `test_api.py` 必须 patch `ai_module.ai_vision.analyze_sketch` / `ai_planner.generate_feature_plan` / `chat_edit_feature_plan` 返回 None，否则会真调外部模型（慢 + 不确定）。
2. TestClient 的 WebSocket 与 HTTP 跑在**不同事件循环**，WS 里发 HTTP 会死锁；事件流用 `test_events.py` 直接测 EventBus，WS 只测连接接受。
3. `unittest discover` 会先 import 全部测试模块——`test_api.py` import `backend.main` 触发 `load_dotenv()`，导致后来的 `has_configured_model` 测试读到 .env；该测试已用 `patch.dict(os.environ)` 隔离，新增测试注意同样问题。

## 9. 运行方式

```powershell
# 后端（Python 3.12 venv 已就绪）
.\.venv\Scripts\python.exe -m backend.main        # http://127.0.0.1:8000
# 前端
cd frontend && npm run dev                        # http://127.0.0.1:5173
```

- Vite 已配代理：`/api` → 8000，`/ws` → 8000（ws）。
- Windows 注意：PowerShell 可能拦截 `npm.ps1`，用 `npm.cmd`。
- 工件输出在 `work/new_arch_runs/`（gitignored）。
- `requirements.txt` 含 gradio（legacy 用），新主线实际只需要 fastapi/uvicorn/pydantic/pillow/opencv/numpy/build123d/pyyaml/python-dotenv。

## 10. 已知问题 / 待办线索（给下一步规划）

1. **第④步 FreeCAD 替换**（主线缺口）：worker 内 build123d → FreeCAD；本机未装 FreeCAD，需先装或上 Space 验证。
2. **Monaco 编辑器**：交接报告提到「还没有真正 Monaco 编辑器，只是 textarea」——聊天/描述输入目前仍是 textarea。
3. **前端 chunk > 500KB 警告**：Three.js 全量打包，可考虑动态 import / manualChunks。
4. **模型配置 UI 较基础**：settings 面板可增强（协议选择、测试连接、多 provider 切换）。
5. **`.env.example` 与代码不一致**：example 里是 DASHSCOPE_*/MINIMAX_*（legacy 时代），新代码读 MECHCAD_VISION_*/MECHCAD_PLANNER_*，example 需更新或删除旧键。
6. **force_real_api 字段**存在但用途未完全展开；`clarification_answers` 参数在 generate 里暂未消费。
7. **前端无 e2e**（Playwright/Cypress），目前只有组件测试。
8. **部署**：项目名含 hugging-face-space，但当前无 Dockerfile / Space 配置，未部署过新架构。

## 11. 建议下一步（给 GPT 规划时参考）

优先级建议：

1. **第④步 FreeCAD**（补全架构承诺）：先在本机装 FreeCAD 或准备 Space 环境；worker 接口已就绪，替换面集中在 `cad_worker/freecad_executor.py` 的 `_build_part`。
2. **清理与一致性**：#5 的 .env.example 对齐、#2/#4 前端体验增强、#6 参数落地。
3. **部署上线**：Dockerfile + Space 配置，让新架构真正可访问。
4. **e2e**（#7）：等 UI 稳定后补 Playwright。
