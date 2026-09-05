"""Agent 会话容器（v0.10 对话式 harness）。

每个项目一条持久会话：``messages`` 即与 LLM 的对话历史（不含 system，
system 由 AgentLoop 在请求时前置），持久化到 ``work/agent_sessions/{id}.json``。
运行中用户插话进入 ``pending`` 队列，AgentLoop 在轮间 drain 后追加为 user
消息，下一轮模型自然可见。

线程模型：
- loop 线程：append / drain_pending / set_status
- FastAPI 请求线程：enqueue_user / view / clear
全部操作都持同一把锁且无阻塞调用，锁只保护内存状态；save 在锁内做
（小 JSON 文件写入，毫秒级）。

消息 content 约定：纯文本消息用字符串；带图片的 user 消息用 OpenAI
content-blocks 数组（``image_url``），由 LLM 客户端在 Anthropic 协议分支
请求时转换为 ``source.base64`` 形式。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMAGE_CLIP = 200_000  # dataURL 超长截断保护（约 150KB 图片）


def _now() -> float:
    return time.time()


def _clip_text(text: str, limit: int = 800) -> str:
    return text if len(text) <= limit else text[:limit] + "…(截断)"


# 任务消息里注入的内核上下文起始标记（展示时从此处截断，只留用户原话）
_CONTEXT_MARKERS = ("\n当前特征历史", "\n当前没有特征", "\n可用 op", "\n（读取当前特征上下文失败")


def _strip_task_context(text: str) -> str:
    """裁掉用户任务消息中注入的内核上下文，只保留用户真正输入的指令文本。"""
    cut = len(text)
    for marker in _CONTEXT_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].strip()


def build_user_message(text: str, image_data_url: str | None = None) -> dict[str, Any]:
    """构造一条 user 消息；带图片时使用 OpenAI content-blocks 形式。"""
    clipped = _clip_text(text or "")
    if image_data_url:
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": clipped},
                {"type": "image_url", "image_url": {"url": image_data_url[:IMAGE_CLIP]}},
            ],
        }
    return {"role": "user", "content": clipped}


@dataclass
class AgentSession:
    """一个项目的 agent 会话：历史消息 + 状态 + 待插话队列。"""

    project_id: str
    path: Path
    messages: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    status: str = "idle"  # idle | running | waiting_approval
    updated_at: float = field(default_factory=_now)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------ 用户入口
    def enqueue_user(self, text: str, image_data_url: str | None = None) -> dict[str, Any]:
        """用户插话：进入 pending 队列，等待 loop 在轮间注入。"""
        message = build_user_message(text, image_data_url)
        with self._lock:
            self.pending.append(message)
            self.updated_at = _now()
            self.save()
        return message

    def drain_pending(self) -> list[dict[str, Any]]:
        """loop 轮间取走全部待注入消息并写入历史。"""
        with self._lock:
            drained, self.pending = self.pending, []
            if drained:
                self.messages.extend(drained)
                self.updated_at = _now()
                self.save()
        return drained

    # ------------------------------------------------------------ loop 入口
    def append(self, message: dict[str, Any]) -> None:
        """loop 追加一条 assistant/tool 消息到历史。"""
        with self._lock:
            self.messages.append(message)
            self.updated_at = _now()
            self.save()

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status
            self.updated_at = _now()
            self.save()

    # ------------------------------------------------------------ 视图与持久化
    def view(self, limit: int = 200) -> list[dict[str, Any]]:
        """给前端的展示视图：只保留 user/assistant 轮次，图片以标记表示。

        用户任务消息里注入了内核上下文（特征历史 / 可用 op 清单），那是给模型
        看的，展示时裁掉，只留用户真正说的话。
        """
        with self._lock:
            items = list(self.messages[-limit:])
        out: list[dict[str, Any]] = []
        for message in items:
            role = message.get("role")
            if role not in ("user", "assistant"):
                continue
            content = message.get("content")
            text = ""
            has_image = False
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    elif block.get("type") == "image_url":
                        has_image = True
                text = "\n".join(parts)
            if role == "user":
                text = _strip_task_context(text)
            if role == "assistant" and not text:
                continue  # 纯工具调用轮：文字为空，工具卡由 WS 事件承载
            out.append({"role": role, "text": text, "has_image": has_image})
        return out

    def llm_messages(self) -> list[dict[str, Any]]:
        """给 AgentLoop 的历史副本（不含 system，loop 自己前置）。"""
        with self._lock:
            return json.loads(json.dumps(self.messages, ensure_ascii=False))

    def save(self) -> None:
        """调用方需已持锁（或接受微小竞态——仅整文件覆盖写）。"""
        payload = {
            "project_id": self.project_id,
            "status": self.status,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "pending": self.pending,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def clear(self) -> None:
        with self._lock:
            self.messages = []
            self.pending = []
            self.status = "idle"
            self.updated_at = _now()
            self.save()


class SessionRegistry:
    """project_id → AgentSession 内存注册表；首次访问时从磁盘惰性恢复。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._sessions: dict[str, AgentSession] = {}
        self._lock = threading.Lock()

    def _path_for(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def get(self, project_id: str) -> AgentSession:
        with self._lock:
            session = self._sessions.get(project_id)
            if session is not None:
                return session
            path = self._path_for(project_id)
            session = AgentSession(project_id=project_id, path=path)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    session.messages = list(data.get("messages") or [])
                    session.pending = list(data.get("pending") or [])
                    session.status = "idle"  # 进程重启后运行态一律复位
                except (ValueError, OSError):
                    session.messages = []
            self._sessions[project_id] = session
            return session

    def drop(self, project_id: str) -> None:
        with self._lock:
            self._sessions.pop(project_id, None)
