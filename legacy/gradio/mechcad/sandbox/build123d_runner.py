from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "compile",
    "open",
    "__import__",
    "input",
    "globals",
    "locals",
    "vars",
    "dir",
}
FORBIDDEN_MODULES = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "pathlib",
    "shutil",
    "requests",
    "urllib",
    "importlib",
}
ALLOWED_IMPORTS = {"build123d", "math"}
UNSUPPORTED_BUILD123D_CALLS = {
    "BuildSketch",
    "Rectangle",
    "extrude",
    "revolve",
    "make_face",
}


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    stdout: str
    stderr: str
    step_path: str | None
    stl_path: str | None


def run_build123d(code: str, run_dir: Path, timeout: int = 30) -> SandboxResult:
    try:
        validate_code(code)
    except ValueError as exc:
        return SandboxResult(False, "", f"Static check failed: {exc}", None, None)

    script_path = run_dir / "generated_model.py"
    script_path.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path.name)],
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            False,
            (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            f"Build123d execution timed out after {timeout}s.",
            None,
            None,
        )
    step_path = run_dir / "model.step"
    stl_path = run_dir / "model.stl"
    ok = proc.returncode == 0 and step_path.exists() and stl_path.exists()
    return SandboxResult(
        ok=ok,
        stdout=proc.stdout[-4000:],
        stderr=proc.stderr[-4000:],
        step_path=str(step_path) if step_path.exists() else None,
        stl_path=str(stl_path) if stl_path.exists() else None,
    )


def validate_code(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _validate_import(node)
        if isinstance(node, ast.Call):
            _validate_call(node)
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("dunder attribute access is not allowed")


def _validate_import(node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        modules = [alias.name.split(".")[0] for alias in node.names]
    else:
        modules = [(node.module or "").split(".")[0]]
    for module in modules:
        if module in FORBIDDEN_MODULES or module not in ALLOWED_IMPORTS:
            raise ValueError(f"import not allowed: {module}")


def _validate_call(node: ast.Call) -> None:
    name = ""
    if isinstance(node.func, ast.Name):
        name = node.func.id
    elif isinstance(node.func, ast.Attribute):
        name = node.func.attr
    if name in FORBIDDEN_NAMES:
        raise ValueError(f"call not allowed: {name}")
    if name in UNSUPPORTED_BUILD123D_CALLS:
        raise ValueError(f"unsupported Build123d call for MVP sandbox: {name}")
    if name == "Hole" and any(keyword.arg == "diameter" for keyword in node.keywords):
        raise ValueError("Hole(diameter=...) is not supported; use Hole(radius) or subtract a Cylinder")
