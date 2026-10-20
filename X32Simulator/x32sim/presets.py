# -*- coding: utf-8 -*-
"""x32sim/presets — 台面预设: scope 模型 / 抓取 / 回归"""

import collections
import threading
import time

from .bridge import _bridge_force_sync
from .consts import FX_COUNT, NAME_COUNTS, PRESET_DEFAULT_SCOPE, PRESET_GROUP_LABEL, PRESET_GROUP_PARAMS, PRESET_PARAM_LABEL
from .logbus import log
from .state import STATE, get_value, set_value
from .util import _clamp01, _finite

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ---------------------------------------------------------------- 推子快照
# 灵感来自"电动推子自动化":保存当前所有推子位置,一键让全部推子自动滑回

SNAPSHOTS = []                       # [{id, name, time, scope, data, count}]
SNAP_ID = [0]
AUTO_SNAPS = collections.deque(maxlen=5)   # 自动撤销点: 每次卡片执行前自动保存, 最多留 5 个
RECALL_STATE = {"running": None, "name": "", "progress": 0.0}
RECALL_STOP = threading.Event()
# 回归打断机制: 曾经只靠一个全局 Event —— 新回归的 worker 开头 clear() 会把
# "停止旧回归" 刚 set 的信号吃掉 (与卡片 SHOW_STOP 同族的坑), 旧回归的渐变
# 会继续跑, 和新回归抢同一批通道。现在每次启动/停止都发一个新"代"(gen),
# worker 只认自己那代; 旧 worker 下次检查 (≤0.1s) 发现代号过期即退场。
RECALL_GEN = [0]
RECALL_LOCK = threading.Lock()
RECALL_ZONE = {"gen": None, "keys": set()}   # 正在被回归渐变驱动的目标, 停止时同步用


def _recall_zone_register(starts, gen):
    with RECALL_LOCK:
        RECALL_ZONE["gen"] = gen
        RECALL_ZONE["keys"] = set((k, i) for k, i, _a, _b in starts)


def _recall_zone_clear(gen):
    """只有当代 worker 才能清 zone —— 旧代退场时不能把新代的登记擦掉。"""
    with RECALL_LOCK:
        if RECALL_ZONE["gen"] == gen:
            RECALL_ZONE["keys"] = set()


def _recall_stop_current():
    """停止当前回归 (幂等) + 把渐变冻结值同步给真台。"""
    with RECALL_LOCK:
        RECALL_GEN[0] += 1
        keys = list(RECALL_ZONE["keys"])
        RECALL_ZONE["keys"] = set()
    RECALL_STOP.set()
    RECALL_STATE["running"] = None
    if keys:
        _bridge_force_sync(keys)
    log("SNAPSHOT 手动停止回归")


def preset_group_cap(g):
    """该组有多少路（main 只有一路且编号固定 0）。"""
    if g in ("main", "scene"):
        return 1
    if g == "fx":
        return FX_COUNT
    return NAME_COUNTS.get(g, 0)


def clean_preset_scope(scope):
    """规范化预设范围。返回 (cleaned, ok, why)。

    只认白名单 —— 配置文件可手改，脏范围不能让回归变成"乱写"。
    ⚠️ ok=False 的两种情况, 调用方必须**拒绝**, 绝不能静默回落全量:
       - 传入的 scope 结构不对 (不是 dict / groups 不是 list ...);
       - 清完 groups 或 params 为空 (一个组都不合法 / 一个参数都不合法),
         或某组的编号全部越界。
       "局部预设"被脏数据变成全局, 回归时误改全台, 比报错严重得多 ——
       这就是本函数返回 ok 而不是自己挑默认值的原因。
    scope=None → 默认全量范围, ok=True (旧格式预设没有 scope 字段)。
    """
    default = {"groups": list(PRESET_DEFAULT_SCOPE["groups"]),
               "params": list(PRESET_DEFAULT_SCOPE["params"]), "indices": {}}
    if scope is None:
        return default, True, ""
    if not isinstance(scope, dict):
        return default, False, "预设范围格式不对（应为 {groups, params, indices}）"
    raw_groups = scope.get("groups")
    if not isinstance(raw_groups, list):
        return default, False, "预设范围缺少 groups 列表"
    groups = []
    for g in raw_groups:
        if g in PRESET_GROUP_PARAMS and g not in groups:
            groups.append(g)
    if not groups:
        return default, False, "范围里没有合法的组（至少选择一组）"
    raw_params = scope.get("params")
    if not isinstance(raw_params, list):
        return default, False, "预设范围缺少 params 列表"
    params = []
    for p in raw_params:
        if p not in PRESET_PARAM_LABEL:
            continue
        if any(p in PRESET_GROUP_PARAMS[g] for g in groups) and p not in params:
            params.append(p)
    if not params:
        return default, False, "范围里没有合法的参数（当前所选组不支持任何已选参数）"
    indices = {}
    raw = scope.get("indices")
    if isinstance(raw, dict):
        for g, arr in raw.items():
            if g not in groups or g not in NAME_COUNTS:
                continue
            cap = preset_group_cap(g)
            picked = []
            for v in (arr if isinstance(arr, list) else []):
                try:
                    n = int(float(v))
                except (TypeError, ValueError):
                    continue
                # 越界编号**丢弃**而不是钳到边界: 手写 999 更可能是笔误,
                # 钳成 32 等于偷偷改了一个用户没选的路
                if 1 <= n <= cap and n not in picked:
                    picked.append(n)
            if picked:
                indices[g] = sorted(picked)
            elif isinstance(arr, list) and arr:
                # 原始列表非空但清完为空 = 编号全越界 → 这个组的限制会凭空消失,
                # 必须算非法, 不能让"局部"悄悄变"整组"
                return default, False, "%s 的编号全部越界（1–%d）" % (
                    PRESET_GROUP_LABEL.get(g, g), cap)
    return {"groups": groups, "params": params, "indices": indices}, True, ""


def preset_scope_text(scope):
    """人话摘要（列表与日志共用）。"""
    gs = [PRESET_GROUP_LABEL.get(g, g) for g in scope.get("groups", [])]
    ps = [PRESET_PARAM_LABEL.get(p, p) for p in scope.get("params", [])
          if p not in ("slot", "scene")]
    txt = "、".join(gs)
    if ps:
        txt += " · " + "+".join(ps)
    idx = scope.get("indices") or {}
    if idx:
        parts = []
        for g, arr in idx.items():
            shown = ",".join(str(x) for x in arr[:6]) + ("…" if len(arr) > 6 else "")
            parts.append("%s %s" % (PRESET_GROUP_LABEL.get(g, g), shown))
        txt += " · 仅 " + ";".join(parts)
    return txt


def _snap_group_items_locked(g, params, idxs):
    """持锁调用：抓一个组的参数。返回 {str(idx): {...}}，main 用 "0"。"""
    bag = {}
    if g == "main":
        item = {}
        if "fader" in params:
            item["fader"] = STATE.main.get("fader", 0.75)
        if "on" in params:
            item["on"] = STATE.main.get("on", 1)
        if "name" in params:
            item["name"] = STATE.names.get(("main", 0), "")
        if item:
            bag["0"] = item
        return bag
    store = getattr(STATE, g, {}) or {}
    for i in idxs:
        row = store.get(i)
        if row is None:
            continue
        item = {}
        if "fader" in params:
            item["fader"] = row.get("fader", 0.75)
        if "on" in params:
            item["on"] = row.get("on", 1)
        if "name" in params:
            item["name"] = STATE.names.get((g, i), "")
        if "dca" in params and g == "ch":
            item["dca"] = sum(1 << (d - 1) for d in range(1, 9)
                              if i in STATE.dca_members.get(d, []))
        bag[str(i)] = item
    return bag


def _grab_state_scoped_locked(scope):
    """持锁调用：按范围抓取。返回 (data, 值数量)。"""
    data, count = {}, 0
    for g in scope["groups"]:
        params = [p for p in scope["params"] if p in PRESET_GROUP_PARAMS.get(g, ())]
        if not params:
            continue
        if g == "scene":
            data["scene"] = int(STATE.scene)
            count += 1
            continue
        if g == "fx":
            slots = {}
            for i in range(1, FX_COUNT + 1):
                fx = STATE.fx[i]
                par = fx.get("par") or {}
                slots[str(i)] = {"type": fx.get("type", ""), "name": fx.get("name", ""),
                                 "on": fx.get("on", 1), "mix": fx.get("mix", 0.25),
                                 "par": {str(pp): v for pp, v in par.items()}}
                count += 4 + len(par)
            data["fx"] = slots
            continue
        if g == "main":
            idxs = [0]
        else:
            idxs = scope["indices"].get(g) or list(range(1, preset_group_cap(g) + 1))
        bag = _snap_group_items_locked(g, params, idxs)
        data[g] = bag
        count += sum(len(v) for v in bag.values())
    return data, count


def _grab_state_locked():
    """兼容旧调用：全量「推子+静音」抓取。"""
    data, _ = _grab_state_scoped_locked({"groups": list(PRESET_DEFAULT_SCOPE["groups"]),
                                         "params": ["fader", "on"], "indices": {}})
    return data


def save_snapshot(name=None, auto=False, scope=None):
    """保存预设。返回 (snap, saved_ok) —— saved_ok=False 表示只在内存里, 没落盘
    (写盘失败的"假成功"由 HTTP 层据此回报, 见 save_user_config)。
    scope 非法时抛 ValueError(原因) —— 由 HTTP 层转 400, 绝不静默存成全量。"""
    # 延迟导入: presets→persist→presets 会成环, 只在这一处打破
    # (合成单文件形态下该行被剥离, 同一命名空间直接解析)。
    from .persist import save_user_config
    if scope is None:
        sc = {"groups": list(PRESET_DEFAULT_SCOPE["groups"]),
              "params": list(PRESET_DEFAULT_SCOPE["params"]), "indices": {}}
    else:
        sc, ok, why = clean_preset_scope(scope)
        if not ok:
            raise ValueError(why)
    with STATE.lock:
        data, count = _grab_state_scoped_locked(sc)
        scene_now = int(STATE.scene)
    SNAP_ID[0] += 1
    snap = {"id": SNAP_ID[0],
            "name": (str(name).strip()[:20] if name else "") or "预设 %d" % SNAP_ID[0],
            "time": time.strftime("%H:%M:%S"),
            "scene": scene_now, "scope": sc, "data": data, "count": count}
    if auto:
        AUTO_SNAPS.append(snap)
        log("SNAPSHOT 自动撤销点: %s (%d 值)" % (snap["name"], count))
        return snap, True
    SNAPSHOTS.insert(0, snap)
    if len(SNAPSHOTS) > 8:
        SNAPSHOTS.pop()
    saved = save_user_config()     # 预设必须跨重启（以前只存在内存里，重启即丢）
    log("SNAPSHOT 保存: %s (%d 值 · %s)%s" % (
        snap["name"], count, preset_scope_text(sc), "" if saved else " ⚠️ 未落盘"))
    return snap, saved


def recall_snapshot(snap_id, seconds=3.0):
    snap = next((s for s in SNAPSHOTS if s["id"] == snap_id), None)
    if not snap:
        return None
    return start_recall(snap, seconds)


def _snap_items(snap):
    """统一新旧两种预设格式 → [(group, idx, item)]。

    旧格式（本次升级前）：顶层就是 ch/bus/... 的 {i: {fader, on}}；
    新格式：snap["data"][group][i] = {...}，可能只含部分参数。
    兼容旧数据是有意的：用户配置里可能还躺着升级前保存的快照。
    """
    out = []
    src = snap.get("data")
    if isinstance(src, dict):
        for g, bag in src.items():
            if g == "scene" or not isinstance(bag, dict):
                continue
            for k, item in bag.items():
                if not isinstance(item, dict):
                    continue
                try:
                    i = int(k)
                except (TypeError, ValueError):
                    continue
                out.append((g, i, item))
        return out
    for g in ("ch", "bus", "dca", "auxin", "usb", "fxret", "mtx", "main"):
        for k, item in (snap.get(g) or {}).items():
            if isinstance(item, dict):
                try:
                    out.append((g, int(k), item))
                except (TypeError, ValueError):
                    continue
    return out


def _recall_instant(snap):
    """立即应用：开关 / 名称 / DCA 归属 / FX 整槽 / 场景号。"""
    names_changed = False
    if isinstance(snap.get("data"), dict) and "scene" in snap["data"]:
        with STATE.lock:
            try:
                STATE.scene = min(max(int(snap["data"]["scene"]), 0), 99)
            except (TypeError, ValueError):
                pass
    for g, i, item in _snap_items(snap):
        if g == "fx":
            if 1 <= i <= FX_COUNT:
                if "type" in item:
                    set_value("fx_type", i, item.get("type", ""), source="action")
                if "name" in item:
                    set_value("fx_name", i, item.get("name", ""), source="action")
                if "on" in item:
                    set_value("fx_on", i, item.get("on", 1), source="action")
                if "mix" in item:
                    set_value("fx_mix", i, item.get("mix", 0.25), source="action")
                for p, v in (item.get("par") or {}).items():
                    set_value("fx_par_%s" % p, i, v, source="action")
            continue
        if "on" in item:
            set_value("main_on" if g == "main" else g + "_on", i,
                      item.get("on", 1), source="action")
        if "dca" in item and g == "ch":
            set_value("ch_dca", i, item.get("dca", 0), source="action")
        if "name" in item:
            nm = str(item.get("name") or "").strip()[:12]
            with STATE.lock:
                if nm:
                    STATE.names[(g, i)] = nm
                else:
                    STATE.names.pop((g, i), None)
            names_changed = True
    if names_changed:
        # 延迟导入: 打破 presets↔persist 环 (另一处在 save_snapshot)
        from .persist import save_user_config
        save_user_config()


def _recall_fader_starts(snap):
    """[(kind, idx, 当前值, 目标值)] —— 只有范围里带 fader 的项才动。"""
    out = []
    for g, i, item in _snap_items(snap):
        if "fader" not in item:
            continue
        kind = "main_fader" if g == "main" else g + "_fader"
        try:
            target = _clamp01(item["fader"])
        except (TypeError, ValueError):
            continue
        out.append((kind, i, get_value(kind, i), target))
    return out


def _recall_body(snap, seconds, my_gen=None):
    """回归主体（阻塞）。调用方负责 RECALL_STATE 与线程。

    my_gen: 本次回归的代 (见 RECALL_GEN)。代号过期 (又有新回归启动/手动停止)
    立即退场 —— 曾经旧回归会和新回归抢同一批通道, 谁后跑完谁说话。
    """
    RECALL_STATE["running"] = snap.get("id")
    RECALL_STATE["name"] = snap.get("name", "")
    RECALL_STATE["progress"] = 0.0
    try:
        starts = _recall_fader_starts(snap)
        if my_gen is not None:
            _recall_zone_register(starts, my_gen)
        _recall_instant(snap)
        log("SNAPSHOT 回归开始: %s (%d 项 · %d 路推子 %.1f 秒归位)"
            % (snap.get("name", ""), snap.get("count", 0), len(starts), seconds))
        steps = max(1, int(seconds / 0.1))
        for s in range(1, steps + 1):
            if RECALL_STOP.is_set() or (my_gen is not None and my_gen != RECALL_GEN[0]):
                log("SNAPSHOT 回归被打断")
                return
            time.sleep(0.1)
            ratio = s / steps
            for kind, i, a, b in starts:
                set_value(kind, i, a + (b - a) * ratio, source="action")
            RECALL_STATE["progress"] = ratio
        log("SNAPSHOT 回归完成: %s" % snap.get("name", ""))
    except Exception as e:
        log("SNAPSHOT 回归出错: %r" % e)
    finally:
        stale = my_gen is not None and my_gen != RECALL_GEN[0]
        if not stale:
            if RECALL_STATE["running"] == snap.get("id"):
                RECALL_STATE["running"] = None
                RECALL_STATE["progress"] = 1.0
            if my_gen is not None:
                _recall_zone_clear(my_gen)


def start_recall(snap, seconds=3.0):
    """异步回归（网页按钮用）。"""
    with RECALL_LOCK:
        RECALL_GEN[0] += 1          # 旧代作废 (比 set 更可靠: worker 的 clear 吃不掉代号)
        my_gen = RECALL_GEN[0]
        RECALL_STOP.set()           # 打断进行中的回归
    seconds = min(max(_finite(seconds, 1.0), 0.5), 15.0)

    def worker():
        RECALL_STOP.clear()
        _recall_body(snap, seconds, my_gen)

    threading.Thread(target=worker, daemon=True).start()
    return snap


def recall_now(snap, seconds=1.0):
    """同步回归（测试/脚本用）：阻塞到推子滑完。

    ⚠️ 卡片动作**不要**用它 —— 时间轴是单线程非阻塞的，这里一阻塞，
    整张卡片的进度条与并行渐变都会被冻住。卡片里用「瞬时项 + 渐变分支」组合。
    """
    with RECALL_LOCK:
        RECALL_GEN[0] += 1
        my_gen = RECALL_GEN[0]
        RECALL_STOP.set()
        RECALL_STOP.clear()
    seconds = min(max(_finite(seconds, 1.0), 0.0), 15.0)
    if seconds <= 0:
        _recall_instant(snap)
        return snap
    _recall_body(snap, seconds, my_gen)
    return snap


def find_snapshot(pid):
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return None
    return next((s for s in SNAPSHOTS if s["id"] == pid), None)


def snap_fade_targets(snap):
    """预设里带 fader 的项 → [(kind, idx, 目标值)]，交给时间轴渐变分支插值。"""
    out = []
    if not snap:
        return out
    for g, i, item in _snap_items(snap):
        if "fader" not in item:
            continue
        kind = "main_fader" if g == "main" else g + "_fader"
        try:
            out.append((kind, i, _clamp01(item["fader"])))
        except (TypeError, ValueError):
            continue
    return out
