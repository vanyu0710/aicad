"""设计计算调研（纯算术，无 CAD / 无 IO）——agent 的 `design_calculate` 工具后端。

对齐 D2：backend 进程不 import 任何 CAD 库。本模块只用标准库；齿轮几何公式与
mechcad-kernel 的 mech_kernel/gear.py 同源（ISO 6336/21771），一致性由
tests/test_designcalc.py 的对拍用例锁定。

诚实原则：工程估算类输出（壁厚、许用应力）一律带 method="empirical" 标注，
公式粗算不等于强度校核，交付前必须人工复核。
"""
from __future__ import annotations

import bisect
import math
from typing import Any

# GB/T 1357 模数第一系列（常用段）
STANDARD_MODULES = [
    1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0,
]

# 常用轴径（mm，轴承位优先取整到 5 的倍数的粗系列）
STANDARD_SHAFT_DIAMETERS = [
    12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 35, 38, 40, 42, 45, 48, 50,
    55, 60, 65, 70, 75, 80, 85, 90, 95, 100,
]

# 20° 压力角标准齿轮不根切的最少齿数
MIN_TEETH_NO_UNDERCUT = 17


class CalcError(ValueError):
    """设计计算输入非法。message 面向 LLM，要能指导改参。"""


def _num(name: str, value: Any, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalcError(f"{name} 必须是数字（收到 {value!r}）")
    v = float(value)
    if not math.isfinite(v) or v < lo or v > hi:
        raise CalcError(f"{name} 必须在 [{lo}, {hi}] 内（收到 {v}）")
    return v


def _int(name: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value):
        raise CalcError(f"{name} 必须是整数（收到 {value!r}）")
    v = int(value)
    if v < lo or v > hi:
        raise CalcError(f"{name} 必须在 [{lo}, {hi}] 内（收到 {v}）")
    return v


# ------------------------------------------------------------------ 传动比分级

def gear_ratio_split(
    total_ratio: float,
    max_stages: int = 4,
    min_stage_ratio: float = 3.0,
    max_stage_ratio: float = 8.0,
    tolerance: float = 0.02,
    top_n: int = 5,
) -> dict:
    """把总传动比拆成多级齿轮副（每级 = 一对整数齿数比 z2/z1）。

    搜索空间：小齿轮 z1 ∈ [17, 30]（≥17 免根切），大齿轮 z2 ≤ 140，
    单级传动比限制在 [min_stage_ratio, max_stage_ratio]（>8 单级结构过大，
    <3 级数浪费）。返回按 (级数, 总误差, 最大齿轮直径) 排序的方案列表。
    """
    total = _num("total_ratio", total_ratio, 1.05, 1000.0)
    stages_max = _int("max_stages", max_stages, 1, 4)
    rmin = _num("min_stage_ratio", min_stage_ratio, 1.05, 20.0)
    rmax = _num("max_stage_ratio", max_stage_ratio, 1.05, 30.0)
    tol = _num("tolerance", tolerance, 0.0001, 0.10)
    n_out = _int("top_n", top_n, 1, 20)
    if rmin > rmax:
        raise CalcError("min_stage_ratio 不能大于 max_stage_ratio")

    # 候选单级传动比：按 ratio 去重，同比值取齿数最小的紧凑副
    cand: dict[float, dict] = {}
    for z1 in range(MIN_TEETH_NO_UNDERCUT, 31):
        for z2 in range(z1 + 2, 141):
            r = z2 / z1
            if rmin <= r <= rmax:
                key = round(r, 4)
                if key not in cand or z1 + z2 < cand[key]["z1"] + cand[key]["z2"]:
                    cand[key] = {"ratio": r, "z1": z1, "z2": z2, "ratio_key": key}
    candidates = sorted(cand.values(), key=lambda c: c["ratio"])
    if not candidates:
        raise CalcError(
            f"候选单级传动比集合为空（min={rmin}, max={rmax}），放宽范围再试"
        )

    log_total = math.log(total)
    tol_log = math.log(1.0 + tol)
    rmin_log = math.log(candidates[0]["ratio"])
    for c in candidates:
        c["log"] = math.log(c["ratio"])

    # 按级数 k 从少到多分别枚举（少级数天然更优，不能被高级数淹没）。
    # 精确 k 级 DFS：非递增序去排列重复 + 剩余量上下界剪枝 + 节点预算。
    ratio_sorted = [c["ratio"] for c in candidates]  # 升序

    def split_exact(k: int, node_budget: int = 50000) -> list[dict]:
        best: dict[tuple, dict] = {}
        nodes = [0]

        def rec(remaining: float, cap_log: float, depth: int, path: list[dict]) -> None:
            nodes[0] += 1
            if nodes[0] > node_budget:
                return
            if depth == k - 1:
                # 最后一级：需要的传动比必须在候选表里（非递增 → ratio ≤ exp(cap_log)）
                pos = bisect.bisect_left(ratio_sorted, math.exp(remaining))
                for i in (pos - 1, pos):
                    if 0 <= i < len(candidates) and candidates[i]["log"] <= cap_log + 1e-12 \
                            and abs(remaining - candidates[i]["log"]) <= tol_log:
                        key = tuple(p["ratio_key"] for p in path) + (candidates[i]["ratio_key"],)
                        err = abs(remaining - candidates[i]["log"])
                        prev = best.get(key)
                        if prev is None or err < prev[0]:
                            best[key] = (err, {
                                "stages": path + [candidates[i]],
                                "stage_count": k,
                                "exact_total": math.exp(
                                    log_total - (remaining - candidates[i]["log"])),
                            })
                return
            left = k - depth - 1
            for c in candidates:
                rl = c["log"]
                if rl > cap_log + 1e-12:
                    break                       # 非递增枚举
                nr = remaining - rl
                if nr > left * rl + tol_log:
                    continue                    # 这一级取小了，后面（更大）也许补得上
                if nr < left * rmin_log - tol_log:
                    break                        # 取大了，后面只会更小 → 停
                rec(nr, rl, depth + 1, path + [c])

        rec(log_total, math.inf, 0, [])
        return [s for _, s in sorted(best.values(),
                                     key=lambda p: (round(p[0], 5),
                                                    max(st["z2"] for st in p[1]["stages"])))[:20]]

    solutions: list[dict] = []
    for k in range(1, stages_max + 1):
        solutions = split_exact(k)
        if solutions:
            break  # 找到最小可行级数的方案集即可（级数是首要排序维度）

    # 排序：相对误差 → 最大齿轮（齿数合计近似直径代价）
    def score(s: dict) -> tuple:
        err = abs(math.log(s["exact_total"] / total))
        worst = max(st["z2"] for st in s["stages"])
        teeth_sum = sum(st["z1"] + st["z2"] for st in s["stages"])
        return (round(err, 4), worst, teeth_sum)

    solutions.sort(key=score)
    schemes = []
    for s in solutions[:n_out]:
        stages = []
        for st in s["stages"]:
            exact = st["z2"] / st["z1"]
            stages.append({
                "z1": st["z1"], "z2": st["z2"],
                "stage_ratio": round(exact, 4),
                "note": f"第 {len(stages) + 1} 级 {st['z1']}/{st['z2']}",
            })
        schemes.append({
            "stages": stages,
            "stage_count": s["stage_count"],
            "exact_total_ratio": round(s["exact_total"], 3),
            "ratio_error_pct": round((s["exact_total"] / total - 1.0) * 100.0, 2),
        })

    stage_count_notes = []
    if not schemes:
        stage_count_notes.append(
            f"在单级 {rmin}~{rmax}、z2≤140 约束下无解；请放宽 min/max_stage_ratio 或提高 max_stages"
        )
    for k in range(1, stages_max + 1):
        per_stage = total ** (1.0 / k)
        if not (rmin - 1e-9 <= per_stage <= rmax + 1e-9):
            stage_count_notes.append(
                f"{k} 级需要每级 {per_stage:.2f}，超出单级允许 {rmin}~{rmax}"
            )
    return {
        "kind": "gear_ratio_split",
        "total_ratio": total,
        "constraints": {
            "min_stage_ratio": rmin, "max_stage_ratio": rmax,
            "max_stages": stages_max, "tolerance": tol,
            "pinion_teeth_range": [MIN_TEETH_NO_UNDERCUT, 30],
            "max_gear_teeth": 140,
        },
        "schemes": schemes,
        "infeasible_stage_notes": stage_count_notes,
        "assumptions": [
            "未做变位修正；根切由 z1>=17 保证",
            "实际还需按中心距圆整/装配空间复核级间分配",
        ],
    }


# ------------------------------------------------------------------ 齿轮副几何

def gear_pair(
    module: float,
    z1: int,
    z2: int,
    pressure_angle_deg: float = 20.0,
    addendum_ratio: float = 1.0,
    dedendum_ratio: float = 1.25,
) -> dict:
    """标准直齿圆柱齿轮副几何（与 kernel gear_geometry/center_distance 同源）。"""
    m = _num("module", module, 0.1, 50.0)
    t1 = _int("z1", z1, 6, 400)
    t2 = _int("z2", z2, 6, 400)
    alpha = math.radians(_num("pressure_angle_deg", pressure_angle_deg, 14.0, 30.0))
    ha = _num("addendum_ratio", addendum_ratio, 0.5, 1.5)
    hf = _num("dedendum_ratio", dedendum_ratio, 1.0, 1.5)

    def one(z: int) -> dict:
        r = m * z / 2.0
        return {
            "teeth": z,
            "pitch_radius": round(r, 3),
            "pitch_diameter": round(2 * r, 3),
            "base_radius": round(r * math.cos(alpha), 3),
            "addendum_radius": round(r + ha * m, 3),
            "dedendum_radius": round(r - hf * m, 3),
            "tip_diameter": round(2 * (r + ha * m), 3),
            "root_diameter": round(2 * (r - hf * m), 3),
        }

    a = m * (t1 + t2) / 2.0  # 中心距
    rb1 = (m * t1 / 2.0) * math.cos(alpha)
    rb2 = (m * t2 / 2.0) * math.cos(alpha)
    ra1 = m * t1 / 2.0 + ha * m
    ra2 = m * t2 / 2.0 + ha * m
    pf = math.pi * m * math.cos(alpha)  # 基圆齿距
    eps_alpha = (
        (math.sqrt(max(ra1 * ra1 - rb1 * rb1, 0.0))
         + math.sqrt(max(ra2 * ra2 - rb2 * rb2, 0.0))
         - a * math.tan(alpha)) / pf
    )
    ratio = t2 / t1
    warnings = []
    if t1 < MIN_TEETH_NO_UNDERCUT:
        warnings.append(
            f"z1={t1} < {MIN_TEETH_NO_UNDERCUT}：标准齿轮根切，需正变位或改小传动比"
        )
    if eps_alpha < 1.2:
        warnings.append(f"端面重合度 {eps_alpha:.2f} < 1.2，传动平稳性差（可增大齿数或加宽齿宽）")
    return {
        "kind": "gear_pair",
        "module": m,
        "module_is_standard": any(abs(m - s) < 1e-9 for s in STANDARD_MODULES),
        "pressure_angle_deg": math.degrees(alpha),
        "ratio": round(ratio, 4),
        "center_distance": round(a, 3),
        "circular_pitch": round(math.pi * m, 3),
        "tooth_thickness_at_pitch": round(math.pi * m / 2.0, 3),
        "transverse_contact_ratio": round(eps_alpha, 3),
        "pinion": one(t1),
        "gear": one(t2),
        "warnings": warnings,
        "assumptions": ["标准齿轮，无变位；顶隙按 c*=0.25 隐含在 hf=1.25 中"],
    }


def nearest_standard_module(target: float) -> dict:
    """给定计算模数，返回第一系列圆整值（偏大优先，强度保守）。"""
    t = _num("target", target, 0.1, 50.0)
    up = next((s for s in STANDARD_MODULES if s >= t - 1e-9), STANDARD_MODULES[-1])
    down = max((s for s in STANDARD_MODULES if s <= t + 1e-9), default=STANDARD_MODULES[0])
    return {
        "kind": "nearest_standard_module",
        "target": t,
        "rounded_up": up,
        "rounded_down": down,
        "recommended": up,
        "note": "工程上模数偏圆整值大者偏安全；小齿轮材料/热处理未定前仅作选型参考",
    }


# ------------------------------------------------------------------ 轴径估算

def shaft_diameter(
    power_kw: float | None = None,
    rpm: float | None = None,
    torque_nm: float | None = None,
    allowable_shear_mpa: float = 40.0,
) -> dict:
    """扭转刚度/强度粗算最小轴径：d >= (16000*T / (pi*[tau]))^(1/3)。

    输入扭矩（牛·米）或 功率(kW)+转速(rpm) 二选一。结果未含键槽削弱
    （有键槽 ×1.05~1.2），仅作初估，正式设计需做弯扭合成与疲劳校核。
    """
    if torque_nm is not None:
        t_nm = _num("torque_nm", torque_nm, 0.001, 1e7)
        source = "torque_nm"
    elif power_kw is not None and rpm is not None:
        p = _num("power_kw", power_kw, 1e-4, 1e5)
        n = _num("rpm", rpm, 1.0, 1e6)
        t_nm = 9550.0 * p / n
        source = "power_kw+rpm"
    else:
        raise CalcError("需要 torque_nm，或 power_kw + rpm")
    tau = _num("allowable_shear_mpa", allowable_shear_mpa, 5.0, 200.0)
    d_min = (16000.0 * t_nm / (math.pi * tau)) ** (1.0 / 3.0)
    d_key = d_min * 1.10  # 单键槽削弱 10%（机械设计手册经验值）
    recommended = next((s for s in STANDARD_SHAFT_DIAMETERS if s >= d_key), STANDARD_SHAFT_DIAMETERS[-1])
    return {
        "kind": "shaft_diameter",
        "input_source": source,
        "torque_nm": round(t_nm, 3),
        "allowable_shear_mpa": tau,
        "d_min_mm": round(d_min, 2),
        "d_with_keyway_mm": round(d_key, 2),
        "recommended_standard_mm": recommended,
        "method": "empirical",
        "assumptions": [
            "实心圆轴、纯扭转初估；未做弯曲/疲劳/刚度校核",
            "[tau]=40MPa 为 45 钢粗算惯例值，正式选型按材料与工况复核",
        ],
    }


# ------------------------------------------------------------------ 壳体壁厚

def housing_wall(
    center_distance_mm: float | None = None,
    shaft_diameter_mm: float | None = None,
) -> dict:
    """铸造减速器箱体壁厚经验估算（非校核！）。

    t ≈ 0.025·a + 3（a=大级中心距），下限 6mm、上限 14mm；
    无中心距时用 t ≈ 0.04·d_shaft + 4 的更粗近似。
    """
    if center_distance_mm is not None:
        a = _num("center_distance_mm", center_distance_mm, 10.0, 4000.0)
        t = 0.025 * a + 3.0
        basis = {"center_distance_mm": a}
    elif shaft_diameter_mm is not None:
        d = _num("shaft_diameter_mm", shaft_diameter_mm, 1.0, 1000.0)
        t = 0.04 * d + 4.0
        basis = {"shaft_diameter_mm": d}
    else:
        raise CalcError("需要 center_distance_mm 或 shaft_diameter_mm")
    t = min(max(t, 6.0), 14.0)
    return {
        "kind": "housing_wall",
        "basis": basis,
        "wall_thickness_mm": round(t, 1),
        "method": "empirical",
        "note": "铸造箱体（灰口铸铁 HT200 级）经验壁厚；轴承座凸台与肋板另算，装配孔壁需 ≥2 倍孔径余量复核",
    }


# ------------------------------------------------------------------ 分发

CALCULATORS = {
    "gear_ratio_split": gear_ratio_split,
    "gear_pair": gear_pair,
    "nearest_standard_module": nearest_standard_module,
    "shaft_diameter": shaft_diameter,
    "housing_wall": housing_wall,
}


def run_builtin(kind: str, params: dict | None) -> dict:
    """按 kind 调内置计算器。params 是原始 JSON dict，由各计算器自行校验。"""
    fn = CALCULATORS.get(kind)
    if fn is None:
        raise CalcError(
            f"未知计算 kind: {kind}；可选 {sorted(CALCULATORS)} 或 kind='custom' 走沙箱"
        )
    if params is not None and not isinstance(params, dict):
        raise CalcError("params 必须是 JSON 对象")
    return fn(**(params or {}))
