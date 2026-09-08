"""受限纯算术沙箱——design_calculate(kind="custom") 的执行后端。

模型可以自行编写**纯算术**调研公式（超出内置 kind 覆盖时）。这里守三条线：

1. **AST 白名单**：只允许算术表达式、赋值、for/if、对 range/数学函数的调用；
   import / def / class / 属性访问（杜绝 `().__class__` 链）/ 下标 / lambda /
   字符串格式化 / 超大数值常量 全部拒绝。父子进程各校验一次。
2. **子进程隔离**：`python -I`（isolated：不吃用户 site-packages 与环境），
   `__builtins__` 清空，命名空间只注入白名单数学函数与用户变量。
   无文件/网络接口可达（没有 import，也没有暴露任何 IO 函数）。
3. **资源限额**：代码 ≤ 4000 字符、变量 ≤ 32 个、range 上界 ≤ 1e7、
   超时默认 5s（kill）、结果 JSON ≤ 4000 字符且只含标量/list/dict。

与"worker 永不执行任意 AI Python"的边界一致：这里执行的不是 CAD 代码，
不接触几何与会话状态，失败/超时只回喂错误给模型。
`MECHCAD_AGENT_CALC_SANDBOX=off` 可整体禁用 custom kind。
"""
from __future__ import annotations

import ast
import json
import keyword
import math
import os
import subprocess
import sys
from typing import Any

MAX_CODE_CHARS = 4000
MAX_VARIABLES = 32
MAX_RANGE_ARG = 10 ** 7
MAX_RESULT_CHARS = 4000
DEFAULT_TIMEOUT = 5.0

_ALLOWED_BUILTIN_NAMES = {
    "sqrt", "cbrt", "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "exp", "log", "log2", "log10", "pow", "abs", "min", "max", "sum",
    "round", "floor", "ceil", "factorial", "hypot", "radians", "degrees",
    "gcd", "pi", "PI", "tau", "e", "E", "inf", "range", "len",
    "result",  # 约定输出变量（允许出现于 AST 的名字检查）
}


class SandboxError(ValueError):
    """沙箱拒绝执行。message 面向 LLM，指导其改用内置 kind 或修正代码。"""


def _allowed_namespace() -> dict[str, Any]:
    ns: dict[str, Any] = {
        "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
        "exp": math.exp, "log": math.log, "log2": math.log2, "log10": math.log10,
        "pow": pow, "abs": abs, "min": min, "max": max, "sum": sum,
        "round": round, "floor": math.floor, "ceil": math.ceil,
        "factorial": math.factorial, "hypot": math.hypot,
        "radians": math.radians, "degrees": math.degrees, "gcd": math.gcd,
        "pi": math.pi, "PI": math.pi, "tau": math.tau, "e": math.e, "E": math.e,
        "inf": math.inf, "range": range, "len": len,
    }
    return ns


_ALLOWED_STMTS = (ast.Assign, ast.AugAssign, ast.For, ast.If, ast.Expr)
_ALLOWED_EXPRS = (
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call,
    ast.Name, ast.Load, ast.Store,
    ast.operator, ast.unaryop, ast.cmpop, ast.boolop,
    ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.keyword,
    ast.And, ast.Or, ast.Not,
    # 具体算术/比较运算符节点
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn,
)


def validate_code(code: str) -> ast.Module:
    """AST 层静态校验；违规抛 SandboxError（含可读原因）。"""
    if not isinstance(code, str) or not code.strip():
        raise SandboxError("code 不能为空")
    if len(code) > MAX_CODE_CHARS:
        raise SandboxError(f"code 超长（{len(code)} > {MAX_CODE_CHARS} 字符）")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxError(f"代码语法错误: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            continue
        if isinstance(node, _ALLOWED_STMTS) or isinstance(node, _ALLOWED_EXPRS):
            pass
        elif isinstance(node, ast.Name):
            pass  # 变量名在运行时由受限 namespace 决定
        elif isinstance(node, ast.arguments) or isinstance(node, ast.arg):
            raise SandboxError("禁止定义函数（def/lambda），写表达式即可")
        else:
            # Lambda/Import/Attribute/Subscript/Comprehension/While/With/... 全部落这里
            raise SandboxError(
                f"不允许的语法结构 {type(node).__name__}：沙箱只支持纯算术表达式、"
                "赋值、for/if（无 import、无属性访问、无下标、无自定义函数）"
            )
    # 数值常量限额（防 range 爆炸）
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            v = abs(float(node.value))
            if v > 10 ** 9:
                raise SandboxError(f"数值常量 {node.value} 超出 1e9 上限")
    # 顶层必须给 result 赋值
    assigns_result = any(
        isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "result" for t in stmt.targets)
        for stmt in tree.body
    )
    if not assigns_result:
        raise SandboxError("代码必须以 `result = ...` 输出最终结果（可以是 dict 汇总）")
    return tree


def _jsonable(value: Any, _depth: int = 0) -> Any:
    """结果必须只含标量/list/dict，深度 ≤ 4，总长受限。"""
    if _depth > 4:
        raise SandboxError("结果嵌套过深（> 4 层）")
    if value is None or isinstance(value, bool) or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SandboxError("结果含 NaN/Inf")
        return round(value, 10)
    if isinstance(value, (list, tuple)):
        if len(value) > 200:
            raise SandboxError("结果 list 长度 > 200")
        return [_jsonable(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise SandboxError("结果 dict 键数 > 100")
        out = {}
        for k, v in value.items():
            if not isinstance(k, (str, int)):
                raise SandboxError("结果 dict 键必须是 str/int")
            out[str(k)] = _jsonable(v, _depth + 1)
        return out
    raise SandboxError(f"结果含不支持的类型 {type(value).__name__}")


# 子进程执行器（-I 隔离模式下运行，stdin 收 JSON，stdout 回 JSON）。
_RUNNER = r"""
import ast, json, math, sys

payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
code = payload["code"]
variables = payload.get("variables") or {}

_ALLOWED_STMTS = tuple(
    getattr(ast, n) for n in ("Assign", "AugAssign", "For", "If", "Expr")
)
_ALLOWED_EXPRS = tuple(
    getattr(ast, n) for n in (
        "BinOp", "UnaryOp", "BoolOp", "Compare", "Call", "Name", "Load", "Store",
        "operator", "unaryop", "cmpop", "boolop", "Constant", "Tuple", "List",
        "Dict", "keyword", "And", "Or", "Not", "Add", "Sub", "Mult", "Div",
        "FloorDiv", "Mod", "Pow", "USub", "UAdd", "Eq", "NotEq", "Lt", "LtE",
        "Gt", "GtE", "Is", "IsNot", "In", "NotIn",
    )
)

def check(src):
    tree = ast.parse(src, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Module) or isinstance(node, ast.Name) or node in ():
            continue
        if not (isinstance(node, _ALLOWED_STMTS) or isinstance(node, _ALLOWED_EXPRS)):
            raise ValueError("disallowed syntax " + type(node).__name__)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "range":
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, (int, float)) \
                        and abs(a.value) > 10 ** 7:
                    raise ValueError("range arg too large")
    if not any(
        isinstance(s, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "result" for t in s.targets)
        for s in tree.body
    ):
        raise ValueError("missing result assignment")
    return tree

ns = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "exp": math.exp, "log": math.log, "log2": math.log2, "log10": math.log10,
    "pow": pow, "abs": abs, "min": min, "max": max, "sum": sum, "round": round,
    "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
    "hypot": math.hypot, "radians": math.radians, "degrees": math.degrees,
    "gcd": math.gcd, "pi": math.pi, "tau": math.tau, "e": math.e,
    "range": range, "len": len, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
}
ns.update(variables)
g = {"__builtins__": {}}
g.update(ns)
try:
    check(code)
    exec(compile(code, "<sandbox>", "exec"), g)
    out = {"ok": True, "result": g.get("result")}
except Exception as exc:
    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, default=str).encode("utf-8"))
"""


def validate_variables(variables: Any) -> dict[str, float]:
    if variables is None:
        return {}
    if not isinstance(variables, dict):
        raise SandboxError("variables 必须是 {名字: 数字} 的 JSON 对象")
    if len(variables) > MAX_VARIABLES:
        raise SandboxError(f"variables 数量超过上限 {MAX_VARIABLES}")
    out: dict[str, float] = {}
    for name, value in variables.items():
        if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) \
                or name in _ALLOWED_BUILTIN_NAMES:
            raise SandboxError(f"非法变量名 {name!r}（需为未被占用的标识符）")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SandboxError(f"变量 {name} 必须是数字（收到 {value!r}）")
        v = float(value)
        if not math.isfinite(v):
            raise SandboxError(f"变量 {name} 不是有限数字")
        out[name] = v
    return out


def sandbox_enabled() -> bool:
    return os.environ.get("MECHCAD_AGENT_CALC_SANDBOX", "on").strip().lower() != "off"


def run_sandboxed(code: str, variables: dict | None = None,
                  timeout: float = DEFAULT_TIMEOUT) -> dict:
    """执行纯算术代码，返回 {"result": ...}。失败抛 SandboxError。"""
    if not sandbox_enabled():
        raise SandboxError(
            "custom 沙箱计算已被管理员禁用（MECHCAD_AGENT_CALC_SANDBOX=off），"
            "请改用内置 kind 完成调研"
        )
    validate_code(code)  # 父进程校验（快速反馈）
    # 补一层 range 参数检查（父进程侧与 runner 对齐）
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "range":
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, (int, float)) \
                        and abs(a.value) > MAX_RANGE_ARG:
                    raise SandboxError(f"range 参数上限 {MAX_RANGE_ARG}（收到 {a.value}）")
    variables = validate_variables(variables)
    payload = json.dumps({"code": code, "variables": variables}, ensure_ascii=False)
    child_env = {}
    if os.name == "nt":
        # Windows 上 CPython 启动需要 SystemRoot；其余环境变量一律不继承
        for key in ("SystemRoot", "SYSTEMROOT"):
            if key in os.environ:
                child_env[key] = os.environ[key]
                break
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _RUNNER],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=child_env,  # 不继承任何（其余）环境变量
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"计算超时（> {timeout}s）：请缩小循环规模或改用内置 kind") from exc
    except OSError as exc:
        raise SandboxError(f"沙箱进程无法启动: {exc}") from exc
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not raw:
        stderr_tail = proc.stderr.decode("utf-8", errors="replace")[-400:]
        raise SandboxError(f"沙箱执行失败（exit={proc.returncode}）: {stderr_tail or raw or '无输出'}")
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SandboxError(f"沙箱输出非法 JSON: {raw[:200]!r}") from exc
    if not out.get("ok"):
        raise SandboxError(f"计算错误: {out.get('error')}")
    result = _jsonable(out.get("result"))
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        raise SandboxError(f"结果过大（{len(text)} > {MAX_RESULT_CHARS} 字符）：请只返回关键数值")
    return {"kind": "custom", "result": result, "variables_used": sorted(variables),
            "method": "sandboxed_custom_arithmetic"}
