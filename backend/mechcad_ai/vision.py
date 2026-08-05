from __future__ import annotations

"""Vision model integration: read a hand sketch + description into a structured
vision-analysis JSON, following the centralized prompts.yaml prompt."""

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
) -> dict[str, Any] | None:
    """Run the vision model over the sketch. Returns the vision JSON dict, or None
    when no vision model is configured (caller falls back to local analysis)."""
    if not has_configured_model(settings, "vision"):
        return None

    prompt = get_prompt("vision_analysis")
    image_base64 = _image_to_base64(image) if image is not None else None

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"用户功能描述：\n{description}\n\n请按上述规则读取并返回 JSON。"},
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
