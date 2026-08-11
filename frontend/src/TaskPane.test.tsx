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

  it("switches to export and renders download links", async () => {
    const user = userEvent.setup();
    renderPane();
    await user.click(screen.getByRole("tab", { name: "导出" }));
    expect(screen.getByText("导出产物")).toBeInTheDocument();
    expect(screen.getByText("STEP")).toBeInTheDocument();
    expect(screen.getByText("STL")).toBeInTheDocument();
  });
});
