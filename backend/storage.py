from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4


ARTIFACT_ROOT = Path("work") / "new_arch_runs"

_SNAPSHOT_KIND = re.compile(r"^snapshot_s\d+$")
# v0.12 逐件交付：part_{NN}_{slug}.step/.stl（slug 无路径分隔符/盘符，防目录穿越）
_PART_KIND = re.compile(r"^part_\d{2}_[^/\\:\0]{1,64}\.(step|stl)$")


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
        if _PART_KIND.match(kind):
            # agent 逐件归档的零件 STEP/STL（文件名即 kind）
            return ARTIFACT_ROOT / run_id / kind
        raise KeyError(kind)
    return ARTIFACT_ROOT / run_id / names[kind]
