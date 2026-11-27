# -*- coding: utf-8 -*-
"""x32sim/wingfx — WING 效果器的**命名参数**表（field → 线上叶子名）。

WING 的 FX 参数不是 X32 的 `/fx/<槽>/par/<号>`，而是命名叶子：
  `/fx/1/time`（延迟时间 ms）、`/fx/2/pdel`（预延迟）、`/fx/2/dcy`（衰减 s）。
名字随算法变化，同一个概念在不同算法里也可能叫法不同。

**内部键仍是 X32 的 par 号**（`fx_par_07`）——卡片、预设、导入存档、两侧 UI
全都用它。这里只做 field → WING 名字的翻译：

    (算法, par 号) --fxlib--> field --本表--> WING 叶子名

这样换控台时存档不用迁移，也不会出现"同一个 fx_par_07 在两家指不同参数"的
串味（顺序若不同，序号只用于本地，线上发的是名字）。

数据来源与置信度（每条都可追）：
  · "doc"      《WING Remote Protocols V3.1》原文逐条列出 —— 只有 ROOM 的 15 项
  · "field"    社区实测：`/fx/1/time 500`、`/fx/2/pdel 26`、`/fx/2/dcy 1.7`
  · "inferred" 由上面两类推得（所有混响都有预延迟与衰减）
  · "probe"    真机 `/fx/N ,s ?` 枚举得到，写进 wing_params.json 后覆盖本表

⚠️ 没有证据的参数一律不填，前端按 `w=false` 灰掉。
WING 对不存在的节点回 `NODE NOT FOUND`（安全失败，不会误改别的参数），
但用户看到的是"拖了没反应"——比下标串味好，仍不如直接不给滑杆。
"""

import json
import os

from .fxlib import X32_FX_ALGOS

# ---------------------------------------------------------------- 内置表
# key = 内部算法（与 fxlib 一致），value = {field: WING 叶子名}
WING_FX_PARAMS = {
    # ROOM —— 文档原文给出完整 15 项（X32 的 level 在 WING 混响里没有对应）
    "ROOM": {
        "pre_delay": "pdel",
        "decay": "dcy",
        "size": "size",
        "damping": "damp",
        "diffuse": "diff",
        "lo_cut": "lc",
        "hi_cut": "hc",
        "bass_multi": "mult",
        "spread": "sprd",
        "shape": "shp",
        "spin": "spin",
        "echo_l": "ecl",
        "echo_r": "ecr",
        "echo_feed_l": "efl",
        "echo_feed_r": "efr",
    },
    # 其余混响：预延迟/衰减是共同项（V-REV 有社区实测，其余同一命名体系）
    "HALL": {"pre_delay": "pdel", "decay": "dcy"},
    "CHAM": {"pre_delay": "pdel", "decay": "dcy"},
    "PLAT": {"pre_delay": "pdel", "decay": "dcy"},
    "AMBI": {"pre_delay": "pdel", "decay": "dcy"},
    "VREV": {"pre_delay": "pdel", "decay": "dcy"},
    "VRM": {"pre_delay": "pdel", "decay": "dcy"},
    "GATE": {"pre_delay": "pdel", "decay": "dcy"},
    "RVRS": {"pre_delay": "pdel", "decay": "dcy"},
    # Stereo Delay —— `/fx/1/time 500`（社区实测，500 ms 浮点）
    "DLY": {"time": "time"},
}

# 每个 name 的证据等级（供 /api/info 与排查用；probe 覆盖后变 "probe"）
WING_PARAM_SOURCE = {}
for _algo, _m in WING_FX_PARAMS.items():
    for _f, _n in _m.items():
        WING_PARAM_SOURCE["%s.%s" % (_algo, _n)] = "doc" if _algo == "ROOM" else (
            "field" if _algo in ("VREV", "DLY") else "inferred")


# ---------------------------------------------------------------- 真机覆盖
def probe_path():
    """wing_params.json 的位置（与 x32_simulator.py 同目录）。

    两种形态都要对：包形态下 `__file__` 在 x32sim/ 里，单文件形态下就在根目录。
    """
    base = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(base) == "x32sim":
        base = os.path.dirname(base)
    return os.path.join(base, "wing_params.json")


_PROBE = {"mtime": None, "table": None}


def _load_probe():
    """读探测结果并按 field 合并。文件不存在/损坏 → 空表（不影响内置表）。"""
    p = probe_path()
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    if _PROBE["mtime"] == mt and _PROBE["table"] is not None:
        return _PROBE["table"]
    table = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for algo, params in (raw.get("algos") or {}).items():
            m = {}
            for item in params:
                field = item.get("field")
                name = item.get("name")
                if field and name:
                    m[str(field)] = str(name)
            if m:
                table[str(algo)] = m
    except (OSError, ValueError, TypeError, AttributeError):
        table = {}
    _PROBE["mtime"] = mt
    _PROBE["table"] = table
    return table


def wing_params(algo):
    """某算法当前生效的 field → 叶子名表（内置 + 真机探测覆盖）。"""
    base = dict(WING_FX_PARAMS.get(algo) or {})
    base.update(_load_probe().get(algo) or {})
    return base


def wing_par_name(algo, pnum):
    """(算法, par 号) → WING 叶子名；无证据返回 None（调用方拒绝写入）。"""
    d = X32_FX_ALGOS.get(algo or "")
    if not d:
        return None
    try:
        n = int(pnum)
    except (TypeError, ValueError):
        return None
    if n < 1 or n > len(d["params"]):
        return None
    field = d["params"][n - 1]["field"]
    return wing_params(algo).get(field) or None


def wing_par_num(algo, name):
    """WING 叶子名 → 该算法的 par 号（**反查**，用于真台回读 `/fx/1/pdel`）。"""
    d = X32_FX_ALGOS.get(algo or "")
    if not d:
        return None
    table = wing_params(algo)
    for field, nm in table.items():
        if nm != name:
            continue
        for i, p in enumerate(d["params"]):
            if p["field"] == field:
                return i + 1
    return None


def wing_par_sources():
    """field 级证据清单（诊断用）：{算法: {叶子名: 来源}}。"""
    out = {}
    for algo in WING_FX_PARAMS:
        m = {}
        for field, name in wing_params(algo).items():
            m[name] = WING_PARAM_SOURCE.get("%s.%s" % (algo, name), "probe")
        out[algo] = m
    return out
