import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ApprovalPanel from "./ApprovalPanel";
import type { Approval } from "./api";

const base: Approval = {
  approval_id: "approval-1",
  kind: "destructive_op",
  op: "delete_feature",
  args: { feature_id: "F_0001" },
  message: "即将删除特征 F_0001，是否继续？",
};

function renderPanel(approvals: Approval[] = [base], onResolve = vi.fn()) {
  return render(<ApprovalPanel approvals={approvals} busy={false} onResolve={onResolve} />);
}

describe("ApprovalPanel", () => {
  it("renders nothing when no approvals", () => {
    const { container } = renderPanel([]);
    expect(container.firstChild).toBeNull();
  });

  it("renders kind, op and message", () => {
    renderPanel();
    expect(screen.getByText("破坏性操作")).toBeInTheDocument();
    expect(screen.getByText("delete_feature")).toBeInTheDocument();
    expect(screen.getByText("即将删除特征 F_0001，是否继续？")).toBeInTheDocument();
  });

  it("approve resolves with approve action", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    renderPanel([base], onResolve);
    await user.click(screen.getByRole("button", { name: "批准" }));
    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve.mock.calls[0][0]).toEqual(base);
    expect(onResolve.mock.calls[0][1]).toBe("approve");
  });

  it("reject resolves with reject action", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    renderPanel([base], onResolve);
    await user.click(screen.getByRole("button", { name: "拒绝" }));
    expect(onResolve).toHaveBeenCalledTimes(1);
    expect(onResolve.mock.calls[0][0]).toEqual(base);
    expect(onResolve.mock.calls[0][1]).toBe("reject");
  });

  it("edit lets user override args and submit edit action", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    renderPanel([base], onResolve);
    await user.click(screen.getByRole("button", { name: "改参" }));
    const input = screen.getByDisplayValue("F_0001");
    await user.clear(input);
    await user.type(input, "F_0002");
    await user.click(screen.getByRole("button", { name: "确认改参" }));
    expect(onResolve).toHaveBeenCalledWith(base, "edit", { feature_id: "F_0002" });
  });

  it("marks ask_user kind label", () => {
    const ask: Approval = { ...base, kind: "ask_user", op: "ask_user", args: { question: "孔径？" } };
    renderPanel([ask]);
    expect(screen.getByText("提问")).toBeInTheDocument();
  });
});
