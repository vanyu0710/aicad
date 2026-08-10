from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.schemas import DesignSnapshot, FeaturePlanV3, ModelConfig
from backend.session import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_and_get_project(self) -> None:
        store = SessionStore()
        project = store.create_project("Demo")
        self.assertEqual(project.name, "Demo")
        self.assertEqual(len(project.history), 0)
        self.assertEqual(store.get_project(project.project_id).project_id, project.project_id)

    def test_get_missing_project_raises_key_error(self) -> None:
        store = SessionStore()
        with self.assertRaises(KeyError):
            store.get_project("nope")

    def test_update_config_replaces_settings(self) -> None:
        store = SessionStore()
        project = store.create_project()
        config = ModelConfig(vision_model="v1", planner_model="p1")
        store.update_config(project.project_id, config)
        self.assertEqual(store.get_project(project.project_id).settings.planner_model, "p1")

    def test_commit_snapshot_pushes_history_and_clears_redo(self) -> None:
        store = SessionStore()
        project = store.create_project()
        first = _snapshot("first")
        second = _snapshot("second")
        third = _snapshot("third")

        store.commit_snapshot(project.project_id, first)
        store.commit_snapshot(project.project_id, second)
        self.assertEqual(store.get_project(project.project_id).current.feature_plan.part_family, "second")
        self.assertEqual(len(store.get_project(project.project_id).history), 2)

        # Move into redo stack, then a new commit must clear it.
        store.undo(project.project_id)
        self.assertEqual(len(store.get_project(project.project_id).redo_stack), 1)
        store.commit_snapshot(project.project_id, third)
        self.assertEqual(len(store.get_project(project.project_id).redo_stack), 0)
        self.assertEqual(store.get_project(project.project_id).current.feature_plan.part_family, "third")

    def test_undo_redo_round_trip(self) -> None:
        store = SessionStore()
        project = store.create_project()
        store.commit_snapshot(project.project_id, _snapshot("a"))
        store.commit_snapshot(project.project_id, _snapshot("b"))
        store.commit_snapshot(project.project_id, _snapshot("c"))

        current = store.get_project(project.project_id)
        self.assertEqual(current.current.feature_plan.part_family, "c")
        self.assertEqual(len(current.history), 3)

        store.undo(project.project_id)
        store.undo(project.project_id)
        current = store.get_project(project.project_id)
        self.assertEqual(current.current.feature_plan.part_family, "a")
        self.assertEqual(len(current.history), 1)
        self.assertEqual(len(current.redo_stack), 2)

        store.redo(project.project_id)
        current = store.get_project(project.project_id)
        self.assertEqual(current.current.feature_plan.part_family, "b")
        self.assertEqual(len(current.history), 2)
        self.assertEqual(len(current.redo_stack), 1)

    def test_undo_at_beginning_is_noop(self) -> None:
        store = SessionStore()
        project = store.create_project()
        before = store.get_project(project.project_id)
        store.undo(project.project_id)
        after = store.get_project(project.project_id)
        self.assertEqual(before.current.id, after.current.id)
        self.assertEqual(len(after.history), 0)

    def test_redo_without_undo_is_noop(self) -> None:
        store = SessionStore()
        project = store.create_project()
        store.commit_snapshot(project.project_id, _snapshot("a"))
        store.redo(project.project_id)
        current = store.get_project(project.project_id)
        self.assertEqual(current.current.feature_plan.part_family, "a")
        self.assertEqual(len(current.redo_stack), 0)

    def test_committed_snapshots_are_deep_copies(self) -> None:
        store = SessionStore()
        project = store.create_project()
        first = _snapshot("base")
        store.commit_snapshot(project.project_id, first)
        # Mutating the original object must not affect the stored snapshot.
        first.feature_plan.part_family = "mutated"
        stored = store.get_project(project.project_id).current
        self.assertEqual(stored.feature_plan.part_family, "base")
        # history holds the previous (initial empty) snapshot, untouched.
        previous = store.get_project(project.project_id).history[0]
        self.assertEqual(previous.feature_plan.part_family, "unknown")


def _snapshot(family: str) -> DesignSnapshot:
    return DesignSnapshot(feature_plan=FeaturePlanV3(part_family=family))



    def test_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            first = SessionStore(path)
            project = first.create_project("Persisted")
            second = SessionStore(path)
            restored = second.get_project(project.project_id)
            self.assertEqual(restored.name, "Persisted")

    def test_list_projects_sorted_by_updated_at(self) -> None:
        store = SessionStore()
        older = store.create_project("Older")
        newer = store.create_project("Newer")
        older.updated_at = "2000-01-01T00:00:00+00:00"
        names = [project.name for project in store.list_projects()]
        self.assertEqual(names[0], "Newer")
        self.assertEqual(set(names), {"Newer", "Older"})

    def test_delete_project_removes_and_raises_for_missing(self) -> None:
        store = SessionStore()
        project = store.create_project("Delete me")
        store.delete_project(project.project_id)
        with self.assertRaises(KeyError):
            store.get_project(project.project_id)
        with self.assertRaises(KeyError):
            store.delete_project(project.project_id)
if __name__ == "__main__":
    unittest.main()
