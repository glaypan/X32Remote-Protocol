# -*- coding: utf-8 -*-
"""x32sim/quickkeys — 自定义控制键 (步进 + 电动推子渐变)"""

import threading
import time

from .logbus import log
from .state import STATE, db_to_fader, get_value, set_value
from .util import curve_ratio

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ------------------------------------------------------------ 自定义控制键
# 歌手实时微调场景: 「耳返增大/减小」等大按钮,按步长(1.5/3/6 dB)调整,可带渐变
KEY_CFG = {"step_db": 3.0, "ramp": 0.4}   # ramp 秒:渐变速度,0=瞬间
QUICK_KEYS = [
    {"id": "iem_up",  "name": "耳返 增大", "icon": "🎧➕", "kind": "bus_fader", "idx": 15, "sign": +1},
    {"id": "iem_dn",  "name": "耳返 减小", "icon": "🎧➖", "kind": "bus_fader", "idx": 15, "sign": -1},
    {"id": "main_up", "name": "主输出 增大", "icon": "🔊➕", "kind": "main_fader", "idx": 0, "sign": +1},
    {"id": "main_dn", "name": "主输出 减小", "icon": "🔉➖", "kind": "main_fader", "idx": 0, "sign": -1},
    {"id": "dca1_up", "name": "乐器组 增大", "icon": "🎸➕", "kind": "dca_fader", "idx": 1, "sign": +1},
    {"id": "dca1_dn", "name": "乐器组 减小", "icon": "🎸➖", "kind": "dca_fader", "idx": 1, "sign": -1},
]
RAMP_STOP = threading.Event()   # 新的推子运动打断上一次


def ramp_to(kind, idx, target, seconds, curve="smooth"):
    """推子平滑运动到目标位置(电动推子),可被下一次运动打断。"""
    RAMP_STOP.set()

    def worker():
        RAMP_STOP.clear()
        start = get_value(kind, idx)
        steps = max(1, int(seconds / 0.05))
        try:
            for s in range(1, steps + 1):
                if RAMP_STOP.is_set():
                    return
                time.sleep(seconds / steps)
                ratio = curve_ratio(s / steps, curve)
                set_value(kind, idx, start + (target - start) * ratio)
        finally:
            if not RAMP_STOP.is_set():
                set_value(kind, idx, target)
    threading.Thread(target=worker, daemon=True).start()


# 控制键允许的 kind（与 /api/keyadd、/api/import 的 kindmap 保持一致）
QUICK_KEY_KINDS = {"ch_fader", "bus_fader", "dca_fader", "main_fader",
                   "auxin_fader", "usb_fader", "fxret_fader", "mtx_fader"}


def apply_key(key_id):
    key = next((k for k in QUICK_KEYS if k["id"] == key_id), None)
    if not key:
        return None
    step = float(KEY_CFG["step_db"])
    ramp = float(KEY_CFG["ramp"])
    cur_db = STATE.fader_to_db(get_value(key["kind"], key["idx"]))
    new_db = min(max(cur_db + key["sign"] * step, -90.0), 10.0)
    target = db_to_fader(new_db)
    if ramp > 0.01:
        ramp_to(key["kind"], key["idx"], target, ramp)
    else:
        set_value(key["kind"], key["idx"], target)
    log("KEY %s: %.1f dB → %.1f dB (%s)" %
        (key["name"], cur_db, new_db, "渐变 %.1f 秒" % ramp if ramp > 0.01 else "瞬间"))
    return {"id": key_id, "from_db": cur_db, "to_db": new_db}
