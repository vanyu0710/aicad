# Varen CAD 模型基线测试 —— 变速箱流程（2026-09-07）

> 目的：挑选能稳定驱动 varen agent loop（逐步 function calling 建齿轮）的 planner 模型。
> 三个 API 服务商 + 6 个候选模型 + 1 个显式提示"省额度"的 gpt-6-astra。
> 方法：先 `GET /models` 核实真实模型 ID（确认无套壳名），再发最小 prompt 探测连通性，
> 最后用带 tools 的请求验证**结构化函数调用**（varen agent 的核心机制）。

---

## 1. 服务商连通性（`GET /models`）

| 服务商 | Base URL | 连通 | 鉴权 | 备注 |
|---|---|---|---|---|
| **API1 scnet** | `https://api.scnet.cn/api/llm/v1` | ✅ | ✅ | 38 个模型，含 `Qwen3.8-Flash`/`GLM-5.3-Flash` |
| **API2 deepseek** | `https://api.deepseek.com` | ✅ | ✅ | 3 个模型 |
| **API3 lingshuai** | `https://api.lingshuai.cc` | ✅(需 `-k`) | ✅ | 模型名 `gpt-5.6-*` 等；Windows 需跳过 TLS 吊销检查（CRYPT_E_REVOCATION_OFFLINE） |

> ⚠️ API3 在 Windows Python `requests`/`urllib` 下会因**证书吊销服务器离线**而握手失败（`CRYPT_E_REVOCATION_OFFLINE`）。
> 若要用 API3，后端需要 `verify=False` 或关闭吊销检查（见文末"配置 API3 的注意"）。

---

## 2. 候选模型实测（6 个全部可用，全部支持 OpenAI 格式 function calling）

| # | 服务商 | 模型 ID（API 实际返回） | 对话 | 函数调用 | 建议角色 |
|---|---|---|---|---|---|
| 1 | API1 | `Qwen3.8-Flash` | ✅ 准确 | ✅ `make_gear` 参数正确 | **主推**（国产、快、便宜） |
| 2 | API1 | `GLM-5.3-Flash` | ✅ 空回复 | ✅ 参数正确 | 备选（偶尔空回复，需加大 max_tokens 或加系统提示词） |
| 3 | API2 | `deepseek-v4-pro` | ✅ | ✅ 参数正确 | 主推（推理强，函数调用稳） |
| 4 | API2 | `deepseek-v4-flash-vision-exp` | ✅ | ✅ **需 max_tokens≥400** | 主推（视觉 + 推理，慢些） |
| 5 | API3 | `gpt-5.6-sol` | ✅ | ✅ 参数正确 | 主推（质量高，较贵） |
| 6 | API3 | `gpt-5.6-terra` | ✅ | ✅ 参数正确 | 备选（质量高，较贵） |

### 关键实测结论

- **OpenAI 协议**：全部返回 `choices[].message.tool_calls[].function.arguments`（标准 OpenAI 格式），
  varen 的 `chat_completion_with_tools(protocol="openai")` 直接可用。
- **DeepSeek-v4-flash-vision-exp 的坑**：它是推理模型，`max_tokens=200` 会因 reasoning token 挤满输出
  （`finish_reason=length`，参数被截断成 `{"module": `）。**必须设 `max_tokens ≥ 400`**，否则函数调用失败。
- **GLM-5.3-Flash 的坑**：最小 prompt 下返回了空 `content`（可能被当作"推理后无必要正文"），
  实际函数调用正常，但需确认对话式任务下会给正文。
- **API3 / gpt-* 都是高 token 模型**：最小测试 `prompt_tokens=4403`，远高于其它（DeepSeek ~96、Qwen ~377）。
  这表明 lingshuai 的模型**自带超长 system/上下文注入**，成本高，适合质量优先的场景。

### 成本对比（单次最小对话 token）

| 模型 | prompt | completion | 备注 |
|---|---|---|---|
| Qwen3.8-Flash | 377 | 534 | 正常 |
| GLM-5.3-Flash | 25 | 32 | 最小 |
| DeepSeek v4 pro | 96 | 152 | 正常 |
| DeepSeek v4 flash vision exp | 96 | 17 | 正常 |
| gpt-5.6-sol | 4403 | 47 | **超长 system 注入** |
| gpt-5.6-terra | 4403 | 48 | **超长 system 注入** |

---

## 3. 测试变速箱任务的最小脚本

对比测试用一次简单 API 调用探查真实能力，不下发真实 CAD/长链。真实全流程需按 `docs/PRODUCT_FLOW_GEARBOX.md` 的逻辑走，
其 LLM 驱动的实证见第 5 节（已用真实 Qwen 建出齿轮+轴并逐件归档）。

## 4. 推荐选型（结合第 5 节实测）

- **首选**：`Qwen3.8-Flash`（API1，功能齐、稳、便宜）← 主 planner；**实测能一次到位驱动建模 + `finish_part` 归档**（适合齿轮/轴/板等参数化零件）。
- **备选**：`deepseek-v4-pro`（API2，推理强、函数调用稳）——适合**复杂空间结构**（箱体内腔）和长流程，比 Qwen 更能hold 住多基准面推导。
- **质量优先、预算充足**：`gpt-5.6-sol`（API3，但注意每次 context 注入 ~4400 token）。
- **复杂箱体/内腔类的零件**：建议用 `deepseek-v4-pro` 或 `gpt-5.6-sol`（见第 5 节"Qwen 在复杂 3D 空间会打转"）；Qwen 在这类需多 offset 推导的场景会反复 create_workplane 而不闭合草图。

### ⚠️ 关于 `gpt-6-astra`（省额度）

`gpt-6-astra` 是 lingshuai 最高端模型，**单次请求 `prompt_tokens` 可能高达 4400+**，用于长 agent 循环时
每轮都重新注入 ~4400 token 上下文，token 消耗会非常快（用户明确提醒不能烧太多）。
模型目录确认它存在（`gpt-6-astra`），但**不推荐用作主 planner**，除非单个任务预算 > 50w token。

> 结论一句话：**6 个模型全部可用、全部支持函数调用**。功能都达标，主要差异在成本与"是否超长 context 注入"。
> 用 Qwen3.8-Flash 平替最划算，用 gpt-5.6-sol / gpt-6-astra 是烧钱路线。

## 5. 全 6 模型统一 CAD 建模实测（2026-09-07）

**统一任务**：每个模型驱动 varen 建「真渐开线齿轮 make_gear m=2 z=17 w=16 + 圆柱轴 Ø14×120」，逐件 `finish_part` 归档（同一基准，测真实建模+归档能力）。结果写入 `docs/model_benchmark_report.html`（可视化）。

| 模型 | 状态 | 零件 | 步数 | 耗时 | 齿轮 STEP | 备注 |
|---|---|---|---|---|---|---|
| Qwen3.8-Flash | ✅ | 2/2 | 10 | 120s | 1.6MB | 完整（此前 tmp 验证） |
| DeepSeek-V4-Pro | ✅ | 2/2 | **7** | **122s** | 428KB | 最快最干净 |
| DeepSeek-V4-Flash-Vision | ✅ | 2/2 | 10 | 169s | 1.6MB | 完整，先问 1 次 |
| GLM-5.3-Flash | ✅ | 2/2 | 9 | 320s | 1.6MB | 完整，慢 |
| GPT-5.6-Terra | ✅ | 2/2 | 12 | 326s | 1.6MB | 完整，最慢 |
| GPT-5.6-Sol | ❌ | 1/2 | 4 | ~130s | 428KB | **流式 SSE 不兼容+陷入调研循环**，仅完成齿轮1，未建轴 |

**关键结论**
- **5/6 模型能完成建模+逐件归档**（Qwen、DS-Pro、DS-Vision、GLM、Terra）。这证明 varen 的 F1 建模链路在主流模型下可信。
- **GPT-5.6-Sol 失败**：API3（lingshuai）的 gpt-5.6 系列**流式 SSE 与 OpenAI 标准不兼容**——bare `requests` 非流式能拿到 tool_calls，但 backend 的流式聚合（`_openai_tool_round_stream`）拿不到，导致 agent loop 那轮 tool_calls 为空。非流式下能进建模，但 Sol 又陷入过度调研循环（连续 4 次 design_calculate）。**这是后端要修的流式兼容 bug**（我已加了一个回退尝试，但流式下该模型连 SSE 都不发，需要专修）。
- **成本**：gpt-5.6-* 单次请求自带 ~3700-4400 token 上下文（比其它高一个量级）；gpt-6-astra 更贵，未测试（用户叮嘱省额度）。

**推荐**：日常/流畅演示 → `Qwen3.8-Flash`（便宜稳）；复杂箱体/高质量 → `DeepSeek-V4-Pro`（推理强、步数最少）；用 API3 需先修流式兼容（否则只能非流式且 Sol 会调研循环）。

## 5b. 补充：Qwen3.8-Flash 建模回归细则（同一任务，mode=auto）

**用真实 Qwen3.8-Flash + 真实 MechKernel worker + 真实 agent loop 跑了一次最小 CAD 建模任务**
（任务："用 make_gear 建一个 m=2 z=17 w=16 直齿轮，然后建一根 Ø14×120 圆柱轴，建完各用 finish_part 归档"）：
`mode="auto"`（非计划模式，直接测建模链路）。

### 结果：✅ 完整走通逐件交付

```
step1  make_gear          → 真渐开线齿轮 m=2 z=17 b=16（中心孔 Ø14）
       finish_part        → part_01_gear_pinion.step (1.6MB BRep ISO-10303-21)
                            part_01_gear_pinion.stl  (47MB 完整样条网格)
step5-9 建输入轴           → create_workplane→new_sketch→add_circle→close_sketch→extrude(Ø14×120)
       finish_part        → part_02_input_shaft.step (5.7KB) + .stl
RESULT ok=True steps=10 parts=2 calc=0
```

- **每个零件建模 → `finish_part` → 归档 STEP/STL → 内核 reset → 下一个零件**：全部自主完成。
- `close_sketch → extrude` 序列被模型**正确调用**（这次没打转——因为任务明确、无复杂空间推理）。
- 每一次 `finish_part` 都触发 `artifact_ready` 与快照渲染；`validate_geometry` 有一次 target 不存在，后修正为 `_current_geometry`。
- **结论：Qwen3.8-Flash 完全能驱动 varen 建模。** 它对明确参数化的零件（齿轮、轴）一次到位，逐件交付链路 (F1) 在真实 LLM 下可信。

### 关键发现：Qwen 在"复杂 3D 空间建模+计划模式"下会打转

**真实变速箱任务（plan 模式，含复杂箱体）** 复盘：
- ✅ 调研（`design_calculate`×8：分级/中心距/轴/壁厚）→ ask_user → propose_plan（15 步 BOM，含 housing/cover/shaft×4/gear×6 全部带 part）→ 批准 → 开始执行。
- ❌ 但模型卡在 **box 箱体的 s1 步**：反复 `create_workplane`（-82/-111/-126/-71 不同 offset）+ `add_rectangle`，**从不 `close_sketch → extrude`**，也没有推进到后面的齿轮/轴/finish_part。

**根因**：不是 kernel 缺陷，也不是流程缺陷。是 **Qwen 对"箱体"这种需要多基准面层次、offset 推导的复杂 3D 空间结构把握不足**——它在脑内推导空间关系而迷失。对明确参数化零件（齿轮、轴）则一次成功。

### 对选型与调优的启示

| 场景 | Qwen3.8-Flash 表现 | 建议 |
|---|---|---|
| 明确参数化零件逐个建（齿轮/轴/板） | ✅ 稳定，`make_gear`+`finish_part` 一次到位 | 推荐 |
| 复杂空间结构（箱体、多级 offset 的内腔） | ⚠️ 会打转，不闭合草图 | 需更强的提示词引导，或改用更强的模型（deepseek-v4-pro / gpt-5.6-sol） |
| 计划模式长流程 | ✅ 能走完调研→计划→批准→执行 | 但复杂零件卡住时需人工在 `finish_part` 上兜底 |

**推荐**：
- F1 简单零件/演示流程 → `Qwen3.8-Flash`（便宜、够用）
- 正式复杂变速箱（含箱体内腔）→ **建议 `deepseek-v4-pro` 或 `gpt-5.6-sol`**（更强的 3D 推理；后者贵、context 大，但箱体这种复杂件值得）。
- 后续若让 Qwen 负责的箱体，提示词需明确"箱体= 单一粗坯矩形→extrude→shell/内切拉伸，先 close_sketch 再 extrude"，减少空间猜测。

## 5c. 齿轮几何缺陷与修复（渲染复检发现，2026-09-07）

**用户对渲染图复检时发现**（presentation 质量重渲染后）：齿轮的齿是**悬浮的独立梯形块**（与毂盘间有 ~1.5mm 环缝）、标称 bore=14 的齿轮**没有通孔**。STEP 实体计数实锤：

- DS-Pro（bore=0）齿轮 STEP = **18 个独立实体**（1 毂盘 + 17 浮齿）
- Terra（bore=14）体积 9272 < 毂盘理论 10568 → "打孔"实际切掉了底部环带

**根因（mech_kernel/gear.py 两处 bug，v2.12.1 修复）**：
1. `_build_involute_tooth_face` 齿廓闭合在基圆 rb 的单尖点，未延伸到齿根 rf → 齿与毂盘不连接。修复：齿廓沿径向延伸进齿根域（根半角 0.45×角节距），融合后单实体。
2. 打孔 `Cylinder(width, bore/2)` 半径/高写反且居中 z=0 → 切的是底环带非通孔。修复：`Cylinder(bore/2, width, align=(…,MIN))`。

**修复验证**：bore=0/14 均 **1 个实体**；钻孔体积与理论通孔偏差 0.64%；内核全量 377/377 绿（新增 `test_gear_is_single_connected_solid` 回归门 + 孔体积断言收紧到 3%）。修复后渲染见 `docs/gear_fixed.png`（连体齿 + 真通孔）。

**复检缺口（待办）**：`validate_geometry` 只查网格健康（破面/退化/流形），查不出"齿悬浮/孔未打穿"这类**设计性缺陷**；当时的 op 测试体积断言太宽也放行了。已修的防线：内核回归测试锁"单实体+真通孔"。仍缺的防线：agent 收尾的**设计复检**（finish_part 前 measure 关键尺寸对照计划 + 渲染喂 vision 模型自检外观，DeepSeek-V4-Flash-Vision 可充当 vision 审查）——待排期。

## 6. 配置 API3 的注意

`requests` / `urllib`（Python）在 Windows 下访问 API3 会因证书吊销检查离线而失败（`CRYPT_E_REVOCATION_OFFLINE`）。
`curl -k` 或 `verify=False` 可绕过。varen 后端用 `httpx`/`requests`，若要用 API3，得在 `mechcad_ai/client.py`
给该 base_url 传 `verify=False`（会触发不安全警告），或在系统里把 lingshuai 证书加入信任。

> **后记（2026-09-07 实测）**：aicad venv 的 `requests` 下 API3 用 `verify=True` 也能连（certifi 吊销缓存较全），
> 上述证书问题主要在 curl / 系统 shell。但 **API3 的 gpt-5.6 系列流式 SSE 与 OpenAI 不兼容**，需后端修流式聚合才能流畅使用。
