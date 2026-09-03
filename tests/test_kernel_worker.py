"""backend/kernel_worker.py 的 RPC client 测试（fake 子进程，无真实 kernel）。"""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from unittest.mock import patch

from backend.kernel_worker import KernelWorkerClient, KernelWorkerError, KernelWorkerManager


class BlockingStdout:
    """模拟阻塞读：直到进程被 watchdog kill 才返回 EOF。"""

    def __init__(self, proc: "FakeProc") -> None:
        self.proc = proc

    def readline(self) -> str:
        while not self.proc.killed:
            time.sleep(0.01)
        return ""

    def close(self) -> None:
        pass


class FakeStdin:
    def __init__(self, proc: "FakeProc") -> None:
        self.proc = proc
        self.closed = False

    def write(self, text: str) -> None:
        self.proc.written_lines.append(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeStdout:
    def __init__(self, proc: "FakeProc") -> None:
        self.proc = proc

    def readline(self) -> str:
        if self.proc.responses:
            action = self.proc.responses.pop(0)
            if isinstance(action, Exception):
                raise action
            if callable(action):
                return action(self.proc.written_lines)
            return action
        return ""  # EOF —— 模拟 worker 死亡

    def close(self) -> None:
        pass


class FakeProc:
    def __init__(self, responses=None) -> None:
        self.responses = list(responses or [])
        self.written_lines: list[str] = []
        self.stdin = FakeStdin(self)
        self.stdout = FakeStdout(self)
        self.stderr = None
        self.killed = False
        self.wait_calls = 0

    def poll(self) -> int | None:
        return None if not self.killed else 1

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        self.wait_calls += 1
        return 0


def _ok_response(lines: list[str]) -> str:
    request = json.loads(lines[-1])
    return json.dumps({"id": request["id"], "ok": True, "data": {"pong": True, "echo": request["cmd"]}})


def _make_client(responses) -> tuple[KernelWorkerClient, FakeProc]:
    proc = FakeProc(responses)
    client = KernelWorkerClient(timeout=5)
    client._proc = proc  # 直接注入，跳过真实 Popen
    return client, proc


class KernelWorkerClientTests(unittest.TestCase):
    def test_request_encodes_and_decodes(self) -> None:
        client, proc = _make_client([_ok_response])
        data = client.request_ok("ping")
        self.assertEqual(data["pong"], True)
        self.assertEqual(data["echo"], "ping")
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["cmd"], "ping")
        self.assertEqual(sent["payload"], {})
        self.assertTrue(sent["id"])

    def test_execute_passes_op_and_args(self) -> None:
        client, proc = _make_client([
            lambda lines: json.dumps({
                "id": json.loads(lines[-1])["id"],
                "ok": True,
                "data": {"success": True, "feature_id": "F_0001"},
            })
        ])
        data = client.execute("create_workplane", {"name": "base"})
        self.assertTrue(data["success"])
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["cmd"], "execute")
        self.assertEqual(sent["payload"]["op"], "create_workplane")
        self.assertEqual(sent["payload"]["args"], {"name": "base"})
        self.assertFalse(sent["payload"]["allow_experimental"])

    def test_rpc_error_raises_with_kind(self) -> None:
        client, _ = _make_client([
            json.dumps({"id": "x", "ok": False, "error": {"kind": "UNKNOWN_CMD", "message": "未知命令: nope"}})
        ])
        with self.assertRaises(KernelWorkerError) as ctx:
            client.request_ok("nope")
        self.assertEqual(ctx.exception.kind, "UNKNOWN_CMD")

    def test_worker_death_raises_worker_dead(self) -> None:
        client, proc = _make_client([])  # readline 立即 EOF
        with self.assertRaises(KernelWorkerError) as ctx:
            client.request_ok("ping")
        self.assertEqual(ctx.exception.kind, "WORKER_DEAD")
        self.assertTrue(proc.killed or proc.wait_calls >= 1)

    def test_malformed_response_raises(self) -> None:
        client, _ = _make_client(["not json\n"])
        with self.assertRaises(KernelWorkerError):
            client.request_ok("ping")

    def test_timeout_kills_process(self) -> None:
        # stdout 阻塞直到 watchdog kill → readline 返回 EOF → WORKER_DEAD
        proc = FakeProc()
        proc.stdout = BlockingStdout(proc)
        client = KernelWorkerClient(timeout=0.2)
        client._proc = proc
        with self.assertRaises(KernelWorkerError) as ctx:
            client.request_ok("slow_op")
        self.assertEqual(ctx.exception.kind, "WORKER_DEAD")
        self.assertTrue(proc.killed)

    def test_restart_increments_counter(self) -> None:
        client = KernelWorkerClient(timeout=5)
        calls: list[list] = []

        def fake_spawn() -> FakeProc:
            calls.append([])
            return FakeProc([_ok_response])

        with patch.object(client, "_spawn", side_effect=fake_spawn):
            client.start()
            self.assertTrue(client.is_alive())
            client.restart()
            self.assertEqual(client.restart_count, 1)
            client.stop()
            self.assertFalse(client.is_alive())


class KernelWorkerManagerTests(unittest.TestCase):
    def test_get_or_start_creates_and_reuses(self) -> None:
        manager = KernelWorkerManager()
        with patch.object(KernelWorkerClient, "is_alive", return_value=True), \
             patch.object(KernelWorkerClient, "start", return_value=None) as start_mock:
            first = manager.get_or_start("proj-1")
            second = manager.get_or_start("proj-1")
            self.assertIs(first, second)
            start_mock.assert_not_called()  # 已存活则不再启动
            manager.stop("proj-1")
            self.assertIsNone(manager.get("proj-1"))

    def test_get_or_start_restarts_dead_worker(self) -> None:
        manager = KernelWorkerManager()
        states = {"alive": False}

        def is_alive(inner_self) -> bool:
            return states["alive"]

        def restart(inner_self) -> None:
            states["alive"] = True
            inner_self.restart_count += 1

        with patch.object(KernelWorkerClient, "is_alive", is_alive), \
             patch.object(KernelWorkerClient, "restart", restart), \
             patch.object(KernelWorkerClient, "start", lambda inner: None):
            worker = manager.get_or_start("proj-2")
            self.assertEqual(worker.restart_count, 0)
            states["alive"] = False  # 模拟崩溃
            worker = manager.get_or_start("proj-2")
            self.assertEqual(worker.restart_count, 1)
            manager.stop_all()


if __name__ == "__main__":
    unittest.main()
