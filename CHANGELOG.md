## v0.14.0-alpha - F2a 装配视图：项目零件库 + 位姿 manifest + 装配 STEP/干涉/预览（对标设计文档 ASSEMBLY_F2_DESIGN）

装配 = 已归档零件 + 位姿 manifest 之上的**视图**（设计 D1）：内核单几何契约零改动，零件改参走"单会话重做 + 重新归档"，manifest 更新即装配更新。

- **项目零件库**（`backend/storage.py`）：`work/project_parts/{project}/`——`vNNN_名.step/.stl` 版本化归档 + `parts_manifest.json` 原子写（tmp+replace）+ 文件名白名单防穿越；`finish_part` 在 run 归档之外同步写库（版本递增、upsert 不重复），`part_rec` 加 `library_*`/`pose` 字段。
- **BOM 位姿**：`propose_plan.bom[].pose`（position/rotation_deg，数值来自 design_calculate 调研：中心距/轴长/凸台位）；`_normalize_pose` 严格校验（非法丢字段不整体拒）。`depends_on` 从死数据转为装配顺序/报告分组。
- **内核 v2.14 三命令**（`mech_kernel/assembly_scene.py` + server dispatch，**无状态**：不读写 kernel 实例、不进事务）：`export_assembly`（逐件 import+位姿 → 带 label 的 Compound 树 → build123d XCAF 装配 STEP；中文产品名经 reader→TDataStd_Name→writer 回写修正 pyOCP UTF-8 逐字节 mojibake）、`assembly_interference`（bbox 预过滤 + collision 全对求交 + expected_overlaps 豁免，重合体 boolean 已知坑透传）、`render_assembly`（分件着色四视角证据网格）。
- **agent 收尾工具 `export_assembly`**：全部零件归档后一键交付（装配 STEP + 干涉 + 预览图 + `assembly_NNN_report.json`），结果写 manifest + `ArtifactSet.assembly` 投影 + `artifact_ready{kind:"assembly"}`；门控：计划批准 + 无 pending 零件 + 库非空。**发现并修复两个集成 bug**：server 脚本模式下相对导入 ImportError（测试包导入发现不了）；库路径未 resolve 导致 worker 在内核仓解析失败。
- **前端**：Viewport `models[]` 多件按位姿叠加（**不 center()**）+ 显隐/点选高亮；App 装配模式自动切换 + 装配面板（STEP/报告链接、干涉计数、零件位姿列表）；`assemblyArtifactUrl`；零件库 REST（manifest + 库文件下载）。
- **修复**：`_commit_kernel_state_snapshot` 手动改参/undo 后丢 parts 清单（现存 bug）→ 现 carry-over parts+assembly。
- 测试：aicad 392+（pose 规范化、库写/版本 upsert、export 门控与成功、RPC echo、URL、carry-over 回归）、内核 391（assembly_scene 5 + 既有）、前端 61；E2E `scripts/e2e_assembly_flow.py` 13/13（真实 worker：3 件带位姿入库 → 装配 STEP 回读 3 具名产品 → 故意插入的凸台×底板干涉命中 → 预览/报告落盘）。

## v0.13.2-alpha - 标准平面轴系契约修复 + 计划收尾门控（真实 LLM 全流程二次验收驱动）

- **内核 v2.13.2 平面契约修复（mechcad-kernel）**：真实 LLM 建箱体时**反复试探平面映射、撤销重来约 30 步**，定位到两处根因：
  - `XZ` 声明为 `(x_dir=+x, y_dir=+z, normal=+y)` 是**左手系**（x×y=−y≠normal），build123d 按右手系重算 → 草图 v 轴变 −z、拉伸方向翻转。现改为 `normal=−y`（保留"横 x 纵 z"直觉且右手系）。
  - `_sketch_plane` 把**所有过原点标准平面**一律短路成 `None`，于是回退到 `direction="Z"` → Plane.XY，XZ/YZ 的声明轴从未生效。现仅当声明轴与 direction 等价时才走快路径；否则按 `x_dir+y_dir` 构造真实平面（v 严格等于声明的 y_dir，左手系声明自动翻转 y_dir）。
  - 顺带修复 custom/face 平面 `y_dir` 未推导（默认 (0,1,0)，法向为 Y 时与 x_dir 平行 → `x_dir and y_dir must not be parallel` 崩溃）。
  - 回归测试：`test_standard_plane_axes_match_declaration` + `test_standard_planes_are_right_handed`（内核 386/386）。
  - **实测效果**：同一任务平面试探从 ~30 次降到 **0 次**。
- **计划未完成不得收工**：模型归档第一件就写总结（真实 LLM 曾只建 1 件就 PASS）。提示词明确"finish_part 是中途动作"，harness 加门控——模型停止时若批准计划仍有 pending 步骤，注入提醒一次并继续（`_plan_has_pending`）。
- **防重复归档**：真实 LLM 把 11 件归档成 24 次（修改零件时旧版本也归档）。`finish_part` 对同名零件二次归档返回 `DUPLICATE_PART` 拒绝。
- **真实 LLM 全流程二次验收（deepseek-v4.1-flash）PASS**：**BOM 11 项全部建成**（6 齿轮 + 4 轴 + 1 箱体），箱体独立核验单实体 196×596×243mm、4 底脚安装孔 + 8 组轴承孔（r13.5~50）、带法兰与加强筋。测试：aicad 385、内核 386 全绿。

## v0.13.1-alpha - 重推理模型适配 + 调研防打转（真实 LLM 全流程验收驱动）

换用 `deepseek-v4.1-flash-expires-on-0910`（重推理模型）跑"1:100 变速箱"全流程时暴露两处适配缺口，均已修复：

- **输出预算不足**：重推理模型 reasoning token 挤占 `max_tokens`，原 8192 在"出 BOM 计划"这类长输出时被 reasoning 吃光（`finish_reason=length`、正文为空、agent 静默结束）。planner `max_tokens` 改为可配置（`MECHCAD_PLANNER_MAX_TOKENS`，默认 32768）；实测该模型支持至 65536。
- **调研打转硬门控**：模型可能反复调用 `design_calculate` 同类计算而不推进。提示词加"调研 ≤6 次、必须一轮内合并、凑齐即停"，harness 层加 `RESEARCH_BUDGET_EXCEEDED` 硬门（计划批准前累计超 8 次即拒绝并强制推进到提问/计划）——不只靠提示词。

**真实 LLM 全流程验收（deepseek-v4.1-flash，670s/85 步）PASS**：7 步调研 → `ask_user` → **BOM 计划（11 项零件 / 15 步）** → 批准 → 逐件建模归档 **11 件全部成功**（6 齿轮 via=ops 真渐开线 + 4 阶梯轴 via=script + 1 箱体 via=script）。独立核验箱体 STEP：单实体、473×230×162mm、8 种半径圆柱面各 2 个（4 轴两端轴承孔）。36 张四视角快照。脚本 `scripts/real_llm_gearbox.py` 可复跑。

## v0.13.0-alpha - 代码通道 run_build_script：模型写脚本、几何走内核（对标 DSH）

同一模型在真实 coding harness 里能建复杂壳体、在 varen 里却打转——根因是表达力：op 菜单逐调用、坐标全手算、无循环变量。本版给 agent 开**建模脚本通道**（借鉴 DeepSeek Harness：原始 traceback 反馈、跑前检查点/跑败回滚、提交后自动复检），同时守住"几何主权归内核"：**脚本命名空间不提供裸 build123d，只注入 `k`（kernel 公开 op 门面）+ `math`**，脚本里每个 op 照常进 `_op_history`/`feature_graph` → 代码件与 op 件一样可参数重放、可特征树编辑。

- **内核 v2.13（mechcad-kernel）**：`script_sandbox.py` AST 白名单（import 只许 math；禁危险模块/`eval` 等反射名/**一切 `_` 开头属性访问**；`__import__` 换守卫版双保险）+ `ScriptKernel` 门面（34 公开 op 同名直调 + 守卫 `execute()`，黑名单 `export/save_project/load_project/run_script`）+ `MechKernel.run_script`（`_snapshot` 检查点 → 受限 namespace exec + stdout/stderr 捕获 → 异常 `_restore` 整体回滚并回传原始 traceback；成功返回 solids/volume/bbox/ops_executed + iso 渲染）；`query` 新增 `what="solid_count"`。新测试 `test_v12_script_channel.py` 7 项（核心断言：**代码件 rebuild/update_feature 照常重放**）。
- **aicad 后端**：`kernel_worker.run_script()` RPC；loop 合成工具 `run_build_script`（计划门控期不可用，同 finish_part；成功后手动触发 STL 导出+快照；回喂 compact JSON：solids/volume/bbox/stdout(2000)/traceback(4000)）；**finish_part 设计复检门**（DSH L1 review 的 CAD 化）：导出前 `solid_count==1` 契约检查，多实体（悬浮特征）拒绝归档——v0.12 渲染复检抓出的"浮齿"类缺陷从此机器拦截；`part_rec.built_via: ops|script` 进 `PartArtifact`/execution_report。
- **特征契约校验（真实 LLM 验收发现的第二个缺口）**：Qwen 首轮验收 `undo` 回滚掉 4 个螺栓孔+1 个轴承孔后，最终总结仍声称全部存在（STEP 实测仅 3 个圆柱面）——单实体门拦得住"多体"，拦不住"少特征+谎报"。新增 `finish_part.feature_contract`（[{radius_mm, count}]）：归档前用 `select cylinder` 实测圆柱面半径计数，断言不符返回 `FEATURE_CONTRACT_MISMATCH` 拒绝归档；提示词补"undo 后必须 select/measure 重验、总结只写实测存在的特征"。E2E 增正/负例（错契约被真实内核拦截）。
- **四视角快照**：`render_snapshot` 默认 `views=[iso,front,top,side]`（内核自动 compose_grid 拼图），会话流与 GLM 截图同风格。
- **提示词 zh/en**：复杂零件（箱体/阶梯轴/筋/孔阵列）用 run_build_script 一次成型；脚本失败自动回滚按 traceback 改完重跑；复检门说明。
- 测试：aicad 后端 381+（run_build_script 成功/失败回传/门控/built_via 流转、复检门拒绝多实体、RPC echo）；内核 384+。E2E `scripts/e2e_housing_script.py`（真实 worker 零 token）12/12：脚本建"底板+4 螺栓孔+双轴承凸台+通孔+三角筋"壳体 7.3s、单实体、体积对账、rebuild 可重放、复检门正确识别双实体拒绝。

## v0.12.0-alpha - 变速箱级多零件流程：设计调研 + BOM 计划 + 逐件交付

落地 `docs/PRODUCT_FLOW_GEARBOX.md` 的 F1：用户输入"设计个 1:100 的变速箱"即可走完 **自动计划模式 → 调研计算 → 提问澄清 → BOM 计划批准 → 逐件建模归档 → 零件级交付**。

- **设计调研 `design_calculate` 合成工具**（`backend/agent/designcalc.py`，纯算术、无 CAD import，对齐 D2）：
  - 内置 kind：`gear_ratio_split`（总传动比多级拆分：1:100 给出精确的 85/17×85/17×68/17=100.0 等方案，含根切下限 z1≥17、单级 3~8、z2≤140 工程约束）、`gear_pair`（ISO 6336 齿轮副全几何 + 中心距 + 端面重合度 + 根切警告）、`nearest_standard_module`、`shaft_diameter`（扭转初估 + 键槽削弱 + 标准径圆整）、`housing_wall`（铸造箱体经验壁厚）；经验公式输出一律 `method="empirical"` 不冒充校核。
  - `kind="custom"` 沙箱（`backend/agent/calc_sandbox.py`）：模型可自行编写纯算术调研代码，`python -I` 子进程 + AST 白名单（禁 import/属性/下标/lambda/推导式）、`__builtins__` 清空、5s 超时、结果 JSON 限 4000 字符；`MECHCAD_AGENT_CALC_SANDBOX=off` 可关。内置计算与内核 `gear.py` 公式由对拍测试锁一致。
  - 计划门控期间可用（归入 `_SYNTHETIC_TOOLS`），每次调用出 `agent_step` 卡片、转录进 execution_report。
- **BOM 形态计划**：`propose_plan` 增加顶层 `bom`（part/role/quantity/key_params/depends_on，key_params 应来自调研数值），steps 支持 `part` 归属；session.plan / `plan_updated` / `plan_review` 审批卡全链路透传，旧计划无 bom 完全兼容。前端 `PlanReviewCard` 渲染零件清单表 + 按零件分组步骤；`ChatColumn` 进度清单升级为"零件（n/m）→ 步骤"两级打勾。
- **逐件交付 `finish_part` 合成工具**：一个零件建模完成 → 导出 `part_NN_名称.step/.stl` 归档 + `artifact_ready{kind:"part"}` + 计划该件步骤自动打勾 → 调 kernel `reset` 清空会话 → 下一件从空会话开始（禁止零件互相融合）。reset 失败时报 WORKER_ERROR 并命令模型停止建模（防融合）。execution_report 增 `parts` 表；`ArtifactSet.parts`（`PartArtifact`）+ `/api/artifacts/{run}/{part文件}` 下载（storage 正则防目录穿越）；前端产物区列零件级 STEP/STL 链接。
- **复杂任务自动进计划模式**：`/agent/message` 对命中多零件关键词（变速箱/减速器/装配/gearbox/…）的任务把 auto 提升为 plan（只升不降），响应带生效 `mode`；用户显式 plan 不受影响。默认 `max_steps` 30→60（调研/提问/归档同样计步）。
- **内核配套（mechcad-kernel v2.12，本仓依赖）**：`make_gear` 注册为公开 op（真渐开线齿轮坯，`involute_teeth_threshold` 可控齿形回退，new_body/add/cut 语义 + confirm_replace 守护，可 update_feature 参数化重放）；worker RPC 新增 `reset` 命令。公开 op 33→34。顺带修复工作区遗留的 revolve 半重构缺陷（弧/折线剖面 wire 成功时 `new_solid` 未绑定，4 个内核测试恢复通过）。
- 测试：后端 374（+37：designcalc 分级/几何/对拍、沙箱放行与逃逸、BOM 计划流、finish_part 成功与三类失败、自动 plan 启发式、parts 契约）；前端 58（+3：审批卡 BOM、分组清单进度、无 bom 回退）。内核仓 374（+14：make_gear op、reset RPC）。质量门 compileall + `git diff --check` 全绿。

## v0.11.0-alpha - Harness 能力升级：提问卡片 + 计划模式 + 进度清单

参考 deepagents / Claude Code plan mode / LangGraph HITL，复用现有 `ApprovalBroker` + `pendingApprovals` + `/agent/resolve` + `options` 广播通道。

- **结构化提问卡片（`ask_user` 升级）**：工具 schema 改为 `questions[]`（1–4 问，每问 `single|multi|text` + `options[{label,description}]` + `required` + `allowFreeText`）；UI 渲染单选/多选/文本并自动追加"其他"（模型不得自建 Other）；答案以 **Q/A 转录**回喂模型。修复了 v0.10 中 ask_user 回答链路实际断裂的问题（旧 edit 表单无 answer 字段）。
- **计划模式（开关式，聊天框可开）**：`propose_plan` 合成工具产出分步计划 → `plan_review` 审批卡（批准 / 要求修改并反馈）；harness 层门控——计划获批前模型只看到只读 op + `ask_user/propose_plan/update_plan`，建模 op 被拦（`PLAN_REQUIRED`）；批准后解锁全量工具表。
- **实时进度清单**：`update_plan`（write_todos 式，整表替换、每轮至多一次）更新步骤状态 `pending/in_progress/completed`，经 `plan_updated` WS 事件推前端会话流清单；计划持久化进 `session.plan`，重开可恢复。
- **HITL 加固**：破坏性操作/修复的 reject 回喂统一加"除非用户明确要求否则不要重试"；timeout 不再静默 SKIPPED，而是回喂为"视为拒绝、不要重试、可换方案"，让模型知情。
- 测试：后端 337（+提问转录/多问/跳过、计划门控/批准/拒绝/每轮一次 update_plan）、前端 55（+问题卡片 single/multi/Other/skip、计划清单渲染）。

## v0.10.0-alpha - 对话式 Agent 会话（真 harness 交互形态）

- **每项目一条持久会话（`backend/agent/session.py`）**：对话历史落盘 `work/agent_sessions/{id}.json`，
  重开项目可回看；`SessionRegistry` 惰性恢复。
- **统一对话入口 `POST /agent/message`**：agent 空闲 → 开新任务（消息自动携带最新特征上下文，可带草图图片）；
  运行中 → 插话进入 pending 队列，loop 在轮间与审批结束后注入，下一轮模型可见（WS `agent_queued` 提示）。
  `GET /agent/session`（展示视图）、`POST /agent/session/clear`；`/agent/start` 保留为兼容薄壳。
- **审批契约修复（实锤 bug）**：`approval_required` WS 事件此前在 `approval_id` 生成前发出，前端永远无法渲染
  审批卡。`ApprovalBroker` 拆 `create()`（生成 id）+ `wait()`（阻塞），事件先带 id 再等待。
- **token 级流式（`backend/mechcad_ai/client.py`）**：`chat_completion_with_tools` 增 `on_text_delta`；
  OpenAI（`delta.tool_calls` 按 index 拼装）与 Anthropic（`content_block_delta`/`input_json_delta`）双协议 SSE
  聚合为同一 `ToolCallRound`，循环逻辑不变；流开始后不再重试。模型文字经 WS `agent_text_delta` 打字机显示。
- **前端会话流（TaskPane AI 助手 tab）**：user/assistant 气泡 + 内嵌工具卡（op/参数预览/结果/自修复标记）+
  流式光标 + 自动贴底；生成按钮/Ctrl+G/聊天框全部收敛为同一条发送路径；legacy `/chat` 从 UI 摘除（API 保留）。
- **vision 进 agent**：任务消息支持草图图片（OpenAI `image_url` blocks；Anthropic 分支请求时转换为
  `source.base64`）。UI 在会话首条消息自动携带左侧上传的草图。
- **可视化快照**：几何变化时自动调 kernel `render`（iso 证据图，480px）落盘
  `snapshot_s{步号}.png`，WS `agent_snapshot` 推送 URL，会话流内嵌缩略图（点击看原图）；
  base64 不进 LLM 上下文。`/api/artifacts/{run}/snapshot_s{n}` 新 artifact 种类。
- 测试：后端 330/330（+18：会话流、插话注入、审批 id、SSE 双协议、端点、快照）、前端 72/72（+8：会话流渲染、store 动作、快照）。

## v0.9.0-alpha - Harness 主链路化 + P2 人机协作（弱化 FeaturePlanV3）

- **主路径全面切 MechKernel harness**：前端主按钮/Ctrl+G、特征树、属性面板、undo/redo 全部走 kernel
  worker RPC；FeaturePlanV3 → 受控 build123d worker 链路**冻结保留**（代码/测试/端点不动，
  UI 不再展示旧入口，README 标注 frozen，git 可随时切回）。
- **人机协作确认点（P2，`backend/agent/approvals.py`）**：
  - `ApprovalBroker`：agent 线程 `request()` 阻塞等待，`POST /agent/resolve` 由前端答复（approve/reject/edit+args_override）。
  - 三类暂停：破坏性操作（delete_feature / confirm_replace / shell）、破坏性修复（RECOVERABLE 且 fix 含 confirm_replace）、`ask_user` 合成工具。
  - 超时由 `MECHCAD_AGENT_APPROVAL_TIMEOUT`（默认 600s）控制，超时自动跳过并告知模型。
  - WS 事件 `approval_required`；前端新 `ApprovalPanel`（批准/改参/拒绝）。
- **kernel 直连 REST**：`/kernel/feature_tree`、`/kernel/update_feature`、`/kernel/delete_feature`、
  `/kernel/undo`、`/kernel/redo`（参数化重放，完成后重导 STL/STEP + 提交快照）。`/undo` `/redo`
  在 kernel worker 存活时走内核，否则回退 legacy 快照。
- **前端**：agent 状态入 store；`KernelFeatureTree`（op_history + nodes + 状态徽标 + 删除）、
  `KernelFeatureForm`（改参数→update_feature）、`ApprovalPanel`；hasModel 只看 stl（agent 无 obj）。
  旧 FeatureTree/FeatureForm/ClarificationPanel 保留但不再默认路径。
- **agent 开局上下文**：首条用户消息含当前 feature_graph 摘要 + 可用 op 列表，支撑"暂停→手动接管→交还继续"的连续性。
- 测试：后端 315/315（新增 test_approvals + loop/agent_api/kernel_worker 扩展）、前端 64/64（新增 16）。
- **冻结说明**：ai.py / geometry / generic_engine / validation / evidence_gate / cad_worker 等 legacy 模块及
  旧测试全部保留；网络环境下 MECHCAD_KERNEL_REPO / MECHCAD_KERNEL_PYTHON / MECHCAD_KERNEL_TIMEOUT 配置内核。

- 新增 MechKernel worker RPC：`mechcad-kernel` 仓的 `mech_kernel/server.py`（stdio JSON-lines，见其 HANDOFF）。
  aicad 侧 `backend/kernel_worker.py` 提供会话级 client 与 manager（一会话一内核实例，崩溃自动重启；历史重放恢复留 P4）。
- 新增 agent loop（`backend/agent/`）：LLM 通过原生 function calling 直接驱动 33 个公开 kernel op，
  逐步建模 + StepResult 结构化反馈 + RECOVERABLE 自修复（按 schema 过滤 suggestion.fix 自动重试一次）。
  提示词集中在 `prompts/prompts.yaml` 的 `agent_modeling`（中英）。
- 新增 REST：`POST /api/projects/{id}/agent/start`、`POST /api/projects/{id}/agent/stop`；
  WebSocket 新事件 `agent_step` / `agent_done`。前端视口上方新增 Agent 运行条（开始/停止/步数）。
- 快照契约：agent 运行把 feature_graph / op_history / 最终文字总结写入 `ExecutionReport` 扩展字段
  （`feature_graph`、`agent_steps`、`agent_final_text` 等），`feature_plan` 仅存占位 —— FeaturePlanV3 不再承担 agent 路径的执行语义（D1）。
- 冻结说明：既有 FeaturePlanV3 → 受控 build123d worker 链路（/generate、/chat、PATCH features）保持原样可切换，
  agent 是叠加路径而非替换。
- 环境变量：`MECHCAD_KERNEL_REPO`、`MECHCAD_KERNEL_PYTHON`、`MECHCAD_KERNEL_TIMEOUT`（见 .env.example）。
- 环境修复：`tests/test_geometry_measurement.py`、`tests/test_geometry_verification.py` 先安装 font guard
  再导入 build123d（损坏 Windows 字体会让导入崩溃）；`tests/test_evidence_gate.py` 的 `_plan` fixture
  补显式 X/Y（v0.7.3 校验收紧后的过期测试修复）。

## v0.7.3-B - Generic Groove Verification

- `annular_groove` and `internal_annular_groove` use the same GeometryEvidence types as holes and bosses.
- Groove identity is axial: bind the root cylinder by `z_start` / occupancy, not XY. Width is V-span; depth is derived from host vs root diameters.
- Host outer/inner cylinders stay reserved. Two unlabeled same-Ø grooves are `AMBIGUOUS`. A groove and a hole cannot both PASS one cylinder.

## v0.7.3-A.1 - General execution and envelope rules

- Worker no longer treats a centered datum name as an executable XY. Features that need position require explicit X and Y, matching the evidence-layer contract.
- Validation blocks child holes and circular patterns that fall outside the host in-plane envelope for both boxes and cylinders.
- Smart fill no longer invents a circular pitch radius or hole diameter without a clue. Missing values stay unresolved instead of becoming out-of-host geometry.

## v0.7.3-A - Generic Boss Verification

- `boss_cylinder` now uses the same GeometryEvidence pipeline as holes: signature, resolver, exclusive assignment, property verifiers.
- Boss checks: existence, diameter, height, axis, position, host. No new evidence types.
- Host cylinder primitives stay reserved. A hole and a boss cannot both PASS the same cylinder.
- Two unpositioned identical bosses remain `AMBIGUOUS` with no PASS.

## v0.7.2.1 - Hole Verification Adversarial Gate

- Position-first identity: Ø6 plan at a located XY against an Ø5 cylinder is `diameter=FAIL`, not `existence=FAIL`.
- Host inner/outer cylinders cannot MATCH a child hole (no hollow-bore False PASS).
- Exclusive candidate assignment: two FeaturePlan holes cannot both PASS one BRep cylinder.
- Added `tests/test_geometry_hole_adversarial.py` as the False-PASS gate.

## v0.7.2 Phase 1D-2.2 - Complete Hole Verification

- Hole verification now consumes `GeometryEvidence`. `through_hole` and `blind_hole` report existence, diameter, position, axis, depth, and through-span.
- A property may be `PASS` only against a `MATCHED` evidence row. `AMBIGUOUS` never auto-selects and never becomes `PASS`.
- Depth and through-ness compare cylindrical V-span with host AABB size or specified blind depth. This is software span evidence, not a topological both-ends-open proof.
- Unique leftover cylinders keep identity when position/axis disagree, so those properties can `FAIL` instead of pretending the hole was not found.
- Worker writes additive `geometry_evidence` beside measurement/verification. CAD execution, Evidence Gate, FeaturePlan, UI, and export are unchanged.
- Isolated box/cylinder base verification verdicts are unchanged. Boss remains evidence-only.

## v0.7.1 Phase 1D-2.1 - Feature Geometry Evidence Resolver

- Added a pure Feature-to-BRep evidence layer: `FeatureGeometrySignature`, `ReferenceContext`, `GeometryCandidate`, `CorrespondenceResult`, and `GeometryEvidence`.
- Correspondence uses `MATCHED` / `AMBIGUOUS` / `NOT_FOUND` / `UNAVAILABLE`. `AMBIGUOUS` never auto-selects a candidate.
- First bindable types: `box_base`, `cylinder_base`, `hollow_cylinder`, `through_hole`, `blind_hole`, and `boss_cylinder`.
- Position evidence is axis ∩ host AABB plane, never raw `CylinderFact.center` as feature XY.
- Verification verdicts, Worker CAD execution, FeaturePlan, Evidence Gate, UI, and export are unchanged. Worker persistence of `geometry_evidence` is deferred.
- Added `docs/rfc-1d-2.1-feature-geometry-evidence-resolver.md` and `docs/geometry_evidence.md`.

## v0.7-R1 - Repository Stabilization

- Audited the repository and established a KEEP / MIGRATE / DEPRECATE / DELETE baseline without deleting uncertain code.
- Added `ARCHITECTURE.md`, `FEATURE_SUPPORT.md`, `DEVELOPMENT.md`, and `docs/v0.7-r1-repository-audit.md`.
- Removed duplicated required-dimension and feature-group tables from `backend/validation.py` and `backend/ai.py`; both now derive from the canonical `FEATURE_DEFINITIONS` registry.
- Preserved the root `mechcad/` compatibility shim and unused frontend layout components with explicit DEPRECATE status.
- Updated README documentation links and corrected the virtualenv command path.
- Validation baseline after R1: 199 backend tests, 48 frontend tests, and a successful production build.

## v0.7.0 Phase 1D-2 - Geometry Semantic Verification Engine

- Added a pure, extensible semantic verification framework over `FeaturePlanV3` intent and 1D-1 BRep measurement facts.
- Added property-level `PASS` / `FAIL` / `UNKNOWN` / `UNSUPPORTED` / `SKIPPED` results, ephemeral candidate correspondence, centralized software verification tolerances, feature aggregation, and model aggregation.
- Added a registry covering every canonical feature definition. Level-A implementations currently verify isolated box/cylinder/hollow-cylinder bases using BRep bounding dimensions, derived base volume, and unique cylindrical-surface diameter/axis evidence.
- Added explicit global bounding-box and volume verification through `VerificationContext`; tolerances are software comparison tolerances, not manufacturing or GD&T tolerances.
- Cylindrical correspondence is report-local and conservative: duplicate candidates are `UNKNOWN`, missing candidates are `FAIL` only when measurement succeeded, and unavailable measurement remains `UNKNOWN`.
- Holes, bosses, grooves, patterns, fillets, chamfers, ribs, sketches, and feature relations remain registered but `UNSUPPORTED`; no semantic class is inferred from a cylindrical face.
- Worker reports now contain additive `geometry_verification` evidence beside `geometry_measurement`, without changing CAD execution, Evidence Gate, `geometry_valid`, `production_ready`, UI, or export behavior.
- Added focused deterministic, purity, adversarial, aggregation, and Worker integration coverage plus `docs/geometry_verification.md`.

## v0.7.0 Phase 1D-1 - Geometry Measurement Foundation

- Added a deterministic, read-only Build123d/OpenCascade BRep measurement layer for final-shape bounding boxes, solid volume, and observable cylindrical surfaces.
- Added explicit `MEASUREMENT_SUCCESS`, `MEASUREMENT_UNAVAILABLE`, and `MEASUREMENT_ERROR` states; unavailable measurements are reported instead of inferred.
- Worker execution reports now include an additive `geometry_measurement` object captured from the final BRep before export, independently of STEP/STL/OBJ export success.
- Cylindrical observations deliberately remain non-semantic: `measurement_index` is report-local ordering, not a feature reference or topology ID.
- Added focused geometry tests for boxes, cylinders, cylindrical cuts, multiple cylinders, repeatability, and read-only behavior, plus Worker report integration coverage.
- Added `docs/geometry_measurement.md` documenting the measurement/verification boundary and current API limitations. No semantic verification, tolerance policy, UI, or production-ready behavior changed.

## v0.7.0 Phase 1C - Evidence Conflict Gate

- Added structured evidence conflict models (`EvidenceConflict`, `EvidenceConflictSource`, `EvidenceResolution`, `EvidenceGateResult`) to the schema layer.
- `EvidenceSet` now persists `conflict_details` alongside the legacy `conflicts` strings, so existing consumers stay compatible.
- `build_evidence_set()` records numeric conflicts with a deterministic `0.01mm` tolerance and fills `EvidenceItem.conflict_with`.
- Added `backend/evidence_gate.py` with a pure `evaluate_evidence_gate()` that classifies material vs non-material conflicts against the current FeaturePlan.
- Strict and Smart modes both block unresolved material evidence conflicts; Smart mode returns `REQUIRE_RESOLUTION` instead of silently guessing.
- Added `apply_evidence_resolutions()` and `GenerateRequest.evidence_resolutions` for explicit, auditable user resolution without deleting original evidence.
- `_execution_gate` now runs before every CAD entry (generate, chat, property patch, restored snapshots) and records a `blocked` validation step with the structured gate result.
- Clarification questions now surface unresolved material conflicts; plain-language answers such as hole diameter selection still resolve them as a compatibility fallback.
- Added 12 regression tests including the mandatory CAD Worker invocation count `== 0` negative test and gate idempotence checks.
- Frontend API types now include the additive conflict and resolution shapes; no UI behavior changed.

## v0.7.0 Phase 1B - Pure Validation

- Added explicit `backend/normalization.py` with idempotent `normalize_feature_plan()`; auto IDs, duplicate/missing dependency bookkeeping, non-positive dimensions, assumption aggregation, and `requires_confirmation` are no longer hidden inside Pydantic model validation.
- Removed `FeaturePlanV3.validate_engineering_contract` and `model_validator`; model construction now only performs shape validation.
- `validate_feature_plan()` is now a pure function: it no longer writes `self_checks`, rewrites `design_review`, or reorders features.
- Added `compute_feature_order()` (read-only ordering) and explicit `order_feature_plan()`; validation uses the read-only form.
- Added `apply_validation_result()` so orchestration layers explicitly write validation checks into `self_checks` and `[validation]`-prefixed design review entries.
- All plan-producing paths now call `normalize_feature_plan()` before validation/execution: initial AI planning, local fallback, chat edits, property patches, clarification answers, template plans, restored snapshots, and CAD Worker loading.
- Existing API return keys (`checks / blocking / warnings / order / base_ready`) and strict/smart semantics are unchanged.
- Added explicit mutation-regression, double-validation stability, and normalized/validated snapshot undo/redo tests; repaired previously hidden SessionStore tests caused by a misplaced `_snapshot` helper.

## v0.7.0 Phase 1A - Canonical Feature Semantics

- Added `FeatureDefinition`, `ConstraintSpec`, and `VerificationContractSpec` to the schema layer as stateless feature metadata.
- Added `backend/feature_definitions.py` with `FeatureDefinitionRegistry`, `get_feature_definition()`, and canonical definitions for all 16 supported feature types plus 6 known unsupported types.
- `CapabilityRegistry` now derives `CAPABILITIES` from the canonical registry; `feature_semantics()` preserves the legacy `/api/capabilities` shape while reading from the registry.
- Legacy validation/AI/normalization dimension tables are marked with `TODO(v0.7)` migration notes and regression tests now assert they match the canonical registry.
- Fixed drift where AI required-dimension tables were missing internal annular grooves, link plates, and linear patterns.
- Fixed a latent missing `CapabilityRegistry.all()` method used by `GET /api/capabilities`.
- Added 11 backend tests for registry coverage, invalid type rejection, duplicate registration, derived capability/semantic equivalence, and legacy-table consistency.

## v0.6.0 - Generic Modeling Core and UI Acceptance Layer

- Added a generic input/evidence layer: `InputRouter` distinguishes text-only, image-only, and mixed inputs; pure text generation no longer needs a vision model.
- Added `EvidenceSet`, `DesignIntent`, `FeatureSemantics`, and `FamilyTemplate` models with deterministic clue extraction for Chinese/English dimensions.
- Added a data-driven `FamilyTemplateRegistry` covering flange, tube with internal groove, link/rocker plate, phone stand, and spur-gear blank templates.
- Spur gear teeth are explicitly unsupported; the template and capability registry mark `spur_gear_teeth` as unsupported instead of producing a fake gear.
- The planning fallback now builds template plans with evidence, intent, assumptions, and completeness; Smart mode fills auditable defaults and Strict mode keeps missing values unresolved.
- CAD Worker reports per-feature `modeled`, `skipped`, and `failed` statuses, including a real geometry acceptance check based on solid volume change.
- Non-intersecting cuts are now marked `failed` with a "geometry did not change" warning instead of being reported as modeled.
- Added `ExecutionReport` with four independent statuses: `execution_ok`, `plan_complete`, `geometry_valid`, and `production_ready`, plus fallback, assumptions, completeness score, skipped/failed features, and worker details.
- Added `GET /api/capabilities` exposing registered capabilities and feature semantics for UI and audit reuse.
- Frontend adds execution acceptance report, dimension evidence strip, design intent summary, and a feature-tree pipeline header (evidence -> intent -> features -> acceptance).
- Worker messages and validation messages were cleaned to UTF-8 Chinese/English pairs.
## v0.5.0 Phase 1 - Capability & Generic Edit Runtime Foundation

- Added static capability metadata (`ParameterSpec`, `FeatureCapability`, `CapabilityIssue`) to `backend/schemas.py`.
- Added `CapabilityRegistry` and a module-level `CAPABILITIES` singleton with 14 real feature types: 3 bases, 3 holes, 2 slots/pockets, 1 annular groove, 3 additive features, and 2 patterns.
- Chat edits, LLM edits, and property-panel patches now share one structural validation gate before the existing FeaturePlan checks.
- Invalid operations fail individually as blocked ProcessSteps; valid operations in the same edit set still execute.
- Property-panel patches that violate capability rules return HTTP 422 with structured `CapabilityIssue` data.
- Unknown features, disallowed operations, unknown/read-only parameters, negative values, and wrong value types use fixed error codes.
- `fillet`, `chamfer`, `thread`, `gear`, and `sheet_metal` are intentionally unregistered and are reported as unsupported instead of being silently accepted.
- Added `docs/capability_runtime.md` describing the registry, error codes, edit flow, responsibility boundary, and how to register new capabilities.
- Stabilized stub-quality AI tests so they stay deterministic when `.env` has real model credentials configured.

## v0.4.3 - Complex Part Validation and UTF-8 Regression Guard

- Added realistic complex part validation through the controlled Build123d Worker:
  - motor mounting plate with a pilot boss, central bore, and four mounting holes;
  - shaft with a center bore and two seal grooves;
  - flanged end cap with a center bore, face O-ring groove, and eight-hole bolt circle;
  - support bracket with a reinforcing rib and two bossed mounting bores.
- Every case passed deterministic self-checks and exported STEP/STL/OBJ with per-feature execution reports.
- Verified the Chinese description path end to end: Chinese tube/pipe and bracket descriptions were recognized and modeled without source-level encoding changes.
- Added an encoding regression test that guards key Chinese source strings and rejects `U+FFFD` replacement characters.

## v0.4.2 - Reliability, Validation, and Feature Tree

- Restored `gpt-5.5` planner, cleaned `.env` to `MECHCAD_*`, and added per-role timeout/retry settings.
- API retries recover from transient ReadTimeout/connection/5xx failures and report the final attempt count in the process timeline.
- CAD Worker writes `feature_statuses` back into the FeaturePlan; API and feature tree no longer show stale `unresolved` statuses.
- Flange center holes now use the independent `hole_diameter` dimension instead of misreading the outer diameter.
- Added deterministic engineering self-checks (`validate_feature_plan`) and staged dependency ordering (base -> remove -> add -> pattern -> modify).
- Strict mode blocks missing dimensions, unconfirmed assumptions, and blocking checks; smart mode keeps auditable assumptions while still blocking geometry contradictions and invalid dependencies.
- Missing X/Y placement is rejected in strict mode and skipped by the CAD Worker instead of silently defaulting to the origin.
- CAD Worker reorders features before execution; chat, property edits, generation, and undo/redo all refresh validation consistently.
- Frontend feature tree is now a read-only grouped view with status badges, missing/assumption markers, dependency indentation, and summary counts.
- Validation messages are bilingual (Chinese/English) and exported into `self_checks` and design review.

## v0.4.1 - Stability and Config Fixes

- Switched the planner back to `gpt-5.5`; `.env` now exposes only authoritative `MECHCAD_*` variables.
- Added per-role API timeouts and retries with real error propagation into process steps before local fallback.
- Fixed CAD execution status write-back for cylinder/hollow-cylinder bases.

# MechCAD IDE Changelog

## v0.4.0 - Process-First Timeline + Feature-Level Editing

- Added `ProcessStep` and `ProcessRecorder`; every run now records `upload -> vision -> planning -> validation -> chat_edit/cad -> export`.
- Chat edits now produce an auditable `FeatureEditSet` with `add`, `update`, `delete`, and `change_type` operations instead of replacing the whole `FeaturePlanV3`.
- Local deterministic editing covers center-hole diameter changes, hole moves, groove deletion, and M6 hole pattern creation.
- Strict mode blocks unconfirmed inferred dimensions; deleting a parent with children asks for confirmation instead of silently breaking dependencies.
- CAD Worker reports per-feature `running/completed/skipped/failed` steps through JSONL and WebSocket, with real error details in the execution report.
- Property-panel edits also generate an `update` process step with before/after snapshots.
- Process timelines persist with snapshots and are restored by undo/redo.
- Frontend adds a Process tab with grouped statuses, feature IDs, warnings, and failure reasons.
- Fixed duplicate Worker progress events and unawaited coroutine warnings in the event bridge.

## v0.3.0 - Productized UI

- SolidWorks-style three-panel layout with full-screen 3D viewport and drawer panels.
- Startup page, recent projects, settings center, and Chinese/English switching.
- Single-port production mode plus Windows tray launcher and desktop shortcut.
- API/model configuration moved into Settings.

## v0.2.0 - New Architecture Baseline

- React + TypeScript + Vite + Three.js frontend.
- FastAPI REST + WebSocket backend.
- Controlled CAD Worker subprocess with Build123d.
- FeaturePlanV3 validation, design review, clarification questions, and session persistence.

## v0.1.0 - Initial MVP

- Hand-drawn sketch to 3D model pipeline.
- OpenCV preprocessing, vision analysis, AI planning, sandboxed CAD execution.
- STEP/STL/OBJ export and Gradio UI.
