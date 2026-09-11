from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

PROMPTS_PATH = Path(__file__).resolve().parents[2] / "prompts" / "prompts.yaml"
PROMPTS_EN_PATH = Path(__file__).resolve().parents[2] / "prompts" / "prompts_en.yaml"


@lru_cache(maxsize=4)
def load_prompts(language: str = "zh") -> dict[str, dict]:
    """Load the centralized prompts file for the requested language.

    ``MECHCAD_PROMPTS_FILE`` overrides the prompt file for both languages —
    used by prompt A/B benchmark runs to swap prompt sets without touching
    the shipped files.
    """
    override = os.environ.get("MECHCAD_PROMPTS_FILE")
    if override:
        path = Path(override)
    else:
        path = PROMPTS_PATH if language != "en" else PROMPTS_EN_PATH
    if not path.exists():
        raise FileNotFoundError(f"prompts file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def get_prompt(name: str, language: str = "zh") -> str:
    """Return the raw prompt text for a named prompt and language."""
    prompts = load_prompts(language)
    entry = prompts.get(name)
    if not isinstance(entry, dict):
        raise KeyError(f"prompt not found: {name}")
    prompt = entry.get("prompt")
    if not prompt:
        raise KeyError(f"prompt has no text: {name}")
    return str(prompt).strip()
