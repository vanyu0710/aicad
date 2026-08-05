from __future__ import annotations

import asyncio
import unittest

from backend.events import EventBus
from backend.schemas import StageEvent


class EventBusTests(unittest.TestCase):
    def test_subscriber_receives_published_event(self) -> None:
        async def scenario() -> list[StageEvent]:
            bus = EventBus()
            queue = await bus.subscribe("proj-1")
            event = StageEvent(type="stage_started", project_id="proj-1", stage="upload", message="hi")
            await bus.publish(event)
            return [await queue.get()]

        received = asyncio.run(scenario())
        self.assertEqual(received[0].message, "hi")
        self.assertEqual(received[0].type, "stage_started")

    def test_multiple_subscribers_all_receive(self) -> None:
        async def scenario() -> tuple[StageEvent, StageEvent]:
            bus = EventBus()
            q1 = await bus.subscribe("proj-1")
            q2 = await bus.subscribe("proj-1")
            await bus.publish(StageEvent(type="stage_done", project_id="proj-1", stage="cad", message="ok"))
            return await q1.get(), await q2.get()

        first, second = asyncio.run(scenario())
        self.assertEqual(first.message, "ok")
        self.assertEqual(second.message, "ok")

    def test_publish_without_subscribers_is_noop(self) -> None:
        async def scenario() -> None:
            bus = EventBus()
            await bus.publish(StageEvent(type="stage_done", project_id="nobody", stage="cad", message="x"))

        asyncio.run(scenario())  # must not raise

    def test_unsubscribe_stops_delivery(self) -> None:
        async def scenario() -> bool:
            bus = EventBus()
            queue = await bus.subscribe("proj-1")
            bus.unsubscribe("proj-1", queue)
            await bus.publish(StageEvent(type="stage_done", project_id="proj-1", stage="cad", message="x"))
            try:
                await asyncio.wait_for(queue.get(), timeout=0.2)
                return True
            except asyncio.TimeoutError:
                return False

        self.assertFalse(asyncio.run(scenario()))

    def test_events_for_other_projects_are_isolated(self) -> None:
        async def scenario() -> bool:
            bus = EventBus()
            queue = await bus.subscribe("proj-a")
            await bus.publish(StageEvent(type="stage_done", project_id="proj-b", stage="cad", message="other"))
            try:
                await asyncio.wait_for(queue.get(), timeout=0.2)
                return True
            except asyncio.TimeoutError:
                return False

        self.assertFalse(asyncio.run(scenario()))


if __name__ == "__main__":
    unittest.main()
