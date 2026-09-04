from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4


ARTIFACT_ROOT = Path("work") / "new_arch_runs"

_SNAPSHOT_KIND = re.compile(r"^snapshot_s\d+$")


def create_run_dir() -> tuple[str, Path]:
    run_id = uuid4().hex[:10]
    run_dir = ARTIFACT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def artifact_path(run_id: str, kind: str) -> Path:
    names = {
        "step": "model.step",
        "stl": "model.stl",
        "obj": "model.obj",
        "report": "report.md",
        "execution_report": "execution_report.json",
    }
    if kind not in names:
        if _SNAPSHOT_KIND.match(kind):
            # agent 可视化快照（snapshot_s{步号}.png）
            return ARTIFACT_ROOT / run_id / f"{kind}.png"
        raise KeyError(kind)
    return ARTIFACT_ROOT / run_id / names[kind]
