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
