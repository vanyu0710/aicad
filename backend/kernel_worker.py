"""MechKernel worker 子进程的 JSON-lines RPC client + 生命周期管理。

协议见 `mechcad-kernel/mech_kernel/server.py` 模块 docstring：
请求 ``{"id", "cmd", "payload"}`` → 响应 ``{"id", "ok", "data"|"error"}``。

环境变量：
- ``MECHCAD_KERNEL_REPO``: kernel 仓库根（默认 aicad 旁的 ``mechcad-kernel``）
- ``MECHCAD_KERNEL_PYTHON``: kernel venv 解释器（默认 ``<repo>/.venv/Scripts/python.exe``）
- ``MECHCAD_KERNEL_TIMEOUT``: 单请求超时秒数（默认 120）

backend 进程不 import 任何 CAD 库（D2 边界）；kernel 只跑在子进程里。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KERNEL_REPO = ROOT.parents[1] / "mechcad-kernel"
DEFAULT_TIMEOUT = 120


class KernelWorkerError(RuntimeError):
    """RPC 级错误（协议错误 / worker 崩溃 / 超时）。kernel 的失败 StepResult 不算这类。"""

    def __init__(self, message: str, kind: str = "WORKER_ERROR") -> None:
        super().__init__(message)
        self.kind = kind


def kernel_repo_path() -> Path:
    return Path(os.getenv("MECHCAD_KERNEL_REPO", str(DEFAULT_KERNEL_REPO))).resolve()


def kernel_python_path() -> str:
    configured = os.getenv("MECHCAD_KERNEL_PYTHON")
    if configured:
        return configured
    candidate = kernel_repo_path() / ".venv" / "Scripts" / "python.exe"
    if candidate.exists():
        return str(candidate)
    return sys.executable


class KernelWorkerClient:
    """单会话 worker：一会话（项目）一实例。线程安全，单飞（同时只允许一个在途请求）。"""

    def __init__(
        self,
        *,
        kernel_repo: Path | None = None,
        python: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.kernel_repo = Path(kernel_repo) if kernel_repo else kernel_repo_path()
        self.python = python or kernel_python_path()
        self.timeout = float(timeout if timeout is not None else os.getenv("MECHCAD_KERNEL_TIMEOUT", str(DEFAULT_TIMEOUT)))
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._request_seq = 0
        self.restart_count = 0

    # ------------------------------------------------------------- lifecycle
    def _spawn(self) -> subprocess.Popen:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(self.kernel_repo) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.Popen(
            [self.python, str(self.kernel_repo / "mech_kernel" / "server.py")],
            cwd=str(self.kernel_repo),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.is_alive():
            return
        self._proc = self._spawn()

    def restart(self) -> None:
        self.stop()
        self.restart_count += 1
        self._proc = self._spawn()

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()

    # ------------------------------------------------------------------- rpc
    def request(self, cmd: str, payload: dict | None = None, *, timeout: float | None = None) -> dict:
        """发送一条命令并等待响应。返回完整响应 envelope；失败抛 KernelWorkerError。"""
        with self._lock:
            if not self.is_alive():
                if self._proc is not None:  # 上一个进程已死 → 丢弃
                    self.stop()
                self.start()
            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            self._request_seq += 1
            request_id = f"req-{self._request_seq}"
            line = json.dumps({"id": request_id, "cmd": cmd, "payload": payload or {}}, ensure_ascii=False)
            effective_timeout = timeout if timeout is not None else self.timeout
            watchdog: threading.Timer | None = None
            try:
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise KernelWorkerError(f"kernel worker stdin 写入失败（{cmd}）: {exc}", kind="WORKER_DEAD") from exc
            watchdog = threading.Timer(effective_timeout, self._on_timeout)
            watchdog.daemon = True
            try:
                watchdog.start()
                response_line = self._readline()
            finally:
                if watchdog is not None:
                    watchdog.cancel()
            if not response_line:
                proc = self._proc
                self.stop()  # 确保进程结束，stderr 才可读
                stderr_tail = ""
                if proc is not None and proc.poll() is not None and proc.stderr is not None:
                    try:
                        stderr_tail = (proc.stderr.read() or "")[-2000:]
                    except (OSError, ValueError):
                        pass
                raise KernelWorkerError(
                    f"kernel worker 在处理 {cmd} 时无响应/退出。\nstderr 尾部: {stderr_tail}",
                    kind="WORKER_DEAD",
                )
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
                raise KernelWorkerError(f"kernel worker 响应不是合法 JSON: {response_line[:200]!r}") from exc
            return response

    def request_ok(self, cmd: str, payload: dict | None = None, *, timeout: float | None = None) -> dict:
        """request 的便捷版：ok:false → 抛错；成功返回 data。"""
        response = self.request(cmd, payload, timeout=timeout)
        if not response.get("ok"):
            error = response.get("error") or {}
            raise KernelWorkerError(
                f"{cmd} 失败: {error.get('kind', 'UNKNOWN')}: {error.get('message', response)}",
                kind=str(error.get("kind", "UNKNOWN")),
            )
        return response.get("data") or {}

    def execute(self, op: str, args: dict | None = None, *, allow_experimental: bool = False) -> dict:
        """执行一个 kernel op，返回 StepResult 的 JSON dict（data.success=False 表示 op 失败）。"""
        return self.request_ok("execute", {
            "op": op,
            "args": args or {},
            "allow_experimental": allow_experimental,
        })

    def capabilities(self) -> dict:
        return self.request_ok("capabilities")

    def feature_tree(self) -> dict:
        return self.request_ok("feature_tree")

    def update_feature(self, feature_id: str, new_params: dict[str, Any], *, timeout: float | None = None) -> dict:
        """参数化更新：改特征参数 → 内核全量重放 → 几何刷新。返回 StepResult。"""
        return self.request_ok("update_feature", {"feature_id": feature_id, "new_params": new_params}, timeout=timeout)

    def delete_feature(self, feature_id: str, *, timeout: float | None = None) -> dict:
        """删除特征（含其依赖者失效风险）→ 内核重放。返回 StepResult。"""
        return self.request_ok("delete_feature", {"feature_id": feature_id}, timeout=timeout)

    def undo(self, steps: int = 1, *, timeout: float | None = None) -> dict:
        return self.request_ok("undo", {"steps": steps}, timeout=timeout)

    def redo(self, steps: int = 1, *, timeout: float | None = None) -> dict:
        return self.request_ok("redo", {"steps": steps}, timeout=timeout)

    def export_mesh(self, path: str, *, fmt: str = "stl", timeout: float | None = None) -> dict:
        return self.request_ok("export_mesh", {"path": path, "format": fmt}, timeout=timeout)

    def export_step(self, path: str, timeout: float | None = None) -> dict:
        return self.request_ok("export", {"path": path, "format": "step"}, timeout=timeout)

    # -------------------------------------------------------------- internal
    def _readline(self) -> str:
        """读一行响应；读过程中断/管道损坏统一转 WORKER_DEAD。"""
        try:
            return self._proc.stdout.readline()
        except (OSError, InterruptedError, ValueError):
            return ""  # 管道已断 → 走 worker 死亡路径

    def _on_timeout(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


class KernelWorkerManager:
    """project_id → worker 实例。P1 崩溃策略：发现死进程则重启（历史丢失，P4 做 _op_history 重放恢复）。"""

    def __init__(self) -> None:
        self._workers: dict[str, KernelWorkerClient] = {}
        self._lock = threading.Lock()

    def get(self, project_id: str) -> KernelWorkerClient | None:
        with self._lock:
            return self._workers.get(project_id)

    def get_or_start(self, project_id: str) -> KernelWorkerClient:
        with self._lock:
            worker = self._workers.get(project_id)
            if worker is None:
                worker = KernelWorkerClient()
                self._workers[project_id] = worker
            elif not worker.is_alive():
                worker.restart()
            return worker

    def stop(self, project_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(project_id, None)
        if worker is not None:
            worker.stop()

    def stop_all(self) -> None:
        with self._lock:
            workers = list(self._workers.items())
            self._workers.clear()
        for _, worker in workers:
            worker.stop()


_manager: KernelWorkerManager | None = None
_manager_lock = threading.Lock()


def get_worker_manager() -> KernelWorkerManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = KernelWorkerManager()
        return _manager
