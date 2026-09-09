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


class KernelWorkerEditCommandsTests(unittest.TestCase):
    """P2 新增 RPC 封装：update_feature / delete_feature / undo / redo。"""

    def _echo_payload(self, expected_cmd: str):
        def responder(lines: list[str]) -> str:
            request = json.loads(lines[-1])
            assert request["cmd"] == expected_cmd, f"{request['cmd']} != {expected_cmd}"
            return json.dumps({"id": request["id"], "ok": True, "data": {"success": True, "echo_cmd": request["cmd"], "echo_payload": request["payload"]}})

        return responder

    def test_update_feature(self) -> None:
        client, proc = _make_client([self._echo_payload("update_feature")])
        data = client.update_feature("F_0001", {"depth": 12})
        self.assertTrue(data["success"])
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["payload"], {"feature_id": "F_0001", "new_params": {"depth": 12}})

    def test_delete_feature(self) -> None:
        client, proc = _make_client([self._echo_payload("delete_feature")])
        client.delete_feature("F_0002")
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["payload"], {"feature_id": "F_0002"})

    def test_undo_redo(self) -> None:
        client, proc = _make_client([self._echo_payload("undo"), self._echo_payload("redo")])
        client.undo(1)
        client.redo(2)
        sent0 = json.loads(proc.written_lines[0])
        sent1 = json.loads(proc.written_lines[1])
        self.assertEqual(sent0["cmd"], "undo")
        self.assertEqual(sent0["payload"], {"steps": 1})
        self.assertEqual(sent1["cmd"], "redo")
        self.assertEqual(sent1["payload"], {"steps": 2})

    def test_run_script_rpc(self) -> None:
        """v0.13 代码通道 RPC：cmd=run_script，payload 带 code/name。"""
        client, proc = _make_client([self._echo_payload("run_script")])
        data = client.run_script("k.extrude(sketch_name='s', depth=10)", name="housing")
        self.assertTrue(data["success"])
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["payload"],
                         {"code": "k.extrude(sketch_name='s', depth=10)", "name": "housing"})

    def test_reset_rpc(self) -> None:
        """v0.12 逐件建模 RPC：cmd=reset，空 payload。"""
        client, proc = _make_client([self._echo_payload("reset")])
        data = client.reset()
        self.assertTrue(data["success"])
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["cmd"], "reset")
        self.assertEqual(sent["payload"], {})

    def test_assembly_rpcs(self) -> None:
        """v0.14 F2a：export_assembly / assembly_interference / render_assembly RPC。"""
        parts = [{"path": "p/a.step", "name": "a", "pose": {"position": [0, 0, 0]}}]
        client, proc = _make_client([
            self._echo_payload("export_assembly"),
            self._echo_payload("assembly_interference"),
            self._echo_payload("render_assembly"),
        ])
        client.export_assembly(parts, "p/asm.step")
        sent = json.loads(proc.written_lines[0])
        self.assertEqual(sent["payload"], {"parts": parts, "out_step": "p/asm.step"})
        client.assembly_interference(parts, expected_overlaps=[{"a": "a", "b": "b"}])
        sent = json.loads(proc.written_lines[1])
        self.assertEqual(sent["payload"]["tolerance"], 0.001)
        self.assertEqual(sent["payload"]["expected_overlaps"], [{"a": "a", "b": "b"}])
        client.render_assembly(parts, size=320)
        sent = json.loads(proc.written_lines[2])
        self.assertEqual(sent["payload"], {"parts": parts, "size": 320})


if __name__ == "__main__":
    unittest.main()
