# -*- coding: utf-8 -*-
"""x32sim/state — 调音台状态 + 唯一写入口 set_value + WriteSink + 电平/推送"""

import math
import struct
import threading
import time

from .consts import DEFAULT_NAMES, FADER_ADDR, FX_COUNT, KIND_LABEL, ON_ADDR, REVERB_DEFAULTS
from .kinds import fx_par_kind, fx_par_num, kind_group
from .logbus import EVENTS, EVT_SEQ, log
from .osc import OscMsg, encode_message
from .profiles import addr_for_write, profile
from .runtime import MAIN_SOCK
from .util import _clamp01, _finite, _truthy

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ---------------------------------------------------------------- 调音台状态

class MixerState:
    def __init__(self):
        self.lock = threading.Lock()
        self.ch = {i: {"fader": 0.75, "on": 1} for i in range(1, 33)}
        self.bus = {i: {"fader": 0.75, "on": 1} for i in range(1, 17)}   # 输出通道 Mix Bus 1-16
        self.dca = {i: {"fader": 0.75, "on": 1} for i in range(1, 9)}
        self.auxin = {i: {"fader": 0.75, "on": 1} for i in range(1, 7)}  # 辅助输入 Aux In 1-6
        self.usb = {i: {"fader": 0.75, "on": 1} for i in range(1, 3)}    # USB 播放返回 1-2
        self.fxret = {i: {"fader": 0.75, "on": 1} for i in range(1, 9)}  # 效果返回 FX Return 1-8
        self.mtx = {i: {"fader": 0.75, "on": 1} for i in range(1, 7)}    # 矩阵输出 Matrix 1-6
        self.main = {"fader": 0.75, "on": 1}
        self.scene = 1
        # FX 机架 1-8: 混响器. par 存**原生单位**参数值 (时间=秒 / 预延迟=毫秒 / 阻尼=0..1),
        # 参数索引随算法变化, 映射表见 X32_FX_ALGOS / M32_FX_ALGOS (默认值需真机标定)
        self.fx = {i: {"type": "", "name": "", "on": 1, "mix": 0.25, "par": {}}
                   for i in range(1, FX_COUNT + 1)}
        for i, algo in enumerate(("HALL", "PLATE", "ROOM", "CHAMBER")):
            if i + 1 <= FX_COUNT:
                self.fx[i + 1]["type"] = algo
                self.fx[i + 1]["par"] = dict(REVERB_DEFAULTS[algo])
        self.fx[1]["name"] = "主厅堂"
        self.fx[2]["name"] = "人声板"
        # DCA 成员表: {dca_idx: [ch_idx, ...]}; 手动推 DCA 时成员推子按 dB 差量级联
        # (与真台一致: /ch/XX/mix/dca 位掩码设置归属, 默认每 8 路一组)
        self.dca_members = {i: [] for i in range(1, 9)}
        for i in range(1, 33):
            self.dca_members[(i - 1) // 8 + 1].append(i)
        # 通道/输出名称: {(group, idx): str}, 卡片·推子行·控制键统一显示
        self.names = dict(DEFAULT_NAMES)

    def fader_to_db(self, f: float) -> float:
        if f <= 0.0:
            return -90.0
        if f >= 0.75:
            return (f - 0.75) / 0.25 * 10.0
        # 历史 bug：此处曾有 `if f <= 0.001: return -90.0`，把 -57.5dB 以下的整段
        # 塌陷成 -∞，导致 -60dB 无法往返（Android/Kotlin 端曾忠实照搬同一 bug）。
        # 改为对下限做 clamp，保住低段分辨率。
        return max(-90.0, 20.0 * math.log10(f / 0.75))


def db_to_fader(db: float) -> float:
    """fader_to_db 的逆运算:-90..+10 dB → 0..1 推子行程"""
    db = min(max(db, -90.0), 10.0)
    if db <= -89.5:
        return 0.0
    if db >= 10.0:
        return 1.0
    if db >= 0.0:
        return 0.75 + db / 10.0 * 0.25
    return 0.75 * (10.0 ** (db / 20.0))

STATE = MixerState()
METER_SUBSCRIBERS = {}      # {(ip, port): 最后活跃时间}; /meters /subscribe /xremote 续期, 30 秒超时清理
METER_LOCK = threading.Lock()
START_TIME = time.time()


def match_address(pattern: str):
    """按 X32 通配符匹配,返回 (group, index, kind) 列表。
    kind: 'ch_fader' | 'ch_on' | 'dca_fader' | 'dca_on' | 'main_fader' | 'main_on'
    """
    segs = pattern.strip("/").split("/")
    results = []

    def seg_match(pat, val):
        return pat == "*" or pat == val

    with STATE.lock:
        if len(segs) == 4 and segs[0] == "ch" and segs[2] == "mix":
            kind = {"fader": "ch_fader", "on": "ch_on", "dca": "ch_dca"}.get(segs[3])
            if kind:
                for i in sorted(STATE.ch):
                    if seg_match(segs[1], "%02d" % i):
                        results.append((kind, i))
        elif len(segs) == 4 and segs[0] == "bus" and segs[2] == "mix":
            kind = {"fader": "bus_fader", "on": "bus_on"}.get(segs[3])
            if kind:
                for i in sorted(STATE.bus):
                    if seg_match(segs[1], "%02d" % i):
                        results.append((kind, i))
        elif len(segs) == 3 and segs[0] == "dca":
            kind = {"fader": "dca_fader", "on": "dca_on"}.get(segs[2])
            if kind:
                for i in sorted(STATE.dca):
                    if seg_match(segs[1], "%02d" % i):
                        results.append((kind, i))
        elif len(segs) == 4 and segs[2] == "mix" and segs[0] in ("auxin", "usb", "fxret", "mtx"):
            kind = {"fader": segs[0] + "_fader", "on": segs[0] + "_on"}.get(segs[3])
            if kind:
                group = {"auxin": STATE.auxin, "usb": STATE.usb,
                         "fxret": STATE.fxret, "mtx": STATE.mtx}[segs[0]]
                val = segs[1].rstrip("LR")            # 真台 FX 返回常用 01L/01R 写法
                for i in sorted(group):
                    if seg_match(val, "%02d" % i):
                        results.append((kind, i))
        elif len(segs) == 4 and segs[0] == "main" and segs[1] == "st" and segs[2] == "mix":
            kind = {"fader": "main_fader", "on": "main_on"}.get(segs[3])
            if kind:
                results.append((kind, 0))
        elif len(segs) >= 3 and segs[0] == "fx" and segs[1].isdigit():
            # FX 机架: /fx/<槽>/type | on | mix | par/<参数号> | config/name
            # ⚠️ 槽号不补零 (/fx/1/...), 参数号补零 (/fx/1/par/01)
            slot = int(segs[1])
            if 1 <= slot <= FX_COUNT:
                if len(segs) == 3:
                    kind = {"type": "fx_type", "on": "fx_on", "mix": "fx_mix"}.get(segs[2])
                    if kind:
                        results.append((kind, slot))
                elif len(segs) == 4 and segs[2] == "par" and segs[3].isdigit():
                    p = int(segs[3])
                    if 1 <= p <= 64:
                        results.append((fx_par_kind(p), slot))
                elif len(segs) == 4 and segs[2] == "config" and segs[3] == "name":
                    results.append(("fx_name", slot))
    return results


def get_value(kind, idx):
    with STATE.lock:
        if kind == "ch_fader":  return float(STATE.ch[idx]["fader"])
        if kind == "ch_on":     return int(STATE.ch[idx]["on"])
        if kind == "ch_dca":    # 通道归属的 DCA 位掩码
            return sum(1 << (d - 1) for d in range(1, 9)
                       if idx in STATE.dca_members.get(d, []))
        if kind == "bus_fader": return float(STATE.bus[idx]["fader"])
        if kind == "bus_on":    return int(STATE.bus[idx]["on"])
        if kind == "dca_fader": return float(STATE.dca[idx]["fader"])
        if kind == "dca_on":    return int(STATE.dca[idx]["on"])
        if kind == "main_fader": return float(STATE.main["fader"])
        if kind == "main_on":   return int(STATE.main["on"])
        for g in ("auxin", "usb", "fxret", "mtx"):
            if kind == g + "_fader": return float(STATE.__dict__[g][idx]["fader"])
            if kind == g + "_on":    return int(STATE.__dict__[g][idx]["on"])
        if 1 <= idx <= FX_COUNT:
            fx = STATE.fx[idx]
            if kind == "fx_type": return fx["type"]
            if kind == "fx_name": return fx["name"]
            if kind == "fx_on":   return int(fx["on"])
            if kind == "fx_mix":  return float(fx["mix"])
            p = fx_par_num(kind)
            if p is not None:     return float(fx["par"].get(p, 0.0))
    return None


def target_exists(kind, idx):
    """本地台面是否存在这个目标。

    换控台后规模可能大于本地（例如 Wing 48 路 vs 本地 32 路），
    目标超出范围时要**明确跳过并记日志**，不能让 KeyError 冒出去。
    """
    try:
        return get_value(kind, idx) is not None
    except (KeyError, IndexError, TypeError):
        return False


def set_value(kind, idx, value, source="manual"):
    # 延迟导入: fx→state 是顶层依赖, state→fx 只在本函数用 —— 放这里打破环
    # (合成单文件形态下该行被剥离, 同一命名空间直接解析)。
    from .fx import _fx_time_fanout, fx_algo, fx_algos, fx_par_clamp
    # source="manual" (网页/控制键/外部 OSC 默认) 会触发冲突检测;
    # source="action"/"dca"/"fade" 是卡片/回归/DCA 级联/渐变插值自己的写入, 不检测
    if source == "manual":
        _check_manual_takeover(kind, idx)
    cascade_from = None
    if kind == "dca_fader" and source == "manual":
        cascade_from = get_value(kind, idx)   # DCA 手动移动 → 成员级联基准
    with STATE.lock:
        if kind == "ch_fader":   STATE.ch[idx]["fader"] = _clamp01(value)
        elif kind == "ch_on":    STATE.ch[idx]["on"] = _truthy(value)
        elif kind == "ch_dca":   # 位掩码: 通道归属哪些 DCA (bit0=DCA1 ...)
            mask = int(value) & 0xFF
            for d in range(1, 9):
                members = STATE.dca_members[d]
                has = idx in members
                want = bool(mask & (1 << (d - 1)))
                if want and not has:
                    members.append(idx)
                    members.sort()
                elif has and not want:
                    members.remove(idx)
        elif kind == "bus_fader": STATE.bus[idx]["fader"] = _clamp01(value)
        elif kind == "bus_on":   STATE.bus[idx]["on"] = _truthy(value)
        elif kind == "dca_fader": STATE.dca[idx]["fader"] = _clamp01(value)
        elif kind == "dca_on":   STATE.dca[idx]["on"] = _truthy(value)
        elif kind == "main_fader": STATE.main["fader"] = _clamp01(value)
        elif kind == "main_on":  STATE.main["on"] = _truthy(value)
        elif 1 <= idx <= FX_COUNT:
            fx = STATE.fx[idx]
            if kind == "fx_type":
                fx["type"] = str(value).upper()[:8]
                # 换算法要重新铺一套该算法的默认参数, 否则参数索引会串味
                if fx["type"] in fx_algos():
                    fx["par"] = dict(REVERB_DEFAULTS.get(fx["type"], {}))
                else:
                    fx["par"] = {}
            elif kind == "fx_name": fx["name"] = str(value)[:12]
            elif kind == "fx_on":   fx["on"] = _truthy(value)
            elif kind == "fx_mix":  fx["mix"] = _clamp01(value)
            else:
                p = fx_par_num(kind)
                if p is not None:
                    fx["par"][p] = fx_par_clamp(idx, p, value)
        else:
            hit = False
            for g in ("auxin", "usb", "fxret", "mtx"):
                if kind == g + "_fader":
                    STATE.__dict__[g][idx]["fader"] = _clamp01(value)
                    hit = True
                elif kind == g + "_on":
                    STATE.__dict__[g][idx]["on"] = _truthy(value)
                    hit = True
            if not hit:
                # 走到这里说明 kind 完全不认识 —— 必须留痕。
                # 曾经的写法是"什么都不做"，于是一个拼错的 kind 表现成
                # "接口返回 ok 但台子没反应"，最难查的那类。
                log("SET 未识别的写入: kind=%r idx=%r value=%r (已忽略)"
                    % (kind, idx, value))
    if source == "manual":
        _push_manual_now(kind, idx)
    # 写出口 (WriteSink): 落库后的值交给各 sink。sink 只能拿到 after 值,
    # 物理上不可能再转发原始入参 (B0-2 的根因); 桥接/推送/冲突检测互不干扰。
    for _sink in SINKS:
        _sink.on_write(kind, idx, get_value(kind, idx), source)
    if cascade_from is not None:
        _dca_cascade(idx, cascade_from, _finite(value, 0.0))
    # 混响时间改动 → 按同步模式广播到联动组。
    # 三个入口(软件改 / Tap 换算 / 真台回读)最终都汇聚到 set_value, 所以只在这里挂一次;
    # 广播出去的写入用 source="action", 不会再触发本分支 —— 天然断开回环。
    p = fx_par_num(kind)
    if p is not None and source in ("manual", "sync"):
        algo = fx_algo(idx)
        if algo and p == algo["time"]:
            _fx_time_fanout(idx, get_value(kind, idx), source)


def _push_manual_now(kind, idx):
    """手动写入立即广播给订阅者 (真台行为: 面板动推子马上推送, 让桥接端及时察觉干预)。"""
    sock = MAIN_SOCK[0]
    if sock is None:
        return
    key = (kind, idx)
    v = get_value(kind, idx)
    if v is None:
        return
    # 节流: 只有"值没变且来得太快"才合并; 值一变就必须推 —— 否则桥接场景下真台的
    # 面板干预会被渐变转发的时间戳挤掉 (见 NOTIFY_LAST 注释)。
    now = time.time()
    with NOTIFY_LOCK:
        last = NOTIFY_LAST.get(key)
        if last is not None and last[0] == v and now - last[1] < 0.08:
            return
        NOTIFY_LAST[key] = [v, now]
    PUSH_CACHE[key] = v           # 立即推送后, 周期推送不再重发
    with METER_LOCK:
        subs = list(METER_SUBSCRIBERS)
    if not subs:
        return
    try:
        reply = value_reply(kind, idx)
        if reply is None:      # 当前控台没有该目标的地址
            return
        pkt = encode_message(reply)
    except Exception:
        return
    for s in subs:
        try:
            sock.sendto(pkt, s)
        except OSError:
            pass


def _dca_cascade(dca_idx, old_fader, new_fader):
    """手动推 DCA: 成员通道推子按 dB 差量级联 (真台手感)。
    卡片/回归写 DCA 走 source="action", 不级联。"""
    try:
        delta = STATE.fader_to_db(min(max(new_fader, 0.0), 1.0)) - \
            STATE.fader_to_db(min(max(old_fader, 0.0), 1.0))
    except Exception:
        return
    if abs(delta) < 0.01:
        return
    with STATE.lock:
        members = list(STATE.dca_members.get(dca_idx, []))
    for c in members:
        cur = get_value("ch_fader", c)
        if cur is None:
            continue
        tgt = db_to_fader(min(max(STATE.fader_to_db(cur) + delta, -90.0), 10.0))
        set_value("ch_fader", c, tgt, source="dca")


def get_name(group, idx):
    with STATE.lock:
        return STATE.names.get((group, idx), "")


def set_name(group, idx, name):
    name = str(name).strip()[:12]
    # 延迟导入: state→persist 与 persist→state/bridge 会成环, 只在函数体内打破
    # (合成单文件形态下该行被剥离, 同一命名空间直接解析)。
    from .persist import save_user_config
    with STATE.lock:
        if name:
            STATE.names[(group, idx)] = name
        else:
            STATE.names.pop((group, idx), None)
    log("NAME %s %s = %s" % (group, idx, name or "(已清除)"))
    save_user_config()          # 网页或 OSC 改名都持久化, 重启不丢
    return name


def target_label(group, idx):
    """日志用目标描述: 有名字显示 名字(编号), 没有显示 组 编号。"""
    n = get_name(group, idx)
    if n:
        return "%s(%s%02d)" % (n, KIND_LABEL.get(group, group), idx)
    return "%s %02d" % (KIND_LABEL.get(group, group), idx)


def value_reply(kind, idx):
    """(kind, idx) → 状态回包的 OSC 消息。

    返回 None 表示**当前控台不支持该目标**（调用方须过滤掉，不能塞进 bundle）。
    """
    v = get_value(kind, idx)
    a = addr_for_write(kind, idx)
    if a is None or v is None:
        return None
    if kind == "ch_dca":
        return OscMsg(a, [int(v)])
    if kind.endswith("_fader") or kind == "fx_mix" or fx_par_num(kind) is not None:
        return OscMsg(a, [float(v)])
    if kind in ("fx_type", "fx_name"):
        return OscMsg(a, [str(v)])
    return OscMsg(a, [int(v)])


PUSH_CACHE = {}      # (kind, idx) -> 上次推送的值; 状态变化推给订阅者 (真台 /xremote 行为)
PUSH_KINDS = (list(FADER_ADDR.keys()) + list(ON_ADDR.keys()))


def _push_state_diffs(sock, subscribers):
    """每次 tick 对比缓存, 把发生变化的推子/静音推给订阅者 (模拟真台变更推送)。"""
    sent = 0
    counts = profile().counts
    for kind in PUSH_KINDS:
        ids = [0] if kind.startswith("main_") else \
            range(1, counts.get(kind_group(kind), 0) + 1)
        for i in ids:
            v = get_value(kind, i)
            if v is None:
                continue
            key = (kind, i)
            old = PUSH_CACHE.get(key)
            if old is not None and abs(old - v) < 1e-4:
                continue
            PUSH_CACHE[key] = v
            reply = value_reply(kind, i)
            if reply is None:      # 当前控台没有这个目标的地址 → 不推送
                continue
            pkt = encode_message(reply)
            for s in subscribers:
                try:
                    sock.sendto(pkt, s)
                    sent += 1
                except OSError:
                    pass
    return sent


def meter_streamer(sock):
    """每 150ms 向所有订阅者推送 /meters/1 blob + 台面变更推送; 30 秒无续期的订阅者自动剔除。"""
    while True:
        time.sleep(0.15)
        now = time.time()
        with METER_LOCK:
            stale = [s for s, ts in METER_SUBSCRIBERS.items() if now - ts > 30.0]
            for s in stale:
                METER_SUBSCRIBERS.pop(s, None)
            if stale:
                log("METER 清理失活订阅者: %d 个" % len(stale))
            subscribers = list(METER_SUBSCRIBERS)
        if not subscribers:
            continue
        floats = compute_levels()
        blob = struct.pack(">i", len(floats))
        for v in floats:
            blob += struct.pack(">f", v)
        pkt = encode_message(OscMsg("/meters/1", [blob]))
        for s in subscribers:
            try:
                sock.sendto(pkt, s)
            except OSError:
                pass
        try:
            _push_state_diffs(sock, subscribers)
        except Exception as e:
            log("推送台面变更出错: %r" % e)


def compute_levels():
    """返回当前电平 dB 列表:32 输入通道 + 16 输出 Bus (UDP 推送与 Web 界面共用)。"""
    t = time.time() - START_TIME
    floats = []
    with STATE.lock:
        for group, count in ((STATE.ch, 32), (STATE.bus, 16)):
            for i in range(1, count + 1):
                ch = group[i]
                if not ch["on"]:
                    floats.append(-32768.0)  # -∞
                    continue
                db = STATE.fader_to_db(ch["fader"])
                # 模拟动态信号: 正弦 + 伪随机起伏
                wiggle = 4.0 * math.sin(t * 1.7 + i * 0.9) + 2.0 * math.sin(t * 4.3 + i * 2.3)
                floats.append(max(-60.0, min(0.0, db + wiggle)))
    return floats
NOTIFY_LAST = {}     # 手动变更推送节流: (kind, idx) -> [上次推送的值, 时间]
                     # ⚠️ 必须**连同值一起**判断"是否重复": 只看时间会把真台的面板干预挤掉。
                     # 桥接时本地渐变每 tick 都转发一次, 真台把转发也当手动变更 → 时间戳被
                     # 持续刷新; 等真正的干预到达, "距上次 < 80ms" 恒成立, 立即推送被丢,
                     # 只剩 150ms 周期推送, 而那时干预值已被下一 tick 的渐变值覆盖,
                     # 推出去的值 equals 渐变值 → 被回声抑制吸收 → 冲突永远检测不到。

# ---- 冲突策略: 手动干预只停该通道, 其余动作继续 ----
# 卡片执行期间, 正在被渐变驱动的 (kind, idx) 登记在 FADE_ZONE;
# 此时有手动写入 (网页推子 / 控制键 / 外部 OSC) 命中同一通道,
# 只把该通道标记为手动接管 (停在手动位置), 卡片其余通道与后续动作照常执行, 并推送网页通知。
FADE_LOCK = threading.Lock()
FADE_ZONE = {}        # (kind, idx) -> 卡片名 (正在被卡片渐变驱动)
INTERRUPTS = set()    # 本轮渐变中被手动接管的 (kind, idx)


def _register_fade(targets, card_name):
    """渐变开始: 登记驱动通道, 并清掉这些通道上一轮的接管标记 (后续动作重新获得控制权)。"""
    with FADE_LOCK:
        for k, i, _a, _b in targets:
            INTERRUPTS.discard((k, i))
            FADE_ZONE[(k, i)] = card_name


def _unregister_fade(targets):
    with FADE_LOCK:
        for k, i, _a, _b in targets:
            FADE_ZONE.pop((k, i), None)


def _check_manual_takeover(kind, idx):
    """手动写入前检查: 该通道正被卡片渐变驱动 → 标记接管 + 记通知事件。"""
    key = (kind, idx)
    with FADE_LOCK:
        zone = FADE_ZONE.get(key)
        if not zone or key in INTERRUPTS:
            return
        INTERRUPTS.add(key)
    text = "⚠️ %s 手动接管 — 卡片「%s」停止驱动该通道, 其余动作继续" % (
        target_label(kind_group(kind), idx), zone)
    EVT_SEQ[0] += 1
    EVENTS.append({"id": EVT_SEQ[0], "time": time.strftime("%H:%M:%S"), "text": text})
    log("CONFLICT %s" % text)


def _interrupted(kind, idx):
    return (kind, idx) in INTERRUPTS


NOTIFY_LOCK = threading.Lock()   # 只护 NOTIFY_LAST (原来借用 BRIDGE_LOCK,
                                #  那会让 state 依赖 bridge —— 模块化要切断)


class WriteSink:
    """写出口协议: set_value 落库后, 把**落库后的值**交给注册的 sink。

    为什么需要: 曾经的写法是 set_value 尾部直接调 _bridge_forward ——
    state 因此 import bridge, 层级关系倒挂; 而且转发的是调用入参,
    本地钳到 1.0 真台却收到 1.7 (B0-2)。SINKS 注册制把这两件事一起解决:
    state 不知道谁在听, sink 也拿不到原始值。
    """

    def on_write(self, kind, idx, value, source):
        raise NotImplementedError


SINKS = []        # 由 app.py 注册 (桥接 sink); 状态层不 import 任何业务模块
