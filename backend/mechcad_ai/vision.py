from __future__ import annotations

"""Vision model integration: read a sketch + description into structured JSON."""

import base64
import io
from typing import Any

from PIL import Image

from backend.mechcad_ai.client import ApiCallError, chat_completion, has_configured_model, parse_json_object
from backend.mechcad_ai.prompts import get_prompt


def analyze_sketch(
    image: Image.Image | None,
    description: str,
    settings,
    language: str = "zh",
) -> dict[str, Any] | None:
    """Run the vision model over the sketch, returning None when unavailable."""
    if not has_configured_model(settings, "vision"):
        return None

    prompt = get_prompt("vision_analysis", language)
    image_base64 = _image_to_base64(image) if image is not None else None
    user_prompt = (
        f"User functional description:\n{description}\n\n"
        "Read the sketch according to the system rules and return strict JSON. Do not output prose."
        if language == "en"
        else f"用户功能描述：\n{description}\n\n请按系统规则读取草图，返回严格 JSON。不要输出解释性散文。"
    )

    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": user_prompt,
        },
    ]
    try:
        content = chat_completion(
            settings,
            "vision",
            messages,
            max_tokens=4096,
            temperature=0.1,
            response_json=True,
            image_base64=image_base64,
        )
    except ApiCallError:
        return None

    parsed = parse_json_object(content)
    parsed.setdefault("source", "api")
    return parsed


def _image_to_base64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
