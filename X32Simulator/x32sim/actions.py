# -*- coding: utf-8 -*-
"""x32sim/actions — 动作内核: set/osc/瞬时/渐变 + 清洗白名单"""

import math

from .bridge import BRIDGE, BRIDGE_SOCK
from .consts import CURVES, FADE_ACT_KINDS, SAVE_MAX, SET_GROUPS, SET_PARAMS, SET_PARAM_LABEL
from .fx import fx_algo
from .kinds import fx_par_kind
from .logbus import log
from .osc import OscMsg, encode_message
from .presets import SNAPSHOTS, _recall_instant, find_snapshot, snap_fade_targets
from .profiles import profile
from .state import STATE, _interrupted, db_to_fader, get_value, match_address, set_value, target_exists, target_label
from .util import _finite

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def set_target_id(target):
    """取出目标的编号。main 固定 0（只有一路）；scene 的编号即场景号。"""
    group = (target or {}).get("group")
    if group == "main":
        return 0
    try:
        return int(float((target or {}).get("id", 1)))
    except (TypeError, ValueError):
        return 1


def resolve_set_target(group, param, idx):
    """(group, param, idx) → 语义 kind; 组合不合法或不适用时返回 None。

    这里是通用动作与控台驱动层的接缝: 只产出 kind，不产出地址。
    """
    if group not in SET_GROUPS:
        return None
    if param is None:
        param = "load" if group == "scene" else "fader"
    if param not in SET_PARAMS.get(group, ()):
        return None
    if group == "scene":
        return "scene"                    # 场景是"动作"而非"值", 单独走
    if group == "fx":
        if param in ("type", "name", "on", "mix"):
            return "fx_" + param
        algo = fx_algo(idx)               # 时间/预延迟/阻尼的参数号随算法变化
        if not algo:
            return None                   # 空槽没有这些参数
        return fx_par_kind(algo[param])
    return "%s_%s" % (group, param)


def _exec_set(act):
    """通用动作: 设置某个语义目标的值。"""
    tg = act.get("target") or {}
    group = tg.get("group")
    param = tg.get("param", "fader")
    idx = set_target_id(tg)

    if group == "scene":
        n = max(1, idx)
        with STATE.lock:
            STATE.scene = n
        log("SHOW 切场景 %d" % n)
        return

    kind = resolve_set_target(group, param, idx)
    if kind is None:
        log("SHOW 跳过设置 %s.%s #%d: 参数组合不被支持" % (group, param, idx))
        return
    p = profile()
    if p.addr(kind, idx) is None:
        log("SHOW 跳过设置 %s.%s #%d: 控台「%s」不支持%s"
            % (group, param, idx, p.label, "（驱动待标定）" if not p.ready else ""))
        return
    if not target_exists(kind, idx):
        log("SHOW 跳过设置 %s.%s #%d: 本地台面没有这个目标" % (group, param, idx))
        return
    set_value(kind, idx, act.get("value", 0.0), source="action")
    log("SHOW 设置 %s" % _act_label(act))


def _exec_osc(act):
    """原始 OSC 动作: 直接发一条自定义消息。

    ⚠️ 这个动作**不是跨控台**的 —— 地址是控台专有的。换控台后同一条消息可能指向
    别的参数（甚至无效），所以 UI 上要明确标注。行为分两种：
      1. 地址能被本机解析（match_address 认得）→ 走 set_value：本地状态同步 + 桥接转发；
      2. 解析不出来 → 只发往桥接的真台；没有桥接则记日志忽略。
    两条路都会写日志，不会静默。
    """
    a = str(act.get("address", ""))
    args = list(act.get("args") or [])
    m = match_address(a)
    if m and args:
        kind, idx = m[0]
        # ⚠️ 推子/静音类 kind 要的是**数字**，但前端允许原始 OSC 带字符串参数
        #    （`/fx/1/config/name "MyHall"` 是合法的）。曾经的写法是直接把
        #    args[0] 递给 set_value，于是一个手滑的 "abc" 就 float() 抛
        #    ValueError 把卡片线程炸掉 —— 界面还显示"卡片已完成"。
        #    现在：转不成数字就降级成"只发真台"，本地状态不碰。
        v = args[0]
        if kind not in ("fx_name", "fx_type"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = None
            if v is not None and not math.isfinite(v):
                v = None          # NaN/inf 也不写本地 —— 会污染 /api/state
        if v is None:
            log("SHOW 原始 OSC %s: 参数 %r 不是数字，已降级为只发真台（不动本地状态）"
                % (a, args[0]))
        else:
            set_value(kind, idx, v, source="action")
            log("SHOW 原始 OSC %s %s (已应用到本地)" % (a, args))
            return
    sock = BRIDGE_SOCK[0]
    if sock is None or BRIDGE["addr"] is None:
        log("SHOW 原始 OSC %s: 未桥接真台, 已忽略" % a)
        return
    try:
        sock.sendto(encode_message(OscMsg(a, args)), BRIDGE["addr"])
        log("SHOW 原始 OSC %s %s (已发往真台)" % (a, args))
    except Exception as e:
        log("SHOW 原始 OSC 发送失败: %r" % e)


def _act_duration(act):
    """动作占用时间轴的秒数; 瞬时动作(静音/场景)为 0。"""
    k = act.get("kind")
    if k == "set":
        # 通用设置默认**瞬时**（duration 缺省为 0）；给了 duration 才占时间轴走渐变
        try:
            d = float(act.get("duration", 0.0) or 0.0)
        except (TypeError, ValueError):
            d = 0.0
        return max(0.05, d) if d > 0 else 0.0
    if k == "preset":
        try:
            return max(0.5, float(act.get("duration", 2.0)))
        except (TypeError, ValueError):
            return 2.0
    if k == "scene":
        # 全局 recall 是瞬时的（模拟器/真台都一样，真台生效有延迟但那是台子的事）;
        # 局部调用要占时间轴，推子按 duration 滑回去
        if (act.get("mode") or "global") == "partial":
            try:
                return max(0.05, float(act.get("duration", 1.0)))
            except (TypeError, ValueError):
                return 1.0
        return 0.0
    if k == "wait" or k in FADE_ACT_KINDS:
        try:
            return max(0.05, float(act.get("duration", 3.0)))
        except (TypeError, ValueError):
            return 3.0
    return 0.0


def _act_label(act):
    """短标签: 卡片执行中显示「2/5 · 音乐 →90%」"""
    k = act.get("kind")
    try:
        if k == "mute":
            return "静音 " + "、".join(target_label("ch", int(c)) for c in
                                       (act.get("chs") or [act.get("ch", 1)]))
        if k == "mute_bus":
            return "静音 " + "、".join(target_label("bus", int(b)) for b in
                                       (act.get("buses") or [act.get("bus", 1)]))
        if k == "scene":
            if (act.get("mode") or "global") == "partial":
                snap = next((s for s in SNAPSHOTS
                             if s["id"] == act.get("presetId")), None)
                return "场景 %d · 局部（%s）" % (
                    int(act.get("scene", 1)),
                    ("预设「%s」" % snap.get("name")) if snap else "预设已删除")
            return "切场景 %d" % int(act.get("scene", 1))
        if k == "preset":
            snap = next((s for s in SNAPSHOTS if s["id"] == act.get("presetId")), None)
            return "调用预设「%s」" % (snap.get("name") if snap else "已删除")
        if k == "unmute_all":
            return "全部取消静音"
        if k == "wait":
            return "等待 %.0f 秒" % float(act.get("duration", 1.0))
        if k == "fade_all":
            return "全体推子→%.0f%%" % (float(act.get("to", 0)) * 100)
        if k == "fade_odd_even":
            return "奇偶过渡"
        if k == "fade_ch":
            ids_ = act.get("chs") or [act.get("ch", 1)]
            return "%s →%.0f%%" % ("、".join(target_label("ch", int(i)) for i in ids_),
                                   float(act.get("to", 0.8)) * 100)
        if k == "fade_bus":
            ids_ = act.get("buses") or [act.get("bus", 1)]
            return "%s →%.0f%%" % ("、".join(target_label("bus", int(i)) for i in ids_),
                                   float(act.get("to", 0.8)) * 100)
        if k == "fade_from":
            return "%s %.0f→%.0f%%" % (target_label("ch", int(act.get("ch", 1))),
                                       float(act.get("frm", 0)) * 100, float(act.get("to", 0.9)) * 100)
        if k == "fade_from_bus":
            return "%s %.0f→%.0f%%" % (target_label("bus", int(act.get("bus", 1))),
                                       float(act.get("frm", 0)) * 100, float(act.get("to", 0.9)) * 100)
        if k == "rel_db":
            grp = act.get("group", "ch")
            key = {"ch": "chs", "bus": "buses", "dca": "dcas"}.get(grp, "chs")
            ids_ = act.get(key) or [1]
            lbl = "、".join(target_label(grp, int(i)) for i in ids_)
            d = float(act.get("delta_db", 3.0))
            return "%s %s%.1f dB" % (lbl, "+" if d >= 0 else "−", abs(d))
        if k == "set":
            tg = act.get("target") or {}
            g = tg.get("group")
            p = tg.get("param", "fader")
            i = set_target_id(tg)
            if g == "scene":
                return "切场景 %d" % i
            if g == "fx":
                if p in ("type", "name"):
                    return "FX%d %s=%s" % (i, SET_PARAM_LABEL.get(p, p), act.get("value", ""))
                if p == "on":
                    return "FX%d %s" % (i, "开启" if act.get("value") else "旁通")
                if p == "mix":
                    return "FX%d 混合比→%.0f%%" % (i, float(act.get("value", 0)) * 100)
                return "FX%d %s→%.2f" % (i, SET_PARAM_LABEL.get(p, p),
                                         float(act.get("value", 0)))
            lbl = target_label(g, i)
            if p == "on":
                return "%s %s" % (lbl, "开启" if act.get("value") else "静音")
            return "%s →%.0f%%" % (lbl, float(act.get("value", 0)) * 100)
        if k == "osc":
            return "原始 OSC %s" % act.get("address", "")
    except Exception:
        pass
    return str(k)


def _fade_targets_for(act):
    """渐变动作 → [(kind, idx, to_v)]; 非渐变返回 None。起点在触发瞬间取当前值。
    预设/局部场景也走这里：推子交给时间轴插值，避免阻塞主循环。"""
    k = act.get("kind")
    if k == "preset":
        return snap_fade_targets(find_snapshot(act.get("presetId"))) or []
    if k == "scene" and (act.get("mode") or "global") == "partial":
        return snap_fade_targets(find_snapshot(act.get("presetId"))) or []
    if k == "fade_all":
        to = float(act.get("to", 0.0))
        return ([("ch_fader", i, to) for i in range(1, 33)] +
                [("bus_fader", i, to) for i in range(1, 17)] +
                [("dca_fader", i, to) for i in range(1, 9)] +
                [(g + "_fader", i, to)
                 for g, n in (("auxin", 6), ("usb", 2), ("fxret", 8), ("mtx", 6))
                 for i in range(1, n + 1)] +
                [("main_fader", 0, to)])
    if k == "fade_odd_even":
        odd_to = float(act.get("odd_to", 0.8))
        even_to = float(act.get("even_to", 0.0))
        return ([("ch_fader", i, odd_to if i % 2 == 1 else even_to) for i in range(1, 33)])
    if k == "fade_ch":
        chs, _ = _filter_targets("ch_fader",
                                 [int(i) for i in (act.get("chs") or [act.get("ch", 1)])])
        return [("ch_fader", i, float(act.get("to", 0.8))) for i in chs]
    if k == "fade_bus":
        buses, _ = _filter_targets("bus_fader",
                                   [int(i) for i in (act.get("buses") or [act.get("bus", 1)])])
        return [("bus_fader", i, float(act.get("to", 0.8))) for i in buses]
    if k == "fade_from":
        ch, _ = _filter_targets("ch_fader", [int(act.get("ch", 1))])
        return [("ch_fader", ch[0], float(act.get("to", 0.9)))] if ch else []
    if k == "fade_from_bus":
        bus, _ = _filter_targets("bus_fader", [int(act.get("bus", 1))])
        return [("bus_fader", bus[0], float(act.get("to", 0.9)))] if bus else []
    if k == "rel_db":
        grp = act.get("group", "ch")
        # ⚠️ .get 而不是 []：group 若是白名单外的值（绕过了清洗的旧数据），
        #    [] 会 KeyError 把渐变线程炸掉；.get 返回 None 就走不了这条路。
        fk = {"ch": "ch_fader", "bus": "bus_fader", "dca": "dca_fader"}.get(grp)
        key = {"ch": "chs", "bus": "buses", "dca": "dcas"}.get(grp)
        if fk is None or key is None:
            return None
        delta = float(act.get("delta_db", 3.0))
        ids_, _ = _filter_targets(fk, [int(i) for i in (act.get(key) or [1])])
        out = []
        for i in ids_:
            cur_db = STATE.fader_to_db(get_value(fk, i))
            out.append((fk, i, db_to_fader(min(max(cur_db + delta, -90.0), 10.0))))
        return out
    if k == "set":
        tg = act.get("target") or {}
        g = tg.get("group")
        p = tg.get("param", "fader")
        if p != "fader":
            return None                  # 只有推子类能渐变, 其余落到瞬时路径
        i = set_target_id(tg)
        kind = resolve_set_target(g, p, i)
        if kind is None or not profile().addr(kind, i) or not target_exists(kind, i):
            return None                  # 组合不合法 / 控台不支持 / 本地无此目标 → 退化为瞬时
        try:
            to = float(act.get("value", 0.0) or 0.0)
        except (TypeError, ValueError):
            to = 0.0
        return [(kind, i, min(max(to, 0.0), 1.0))]
    return None


def _filter_targets(kind, ids_):
    """按当前控台/本地台面规模过滤目标, 返回 (放行的, 被跳过的)。

    保存期只做格式清洗（见 SAVE_MAX），所以这里可能收到超出当前台面范围的编号。
    处理原则与 §4 一致：**明确跳过并记日志**，而不是让 KeyError 把卡片线程炸掉。
    """
    done, skipped = [], []
    for i in ids_:
        if target_exists(kind, i):
            done.append(i)
        else:
            skipped.append(i)
    return done, skipped


def _skip_note(skipped, what):
    if not skipped:
        return ""
    return "（跳过 %s %s：当前控台没有这些路）" % (
        what, "/".join(str(x) for x in skipped))


def _gate_or_log(kind, idx, what):
    """控台门禁: 未标定控台 → 记一条日志并返回 True（调用方应跳过）。

    与 _exec_set 里那段是同一套原则（§4：让"不支持"变成**看得见的降级**）。
    曾经的遗漏：卡片里的 mute 动作在未标定控台下照样改本地 STATE、还打一句
    "SHOW 静音: 01、02" —— 与设置页承诺的"所有写入会被拒绝并记日志"不符。
    """
    p = profile()
    if p.ready and p.addr(kind, idx) is not None:
        return False
    log("SHOW 跳过%s %s #%d: 控台「%s」%s"
        % (what, kind, idx, p.label, "驱动待标定" if not p.ready else "不支持该目标"))
    return True


def _exec_instant(act):
    """瞬时动作: 静音 / 静音Bus / 切场景 / 全部取消静音 (卡片写入, 不做冲突检测)。"""
    k = act["kind"]
    if k == "mute":
        chs = [int(c) for c in (act.get("chs") or [act.get("ch", 1)])]
        ok, bad = _filter_targets("ch_on", chs)
        ok = [c for c in ok if not _gate_or_log("ch_on", c, "静音")]
        for c in ok:
            set_value("ch_on", c, 0, source="action")
        log("SHOW 静音: %s%s" % ("、".join(target_label("ch", c) for c in ok) or "(无可写目标)",
                                _skip_note(bad, "ch")))
    elif k == "mute_bus":
        buses = [int(b) for b in (act.get("buses") or [act.get("bus", 1)])]
        ok, bad = _filter_targets("bus_on", buses)
        ok = [b for b in ok if not _gate_or_log("bus_on", b, "静音")]
        for b in ok:
            set_value("bus_on", b, 0, source="action")
        log("SHOW 静音: %s%s" % ("、".join(target_label("bus", b) for b in ok) or "(无可写目标)",
                                _skip_note(bad, "bus")))
    elif k == "scene":
        # 全局 = 整场 recall（模拟器里就是切场景号，真台发 /scene/N/load）;
        # 局部 = **不发场景号**，只把预设范围内的参数应用上去 —— 真台 OSC
        # 没有"只 recall 一部分"的地址，选择性只能由客户端做。
        mode = act.get("mode") or "global"
        if mode == "partial":
            snap = find_snapshot(act.get("presetId"))
            if not snap:
                # 绝不能退化成全量 recall：那会让"局部"变成"整台被换掉"
                log("SHOW 场景 %d 局部调用失败: 找不到预设 #%s（已跳过，不做全量）"
                    % (int(act.get("scene", 1)), act.get("presetId")))
                return
            _recall_instant(snap)      # 推子由时间轴渐变分支负责
            log("SHOW 场景 %d 局部: 预设「%s」立即项已应用，推子 %.1f 秒归位（其余通道不动）"
                % (int(act.get("scene", 1)), snap.get("name", ""),
                   float(act.get("duration", 1.0) or 0.0)))
            return
        with STATE.lock:
            STATE.scene = int(act.get("scene", 1))
        log("SHOW 切换到场景 %d（全局 recall）" % int(act.get("scene", 1)))
    elif k == "preset":
        snap = find_snapshot(act.get("presetId"))
        if not snap:
            log("SHOW 调用预设失败: 找不到 #%s（已跳过）" % act.get("presetId"))
            return
        _recall_instant(snap)
        log("SHOW 调用预设「%s」立即项已应用，推子 %.1f 秒归位"
            % (snap.get("name", ""), float(act.get("duration", 2.0) or 0.0)))
    elif k == "unmute_all":
        for i in range(1, 33):
            set_value("ch_on", i, 1, source="action")
        for i in range(1, 17):
            set_value("bus_on", i, 1, source="action")
        for i in range(1, 9):
            set_value("dca_on", i, 1, source="action")
        for g, n in (("auxin", 6), ("usb", 2), ("fxret", 8), ("mtx", 6)):
            for i in range(1, n + 1):
                set_value(g + "_on", i, 1, source="action")
        set_value("main_on", 0, 1, source="action")
        log("SHOW 全部取消静音")
    elif k == "set":
        _exec_set(act)
    elif k == "osc":
        _exec_osc(act)


def _apply_fade(fade, ratio):
    for k, i, a, b in fade["starts"]:
        if _interrupted(k, i):
            continue
        # source="fade": 渐变插值。与 action 分开是为了桥接侧的"接管拦截"——
        # 接管后积压的插值必须丢弃, 而显式动作 (set/mute/回归) 不受接管影响。
        set_value(k, i, a + (b - a) * ratio, source="fade")


def _clean_set_action(a, num, why=None):
    """校验通用动作。group/param 必须在白名单内，id 按当前控台规模钳制。"""
    tg = a.get("target")
    if not isinstance(tg, dict):
        if why is not None: why.append("通用设置缺 target")
        return None
    group = tg.get("group")
    if group not in SET_GROUPS:
        if why is not None: why.append("通用设置的组「%s」不在白名单" % group)
        return None
    # scene 的 param 是 "load"（切场景没有"值"的概念），其余默认 "fader"
    param = tg.get("param") or ("load" if group == "scene" else "fader")
    if param not in SET_PARAMS.get(group, ()):
        if why is not None:
            why.append("「%s」不支持参数「%s」" % (group, param))
        return None
    try:
        idx = int(float(tg.get("id", 1)))
    except (TypeError, ValueError):
        idx = 1
    if group == "main":
        idx = 0
    elif group == "scene":
        idx = min(max(idx, 1), 99)
    else:
        # 只按"格式上限"钳（见 SAVE_MAX 的说明）：范围对不对由执行期的
        # target_exists 判断，超了就跳过并记日志 —— 让降级看得见。
        cap = SAVE_MAX.get(group, 64)
        idx = min(max(idx, 1), max(1, cap))

    out = {"kind": "set", "target": {"group": group, "param": param, "id": idx}}
    if group == "fx" and param == "type":
        out["value"] = str(a.get("value", ""))[:8].upper()
    elif group == "fx" and param == "name":
        out["value"] = str(a.get("value", ""))[:12]
    elif param == "on":
        out["value"] = 1 if num("value", 0.0, 0.0, 1.0) >= 0.5 else 0
    elif param == "fader":
        out["value"] = num("value", 0.0, 0.0, 1.0)
    elif group == "fx":
        # 混响时间/预延迟/阻尼吃**原生单位**（秒/毫秒），范围交给 fx_par_clamp 按算法钳制
        out["value"] = num("value", 0.0, -1000.0, 20000.0)
    else:
        out["value"] = num("value", 0.0, 0.0, 1.0)
    if a.get("duration") is not None:
        out["duration"] = num("duration", 0.0, 0.0, 60.0)
    if a.get("at") is not None:
        out["at"] = num("at", 0.0, 0.0, 600.0)
    return out


def _clean_osc_action(a, num, why=None):
    """校验原始 OSC 动作。

    ⚠️ 这个动作是**控台专有**的：地址写死，换控台后可能指向别的参数（甚至无效）。
    这里只做防呆（地址格式、参数个数与类型），**不做语义校验** —— 地址对不对只有
    用户自己知道。这正是它必须以独立动作类型出现、并带醒目提示的原因。
    """
    addr = str(a.get("address", "")).strip()
    if not addr.startswith("/") or len(addr) > 128 or " " in addr:
        if why is not None:
            why.append("osc 地址必须形如 /ch/01/mix/fader（不能有空格）")
        return None
    args = a.get("args")
    if not isinstance(args, list):
        args = [] if a.get("value") is None else [a.get("value")]
    clean = []
    for v in args[:8]:
        if isinstance(v, bool):
            clean.append(1 if v else 0)
        elif isinstance(v, (int, float)):
            clean.append(float(v))
        elif isinstance(v, str):
            clean.append(v[:64])
        else:
            if why is not None:
                why.append("osc 参数只能是数字或字符串")
            return None
    out = {"kind": "osc", "address": addr}
    if clean:
        out["args"] = clean
    if a.get("at") is not None:
        out["at"] = num("at", 0.0, 0.0, 600.0)
    return out


def _clean_action(a, why=None):
    """校验网页提交的动作, 只放行白名单类型与合法数值。

    why 是**可选的丢弃原因收集列表**：给了它，每条被拒的动作都会往里追加一句
    人话（如 "不存在的动作" / "osc 地址必须以 / 开头"）。调用方把它透给前端，
    用户才知道"保存成功但少了东西"—— 这正是 /api/cardedit 曾经的静默丢动作。
    """
    def _reject(msg):
        if why is not None:
            why.append(msg)
        return None

    if not isinstance(a, dict):
        return _reject("不是合法的动作对象")
    k = a.get("kind")
    if k not in ("mute", "mute_bus", "scene", "unmute_all", "wait",
                 "fade_all", "fade_odd_even", "fade_ch", "fade_bus",
                 "fade_from", "fade_from_bus", "rel_db", "set", "osc",
                 "preset"):
        return _reject("不支持的动作类型「%s」" % (k if isinstance(k, str) else k))

    def num(key, default, lo, hi):
        # ⚠️ 必须过 _finite：NaN 会穿透 min/max（见 _finite 的说明），
        #    顺着 snapshot 的 json.dumps 变成字面量 NaN → 前端 JSON.parse 崩
        v = _finite(a.get(key, default), float(default))
        return min(max(v, lo), hi)

    def ids(key_single, key_list, lo, hi):
        """目标列表: 接受单值(旧格式)或数组(多选), 去重排序, 钳制范围。"""
        raw = a.get(key_list)
        if not isinstance(raw, list):
            raw = [a.get(key_single)]
        out_ids = []
        for v in raw:
            try:
                n = int(float(v))
            except (TypeError, ValueError):
                continue
            n = min(max(n, lo), hi)
            if n not in out_ids:
                out_ids.append(n)
        return sorted(out_ids) or [lo]

    curve = a.get("curve") if a.get("curve") in CURVES else "linear"
    out = {"kind": k}
    if k == "set":
        return _clean_set_action(a, num, why)
    if k == "osc":
        return _clean_osc_action(a, num, why)
    if k == "mute":
        out["chs"] = ids("ch", "chs", 1, SAVE_MAX["ch"])
    elif k == "mute_bus":
        out["buses"] = ids("bus", "buses", 1, SAVE_MAX["bus"])
    elif k == "scene":
        out["scene"] = int(num("scene", 1, 1, 99))
        mode = a.get("mode") if a.get("mode") in ("global", "partial") else "global"
        out["mode"] = mode
        if mode == "partial":
            # 局部调用必须挂一条预设：范围从预设来，找不到就明确拒绝，
            # 不能"当作全局"静默执行（那种假成功最难查）
            pid = int(num("presetId", 0, 0, 9999))
            if pid <= 0:
                return _reject("「局部调用场景」需要先选一个预设")
            out["presetId"] = pid
            out["duration"] = num("duration", 1.0, 0.05, 30.0)
            out["curve"] = curve
    elif k == "preset":
        pid = int(num("presetId", 0, 0, 9999))
        if pid <= 0:
            return _reject("「调用预设」缺少预设编号")
        out["presetId"] = pid
        out["duration"] = num("duration", 2.0, 0.5, 15.0)
        out["curve"] = curve
    elif k == "wait":
        out["duration"] = num("duration", 1.0, 0.5, 60.0)
    elif k == "fade_all":
        out.update(to=num("to", 0.0, 0.0, 1.0),
                   duration=num("duration", 3.0, 0.5, 30.0), curve=curve)
    elif k == "fade_odd_even":
        out.update(odd_to=num("odd_to", 0.8, 0.0, 1.0),
                   even_to=num("even_to", 0.0, 0.0, 1.0),
                   duration=num("duration", 4.0, 0.5, 30.0), curve=curve)
    elif k == "fade_ch":
        out.update(chs=ids("ch", "chs", 1, SAVE_MAX["ch"]),
                   to=num("to", 0.8, 0.0, 1.0),
                   duration=num("duration", 3.0, 0.5, 30.0), curve=curve)
    elif k == "fade_bus":
        out.update(buses=ids("bus", "buses", 1, SAVE_MAX["bus"]),
                   to=num("to", 0.8, 0.0, 1.0),
                   duration=num("duration", 3.0, 0.5, 30.0), curve=curve)
    elif k in ("fade_from", "fade_from_bus"):
        out.update(ch=int(num("ch", 1, 1, SAVE_MAX["ch"])) if k == "fade_from"
                   else int(num("bus", 1, 1, SAVE_MAX["bus"])),
                   frm=num("frm", 0.0, 0.0, 1.0),
                   to=num("to", 0.9, 0.0, 1.0),
                   duration=num("duration", 5.0, 0.5, 30.0), curve=curve)
    elif k == "rel_db":
        # 相对增减 dB: 执行瞬间从当前推子位置换算 (+3dB 在高低端手感一致)
        grp = a.get("group") if a.get("group") in ("ch", "bus", "dca") else "ch"
        cap = SAVE_MAX[grp]
        key = {"ch": "chs", "bus": "buses", "dca": "dcas"}[grp]
        out_ids = ids(grp, key, 1, cap)
        out.update(group=grp, **{key: out_ids},
                   delta_db=num("delta_db", 3.0, -30.0, 30.0),
                   duration=num("duration", 2.0, 0.5, 30.0), curve=curve)
    if a.get("at") is not None:
        # 时间轴偏移(秒): 给了 at 的动作并行执行, 缺省仍按顺序排布
        out["at"] = num("at", 0.0, 0.0, 600.0)
    return out


def _clean_actions(items, limit=64):
    """批量清洗动作, 返回 (放行的动作, 被丢的原因列表)。

    ⚠️ 为什么要把"丢的原因"返回去：/api/cardedit 曾经的写法是
        `if isinstance(actions, list) and actions:` + `if actions:` 两道闸 ——
        用户提交 2 个动作（1 合法 1 非法）会拿到 {"ok":true}，实际只存 1 个；
        提交空数组想清空，同样 {"ok":true} 而一个没少。**假成功比报错更难查**。
        现在丢弃明细一路透到前端 toast，用户立刻知道少了什么、为什么。
    """
    if not isinstance(items, list):
        return None, ["actions 不是数组"]
    if len(items) > limit:
        return None, ["动作超过 %d 条上限" % limit]
    why, out = [], []
    for i, a in enumerate(items):
        c = _clean_action(a, why)
        if c:
            out.append(c)
        else:
            why[-1] = "第 %d 个动作: %s" % (i + 1, why[-1]) if why else "第 %d 个动作被丢弃" % (i + 1)
    return out, why
