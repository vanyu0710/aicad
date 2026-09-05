import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatColumn from "./layout/ChatColumn";
import { useAppStore } from "./store";

beforeEach(() => {
  useAppStore.setState({ agentRunning: false });
});

function renderColumn(chat: any[] = []) {
  return render(
    <ChatColumn
      chat={chat}
      chatMessage=""
      busy={false}
      engineLabel="Build123d Worker（受控执行）"
      questions={[]}
      imageFile={null}
      planMode={false}
      onPlanModeChange={vi.fn()}
      onClarificationContinue={vi.fn()}
      onChatMessageChange={vi.fn()}
      onSendChat={vi.fn()}
      onImageChange={vi.fn()}
    />,
  );
}

describe("ChatColumn", () => {
  it("renders the chat input and empty hint", () => {
    renderColumn();
    expect(screen.getByPlaceholderText(/做一个法兰盘/)).toBeInTheDocument();
    expect(screen.getByText(/和 Agent 说说要做什么/)).toBeInTheDocument();
  });

  it("renders user/assistant bubbles with tool cards and snapshots", () => {
    renderColumn([
      { id: "c1", role: "user", text: "做一个法兰，中心孔 30mm", hasImage: true, status: "done", tools: [], snapshots: [] },
      {
        id: "c2",
        role: "assistant",
        text: "好的，先建基准面。",
        hasImage: false,
        status: "streaming",
        tools: [
          { step: 1, op: "create_workplane", argsPreview: '{"name":"base"}', success: true, summary: "", autofix: false },
          { step: 2, op: "extrude", argsPreview: "{}", success: false, summary: "depth 必须大于 0", autofix: true },
        ],
        snapshots: ["/api/artifacts/run9/snapshot_s2"],
      },
    ]);
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
    renderColumn();
    expect(screen.getByText(/将在当前步骤结束后生效/)).toBeInTheDocument();
  });

  it("renders the plan checklist with step statuses", () => {
    render(
      <ChatColumn
        chat={[]}
        chatMessage=""
        busy={false}
        engineLabel="Build123d Worker"
        questions={[]}
        imageFile={null}
        planMode={false}
        onPlanModeChange={vi.fn()}
        onClarificationContinue={vi.fn()}
        onChatMessageChange={vi.fn()}
        onSendChat={vi.fn()}
        onImageChange={vi.fn()}
        plan={{ summary: "底板+中心孔", steps: [
          { id: "s1", title: "建底板", status: "completed" },
          { id: "s2", title: "开中心孔", op: "hole", status: "in_progress" },
        ] }}
      />,
    );
    expect(screen.getByText("底板+中心孔")).toBeInTheDocument();
    expect(screen.getByText("建底板")).toBeInTheDocument();
    expect(screen.getByText("开中心孔")).toBeInTheDocument();
    expect(screen.getByText("hole")).toBeInTheDocument();
  });
});
