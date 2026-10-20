# -*- coding: utf-8 -*-
"""x32sim/fx — FX 机架: 混响同步 / Tap 定拍 / 效果库元信息"""

import threading
import time

from .consts import FX_COUNT, NOTE_BEATS, TAP_BPM_MAX, TAP_BPM_MIN, TAP_INTERVAL_MIN, TAP_MAX_INTERVALS, TAP_RESET_GAP
from .kinds import fx_par_kind
from .logbus import log
from .profiles import profile
from .state import STATE, get_value, set_value
from .util import _clamp01, _finite, _truthy

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def fx_algos():
    """当前控台的效果库（算法名 → 定义）。未标定控台返回空表。

    这是"效果槽与调音台同步"的唯一取数口：混响页的算法下拉、算法参数索引、
    写入校验、Tap 钳制范围、桥接查询参数号，全部经这里 —— 切控台即整体换库。
    """
    return profile().fx_algos


def fx_lib_meta():
    """给 UI 的效果库元信息（不含参数细节）。"""
    p = profile()
    lib = p.fx_algos
    return {"id": p.id, "label": p.label, "vendor": p.vendor,
            "ready": p.ready, "verified": p.verified,
            "slots": int(p.counts.get("fx", 0)), "libCount": len(lib),
            "locked": (not p.ready) or ("fx" not in p.caps),
            "algos": {k: {"label": v["label"], "tmin": v["tmin"], "tmax": v["tmax"]}
                      for k, v in lib.items()}}


def fx_algo(slot):
    """取某个槽的算法定义（按**当前控台的效果库**查）; 未装载或算法不在本库则返回 None。"""
    t = STATE.fx.get(slot, {}).get("type", "")
    return fx_algos().get(t)


def fx_par_clamp(slot, pnum, value):
    """按参数语义钳制: 时间夹到算法范围, 预延迟 0-200ms, 阻尼 0..1, 其余 0..1。"""
    v = _finite(value, 0.0)
    algo = fx_algo(slot)
    if algo:
        if pnum == algo["time"]:
            return min(max(v, algo["tmin"]), algo["tmax"])
        if pnum == algo["predelay"]:
            return min(max(v, 0.0), 200.0)
        if pnum == algo["damp"]:
            return min(max(v, 0.0), 1.0)
    return min(max(v, 0.0), 1.0)


# ---------------------------------------------------------------- 混响同步 / Tap 定拍
# 目标: 一次 Tap 或一次改动, 把「混响时间」按同步模式铺到联动组内所有混响槽;
# 而且**双向**成立 —— 真台上改了混响, 也同步回本地并铺给组内其他槽。
#
# 三种同步模式 (谁说了算):
#   software 软件主导: 软件改动 → 铺组内 + 下发真台; 真台回读只镜像自己, 不再外扩
#   console  台面主导: 真台改动 → 铺组内 + 镜像到软件; 软件改动只改该槽, 不外扩
#   both     双向互同步: 谁改都铺给组内
#
# 防回环的关键: 铺给其他槽的写入一律用 source="action", 它**不会再触发铺散**
# (见 set_value 末尾的钩子), 而且每次转发都记进 FWD_RECENT, 真台回读到的回声
# 由 _fwd_is_echo 吸收 —— 所以 software→真台→回读 与 真台→本地→真台 两条链
# 都在两跳内收敛, 不会无限震荡。
FX_SYNC = {"mode": "software", "group": [1, 2], "followTap": True,
           "bpm": 120.0, "note": "1/4", "dotted": False}
TAP_STATE = {"times": [], "count": 0, "bpm": 0.0}
FX_SYNC_LOCK = threading.Lock()
FX_MODE_LABEL = {"software": "软件主导", "console": "台面主导", "both": "双向互同步"}


def note_seconds(bpm, note="1/4", dotted=False):
    """BPM + 时值 → 秒数。四分音符 = 1 拍 = 60/BPM 秒; 附点 = ×1.5。"""
    beats = NOTE_BEATS.get(str(note), 1.0)
    if dotted:
        beats *= 1.5
    bpm = min(max(_finite(bpm, 120.0), TAP_BPM_MIN), TAP_BPM_MAX)
    return beats * 60.0 / bpm


def tap_reset():
    with FX_SYNC_LOCK:
        TAP_STATE["times"] = []
        TAP_STATE["count"] = 0
        TAP_STATE["bpm"] = 0.0


def tap_register(now=None):
    """记一次 Tap, 返回 (bpm, 点击次数, 是否已成拍)。
    · 两次点击间隔 < TAP_INTERVAL_MIN → 判为抖动, 忽略
    · 距上次点击 > TAP_RESET_GAP → 视为新的一组, 清空重来
    · 取最近 TAP_MAX_INTERVALS 个间隔的**均值** → BPM (比只用最后一次稳得多)
    """
    now = time.time() if now is None else float(now)
    with FX_SYNC_LOCK:
        t = TAP_STATE
        if t["times"] and now - t["times"][-1] > TAP_RESET_GAP:
            t["times"] = []
            t["count"] = 0
        if t["times"] and now - t["times"][-1] < TAP_INTERVAL_MIN:
            return t["bpm"], t["count"], len(t["times"]) >= 2
        t["times"].append(now)
        t["count"] += 1
        if len(t["times"]) > TAP_MAX_INTERVALS + 1:
            t["times"] = t["times"][-(TAP_MAX_INTERVALS + 1):]
        if len(t["times"]) < 2:
            return 0.0, t["count"], False
        gaps = [t["times"][i + 1] - t["times"][i] for i in range(len(t["times"]) - 1)]
        t["bpm"] = min(max(60.0 / (sum(gaps) / len(gaps)), TAP_BPM_MIN), TAP_BPM_MAX)
        FX_SYNC["bpm"] = t["bpm"]
    return TAP_STATE["bpm"], TAP_STATE["count"], True


def fx_sync_targets():
    """联动组里的混响槽。组为空时退化为「所有装了混响算法的槽」, 免得 Tap 变成空动作。"""
    with FX_SYNC_LOCK:
        g = [int(s) for s in FX_SYNC.get("group", [])
             if isinstance(s, (int, float)) and 1 <= int(s) <= FX_COUNT]
    if g:
        return sorted(set(g))
    return [s for s in range(1, FX_COUNT + 1) if fx_algo(s)]


def _fx_time_fanout(src_slot, seconds, source):
    """把一个混响时间铺给联动组里的其他槽。
    source='manual' = 来自软件;  source='sync' = 来自真台回读。"""
    with FX_SYNC_LOCK:
        mode = FX_SYNC.get("mode", "software")
    if mode == "software" and source == "sync":
        return                      # 软件主导: 真台改哪个就只镜像哪个, 不外扩
    if mode == "console" and source == "manual":
        return                      # 台面主导: 软件改动不外扩
    algo = fx_algo(src_slot)
    if not algo:
        return
    kind = fx_par_kind(algo["time"])
    spread = []
    for s in fx_sync_targets():
        if s == src_slot or not fx_algo(s):
            continue
        cur = get_value(kind, s)
        if cur is not None and abs(float(cur) - float(seconds)) < 1e-4:
            continue                # 已经一样了, 不写也不转发 (省一次 OSC)
        set_value(kind, s, float(seconds), source="action")
        spread.append(s)
    if spread:
        log("REVERB 同步[%s] FX%d 时间 %.2fs → 组内 %s" % (
            FX_MODE_LABEL.get(mode, mode), src_slot, float(seconds),
            "/".join("FX%d" % s for s in spread)))


def fx_set_param(slot, field, value, source="manual"):
    """混响槽的统一写入口。field ∈ type/on/mix/name/time/predelay/damp。
    返回 (写入后的值, 错误串)。

    ⚠️ 两道控台门禁（与 §4 控台驱动层同一条原则：不支持的写入要**拒绝并记日志**，
    而不是静默地写到一个"看起来像"的地方）：
      · 控台未标定 → 整个效果槽不可写
      · 控台没有 fx_par 能力 → 时间/预延迟/阻尼不可写（槽级 type/on/mix 仍可）
    """
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        return None, "bad slot"
    if not (1 <= slot <= FX_COUNT):
        return None, "bad slot"
    prof = profile()
    if not prof.ready or "fx" not in prof.caps:
        log("FX 拒绝写入 %s: 控台「%s」%s" % (
            field, prof.label, "驱动待标定" if not prof.ready else "不支持效果槽"))
        return None, "控台「%s」%s，效果槽写入已被拒绝" % (
            prof.label, "驱动待标定" if not prof.ready else "不支持效果槽")
    field = str(field)
    if field == "type":
        t = str(value).strip().upper()
        if t and t not in fx_algos():
            return None, "算法 %s 不在「%s」的效果库中" % (t, prof.label)
        set_value("fx_type", slot, t, source)
        return get_value("fx_type", slot), None
    if field == "name":
        set_value("fx_name", slot, str(value)[:12], source)
        return get_value("fx_name", slot), None
    if field == "on":
        set_value("fx_on", slot, _truthy(value), source)
        return get_value("fx_on", slot), None
    if field == "mix":
        set_value("fx_mix", slot, _clamp01(value), source)
        return get_value("fx_mix", slot), None
    if "fx_par" not in prof.caps:
        log("FX 拒绝写入 %s: 控台「%s」未标定效果参数地址" % (field, prof.label))
        return None, "控台「%s」未标定效果参数地址，无法写入 %s" % (prof.label, field)
    algo = fx_algo(slot)
    if not algo or field not in ("time", "predelay", "damp"):
        return None, "slot has no reverb" if not algo else "unknown field"
    p = algo[field]
    v = fx_par_clamp(slot, p, value)
    set_value(fx_par_kind(p), slot, v, source)   # 若是时间参数, set_value 内部会触发铺散
    return v, None


def fx_tap_apply():
    """把 Tap 出的 BPM 按当前时值换算成混响时间, 写进联动组 (受 followTap 控制)。
    返回 (换算秒数 / None, 明细列表 或 错误串)。"""
    with FX_SYNC_LOCK:
        bpm = FX_SYNC.get("bpm") or TAP_STATE["bpm"]
        note = FX_SYNC.get("note", "1/4")
        dotted = bool(FX_SYNC.get("dotted"))
        follow = bool(FX_SYNC.get("followTap"))
    if not bpm or bpm <= 0:
        return None, "还没 Tap 出拍子"
    secs = note_seconds(bpm, note, dotted)
    if not follow:
        return secs, []             # followTap 关着: 只返回算好的时间, 不写槽
    written = []
    for s in fx_sync_targets():
        algo = fx_algo(s)
        if not algo:
            continue
        clamped = min(max(secs, algo["tmin"]), algo["tmax"])
        # source="action": 一次性写全部目标, 不再触发铺散 (铺散是给"单点改动"用的)
        set_value(fx_par_kind(algo["time"]), s, clamped, source="action")
        written.append({"slot": s, "time": clamped, "clamped": abs(clamped - secs) > 1e-4})
    log("REVERB Tap %.1f BPM × %s%s = %.2fs → %d 个混响槽" % (
        bpm, note, "(附点)" if dotted else "", secs, len(written)))
    return secs, written


def fx_state():
    """给网页/API 的混响快照。算法库一律来自**当前控台驱动**。"""
    lib = fx_algos()
    slots = []
    with STATE.lock:
        for s in range(1, FX_COUNT + 1):
            fx = STATE.fx[s]
            t = fx.get("type") or ""
            algo = lib.get(t)
            item = {"slot": s, "type": t, "label": algo["label"] if algo else "",
                    "name": fx["name"], "on": int(fx["on"]), "mix": float(fx["mix"]),
                    "time": None, "predelay": None, "damp": None,
                    "tmin": algo["tmin"] if algo else None,
                    "tmax": algo["tmax"] if algo else None,
                    # loaded = 槽里有东西；known = 这个算法属于**本控台**的效果库。
                    # 两者必须分开：真台上可能装着本库没有的算法，那叫"未知"，
                    # 不叫"空槽" —— 页面要能区分，否则用户看不到真台上发生了什么。
                    "loaded": bool(t), "known": bool(algo)}
            if algo:
                item["time"] = float(fx["par"].get(algo["time"], 0.0))
                item["predelay"] = float(fx["par"].get(algo["predelay"], 0.0))
                item["damp"] = float(fx["par"].get(algo["damp"], 0.0))
            slots.append(item)
    with FX_SYNC_LOCK:
        sync = dict(FX_SYNC)
        sync["group"] = list(FX_SYNC.get("group", []))
        tap = {"count": TAP_STATE["count"], "bpm": TAP_STATE["bpm"]}
    meta = fx_lib_meta()
    # console/libId: 前端靠 libId 变化判断"效果库换了一套" => 重建算法下拉
    return {"slots": slots, "sync": sync, "tap": tap, "noteBeats": dict(NOTE_BEATS),
            "console": meta, "libId": meta["id"], "algos": meta["algos"]}
