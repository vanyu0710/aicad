from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from backend.schemas import ProcessStep, now_iso


PublishFn = Callable[[str, str, ProcessStep], Awaitable[None] | None]


class ProcessRecorder:
    """Collect ProcessSteps and optionally publish live WebSocket events."""

    def __init__(
        self,
        project_id: str,
        language: str = "zh",
        publish: PublishFn | None = None,
        initial: list[ProcessStep] | None = None,
    ) -> None:
        self.project_id = project_id
        self.language = language
        self.publish = publish
        self.steps = list(initial or [])
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def append(self, step: ProcessStep) -> ProcessStep:
        self.steps.append(step)
        return step

    def ingest(self, step: ProcessStep) -> ProcessStep:
        """Append an externally-produced step and publish its status event."""
        self.steps.append(step)
        event_type = {
            "running": "process_step_started",
            "completed": "process_step_done",
            "failed": "process_step_failed",
            "blocked": "process_step_blocked",
        }.get(step.status, "process_step_done")
        self._emit(event_type, step)
        return step

    def started(
        self,
        stage: str,
        label: str,
        *,
        summary: str = "",
        detail: str = "",
        feature_id: str | None = None,
        operation: str | None = None,
        changed: dict[str, Any] | None = None,
    ) -> ProcessStep:
        step = ProcessStep(
            stage=stage,
            status="running",
            label=label,
            summary=summary,
            detail=detail,
            feature_id=feature_id,
            operation=operation,
            changed=changed,
        )
        self.append(step)
        self._emit("process_step_started", step)
        return step

    def completed(
        self,
        step: ProcessStep,
        *,
        summary: str | None = None,
        detail: str | None = None,
        warnings: list[str] | None = None,
        changed: dict[str, Any] | None = None,
    ) -> ProcessStep:
        step.status = "completed"
        step.completed_at = now_iso()
        if summary is not None:
            step.summary = summary
        if detail is not None:
            step.detail = detail
        if warnings:
            step.warnings.extend(warnings)
        if changed is not None:
            step.changed = changed
        self._emit("process_step_done", step)
        return step

    def failed(
        self,
        step: ProcessStep,
        *,
        error: str,
        detail: str | None = None,
        warnings: list[str] | None = None,
    ) -> ProcessStep:
        step.status = "failed"
        step.completed_at = now_iso()
        step.error = error
        if detail is not None:
            step.detail = detail
        if warnings:
            step.warnings.extend(warnings)
        self._emit("process_step_failed", step)
        return step

    def skipped(
        self,
        step: ProcessStep,
        *,
        reason: str,
    ) -> ProcessStep:
        step.status = "skipped"
        step.completed_at = now_iso()
        step.summary = step.summary or reason
        step.detail = step.detail or reason
        step.warnings.append(reason)
        self._emit("process_step_done", step)
        return step

    def blocked(
        self,
        step: ProcessStep,
        *,
        reason: str,
    ) -> ProcessStep:
        step.status = "blocked"
        step.completed_at = now_iso()
        step.error = reason
        step.detail = step.detail or reason
        self._emit("process_step_blocked", step)
        return step

    def _emit(self, event_type: str, step: ProcessStep) -> None:
        if self.publish is None:
            return
        result = self.publish(self.project_id, event_type, step)
        if result is not None and self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: self._loop.create_task(result))
