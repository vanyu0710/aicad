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
    const ask: Approval = { ...base, kind: "ask_user", op: "ask_user", message: "孔径？", options: { questions: [{ id: "q1", question: "孔径？", type: "text" }] } };
    renderPanel([ask]);
    expect(screen.getByText("提问")).toBeInTheDocument();
  });

  it("ask_user single choice submits answers via edit", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    const ask: Approval = {
      ...base,
      kind: "ask_user",
      op: "ask_user",
      message: "孔直径多少？",
      options: { questions: [{ id: "q1", question: "孔直径多少？", type: "single", options: [{ label: "6mm" }, { label: "8mm" }], required: true }] },
    };
    renderPanel([ask], onResolve);
    const submit = screen.getByRole("button", { name: "提交答案" });
    expect(submit).toBeDisabled(); // 必答未答
    await user.click(screen.getByText("8mm"));
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(onResolve).toHaveBeenCalledWith(ask, "edit", { answers: { q1: "8mm" } });
  });

  it("ask_user multi + other free text merges into array", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    const ask: Approval = {
      ...base,
      kind: "ask_user",
      op: "ask_user",
      message: "需要哪些孔？",
      options: { questions: [{ id: "holes", question: "需要哪些孔？", type: "multi", options: [{ label: "中心孔" }, { label: "螺栓孔" }], required: false, allowFreeText: true }] },
    };
    renderPanel([ask], onResolve);
    await user.click(screen.getByText("中心孔"));
    await user.type(screen.getByLabelText("其他（自定义）"), "定位销孔");
    await user.click(screen.getByRole("button", { name: "提交答案" }));
    const answers = onResolve.mock.calls[0][2].answers;
    expect(answers.holes).toEqual(["中心孔", "定位销孔"]);
  });

  it("ask_user skip resolves reject", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    const ask: Approval = {
      ...base,
      kind: "ask_user",
      op: "ask_user",
      message: "厚度？",
      options: { questions: [{ id: "t", question: "厚度？", type: "single", options: [{ label: "10" }, { label: "12" }] }] },
    };
    renderPanel([ask], onResolve);
    await user.click(screen.getByRole("button", { name: "跳过" }));
    expect(onResolve).toHaveBeenCalledWith(ask, "reject");
  });

  it("renders the plan BOM with quantities and grouped steps", () => {
    const plan: Approval = {
      ...base,
      kind: "plan_review",
      op: "propose_plan",
      message: "两级减速",
      options: {
        plan: {
          summary: "两级减速",
          bom: [
            { id: "p1", part: "小齿轮", role: "高速级", quantity: 2, key_params: { 模数: "2", 齿数: "20" } },
            { id: "p2", part: "箱体" },
          ],
          steps: [
            { id: "s1", title: "建小齿轮", part: "小齿轮", op: "make_gear", status: "pending" },
            { id: "s2", title: "建箱体", part: "箱体", status: "pending" },
          ],
        },
      },
    };
    renderPanel([plan]);
    expect(screen.getByText("零件清单")).toBeInTheDocument();
    expect(screen.getByText("×2")).toBeInTheDocument();
    expect(screen.getByText(/模数=2/)).toBeInTheDocument();
    expect(screen.getAllByText("箱体").length).toBeGreaterThanOrEqual(2);
    // 步骤按零件分组渲染（零件名同时出现在 BOM 与组标题）
    expect(screen.getAllByText("小齿轮").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("建小齿轮")).toBeInTheDocument();
  });

  it("renders ungrouped plan steps when no bom", () => {
    const plan: Approval = {
      ...base,
      kind: "plan_review",
      op: "propose_plan",
      message: "单件",
      options: {
        plan: {
          summary: "单件",
          steps: [{ id: "s1", title: "建底板", status: "pending" }],
        },
      },
    };
    renderPanel([plan]);
    expect(screen.queryByText("零件清单")).not.toBeInTheDocument();
    expect(screen.getByText("建底板")).toBeInTheDocument();
  });
});
