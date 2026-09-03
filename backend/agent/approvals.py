"""Agent 审批中枎（P2 人机协作确认点）。

agent loop 在需要用户确认时调用 ``ApprovalBroker.request()``，该调用阻塞等待
用户通过 REST 端点 ``/agent/resolve`` 给出答复。每条请求有唯一 id、超时；
超时返回一个"用户未响应"的结果，让 agent 跳过该步而非卡死会话。

线程模型：
- ``request()`` 在 agent 线程调用，阻塞在 threading.Event。
- ``resolve()`` 由 FastAPI 的请求线程调用，设置 Event 并回填结果。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

_TIMEOUT_ENV = "MECHCAD_AGENT_APPROVAL_TIMEOUT"
_DEFAULT_TIMEOUT = 600.0

# 动作语义（与前端 /agent/resolve 契约一致）
ALLOWED_ACTIONS = {"approve", "reject", "edit"}


def _timeout_seconds() -> float:
    import os

    try:
        return max(0.0, float(os.getenv(_TIMEOUT_ENV, str(_DEFAULT_TIMEOUT))))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


@dataclass
class ApprovalRequest:
    """一次待审批请求，agent 线程阻塞等待它的答复。"""

    approval_id: str
    kind: str          # "destructive_op" | "destructive_fix" | "ask_user"
    op: str            # 目标 op 名（ask_user 时是 "ask_user"）
    args: dict[str, Any]  # 待执行参数 / 问题上下文
    message: str
    options: dict[str, Any] = field(default_factory=dict)
    requested_at: float = field(default_factory=time.time)
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _result: dict[str, Any] | None = field(default=None, repr=False)


class ApprovalBroker:
    """线程安全的请求↔答复映射。一个 agent 会话一个 broker 实例。"""

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = float(timeout) if timeout is not None else _timeout_seconds()
        self._requests: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"approval-{int(time.time() * 1000)}-{self._seq}"

    def request(
        self,
        *,
        kind: str,
        op: str,
        args: dict[str, Any],
        message: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发起一次审批并阻塞等待结果。

        返回 dict：
        - {"action": "approve", "args": {...}}  —— 用户确认，按该参数继续
        - {"action": "edit",   "args": {...}}   —— 用户改参后确认
        - {"action": "reject", "message": ...}  —— 用户拒绝，agent 应跳过
        - {"action": "timeout", "message": ...} —— 用户未在超时内响应
        """
        request = ApprovalRequest(
            approval_id=self._next_id(),
            kind=kind,
            op=op,
            args=dict(args or {}),
            message=message,
            options=dict(options or {}),
        )
        with self._lock:
            self._requests[request.approval_id] = request
        timed_out = not request._event.wait(timeout=self._timeout)
        with self._lock:
            self._requests.pop(request.approval_id, None)
        if timed_out:
            return {"action": "timeout", "message": f"用户未在 {self._timeout:.0f}s 内响应，已跳过该步骤"}
        return dict(request._result or {"action": "reject", "message": "无结果"})

    def resolve(self, approval_id: str, action: str, args_override: dict[str, Any] | None = None) -> dict[str, Any]:
        """用户答复。返回与决议一致的确认信息；审批不存在时抛 KeyError。"""
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"无效动作: {action}（可为 approve/reject/edit）")
        with self._lock:
            request = self._requests.get(approval_id)
        if request is None:
            raise KeyError(f"审批不存在或已过期: {approval_id}")
        if action == "edit":
            if not args_override:
                raise ValueError("动作 edit 需要 args_override")
            result = {"action": "edit", "args": dict(args_override)}
        elif action == "approve":
            result = {"action": "approve", "args": dict(request.args)}
        else:
            result = {"action": "reject", "message": "用户拒绝该步骤"}
        request._result = result
        request._event.set()
        return result
