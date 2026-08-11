from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI, dist: Path) -> bool:
    """Serve a built Vite app from the same origin as the API.

    Returns False when the dist folder is not ready so callers can decide
    whether to start in API-only mode.
    """
    index_html = dist / "index.html"
    if not index_html.is_file():
        return False

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="mechcad-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path:
            candidate = (dist / full_path).resolve()
            root = dist.resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(str(candidate))
        return FileResponse(str(index_html))

    return True
