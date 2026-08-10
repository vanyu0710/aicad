# MechCAD IDE 界面与操作逻辑优化指南

> 交给后续 AI/开发模型执行。目标不是单纯“换一套 CSS”，而是让用户能清楚地完成：配置模型 -> 上传图纸 -> 处理缺失信息 -> 生成模型 -> 检查特征 -> 修改并重新建模。
>
> 适用仓库：`devops-mechcad-mvp-hugging-face-space`
> 主线：`frontend/` React + TypeScript；`backend/` FastAPI；`cad_worker/` 受控 CAD 执行器。
> 旧版：`legacy/gradio/` 只作参考，不要把旧 Gradio 交互搬回主线。

## 1. 先修复再优化

### 1.1 统一 UTF-8

当前部分文件读取时出现类似“涓ユ牸妯″紡”“鐢熸垚妯″瀷”的乱码。任何 UI 优化之前，必须确认：

- `frontend/src/*.tsx`、`frontend/src/*.ts`、`frontend/src/*.css` 均为 UTF-8（无 BOM 也可以，但全仓库保持一致）。
- `HANDOFF.md` 和新文档也使用 UTF-8。
- 浏览器页面、FastAPI JSON、WebSocket 消息都显示正常中文。
- 不要通过在字符串中替换乱码片段来“修复”；应从 UTF-8 源文本重新写入对应字符串。
- 加一个最小检查：构建后页面中应能搜索到“严格模式”“智能模式”“模型配置”“待确认问题”，且不出现 `锟`、`鐢`、`涓` 等明显乱码片段。

### 1.2 先定义状态，不靠按钮颜色猜状态

页面必须明确显示以下互斥状态之一：

```text
empty              尚未输入
ready              输入完整，可以生成
analyzing          正在读图
planning           正在规划特征
awaiting_questions 有必答问题，暂不能执行
building           CAD 正在执行
ready_to_review    模型已生成，等待检查
failed             本次执行失败
```

状态来自后端事件和 `ProjectState`，前端可以派生显示，但不能把 `busy=true` 当作完整业务状态。所有异步按钮都必须有：禁用条件、处理中显示、失败后恢复、成功后反馈。

## 2. 设计原则

1. 新手只需回答“我要做什么、图纸在哪里、使用严格还是智能模式”，其余配置有清晰默认值。
2. 专业用户可以完整控制视觉模型和规划模型，但高级字段必须分组，不得全部塞在一个窄栏中。
3. 每个动作都说明作用对象和后果，例如“保存参数并重新建模”，而不是笼统的“保存”。
4. 严格模式绝不偷偷补数；智能模式可以提出建议，但建议必须经过用户确认才进入可执行 FeaturePlan。
5. 任何模型失败、CAD 失败、网络失败都要显示真实阶段和可行动的下一步，不显示“生成成功”或伪造产物。
6. 保留水墨/宣纸气质，但优先保证信息层级、可读性和控件可用性；不要为了装饰降低对比度。

## 3. 推荐页面结构

当前四区结构可保留，但应改成明确的工作台流程：

```text
顶部：项目名 | 当前状态 | 严格/智能模式 | 撤销/重做 | 生成/重新生成
左栏：输入与项目设置
中栏：3D 视口 + 视图工具 + 产物下载
右栏：当前特征属性 / 尺寸证据
底栏：待确认问题 | AI 对话 | 设计评审 | 执行日志（使用 Tab）
```

### 3.1 顶部

- 项目名称可编辑，旁边显示“未保存修改/已同步”状态。
- “生成模型”是唯一主操作按钮；存在必答问题时显示“先回答问题”，不可提交。
- 撤销/重做显示 tooltip 和禁用原因。
- 右上角显示 CAD 后端来源：`受控 Worker`、`Build123d` 或 `FreeCAD`，不能把 fallback 隐藏成成功。

### 3.2 左栏：输入与模型配置

把左栏分成两个可折叠区，默认展开“输入”，模型配置在用户主动打开后展开。

输入区包含：

- 图片拖拽区，显示缩略图、文件名、尺寸和“移除”按钮。
- 功能描述 textarea，提供一条真实示例，不要用难以理解的内部 JSON。
- 已识别摘要：零件类型、已读到的尺寸、尚未确认的尺寸。
- 模式用 segmented control 或 radio 卡片：严格 / 智能。每个模式下方只显示三行解释。

模型配置区必须彻底拆成两个独立卡片：

```text
视觉读图模型
  服务商 Provider
  协议 OpenAI 兼容 / Anthropic
  Base URL
  模型名称
  API Key（密码框，显示已配置/未配置，不回显密钥）
  测试连接

建模规划模型
  服务商 Provider
  协议 OpenAI 兼容 / Anthropic
  Base URL
  模型名称
  API Key（密码框，显示已配置/未配置，不回显密钥）
  测试连接
```

每个字段旁边都要有简短帮助文本：

- Provider 是显示名称/预设，不限制厂商；至少支持“自定义 OpenAI 兼容”“自定义 Anthropic”，可预留常见服务商。
- OpenAI 兼容 Base URL 通常填写到 API 根路径；Anthropic 兼容服务常见为服务根路径。不要强制所有供应商使用同一个 `/v1` 规则。
- “留空则使用 `.env` 默认配置”；填写后只影响当前项目。
- API Key 输入框使用 `type=password`，后端 ProjectState 不得返回明文 key。
- 配置变更后显示“未应用到本次生成”，只有点击“应用配置”或“生成模型”才提交。

推荐配置交互：

- Provider 选择预设时只自动填入 Base URL、协议、模型示例，不覆盖用户已经输入的 API Key。
- 切换协议时更新 placeholder 和帮助文案，不删除已有字段。
- 测试连接只发送最小文本请求，不上传用户图片，不触发 CAD。
- 测试结果必须按角色分别显示：成功、401/403、HTML 响应、网络不可达、模型不存在、JSON 解析失败。
- 失败提示要包含角色、状态码、content-type、请求地址诊断和建议动作，但绝不包含 API Key。
- 专业配置可以折叠；API Key 和 Base URL 不应藏在用户找不到的弹窗中。

## 4. 模型配置 API 约定

现有 `ModelConfig` 已有双角色字段，后续模型应补充通用测试接口，而不是把测试逻辑写在前端：

```text
POST /api/model/test
body: {
  role: "vision" | "planner",
  config: ModelConfig
}
response: {
  ok: boolean,
  role: string,
  provider: string,
  protocol: string,
  model: string,
  message: string,
  diagnostics: {
    status_code?: number,
    content_type?: string,
    endpoint?: string,
    used_env_fallback: boolean
  }
}
```

要求：

- 后端复用 `backend/mechcad_ai/client.py` 的协议和 URL 解析逻辑。
- 统一处理 trailing slash、`/v1`、Anthropic `/messages` 和 OpenAI `/chat/completions`。
- 返回脱敏配置摘要，不返回密钥。
- `force_real_api=true` 时，测试或生成失败必须失败，不得静默切回本地 stub。
- 前端显示的“连接成功”只代表最小请求成功，不代表视觉读图或 CAD 建模一定成功。

## 5. 问题确认必须让用户看懂

当前问题区不能只显示 `dimension_refs` 或内部 feature id。每个问题应展示：

```text
问题：顶部环槽的槽宽是多少？
为什么问：图中能看到槽，但没有可靠读出宽度；严格模式不会猜测。
影响：不确认时只生成管体，槽不会进入可执行模型。
可选答案：[5 mm] [8 mm] [自定义输入]
关联特征：顶部环槽
```

后端 `ClarificationQuestion` 应逐步增加：`reason`、`impact`、`answer_type`、`default_value`、`unit`、`answer`。前端使用数字输入、单位、单选或下拉，不要求用户理解 JSON。问题回答后要有“确认并继续建模”按钮，回答必须进入 `clarification_answers` 并留下用户证据。

严格模式：缺尺寸就阻塞，回答前不生成该特征。

智能模式：可显示“建议值”，但必须有“接受建议”或“输入实际值”；未接受的建议只能显示在 `assumptions/design_review`，不得执行。

## 6. 生成与修改流程

### 首次生成

```text
上传图片 -> 输入描述 -> 选择模式 -> 检查模型配置 -> 生成
-> 读图摘要 -> 显示待确认问题 -> 用户回答
-> 生成 FeaturePlan -> 校验 -> CAD Worker -> 预览和下载
```

不要在用户未看到识别摘要和问题时直接跳到“成功”。如果没有图片，也要明确说明正在使用文字描述，不能暗示已经完成视觉识别。

### 特征树与属性修改

- 特征树显示序号、中文名称、操作类型、状态：已执行/待确认/跳过/失败。
- 选中某特征后，右栏分成“参数”“定位”“证据”“依赖”“执行状态”。
- 尺寸显示数值、单位、来源标签：图纸/用户/推断/派生/未知。
- 修改前后显示差异；提交按钮文案为“保存并重新建模”。
- 如果修改导致依赖失效，阻止提交并解释受影响特征。
- 删除特征必须二次确认并列出会被删除或重新计算的子特征。

### AI 对话

- 聊天输入只接受自然语言，例如“把中心孔改成 12 mm”。
- 发送后显示“解析修改 -> 校验依赖 -> 重新执行”的阶段。
- AI 必须返回完整 FeaturePlan，保持已有 feature_id，不输出 diff 或任意 Python。
- 不确定时先产生问题，不得用默认值掩盖。
- 聊天修改、属性修改、问题确认都产生 DesignSnapshot，可撤销/重做。

## 7. 视口、产物和报告

- 3D 视口初始加载 STL/OBJ 后自动居中和适配尺寸；空白时显示原因：无产物、下载失败、解析失败或模型几何为空。
- 增加基础视图动作：适配、前视、俯视、等轴测、线框切换、显示/隐藏网格。图标按钮必须有 tooltip。
- 当前特征在视口中可高亮；第一版无法高亮时，至少同步选中特征树和属性面板。
- 下载区域按“可编辑交换格式 STEP”“预览 STL/OBJ”“执行报告”分组。
- 报告区必须明确：已执行特征、跳过特征、跳过原因、未解决项、CAD 后端、run_id、失败日志。

## 8. 水墨视觉规范

- 维持宣纸白、淡青、墨色、朱印四类颜色，但文字对比度优先。
- 页面主体不使用营销式 hero，不使用大面积卡片堆叠或装饰性渐变球。
- 面板边界轻、圆角不超过 8px；主按钮可用朱红，危险操作使用更明确的文字。
- 所有控件在 1180px 以上桌面稳定布局；小于该宽度要进入响应式单栏或抽屉，不允许横向滚动遮挡主要操作。
- `prefers-reduced-motion` 时关闭漂移、入场和过渡动画。
- 下拉框、密码框、文件框、数字输入都必须有可见标签和键盘焦点样式。

## 9. 建议组件拆分

不要继续把所有交互堆在 `App.tsx`。建议拆为：

```text
AppShell
TopBar
ProjectInputPanel
ModeSelector
ModelConfigPanel
RoleModelCard
ConnectionTestResult
FeatureTree
FeaturePropertyPanel
DimensionEvidence
ViewportToolbar
ClarificationPanel
ChatPanel
DesignReviewPanel
ExecutionLogPanel
```

把 API 请求、WebSocket 订阅、项目状态和表单临时状态分开。模型配置表单允许暂存，只有应用/生成时写入项目状态；请求期间避免通过多个 setState 造成旧配置覆盖新配置。

## 10. 错误与防误操作清单

- 后端未启动：顶部显示“无法连接后端 8001”，给出启动命令。
- WebSocket 断开：REST 仍可用时显示降级状态，不阻塞页面。
- API 403：显示服务商、角色、endpoint、认证建议。
- 返回 `text/html`：提示 Base URL 可能填成网页地址或缺少 API 路径。
- 返回非 JSON：保留 HTTP 状态和 content-type，前端不要直接展示 `JSON.parse` 原始异常。
- CAD 失败：保留真实 worker 日志，不生成下载链接，不显示“模型已生成”。
- 没有尺寸：严格模式禁用建模；智能模式只允许提交已确认的补全。
- 所有按钮在 busy 时防重复提交；网络失败后可重试，不清空用户输入。

## 11. 实施顺序

### P0：可理解、可配置

1. 修复全仓库 UTF-8 和中文文案。
2. 重构 `App.tsx` 的流程区和模型配置区。
3. 增加双角色 Provider/协议/Base URL/模型/API Key 表单。
4. 增加 `/api/model/test` 和脱敏响应。
5. 增加统一状态条、错误诊断、空状态。

### P1：可确认、可修改

1. 扩展澄清问题数据模型和回答 API。
2. 将问题改成中文工程问答控件。
3. 特征树增加状态、中文名称、依赖和尺寸来源。
4. 属性修改、聊天修改、问题确认统一生成快照。

### P2：可检查、可恢复

1. 视口工具栏和特征高亮。
2. 报告分组展示执行/跳过/失败。
3. 撤销/重做反馈和分支覆盖提示。
4. Playwright 端到端测试。

## 12. 验收标准

### 模型配置

- 新用户能在 30 秒内找到两个角色的 API 配置入口。
- 可分别配置视觉和规划模型，支持 OpenAI 兼容与 Anthropic。
- 测试连接不会触发建模，不泄露 API Key。
- 空字段明确提示使用 `.env`；填写后能看到“当前项目配置”。
- 403、HTML、非 JSON、网络错误均有可行动提示。

### 建模主流程

- 用户能明确知道下一步是上传、回答问题还是生成。
- 严格模式不会生成未确认尺寸。
- 智能模式的建议值必须显式接受后才执行。
- 管件槽、板件孔、法兰阵列等特征在树、报告、视口和产物状态中保持一致。
- CAD 失败时没有伪成功文件。

### 工程质量

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
cd frontend
npm test
npm run build
```

补充前端测试：模型配置折叠/展开、协议切换、API Key 脱敏显示、测试连接状态、严格/智能模式切换、问题回答、生成中防重复提交、错误恢复。

## 13. 给执行模型的硬性要求

- 先阅读 `HANDOFF.md`、`backend/schemas.py`、`backend/main.py`、`backend/mechcad_ai/client.py`、`frontend/src/App.tsx`，再修改。
- 只在新架构主线修改；不要恢复 Gradio。
- 不要删除或覆盖已有测试；新增测试覆盖新交互。
- 不要把真实 API Key 写入源码、日志、截图、测试 fixture 或 git。
- 不要把“本地 stub 成功”标成真实模型成功。
- 任何暂时不支持的特征都要显示为“未执行/原因”，不可假装完成。
- 完成后必须启动 8001 后端和 5173 前端，检查浏览器真实交互，并报告修改文件、测试结果和剩余风险。

