# -*- coding: utf-8 -*-
"""x32sim/util — 通用数值/曲线工具 (纯函数, 零依赖)"""

import math

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def _finite(v, default=0.0):
    """把任意外部输入收敛成**有限**浮点数; NaN/inf/非数字一律回落 default。

    ⚠️ 为什么必须单独一层: `min(max(float(v), lo), hi)` 对 NaN 是**全部穿透**的
        （max(nan, 0)=nan、min(nan, 1)=nan），于是 NaN 会顺着 /api/state 的
        json.dumps 变成字面量 NaN —— 那不是合法 JSON，前端 JSON.parse 每次轮询
        都抛 SyntaxError，界面永久卡死；而且 save_user_config 会把 NaN 写盘，
        Python json 还能读回来 → **重启也治不好**。
        所以凡是"外部可控数字进 STATE"的入口，都必须过这一层。
    """
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(x):
        return float(default)
    return x


def _truthy(v):
    """外部"开关"值的归一化。

    ⚠️ 不能写 `1 if v else 0`：字符串 "0" / "false" / "off" 在 Python 里都是
       真值，会被判成 1。网页/OSC 传来的开关经常是字符串，于是"发 0 过去"
       结果变成了开。这里显式认下这几个假值。
    """
    if isinstance(v, str):
        return 0 if v.strip().lower() in ("", "0", "false", "off", "no", "none") else 1
    return 1 if v else 0


def _clamp01(v, default=0.0):
    """外部数字 → [0,1] 有限值（NaN/inf/非数字回落 default）。"""
    return min(max(_finite(v, default), 0.0), 1.0)


def curve_ratio(r: float, curve: str = "linear") -> float:
    """渐变速度曲线。r∈[0,1] 线性进度 → 实际完成比例。
    linear   匀速
    ease_out 先快后慢 (收尾轻柔,适合推子拉下)
    ease_in  先慢后快 (蓄力起推)
    smooth   S 形缓入缓出
    """
    r = min(max(r, 0.0), 1.0)
    if curve == "ease_out":
        return 1.0 - (1.0 - r) ** 2
    if curve == "ease_in":
        return r ** 2
    if curve == "smooth":
        return r * r * (3.0 - 2.0 * r)
    return r
