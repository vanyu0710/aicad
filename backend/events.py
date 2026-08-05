from __future__ import annotations

import asyncio

from backend.schemas import StageEvent


class EventBus:
    def __init__(self) -> None:
        self._queues: dict[str, set[asyncio.Queue[StageEvent]]] = {}

    async def subscribe(self, project_id: str) -> asyncio.Queue[StageEvent]:
        queue: asyncio.Queue[StageEvent] = asyncio.Queue()
        self._queues.setdefault(project_id, set()).add(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue[StageEvent]) -> None:
        queues = self._queues.get(project_id)
        if not queues:
            return
        queues.discard(queue)
        if not queues:
            self._queues.pop(project_id, None)

    async def publish(self, event: StageEvent) -> None:
        for queue in list(self._queues.get(event.project_id, set())):
            await queue.put(event)
