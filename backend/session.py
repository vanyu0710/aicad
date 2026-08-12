from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from backend.schemas import DesignSnapshot, ModelConfig, ProjectState, now_iso


class SessionStore:
    """In-memory project store with optional JSON persistence.

    Persistence is atomic: data is written to a sibling temp file and then
    replaced. Passing no path keeps the store purely in-memory, which keeps
    unit tests deterministic.
    """

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._projects: dict[str, ProjectState] = {}
        self._path = Path(persist_path) if persist_path else None
        if self._path:
            self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw:
                project = ProjectState.model_validate(item)
                self._projects[project.project_id] = project
        except (json.JSONDecodeError, OSError, ValueError):
            self._projects.clear()

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [project.model_dump() for project in self._projects.values()]
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def create_project(self, name: str | None = None) -> ProjectState:
        project_id = uuid4().hex[:10]
        project = ProjectState(project_id=project_id, name=name or "Untitled MechCAD Project")
        self._projects[project_id] = project
        self._save()
        return project

    def get_project(self, project_id: str) -> ProjectState:
        if project_id not in self._projects:
            raise KeyError(project_id)
        return self._projects[project_id]

    def list_projects(self) -> list[ProjectState]:
        return sorted(self._projects.values(), key=lambda item: item.updated_at, reverse=True)

    def delete_project(self, project_id: str) -> None:
        if project_id not in self._projects:
            raise KeyError(project_id)
        del self._projects[project_id]
        self._save()

    def rename_project(self, project_id: str, name: str) -> ProjectState:
        project = self.get_project(project_id)
        project.name = (name or "").strip() or project.name
        project.updated_at = now_iso()
        self._save()
        return project
    def save_project(self, project_id: str) -> ProjectState:
        project = self.get_project(project_id)
        self._save()
        return project

    def update_config(self, project_id: str, model_config: ModelConfig) -> ProjectState:
        project = self.get_project(project_id)
        project.settings = model_config
        project.updated_at = now_iso()
        self._save()
        return project

    def commit_snapshot(self, project_id: str, snapshot: DesignSnapshot) -> ProjectState:
        project = self.get_project(project_id)
        project.history.append(deepcopy(project.current))
        project.current = deepcopy(snapshot)
        project.redo_stack.clear()
        project.updated_at = now_iso()
        self._save()
        return project

    def undo(self, project_id: str) -> ProjectState:
        project = self.get_project(project_id)
        if not project.history:
            return project
        project.redo_stack.append(deepcopy(project.current))
        project.current = project.history.pop()
        project.updated_at = now_iso()
        self._save()
        return project

    def redo(self, project_id: str) -> ProjectState:
        project = self.get_project(project_id)
        if not project.redo_stack:
            return project
        project.history.append(deepcopy(project.current))
        project.current = project.redo_stack.pop()
        project.updated_at = now_iso()
        self._save()
        return project
