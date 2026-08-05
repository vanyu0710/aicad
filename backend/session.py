from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from backend.schemas import DesignSnapshot, ModelConfig, ProjectState, now_iso


class SessionStore:
    def __init__(self) -> None:
        self._projects: dict[str, ProjectState] = {}

    def create_project(self, name: str | None = None) -> ProjectState:
        project_id = uuid4().hex[:10]
        project = ProjectState(project_id=project_id, name=name or "Untitled MechCAD Project")
        self._projects[project_id] = project
        return project

    def get_project(self, project_id: str) -> ProjectState:
        if project_id not in self._projects:
            raise KeyError(project_id)
        return self._projects[project_id]

    def update_config(self, project_id: str, model_config: ModelConfig) -> ProjectState:
        project = self.get_project(project_id)
        project.settings = model_config
        project.updated_at = now_iso()
        return project

    def commit_snapshot(self, project_id: str, snapshot: DesignSnapshot) -> ProjectState:
        project = self.get_project(project_id)
        project.history.append(deepcopy(project.current))
        project.current = deepcopy(snapshot)
        project.redo_stack.clear()
        project.updated_at = now_iso()
        return project

    def undo(self, project_id: str) -> ProjectState:
        project = self.get_project(project_id)
        if not project.history:
            return project
        project.redo_stack.append(deepcopy(project.current))
        project.current = project.history.pop()
        project.updated_at = now_iso()
        return project

    def redo(self, project_id: str) -> ProjectState:
        project = self.get_project(project_id)
        if not project.redo_stack:
            return project
        project.history.append(deepcopy(project.current))
        project.current = project.redo_stack.pop()
        project.updated_at = now_iso()
        return project
