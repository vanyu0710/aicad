"""把 MechKernel capability 清单映射为 LLM 原生 function-calling 工具表。

worker ``capabilities`` 命令返回::

    {"public": [{"name", "category", "description", "inputs", "examples", ...}],
     "experimental": [...]}

FieldSchema 的 type 取值见 mech_kernel/capability_registry.py::

    SUPPORTED_TYPES = {"string", "number", "integer", "boolean",
                       "tuple", "list", "dict", "enum", "string_or_list"}

映射规则（本文件与 test_agent_tools.py 互相锁定）：
- string→string, number→number, integer→integer, boolean→boolean, dict→object
- enum→string + enum（带 default 时数值型枚举也接受 number，见下）
- tuple→array + items(元素类型) + minItems/maxItems=length
- list→array + items(元素类型，缺省为任意)
- string_or_list→anyOf [string, array]
- min/max→minimum/maximum，default/description 原样透传
"""

from __future__ import annotations

import json
from typing import Any

_JSON_TYPE_MAP = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
    "dict": "object",
    "enum": "string",
}

_MAX_EXAMPLES_IN_DESCRIPTION = 2


def _field_to_json_schema(field: dict[str, Any]) -> dict[str, Any]:
    ftype = str(field.get("type", "string"))
    schema: dict[str, Any] = {}

    def _items_schema() -> dict[str, Any] | None:
        items_type = field.get("items_type")
        if not items_type:
            return None
        mapped = _JSON_TYPE_MAP.get(str(items_type))
        if mapped:
            return {"type": mapped}
        # items_type 可能是 enum 类型名之外的值；保守回退为不约束
        return None

    if ftype == "tuple":
        schema["type"] = "array"
        items = _items_schema()
        if items:
            schema["items"] = items
        length = field.get("length")
        if length:
            schema["minItems"] = int(length)
            schema["maxItems"] = int(length)
    elif ftype == "list":
        schema["type"] = "array"
        items = _items_schema()
        if items:
            schema["items"] = items
    elif ftype == "string_or_list":
        item = _items_schema() or {}
        schema["anyOf"] = [{"type": "string"}, {"type": "array", **({"items": item} if item else {})}]
    elif ftype == "enum":
        enum_values = field.get("enum") or []
        schema["type"] = "string" if all(isinstance(v, str) for v in enum_values) else "number"
        schema["enum"] = enum_values
    else:
        schema["type"] = _JSON_TYPE_MAP.get(ftype, "string")

    if "description" in field:
        schema["description"] = str(field["description"])
    if field.get("min") is not None:
        schema["minimum"] = field["min"]
    if field.get("max") is not None:
        schema["maximum"] = field["max"]
    if "default" in field and ftype != "enum":
        schema["default"] = field["default"]
    elif ftype == "enum" and field.get("default") is not None:
        schema["default"] = field["default"]
    return schema


def capability_to_tool(capability: dict[str, Any]) -> dict[str, Any]:
    """单个 capability → OpenAI function tool。"""
    description_parts = [f"[{capability.get('category', 'op')}] {capability.get('description', '')}"]
    examples = capability.get("examples") or []
    if examples:
        rendered = []
        for example in examples[:_MAX_EXAMPLES_IN_DESCRIPTION]:
            try:
                rendered.append(json.dumps(example, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
        if rendered:
            description_parts.append("示例: " + " | ".join(rendered))

    inputs = capability.get("inputs") or {}
    properties = {name: _field_to_json_schema(field) for name, field in inputs.items()}
    required = [name for name, field in inputs.items() if field.get("required")]
    return {
        "type": "function",
        "function": {
            "name": str(capability["name"]),
            "description": "\n".join(part for part in description_parts if part),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def build_llm_tools(capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    """worker capabilities 响应 → LLM tools（默认只含公开 op，不含实验 op）。"""
    return [capability_to_tool(cap) for cap in capabilities.get("public", [])]


def filter_args_to_schema(op: str, args: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    """按 capability schema 过滤 args：丢掉未知字段（用于自修复 fix 的安全合并）。"""
    known = {c["name"]: (c.get("inputs") or {}) for c in capabilities.get("public", [])}
    schema = known.get(op, {})
    return {key: value for key, value in args.items() if key in schema}
