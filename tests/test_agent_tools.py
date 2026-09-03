"""backend/agent/tools.py 的 capability → LLM tool schema 映射测试。

与 mech_kernel/capability_registry.py 的 FieldSchema.to_dict() 输出形状互相锁定：
{type, required, default?, description?, min?, max?, enum?, items_type?, length?}
"""

from __future__ import annotations

import unittest

from backend.agent.tools import build_llm_tools, capability_to_tool, filter_args_to_schema


def _cap(name: str, inputs: dict, description: str = "描述", category: str = "sketch", examples=None) -> dict:
    return {
        "name": name,
        "category": category,
        "description": description,
        "permission": "public",
        "experimental": False,
        "inputs": inputs,
        "outputs": {},
        "examples": examples or [],
    }


class CapabilityToToolTests(unittest.TestCase):
    def test_string_number_boolean_basic(self) -> None:
        tool = capability_to_tool(_cap("new_sketch", {
            "workplane_name": {"type": "string", "required": True},
            "sketch_name": {"type": "string", "required": True},
            "unused_flag": {"type": "boolean", "required": False, "default": False},
        }))
        fn = tool["function"]
        self.assertEqual(tool["type"], "function")
        self.assertEqual(fn["name"], "new_sketch")
        params = fn["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertEqual(params["properties"]["workplane_name"], {"type": "string"})
        self.assertEqual(params["properties"]["unused_flag"]["type"], "boolean")
        self.assertEqual(params["properties"]["unused_flag"]["default"], False)
        self.assertEqual(params["required"], ["workplane_name", "sketch_name"])

    def test_number_min_max_and_description(self) -> None:
        tool = capability_to_tool(_cap("extrude", {
            "depth": {"type": "number", "required": True, "min": 0.001, "description": "拉伸深度"},
        }))
        prop = tool["function"]["parameters"]["properties"]["depth"]
        self.assertEqual(prop["type"], "number")
        self.assertEqual(prop["minimum"], 0.001)
        self.assertNotIn("maximum", prop)
        self.assertEqual(prop["description"], "拉伸深度")

    def test_enum_with_default(self) -> None:
        tool = capability_to_tool(_cap("extrude", {
            "mode": {"type": "enum", "required": False, "default": "new_body",
                     "enum": ["new_body", "add", "cut"]},
        }))
        prop = tool["function"]["parameters"]["properties"]["mode"]
        self.assertEqual(prop["type"], "string")
        self.assertEqual(prop["enum"], ["new_body", "add", "cut"])
        self.assertEqual(prop["default"], "new_body")

    def test_tuple_becomes_sized_array(self) -> None:
        tool = capability_to_tool(_cap("add_circle", {
            "center": {"type": "tuple", "required": True, "items_type": "number", "length": 2},
        }))
        prop = tool["function"]["parameters"]["properties"]["center"]
        self.assertEqual(prop["type"], "array")
        self.assertEqual(prop["items"], {"type": "number"})
        self.assertEqual(prop["minItems"], 2)
        self.assertEqual(prop["maxItems"], 2)
        self.assertEqual(tool["function"]["parameters"]["required"], ["center"])

    def test_list_with_items_type(self) -> None:
        tool = capability_to_tool(_cap("fillet", {
            "edges": {"type": "list", "required": False, "items_type": "string"},
        }))
        prop = tool["function"]["parameters"]["properties"]["edges"]
        self.assertEqual(prop["type"], "array")
        self.assertEqual(prop["items"], {"type": "string"})
        self.assertEqual(tool["function"]["parameters"]["required"], [])

    def test_string_or_list_anyof(self) -> None:
        tool = capability_to_tool(_cap("fillet", {
            "edges": {"type": "string_or_list", "required": False, "default": "all",
                      "items_type": "string"},
        }))
        prop = tool["function"]["parameters"]["properties"]["edges"]
        self.assertIn("anyOf", prop)
        types = [item["type"] for item in prop["anyOf"]]
        self.assertEqual(types, ["string", "array"])
        self.assertEqual(prop["default"], "all")

    def test_description_includes_category_and_examples(self) -> None:
        examples = [{"op": "extrude", "args": {"depth": 10}}]
        tool = capability_to_tool(_cap("extrude", {}, description="拉伸草图", category="body", examples=examples * 3))
        description = tool["function"]["description"]
        self.assertIn("[body]", description)
        self.assertIn("拉伸草图", description)
        # 最多保留 2 条示例
        self.assertEqual(description.count('"op"'), 2)

    def test_function_name_is_safe_identifier(self) -> None:
        import re

        tool = capability_to_tool(_cap("create_workplane", {}))
        self.assertRegex(tool["function"]["name"], r"^[a-zA-Z0-9_-]+$")


class BuildLlmToolsTests(unittest.TestCase):
    def test_build_from_worker_response(self) -> None:
        capabilities = {
            "public": [_cap("undo", {}), _cap("redo", {})],
            "experimental": [_cap("assemble", {})],
        }
        tools = build_llm_tools(capabilities)
        self.assertEqual([t["function"]["name"] for t in tools], ["undo", "redo"])

    def test_build_ignores_experimental(self) -> None:
        capabilities = {"public": [], "experimental": [{"name": "assemble"}]}
        self.assertEqual(build_llm_tools(capabilities), [])

    def test_full_tools_have_required_keys(self) -> None:
        # 模拟 33 个 op 的最小形状，锁死协议契约
        caps = {"public": [_cap(f"op_{i:02d}", {"x": {"type": "number", "required": True}}) for i in range(33)],
                "experimental": []}
        tools = build_llm_tools(caps)
        self.assertEqual(len(tools), 33)
        for tool in tools:
            self.assertEqual(tool["type"], "function")
            self.assertIn("description", tool["function"])
            self.assertIn("parameters", tool["function"])


class FilterArgsToSchemaTests(unittest.TestCase):
    def test_filters_unknown_fields(self) -> None:
        capabilities = {"public": [_cap("extrude", {
            "sketch_name": {"type": "string", "required": True},
            "depth": {"type": "number", "required": True},
            "mode": {"type": "enum", "required": False, "enum": ["new_body", "add", "cut"]},
        })], "experimental": []}
        args = {"sketch_name": "s", "depth": 10, "mode": "add", "bogus": 1, "confirm_replace": True}
        filtered = filter_args_to_schema("extrude", args, capabilities)
        self.assertEqual(filtered, {"sketch_name": "s", "depth": 10, "mode": "add"})

    def test_unknown_op_returns_empty(self) -> None:
        self.assertEqual(filter_args_to_schema("nope", {"a": 1}, {"public": [], "experimental": []}), {})


if __name__ == "__main__":
    unittest.main()
