# F2 装配体工程设计（Assembly Design）

> 状态：**设计文档 · 待审** · 2026-09-09
> 范围：把 `PRODUCT_FLOW_GEARBOX.md` 的 F2（装配架构）+ F3（交付质量）合并落地为可实施设计。
> 根据：两份实现级调研（内核装配/导出/重放机制、IDE 单零件假设清单），本文所有论断带 `文件:行号`。
> 仓路径缩写：`K = mechcad-kernel`，`A = varen cad/aicad`。

---

## 1. 目标与非目标

### 目标（F2a 验收口径）

以"给我设计个 1:100 的变速箱"为端到端场景：

1. **项目零件库**：每次 `finish_part` 归档的零件持久归属项目（不再随 run 目录散落、随快照替换被孤儿化），带稳定身份与位姿。
2. **整装配预览**：前端按位姿把全部零件叠加渲染（多实体、可分件显隐/高亮）。
3. **多实体装配 STEP**：XCAF 装配树（每零件一个具名产品节点 + 颜色），非融合单实体。
4. **全对干涉报告**：装配位姿下逐对 OCC 求交，齿轮啮合区可配预期干涉豁免。
5. **交付包**：装配 STEP + 分零件 STEP/STL + 整装配四视角图 + 干涉报告 + BOM 表（含位姿与关键参数）。

### 非目标（明确不做/留后）

- **装配级参数化联动**（改一个零件参数 → 装配自动重算位姿/干涉）→ F2b，本文只预留接口（§8）。
- **约束求解器 / 配合关系（mate/joint）**：内核 `reference_frames` 无父子变换复合（`K/mech_kernel/reference_frames.py:122-139` 的 `to_world` 只作用于自身基；`AssemblyInstance.world_transform` 字段存在但内核从不计算），做真约束求解是净新增大件 → 留 F3 之后。
- **装配内直接编辑零件**：编辑仍走"单会话重做 + 重新归档"。
- 工程图（2D drawing）、公差分析。

---

## 2. 现状事实（设计的物理约束）

### 2.1 内核：装配是"融合单 solid + 显示级元数据"

- `K/mech_kernel/assembly.py:1-6` 模块注释原文承诺：*"The kernel continues to store a fused STEP-compatible solid … without changing replay semantics or the exported fused geometry."*
- `assemble` op（`K/mech_kernel/kernel.py:3331-3415`）：每次调用**清空** `_assembly_instances`（3349）→ 逐件 `import_step` + rotate/translate → **布尔融合** `assembled + part`（3368-3372）→ `_current_geometry = assembled`、`_has_non_replayable_op = True`（3397-3398）；**不进 `_op_history`**。
- `export` 只导出 `_current_geometry`（`K/mech_kernel/kernel.py:3067`）；server `export_mesh` 同样只认当前几何（`K/mech_kernel/server.py:283-306`）。
- 该语义被测试钉死：`test_v4_profiles.py:66-71`（assemble 必须留在 EXPERIMENTAL）、`:164-175`（融合体积可加）、`:178-187`（assemble 后 delete_feature 必须 RECOVERABLE 拒绝）；`test_v7_assembly_renderer.py:39-80`（显隐/颜色只改显示、全隐藏时 render 必须失败而非回退融合体）。

### 2.2 但导出真装配的技术前提已就绪

- **OCP 完整 XCAF 能力在位**（实测 import 通过）：`OCP.STEPCAFControl`（Writer/Reader）、`OCP.XCAFDoc`、`OCP.TDocStd`、`OCP.XCAFApp`。
- **build123d 0.11.1 已经把它用好**：`export_step` 内部 `_create_xde`（`build123d/exporters3d.py:75-233`）建 `TDocStd_Document("XmlOcaf")` → `XCAFDoc_DocumentTool.ShapeTool/ColorTool` → 沿 build123d 形状树 `AddComponent` + `UpdateAssemblies`（`:114-115` 等），`STEPCAFControl_Writer` 开 `SetNameMode/SetColorMode/SetLayerMode(True)`（`:379-381`）。
- **推论**：只要把装配组装成"带 label/color 的 build123d 树"（`Part(label)` 嵌套或 `Compund` + `relabel`），`export_step` 就产出真装配结构 STEP。**不需要裸 OCP，不需要动内核对象模型**。

### 2.3 事务/快照协议可以白拿

- `_snapshot`（`K/mech_kernel/kernel.py:3966`）与 `_restore`（`:3987`）**已经**包含 `assembly_instances` 与 `frame_registry`——新增场景对象进快照有逐字先例，undo/redo/`run_script` 检查点/server snapshot 全部免费复用。
- ⚠️ 现存坑：两处都是 `copy.copy(AssemblyInstance)` **浅拷贝**，`position/color` 列表与活体共享；新代码若就地 mutate 会污染 undo 快照。F2 的 manifest 对象要么深拷贝、要么按不可变值处理。

### 2.4 干涉与校验的现状与坑

- `K/mech_kernel/collision.py`：`check_pair_interference`（19-147，`part_a & part_b` OCC 求交，7 字段返回契约）、`check_assembly_interference`（150-191，**全对 N(N-1)/2，无 bbox 预过滤**）。
- 已知坑：①完全重合体求交不可靠（`test_v9_collision.py:53-63` 注释与放宽断言）；②demo14 实测 z=60 齿轮 fuse 22s——**N² boolean 性能必须预算**；③kernel 的 `check_interference` op schema 收 `parts: list[dict]` 但 collision 需要活体 Part（`K/mech_kernel/kernel.py:2673-2713`），**JSON-RPC 通道实际传不进零件**——F2 新命令必须自己 import STEP 再算。
- `validate_assembly` 的 relation 实现弱：coaxial 只看方向不查轴线偏移（`:5228-5265`）、clearance 纯 AABB（`:5278-5299`）、`inside` 未实现（`:5316-5318`）、gear_mesh 要调用方手传 pitch diameter（`:5300-5315`）。F2 不依赖它做质量判定，只依赖真实求交。

### 2.5 IDE：全栈"一项目 = 一活会话 = 一份主几何"

19 条单零件假设（调研全表见 §6 逐条处置），最重的四条：

| 位置 | 假设 |
|---|---|
| `A/backend/kernel_worker.py:249,256` | Manager 单层 key `project_id→worker` |
| `A/backend/agent/loop.py:1321-1352` | finish_part 归档到 **run 级散文件** `part_NN_slug`，无项目零件库、part_rec 无位姿 |
| `A/backend/main.py:844-893` | `_commit_kernel_state_snapshot` 手动改参后重建 ArtifactSet **丢 parts 列表**（现存 bug） |
| `A/frontend/src/Viewport.tsx:132-168` | 单 stlUrl、单 model、`geometry.center()`（`:153`）会摧毁位姿 |

- BOM 的 `depends_on` 已规范化、已持久化、**零消费**（`A/backend/agent/loop.py:207-242` → session.plan → 前端类型声明但组件不读）——F2 把它转为装配顺序依据。
- 内核 `reset`（`K/mech_kernel/server.py:271-276`）+ 单实体门（`A/backend/agent/loop.py:1302-1311`）是"一会话一零件"的机制根源，F2a **保留不动**。

---

## 3. 架构决策

### D1（核心）：装配 = 零件库 + 位姿 manifest 之上的「视图」

**不新增活编辑上下文。** 装配的全部真值 = `项目零件库（文件）+ manifest（位姿/身份）`；预览、导出、干涉都是对这两者的**无状态计算**。

理由：
- 内核单几何契约（§2.1）与全部锁定测试零触碰——这是最大的一条省路径；
- 零件级参数化编辑已经存在（单会话重做 + 重新归档），装配随 manifest 更新即更新，F2a 不需要"活装配"；
- 前端/后端改造面收敛为"多一个视图模式 + 一份 manifest"，而非重做状态管理。

**代价（诚实列出）**：改零件参数 → 需重跑该件并重新 `finish_part`（不能只改 manifest）；装配没有"当前未归档改动"的实时性。F2b 用内核 `_parts` 注册表消除这两点（§8）。

### D2：manifest 持久层 = AgentSession（权威）+ ProjectState（投影）

调研结论：`DesignSnapshot` 是 undo/redo 整存整取单元（`A/backend/session.py:84-108`，commit 即整体替换），零件清单会被手动改参/undo 冲掉——**不能**承载 manifest。

- 权威：`work/agent_sessions/{project}.json` 顶层新增 `parts_library`（与 `plan` 解耦，`set_plan` 整体重写 plan 不影响它）；
- 投影：`ProjectState` 新增顶层 `assembly` 字段（不进 history/redo_stack），前端从 `GET /api/projects/{id}` 直接可读。

### D3：位姿由模型从调研计算得出，harness 校验字段合法性

`design_calculate` 已算出中心距/轴长/凸台位（如 `gear_pair.center_distance`、`shaft_diameter`），BOM 项新增 `pose` 即把这些数值落到装配坐标。备选（手工 UI 调位姿 / 约束求解）见 §9 开放决策。

### D4：装配导出/干涉 = 内核两个**无状态 server 命令**，不碰 kernel 对象模型

放 `K/mech_kernel/server.py` dispatch（execute 块后，同 run_script 插入法），实现体放新模块 `K/mech_kernel/assembly_scene.py`：只做 import STEP → 组装带 label 的树/逐对求交 → 写文件/返回 JSON。**不读写 `_current_geometry`、不进 `_op_history`、不进事务**（纯只读计算 + 文件产物，无需 undo）。

### D5：agent 协议只加收尾动作，逐件协议不变

`export_assembly` 合成工具（计划全部归档后可用）：读 manifest → 调 D4 两命令 → 渲染整装配四视角 → 写交付报告。提示词的"一会话一零件/禁止融合"禁令（`A/prompts/prompts.yaml:135-139`）**保留**——装配是归档后的事。

---

## 4. 数据契约

### 4.1 项目零件库目录

```
work/project_parts/{project_id}/
  parts_manifest.json          ← 唯一权威文件（AgentSession 内镜像一份，写序：session 先、库文件后）
  v003_小齿轮1.step / .stl     ← 版本前缀=归档序号；manifest 指向当前版本
  v005_箱体.step / .stl
  assembly_v001.step / .stl    ← export_assembly 产物
  assembly_v001_report.json
```

（`A/backend/storage.py` 新增 `PROJECT_PARTS_ROOT` 与只读解析函数；artifact 路由扩展 `project/{project_id}/{filename}` 形态，沿用 `_PART_KIND` 正则的防穿越思路 `A/backend/storage.py:12`。）

### 4.2 manifest schema（v1）

```jsonc
{
  "schema_version": "1.0",
  "project_id": "…",
  "updated_at": "…",
  "parts": [{
    "name": "小齿轮1",                    // 计划 BOM 零件名，项目内唯一
    "version": 3,                          // 第 3 次归档（重做递增）
    "run_id": "a1b2c3",
    "step_file": "v003_小齿轮1.step",
    "stl_file": "v003_小齿轮1.stl",
    "built_via": "ops",                    // ops | script（沿用 v0.13）
    "volume_mm3": 15787.2,
    "contract_passed": true,               // 单实体 + 特征契约（归档时已验）
    "pose": {                              // ★ F2 新增，装配唯一新增信息
      "position": [x, y, z],               // mm，世界系
      "rotation_deg": [angle, [ax, ay, az]] // 与内核 assemble 的 rotation 格式对齐（K kernel.py:3369-3371）
    },
    "role": "高速级主动轮",
    "depends_on": ["输入轴"]               // 沿用 BOM 字段，转为装配顺序/报告分组依据
  }],
  "assembly": {
    "exported_at": "…", "step_file": "assembly_v001.step",
    "interference": {"total_pairs": 55, "interfering": [...], "exempted": [...], "max_volume_mm3": …}
  }
}
```

校验（harness 侧，`_normalize_bom`/`finish_part`/`export_assembly` 三处）：pose 必须为有限数、rotation 轴单位化、零件名唯一、`contract_passed` 才允许进装配。

### 4.3 BOM 扩展（`propose_plan`）

`bom[].pose` 新增（多零件计划必填，单件任务可省）；schema 描述明确"数值应来自 design_calculate（中心距/轴长），不得拍脑袋"。`_normalize_bom`（`A/backend/agent/loop.py:207-242`）加 pose 规范化（非法即丢 pose 字段并在审批卡标警告，不整体拒绝）。

### 4.4 schemas.py 对应

- `PartArtifact` 加 `pose: AssemblyPose | None`；`ArtifactSet` 加 `assembly: AssemblySummary | None`（投影用，非权威）；
- 新模型 `AssemblyPose{position: list[float], rotation_deg: list | None}`、`AssemblySummary{step_file, interference: dict|None, exported_at}`；
- `ProjectState` 加 `assembly: AssemblySummary | None`（D2 投影）。

---

## 5. 内核命令（K 侧，v2.14）

### 5.1 `export_assembly`（server cmd）

```
payload: {parts: [{path, name, color?, pose:{position, rotation_deg?}}],
          out_step: str, out_stl: str|None}
```

实现（`assembly_scene.py`）：
1. 逐件 `import_step(path)`，`rotate+translate`（与 `assemble` 同款变换语义，保证与内核既有习惯一致）；
2. 每件 `Part(label=name)`（build123d 支持 label 进 XCAF 名字），聚合为顶层 `Compound(label="assembly")`；
3. `export_step(compound, out_step)` → XCAF 装配树（§2.2）；`out_stl` 时逐件 `export_stl` 到临时文件再二进制拼接为**一个多实体 STL**（Three.js STLLoader 可解析多 solid 的 ASCII/binary STL；前端按件拆组需要 manifest 顺序对齐，或退化为逐件 STL 列表——**采用逐件 STL + 前端叠加**，避免拼接歧义，见 §6.3）；
4. 返回 `{ok, solids, volume, bbox, step_bytes}`。

约束：件数上限 32；单件 import 失败 → 整命令失败（列明坏件）；不读写 kernel 实例状态。

### 5.2 `assembly_interference`（server cmd）

```
payload: {parts: [同 5.1], tolerance: 0.001,
          expected_overlaps: [{a, b, max_volume_mm3, reason}]}   ← 齿轮啮合区豁免表
```

实现：import+摆位后调 `collision.check_assembly_interference`（`K/mech_kernel/collision.py:150-191`），叠加豁免：命中 expected_overlaps 的对降级为 `exempted`。返回逐对 `{name_a,name_b,interfering,volume_mm3,center}` + 汇总。
性能预案：先做 **bbox 预过滤**（新增薄函数，不动 collision.py 契约）再求交；N>16 时报告标注耗时预算；重合体 boolean 不可靠（§2.4）→ 结果带 `error` 字段透传，报告如实呈现。

### 5.3 权限

两个命令是 **server 命令**（同 `run_script`/`reset`），不进 PUBLIC_OPS/EXPERIMENTAL_OPS——不触碰 `test_v4_profiles.py:66-71` 锁定的能力集契约。

---

## 6. IDE 变更清单（A 侧，v0.14）

### 6.1 后端

| # | 位置 | 改动 |
|---|---|---|
| 1 | `agent/loop.py` `finish_part` | 归档目标从 run 目录改为**项目零件库**（新增 `parts_library` 参数注入 run_dir 旁路；run 目录保留硬链/副本以兼容现有下载路由）；写 manifest（D2 双写序）；part_rec 加 pose（从 session.plan.bom 按零件名取） |
| 2 | `agent/loop.py` 新合成工具 `export_assembly` | 全部零件 completed 后可用（`_plan_has_pending` 复用）；调 §5 两命令；渲染整装配四视角（复用 `render_snapshot` 对装配 STEP 的临时会话？**否**——用 worker 新 RPC `render_file`：import STEP 后纯渲染不落状态，避免动 `_current_geometry`）；写交付报告 + `assembly_ready` WS 事件 |
| 3 | `kernel_worker.py` | 新 RPC 封装 `export_assembly` / `assembly_interference` / `render_file`（echo 测试同 run_script 模式） |
| 4 | `main.py` | `_commit_kernel_state_snapshot` **修复丢 parts bug**（`:866` 重建 ArtifactSet 时回写 manifest 投影）；`GET /api/projects/{id}` 带 `assembly` 投影；新 REST：`GET /api/projects/{id}/assembly/manifest`、`POST …/assembly/export`（手动重导）、`GET …/artifacts/assembly/{filename}`（库文件下载） |
| 5 | `schemas.py` | §4.4 新模型 |
| 6 | `agent/session.py` | `parts_library` 顶层字段 + `update_part_entry()/set_assembly()`（与 set_plan 解耦） |
| 7 | `prompts.yaml/_en` | BOM pose 要求 + 收尾 export_assembly 协议；逐件禁令不动 |

### 6.2 agent 会话流程（端到端）

```
调研(design_calculate) → ask_user → propose_plan(bom 带 pose) → 批准
→ 逐件建模 finish_part（写零件库+manifest，v0.13 双复检不变）
→ 全部完成 → export_assembly（装配 STEP + 干涉 + 四视角图 + 交付报告）→ 总结
```

### 6.3 前端

| # | 位置 | 改动 |
|---|---|---|
| 1 | `Viewport.tsx` | props 增 `models?: {url, pose?, name?, color?, visible?}[]`；多件模式：逐件 loadAsync 进 `THREE.Group`、每件独立材质、**跳过 `geometry.center()`**（`:153`，改为按 manifest 位姿摆放）、fitCamera 对 Group 包围盒；单 stlUrl 路径完全保留（向后兼容） |
| 2 | `App.tsx` | `assembly` 投影存在且非编辑态 → 视口切装配模式（parts[] 映射 `{url: 库 STL, pose}`）；`assembly_ready` 事件刷新投影；artifact-row 增"装配 STEP / 干涉报告"链接 |
| 3 | 零件库面板（StructurePanel 新 tab） | 件列表：显隐 checkbox、点选高亮（材质 emissive）、版本角标 |
| 4 | `api.ts` | `AssemblyPose/AssemblySummary/ManifestPart` 类型；`projectAssemblyExport()` |
| 5 | 测试 | Viewport 多件渲染（pose 摆放不 center）、面板显隐、装配模式切换；vitest 全绿 |

---

## 7. 测试与验收

**内核（K）**：`test_assembly_scene.py`——export_assembly（两分离件 → STEP 含两具名产品节点：用 `STEPCAFControl_Reader` 回读断言 XCAF 结构；体积=可加；坏路径报错清单）、interference（相交对/分离对/豁免表/bbox 预过滤等价性）、32 件上限。既有 386 项零回归（不碰锁定契约）。

**aicad（A）**：loop 测试（BOM pose 规范化、finish_part 写库+manifest 双写、export_assembly 门控"全部 completed"、投影修复回归 `_commit_kernel_state_snapshot` 不丢 parts）；RPC echo 测试；前端 vitest。

**E2E 零 token**：`scripts/e2e_assembly_flow.py`——脚本化决策：3 件（板+两凸台，故意一处重叠）→ finish_part×3 → export_assembly → 断言：manifest 3 件带 pose、装配 STEP 回读 3 具名产品、干涉报告命中重叠对、四视角图生成。

**真实 LLM 验收**：Qwen/deepseek-v4.1 跑"1:100 变速箱"→ 11 件 + 装配导出 + 干涉报告（齿轮啮合豁免表由调研中心距自动生成）→ 交付包五件套齐 + 整装配四视角图人工目检。**这是 F2 的完成定义。**

---

## 8. F2b 预留（只写接口，不实现）

- 内核 `_parts: Dict[str, PartRecord]` 注册表 + active-part 指针（调研结论：`_current_geometry` 保持"active part 投影"语义，~90 个读点零改动；`_snapshot/_restore` 各加一键，§2.3 先例）；
- manifest → `_parts` 的单向升级路径：F2a 的 pose 字段与 F2b 的 `AssemblyInstance` 位姿格式**同构**（`rotation_deg:[angle,[ax,ay,az]]`），届时只搬持久层不改数据；
- `depends_on` → 装配顺序图 → 未来约束求解的输入；
- 装配重放：把"零件身份 + 位姿"作为可重放 op 记录（替代现在 assemble 的外部路径不可重放）。

## 9. 开放决策（待拍板，默认=推荐）

| 决策 | 选项 | 推荐 |
|---|---|---|
| F2 深度 | A 装配视图（本文）/ B 直接参数化装配 / C 分期 | **A**（B 量级 2-3×F1，且 A 的 manifest 是 B 的子集不返工） |
| 位姿来源 | 模型从调研算 / 手工 UI / 约束求解 | **模型从调研算**（UI 微调可作为 F2a 之后的附加项） |
| 干涉进 F2？ | 进 / 留 F3 | **进**（collision 现成，纯后端，成本低、交付价值高） |

## 10. 风险登记

| 风险 | 缓解 |
|---|---|
| N² boolean 性能（demo14 实测 22s/对量级） | bbox 预过滤 + 32 件上限 + 报告标注耗时；豁免表减少无谓求交 |
| OCC 重合体求交不可靠（§2.4） | error 字段透传、报告如实呈现，不掩盖 |
| manifest/session 双写不一致 | 写序固定（session 先、库文件后）+ 启动时以库文件为准做 reconcile |
| 浅拷贝坑（§2.3） | manifest 对象一律深拷贝或不可变 dict |
| 零件重做后旧版本文件膨胀 | 库目录保留全部版本（可审计），UI 只显示 current；清理策略留后 |
| 前端多 STL 叠加性能 | 11 件 × 每件 ≤50MB STL 会爆——归档 STL 加 `tolerance` 粗化参数（export_mesh 已支持 tolerance 入参，装配预览用 0.05 档） |
