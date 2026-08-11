from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.static_assets import mount_frontend


class StaticAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mechcad-static-")
        self.dist = Path(self._tmp.name)
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text("<html>mechcad</html>", encoding="utf-8")
        (self.dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
        self.app = FastAPI()

        @self.app.get("/api/health")
        def health():
            return {"status": "ok"}

        mount_frontend(self.app, self.dist)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_root_serves_index(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("mechcad", response.text)

    def test_asset_file_is_served(self) -> None:
        response = self.client.get("/assets/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "console.log('ok')")

    def test_spa_fallback_serves_index(self) -> None:
        response = self.client.get("/projects/abc")
        self.assertEqual(response.status_code, 200)
        self.assertIn("mechcad", response.text)

    def test_unknown_api_path_stays_json_404(self) -> None:
        response = self.client.get("/api/no-such-route")
        self.assertEqual(response.status_code, 404)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertIn("detail", response.json())

    def test_spa_fallback_blocks_path_traversal(self) -> None:
        response = self.client.get("/..%2f..%2fbackend%2fmain.py")
        self.assertEqual(response.status_code, 200)
        self.assertIn("mechcad", response.text)

    def test_mount_returns_false_without_dist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mechcad-static-missing-") as missing:
            self.assertFalse(mount_frontend(FastAPI(), Path(missing) / "nope"))


if __name__ == "__main__":
    unittest.main()
