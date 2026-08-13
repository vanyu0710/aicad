from __future__ import annotations

import unittest

from backend.ai import _required_dimensions
from backend.capabilities import CAPABILITIES
from backend.feature_definitions import FEATURE_DEFINITIONS, get_feature_definition, to_capability, to_semantics
from backend.generic_engine import feature_semantics
from backend.mechcad_ai.normalize import _BASE_KEY_ALIASES, _FEATURE_KEY_ALIASES
from backend.validation import _REQUIRED_DIMS


SUPPORTED_TYPES = {
    "box_base",
    "cylinder_base",
    "hollow_cylinder",
    "link_plate",
    "through_hole",
    "blind_hole",
    "counterbore_hole",
    "rectangular_slot",
    "rectangular_pocket",
    "annular_groove",
    "internal_annular_groove",
    "boss_cylinder",
    "rectangular_pad",
    "rib_box",
    "linear_pattern",
    "circular_pattern",
}

UNSUPPORTED_TYPES = {"fillet", "chamfer", "spur_gear", "helical_gear", "thread", "sheet_metal"}


class FeatureDefinitionRegistryTests(unittest.TestCase):
    def test_registry_covers_existing_feature_types(self) -> None:
        registered = {definition.feature_type for definition in FEATURE_DEFINITIONS.list()}
        self.assertEqual(len(registered), 22)
        self.assertTrue(SUPPORTED_TYPES <= registered)
        self.assertTrue(UNSUPPORTED_TYPES <= registered)

    def test_known_unknown_and_unsupported_lookup(self) -> None:
        self.assertIsNotNone(get_feature_definition("through_hole"))
        self.assertIsNotNone(get_feature_definition("fillet"))
        self.assertIsNone(get_feature_definition("not_a_feature"))
        self.assertIsNone(get_feature_definition(""))
        self.assertEqual(get_feature_definition("fillet").implementation_status, "unsupported")

    def test_duplicate_registration_raises(self) -> None:
        with self.assertRaises(ValueError):
            FEATURE_DEFINITIONS.register(FEATURE_DEFINITIONS.get("box_base"))

    def test_parameter_definitions_cover_required_dimensions(self) -> None:
        for definition in FEATURE_DEFINITIONS.supported():
            parameter_names = {parameter.name for parameter in definition.parameters}
            self.assertTrue(set(definition.required_dimensions) <= parameter_names, definition.feature_type)
            for name in definition.required_dimensions:
                parameter = next(item for item in definition.parameters if item.name == name)
                self.assertIsNotNone(parameter.minimum, f"{definition.feature_type}.{name}")

    def test_registry_and_derived_views_are_stateless(self) -> None:
        definition = get_feature_definition("through_hole")
        before = definition.model_dump()
        to_capability(definition)
        to_semantics(definition)
        self.assertEqual(definition.model_dump(), before)


class LegacyConsumerConsistencyTests(unittest.TestCase):
    def test_capabilities_derive_from_registry(self) -> None:
        capabilities = CAPABILITIES.all()
        expected = [
            definition
            for definition in FEATURE_DEFINITIONS.list()
            if definition.implementation_status == "supported" or definition.feature_type == "spur_gear"
        ]
        self.assertEqual([capability.feature_type for capability in capabilities], [definition.feature_type for definition in expected])
        for capability in capabilities:
            definition = get_feature_definition(capability.feature_type)
            self.assertIsNotNone(definition)
            self.assertEqual(capability.model_dump(), to_capability(definition).model_dump())

    def test_capabilities_unsupported_edit_types_remain_unknown(self) -> None:
        self.assertIsNotNone(CAPABILITIES.get("spur_gear"))
        self.assertIsNone(CAPABILITIES.get("fillet"))
        self.assertIsNone(CAPABILITIES.get("thread"))

    def test_validation_required_dimensions_match_registry(self) -> None:
        self.assertEqual(set(_REQUIRED_DIMS), SUPPORTED_TYPES)
        for feature_type, required in _REQUIRED_DIMS.items():
            definition = get_feature_definition(feature_type)
            self.assertIsNotNone(definition)
            self.assertEqual(set(definition.required_dimensions), set(required))

    def test_ai_required_dimensions_match_registry(self) -> None:
        for definition in FEATURE_DEFINITIONS.supported():
            self.assertEqual(
                set(_required_dimensions(definition.feature_type)),
                set(definition.required_dimensions),
                definition.feature_type,
            )

    def test_normalize_aliases_match_registry(self) -> None:
        base_types = {definition.feature_type for definition in FEATURE_DEFINITIONS.supported() if definition.operation == "base"}
        feature_types = {definition.feature_type for definition in FEATURE_DEFINITIONS.supported() if definition.operation != "base"}
        self.assertEqual(set(_BASE_KEY_ALIASES), base_types)
        self.assertEqual(set(_FEATURE_KEY_ALIASES), feature_types)
        for feature_type, aliases in _FEATURE_KEY_ALIASES.items():
            definition = get_feature_definition(feature_type)
            self.assertIsNotNone(definition)
            self.assertEqual(set(aliases), set(definition.required_dimensions))

    def test_legacy_semantics_derive_from_registry(self) -> None:
        semantics = feature_semantics()
        self.assertEqual(len(semantics), 9)
        for semantics_item in semantics:
            definition = get_feature_definition(semantics_item.feature_type)
            self.assertIsNotNone(definition)
            self.assertEqual(semantics_item.required_dimensions, definition.required_dimensions)
            self.assertEqual(semantics_item.centered_placements, definition.centered_placements)


if __name__ == "__main__":
    unittest.main()
