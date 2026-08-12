from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.ai import apply_chat_edit, apply_feature_operations
from backend.mechcad_ai.normalize import normalize_feature_edit_set
from backend.schemas import (
    DesignReview,
    DesignSnapshot,
    DimensionV3,
    FeatureEditOperation,
    FeatureEditSet,
    FeaturePlanV3,
    FeatureV3,
    ModelConfig,
    PlacementV3,
    ProcessStep,
)
from backend.session import SessionStore


def _dim(value: float, source: str = "user", confirmed: bool = True) -> DimensionV3:
    return DimensionV3(value=value, unit="mm", evidence="test", source=source, confirmed_by_user=confirmed)


def _tube_plan(two_holes: bool = True) -> FeaturePlanV3:
    features = [
        FeatureV3(
            id="top_groove",
            type="annular_groove",
            operation="remove",
            dimensions={
                "reduced_outer_diameter": _dim(14),
                "axial_width": _dim(6),
                "z_start": _dim(50),
            },
            placement=PlacementV3(reference="bottom_end_center", axis="Z"),
            depends_on=["base_tube"],
        ),
        FeatureV3(
            id="center_hole",
            type="through_hole",
            operation="remove",
            dimensions={"diameter": _dim(6)},
            placement=PlacementV3(reference="model_center", x=0, y=0, z=30, axis="Z"),
            depends_on=["base_tube"],
        ),
    ]
    if two_holes:
        features.append(
            FeatureV3(
                id="side_hole",
                type="through_hole",
                operation="remove",
                dimensions={"diameter": _dim(4)},
                placement=PlacementV3(reference="model_center", x=8, y=0, z=30, axis="Z"),
                depends_on=["base_tube"],
            )
        )
    return FeaturePlanV3(
        part_family="tube",
        base_feature=FeatureV3(
            id="base_tube",
            type="hollow_cylinder",
            operation="base",
            dimensions={
                "outer_diameter": _dim(20),
                "inner_diameter": _dim(16),
                "length": _dim(60),
            },
            placement=PlacementV3(reference="center", axis="Z"),
        ),
        features=features,
        design_review=DesignReview(),
    )


def _plate_plan() -> FeaturePlanV3:
    return FeaturePlanV3(
        part_family="plate",
        base_feature=FeatureV3(
            id="base_plate",
            type="box_base",
            operation="base",
            dimensions={"length": _dim(60), "width": _dim(30), "height": _dim(4)},
            placement=PlacementV3(reference="origin", axis="Z"),
        ),
        features=[
            FeatureV3(
                id="hole1",
                type="through_hole",
                operation="remove",
                dimensions={"diameter": _dim(6)},
                placement=PlacementV3(reference="model_center", x=10, y=5, z=2, axis="Z"),
                depends_on=["base_plate"],
            )
        ],
        design_review=DesignReview(),
    )


class FeatureEditTests(unittest.TestCase):
    def test_local_update_center_hole_diameter(self) -> None:
        plan = _tube_plan()
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u628a\u4e2d\u5fc3\u5b54\u6539\u6210 12mm", None, "zh")
        self.assertEqual(len(edit_set.operations), 1)
        op = edit_set.operations[0]
        self.assertEqual(op.op, "update")
        self.assertEqual(op.feature_id, "center_hole")
        self.assertEqual(op.dimensions["diameter"].value, 12.0)
        self.assertEqual(questions, [])
        center = next(f for f in updated.features if f.id == "center_hole")
        self.assertEqual(center.dimensions["diameter"].value, 12.0)
        self.assertEqual(steps[0].status, "completed")
        self.assertEqual(steps[0].operation, "update")
        self.assertEqual(steps[0].changed["after"]["dimensions"]["diameter"]["value"], 12.0)

    def test_local_update_plate_height(self) -> None:
        plan = _plate_plan()
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u4fee\u6539\u539a\u5ea6=8mm", None, "zh")
        self.assertEqual(len(edit_set.operations), 1)
        op = edit_set.operations[0]
        self.assertEqual(op.op, "update")
        self.assertEqual(op.feature_id, "base_plate")
        self.assertEqual(op.dimensions["height"].value, 8.0)
        self.assertEqual(updated.base_feature.dimensions["height"].value, 8.0)
        self.assertEqual(steps[0].status, "completed")

    def test_local_move_single_hole(self) -> None:
        plan = _plate_plan()
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u628a\u5b54\u5411\u5185\u79fb\u52a8 3mm", None, "zh")
        self.assertEqual(len(edit_set.operations), 1)
        op = edit_set.operations[0]
        self.assertEqual(op.op, "update")
        self.assertEqual(op.feature_id, "hole1")
        self.assertEqual(op.placement.x, 7.0)
        self.assertEqual(questions, [])
        self.assertEqual(updated.features[0].placement.x, 7.0)
        self.assertEqual(steps[0].changed["after"]["placement"]["x"], 7.0)

    def test_local_delete_top_groove(self) -> None:
        plan = _tube_plan()
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u5220\u9664\u9876\u90e8\u69fd", None, "zh")
        self.assertEqual(len(edit_set.operations), 1)
        self.assertEqual(edit_set.operations[0].op, "delete")
        self.assertEqual(edit_set.operations[0].feature_id, "top_groove")
        self.assertEqual([f.id for f in updated.features], ["center_hole", "side_hole"])
        self.assertEqual(steps[0].status, "completed")
        self.assertIsNone(steps[0].changed["after"])

    def test_local_add_m6_pattern_does_not_touch_center_hole(self) -> None:
        plan = _tube_plan()
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u65b0\u589e 4 \u4e2a M6 \u5b54", None, "zh")
        self.assertEqual([op.op for op in edit_set.operations], ["add"])
        op = edit_set.operations[0]
        self.assertEqual(op.type, "circular_pattern")
        self.assertEqual(op.dimensions["count"].value, 4.0)
        self.assertEqual(op.dimensions["diameter"].value, 6.0)
        center = next(f for f in updated.features if f.id == "center_hole")
        self.assertEqual(center.dimensions["diameter"].value, 6.0)
        pattern = next(f for f in updated.features if f.type == "circular_pattern")
        self.assertTrue(any("pitch_radius" in reason for reason in pattern.unresolved))
        self.assertEqual(steps[0].status, "completed")

    def test_change_verb_containing_add_is_not_misread_as_new_feature(self) -> None:
        plan = _plate_plan()
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u628a\u5b54\u52a0\u5bbd\u5230 8mm", None, "zh")
        self.assertEqual([op.op for op in edit_set.operations], ["update"])
        self.assertEqual(edit_set.operations[0].feature_id, "hole1")
        self.assertEqual(edit_set.operations[0].dimensions["diameter"].value, 8.0)
        self.assertEqual(len(updated.features), 1)

    def test_ambiguous_move_asks_question_and_does_not_execute(self) -> None:
        plan = _tube_plan(two_holes=True)
        updated, questions, edit_set, steps = apply_chat_edit(plan, "\u628a\u5b54\u5411\u5185\u79fb\u52a8 3mm", None, "zh")
        self.assertEqual(edit_set.operations, [])
        self.assertTrue(questions)
        self.assertEqual(steps, [])
        self.assertEqual([f.id for f in updated.features], [f.id for f in plan.features])

    def test_delete_with_children_blocks_and_returns_question(self) -> None:
        plan = FeaturePlanV3(
            part_family="plate",
            base_feature=FeatureV3(
                id="base_plate",
                type="box_base",
                operation="base",
                dimensions={"length": _dim(60), "width": _dim(30), "height": _dim(4)},
                placement=PlacementV3(reference="origin", axis="Z"),
            ),
            features=[
                FeatureV3(id="parent", type="boss_cylinder", operation="add", dimensions={"diameter": _dim(12), "height": _dim(5)}, depends_on=["base_plate"]),
                FeatureV3(id="child", type="through_hole", operation="remove", dimensions={"diameter": _dim(4)}, depends_on=["parent"]),
            ],
            design_review=DesignReview(),
        )
        edit_set = FeatureEditSet(operations=[FeatureEditOperation(op="delete", feature_id="parent")])
        updated, questions, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "blocked")
        self.assertTrue(any("\u540c\u65f6\u5220\u9664" in q.text for q in questions))
        self.assertIn("parent", [f.id for f in updated.features])
        self.assertIn("child", [f.id for f in updated.features])

    def test_strict_mode_blocks_unconfirmed_assumption_update(self) -> None:
        plan = _plate_plan()
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(
                    op="update",
                    feature_id="hole1",
                    dimensions={"diameter": _dim(12, source="assumption", confirmed=False)},
                )
            ]
        )
        settings = ModelConfig(operation_mode="strict")
        updated, questions, steps = apply_feature_operations(plan, edit_set, settings, "zh")
        self.assertEqual(steps[0].status, "blocked")
        self.assertEqual(updated.features[0].dimensions["diameter"].value, 6.0)

    def test_normalize_rejects_full_feature_plan(self) -> None:
        with self.assertRaises(ValueError):
            normalize_feature_edit_set({"schema_version": "3.0", "base_feature": {}, "features": []})

    def test_process_steps_persist_with_snapshot_and_undo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "projects.json"
            store = SessionStore(path)
            project = store.create_project("process")
            first = DesignSnapshot(
                feature_plan=_tube_plan(),
                process=[ProcessStep(stage="validation", status="completed", label="first")],
            )
            second = DesignSnapshot(
                feature_plan=_plate_plan(),
                process=[ProcessStep(stage="chat_edit", status="completed", label="second")],
            )
            store.commit_snapshot(project.project_id, first)
            store.commit_snapshot(project.project_id, second)
            restored = SessionStore(path).get_project(project.project_id)
            self.assertEqual(restored.current.process[0].stage, "chat_edit")
            store.undo(project.project_id)
            self.assertEqual(store.get_project(project.project_id).current.process[0].stage, "validation")


if __name__ == "__main__":
    unittest.main()
