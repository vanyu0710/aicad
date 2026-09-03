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
