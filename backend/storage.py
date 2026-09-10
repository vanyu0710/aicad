from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4


ARTIFACT_ROOT = Path("work") / "new_arch_runs"
# v0.14 F2a：项目零件库（manifest 权威文件 + 版本化零件产物 + 装配产物）
PROJECT_PARTS_ROOT = Path("work") / "project_parts"

_SNAPSHOT_KIND = re.compile(r"^snapshot_s\d+$")
# v0.12 逐件交付：part_{NN}_{slug}.step/.stl（slug 无路径分隔符/盘符，防目录穿越）
_PART_KIND = re.compile(r"^part_\d{2}_[^/\\:\0]{1,64}\.(step|stl)$")
# v0.14 零件库文件名：vNNN_slug.step/.stl / assembly_NNN(_report).step/.stl/.json
_LIB_KIND = re.compile(r"^(v\d{3}_[^/\\:\0]{1,64}|assembly_\d{3})\.(step|stl|json)$")


def project_parts_dir(project_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(project_id))[:64] or "default"
    return PROJECT_PARTS_ROOT / safe


def project_artifact_path(project_id: str, filename: str) -> Path:
    """项目零件库文件解析（kind=文件名，含目录穿越防护）。"""
    if not _LIB_KIND.match(filename):
        raise KeyError(filename)
    return project_parts_dir(project_id) / filename


def read_manifest(project_id: str) -> dict:
    path = project_parts_dir(project_id) / "parts_manifest.json"
    if not path.exists():
        return {"schema_version": "1.0", "project_id": project_id, "parts": [], "assembly": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": "1.0", "project_id": project_id, "parts": [], "assembly": None}
    if not isinstance(data, dict):
        data = {"schema_version": "1.0", "project_id": project_id, "parts": [], "assembly": None}
    data.setdefault("project_id", project_id)
    data.setdefault("parts", [])
    data.setdefault("assembly", None)
    return data


def write_manifest(project_id: str, manifest: dict) -> Path:
    """原子写（tmp + replace）；调用方负责锁。"""
    directory = project_parts_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "parts_manifest.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


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
