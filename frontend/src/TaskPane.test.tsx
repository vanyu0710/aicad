import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TaskPane from "./layout/TaskPane";
import { useAppStore } from "./store";

beforeEach(() => {
  useAppStore.setState({
    ui: {
      leftTab: "feature",
      rightTab: "assistant",
      leftDrawerOpen: false,
      rightDrawerOpen: false,
      leftWidth: 380,
      rightWidth: 360,
      focusMode: false,
      settingsOpen: false,
      commandTab: "features",
    },
  });
});

function renderPane() {
  return render(
    <TaskPane
      busy={false}
      chatMessage=""
      chat={[]}
      events={["worker ok"]}
      processSteps={[]}
      featurePlan={null}
      questions={[]}
      reportMarkdown=""
      review={{ warnings: ["壁厚偏薄"], suggestions: ["增加圆角"], manufacturability: [], standards: [], blocking: [] }}
      unresolved={[]}
      runId="run1"
      engineLabel="Build123d Worker（受控执行）"
      onChatMessageChange={vi.fn()}
      onClarificationContinue={vi.fn()}
      onSendChat={vi.fn()}
      onSelectProcessStep={vi.fn()}
    />,
  );
}

describe("TaskPane", () => {
  it("shows the AI assistant tab by default", () => {
    renderPane();
    expect(screen.getByRole("tab", { name: "AI 助手" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/把中心孔改成 12mm/)).toBeInTheDocument();
  });

  it("closes the drawer from the header close button", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <TaskPane
        busy={false}
        chatMessage=""
      chat={[]}
        events={[]}
        processSteps={[]}
        featurePlan={null}
        questions={[]}
        reportMarkdown=""
        review={undefined}
        unresolved={[]}
        runId=""
        engineLabel="Build123d Worker（受控执行）"
        onChatMessageChange={vi.fn()}
        onClarificationContinue={vi.fn()}
        onSendChat={vi.fn()}
        onSelectProcessStep={vi.fn()}
        onClose={onClose}
      />,
    );
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("switches to design review", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("tab", { name: "设计评审" }));
    expect(screen.getByText("壁厚偏薄")).toBeInTheDocument();
    expect(screen.getByText("增加圆角")).toBeInTheDocument();
  });

  it("shows the process timeline and selects a feature from a step", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    useAppStore.setState({ ui: { ...useAppStore.getState().ui, rightTab: "process" } });
    render(
      <TaskPane
        busy={false}
        chatMessage=""
      chat={[]}
        events={[]}
        processSteps={[
          {
            id: "step-1",
            stage: "cad",
            status: "failed",
            label: "CAD 建模",
            summary: "特征 top_groove 执行失败",
            detail: "groove position exceeds the base length",
            feature_id: "top_groove",
            operation: "update",
            started_at: "2026-08-11T00:00:00Z",
            warnings: [],
          },
        ]}
        featurePlan={null}
        questions={[]}
        reportMarkdown=""
        review={undefined}
        unresolved={[]}
        runId=""
        engineLabel="Build123d Worker（受控执行）"
        onChatMessageChange={vi.fn()}
        onClarificationContinue={vi.fn()}
        onSendChat={vi.fn()}
        onSelectProcessStep={onSelect}
      />,
    );
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("groove position exceeds the base length")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /CAD 建模/ }));
    expect(onSelect).toHaveBeenCalledWith("top_groove");
  });

  it("switches to export and renders download links", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("tab", { name: "导出" }));
    expect(screen.getByText("导出产物")).toBeInTheDocument();
    expect(screen.getByText("STEP")).toBeInTheDocument();
    expect(screen.getByText("STL")).toBeInTheDocument();
  });
});

describe("TaskPane v0.10 agent chat stream", () => {
  const chatStream = [
    { id: "c1", role: "user" as const, text: "做一个法兰，中心孔 30mm", hasImage: true, status: "done" as const, tools: [], snapshots: [] },
    {
      id: "c2",
      role: "assistant" as const,
      text: "好的，先建基准面。",
      hasImage: false,
      status: "streaming" as const,
      tools: [
        { step: 1, op: "create_workplane", argsPreview: '{"name":"base"}', success: true, summary: "", autofix: false },
        { step: 2, op: "extrude", argsPreview: "{}", success: false, summary: "depth 必须大于 0", autofix: true },
      ],
      snapshots: ["/api/artifacts/run9/snapshot_s2"],
    },
  ];

  it("renders user/assistant bubbles with tool cards", () => {
    render(
      <TaskPane
        busy={false}
        chatMessage=""
        chat={chatStream}
        events={[]}
        processSteps={[]}
        featurePlan={null}
        questions={[]}
        reportMarkdown=""
        review={undefined}
        unresolved={[]}
        runId=""
        engineLabel="Build123d Worker（受控执行）"
        onChatMessageChange={vi.fn()}
        onClarificationContinue={vi.fn()}
        onSendChat={vi.fn()}
        onSelectProcessStep={vi.fn()}
      />,
    );
    expect(screen.getByText("做一个法兰，中心孔 30mm")).toBeInTheDocument();
    expect(screen.getByText(/先建基准面/)).toBeInTheDocument();
    expect(screen.getByText("create_workplane")).toBeInTheDocument();
    expect(screen.getByText('{"name":"base"}')).toBeInTheDocument();
    expect(screen.getByText("extrude ·fix")).toBeInTheDocument();
    expect(screen.getByText("已附草图")).toBeInTheDocument();
    expect(document.querySelector(".chat-caret")).not.toBeNull();
    expect(document.querySelector(".chat-snapshot")).not.toBeNull();
  });

  it("shows the queued hint while the agent is running", () => {
    useAppStore.setState({ agentRunning: true });
    render(
      <TaskPane
        busy={false}
        chatMessage=""
        chat={[]}
        events={[]}
        processSteps={[]}
        featurePlan={null}
        questions={[]}
        reportMarkdown=""
        review={undefined}
        unresolved={[]}
        runId=""
        engineLabel="Build123d Worker（受控执行）"
        onChatMessageChange={vi.fn()}
        onClarificationContinue={vi.fn()}
        onSendChat={vi.fn()}
        onSelectProcessStep={vi.fn()}
      />,
    );
    expect(screen.getByText(/将在当前步骤结束后生效/)).toBeInTheDocument();
  });
});

describe("TaskPane v0.6 report", () => {
  it("shows four-dimensional execution report and evidence", async () => {
    const user = userEvent.setup();
    useAppStore.setState({ ui: { ...useAppStore.getState().ui, rightTab: "review" } });
    render(
      <TaskPane
        busy={false}
        chatMessage=""
      chat={[]}
        events={[]}
        processSteps={[]}
        featurePlan={null}
        questions={[]}
        reportMarkdown=""
        executionReport={{
          execution_ok: true,
          plan_complete: true,
          geometry_valid: true,
          production_ready: false,
          fallback_used: false,
          skipped_features: ["hole_2"],
          failed_features: [],
          assumption_count: 3,
          completeness_score: 85,
          engine: "build123d",
          details: ["hole_2 skipped"],
        }}
        evidence={[{ key: "outer_diameter", value: 50, unit: "mm", source: "user", confirmed_by_user: true }]}
        evidenceConflicts={[]}
        designIntent={{ summary: "tube with internal annular groove", function: "sealing", manufacturing_intent: "machined" }}
        review={undefined}
        unresolved={[]}
        runId=""
        engineLabel="Build123d Worker（受控执行）"
        onChatMessageChange={vi.fn()}
        onClarificationContinue={vi.fn()}
        onSendChat={vi.fn()}
        onSelectProcessStep={vi.fn()}
      />,
    );
    expect(screen.getByText("执行验收报告")).toBeInTheDocument();
    expect(screen.getByText("可生产")).toBeInTheDocument();
    expect(screen.getByText("完整度评分：85%")).toBeInTheDocument();
    expect(screen.getByText(/跳过：hole_2/)).toBeInTheDocument();
    expect(screen.getByText("设计意图")).toBeInTheDocument();
  });
});