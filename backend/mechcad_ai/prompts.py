from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_PATH = Path(__file__).resolve().parents[2] / "prompts" / "prompts.yaml"


@lru_cache(maxsize=1)
def load_prompts() -> dict[str, dict]:
    """Load the centralized prompts file. Cached for the process lifetime."""
    if not PROMPTS_PATH.exists():
        raise FileNotFoundError(f"prompts file not found: {PROMPTS_PATH}")
    with PROMPTS_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def get_prompt(name: str) -> str:
    """Return the raw prompt text for a named prompt (vision_analysis, feature_planning, ...)."""
    prompts = load_prompts()
    entry = prompts.get(name)
    if not isinstance(entry, dict):
        raise KeyError(f"prompt not found: {name}")
    prompt = entry.get("prompt")
    if not prompt:
        raise KeyError(f"prompt has no text: {name}")
    return str(prompt).strip()
