# -*- coding: utf-8 -*-
"""x32sim/state — 调音台状态 + 唯一写入口 set_value + WriteSink + 电平/推送"""

import math
import struct
import threading
import time

from .consts import DEFAULT_NAMES, FADER_ADDR, FX_COUNT, KIND_LABEL, ON_ADDR, REVERB_DEFAULTS
from .kinds import fx_par_kind, fx_par_num, kind_group
from .logbus import EVENTS, EVT_SEQ, log
from .osc import OscMsg, encode_bundle, encode_message
from .params import CH_DEEP_ADDR, CH_EQ_ADDR, CH_EXTRA_ADDR, PARAM_SPECS, SEND_BUS, SEND_BY_SEG, blank_row, level_db, level_from_db, param_coerce, param_wire, send_pack, send_unpack  # noqa: E501
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
        # 行字典由 params.blank_row 生成: 推子/静音 + 输入通道的 12 个参数
        # (增益/反相/延时/低切/插入/门) 一次带出出厂值。新增参数只改 params.py
        # 一行 —— 曾经这类默认值散落在各处的 __init__ 里, 漏一处就让新参数在
        # snapshot/render 里 KeyError (表现为界面卡旧状态)。
        self.ch = {i: blank_row("ch") for i in range(1, 33)}
        self.bus = {i: blank_row("bus") for i in range(1, 17)}   # 输出通道 Mix Bus 1-16
        self.dca = {i: blank_row("dca") for i in range(1, 9)}
        self.auxin = {i: blank_row("auxin") for i in range(1, 7)}  # 辅助输入 Aux In 1-6
        self.usb = {i: blank_row("usb") for i in range(1, 3)}    # USB 播放返回 1-2
        self.fxret = {i: blank_row("fxret") for i in range(1, 9)}  # 效果返回 FX Return 1-8
        self.mtx = {i: blank_row("mtx") for i in range(1, 7)}    # 矩阵输出 Matrix 1-6
        self.main = blank_row("main")
        # 发送 (通道 → Bus): 二维目标, 键 = params.send_pack(ch, bus)。
        # ⚠️ 故意**不进** snapshot_state —— 512 行 × 3 字段会把 /api/state 撑爆,
        #    那笔 4G 流量账刚结清。界面按需取 /api/sends?ch=N。
        self.send = {i: blank_row("send")
                     for i in range(1, 32 * SEND_BUS + 1)}
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
        # 曲线定义搬到 params.level_db: 协议 4 段伪对数 (0.75→0dB, 0.5→-10,
        # 0.25→-30, 0.0625→-60)。这里只做入口转发, 保证"界面 / 卡片 rel_db /
        # DCA 级联 / 电平模拟"四处的 dB 全部同源。
        return level_db(f)


def db_to_fader(db: float) -> float:
    """fader_to_db 的逆运算:-90..+10 dB → 0..1 推子行程 (同一 4 段曲线)。"""
    return level_from_db(db)

STATE = MixerState()
METER_SUBSCRIBERS = {}      # {(ip, port): 最后活跃时间}; /meters /subscribe /xremote 续期, 30 秒超时清理
METER_LOCK = threading.Lock()
START_TIME = time.time()


def _row_of(group, idx):
    """分组 → 行字典 (表驱动读写的公共出口)。

    main 只有一路且编号固定 0，"行"就是 STATE.main 本身 (它没有按路分包)。
    越界编号抛 KeyError —— 与旧行为一致，target_exists 正是靠它判断
    "本地台面没有这个目标"。
    """
    if group == "main":
        return STATE.main
    store = getattr(STATE, group, None)
    if store is None:
        raise KeyError(group)
    return store[idx]


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
        elif len(segs) == 4 and segs[0] == "ch" and (segs[2], segs[3]) in CH_EXTRA_ADDR:
            # 通道参数 (增益/反相/延时/低切/插入/门): 与推子同款通配符语义。
            # 地址索引由 params.py 从登记表生成, 新增参数不用来这里补分支。
            for sp in CH_EXTRA_ADDR[(segs[2], segs[3])]:
                for i in sorted(STATE.ch):
                    if seg_match(segs[1], "%02d" % i):
                        results.append((sp.kind, i))
        elif len(segs) == 5 and segs[0] == "ch" and segs[2] == "eq" \
                and (segs[2], segs[4]) in CH_EQ_ADDR:
            # 通道 EQ 段落: /ch/01/eq/3/g —— 段号写死在 spec 里 (每段一个 kind),
            # 所以这里通配符只作用于通道号; 段号写 * 一次展开 4 段。
            for sp in CH_EQ_ADDR[(segs[2], segs[4])]:
                band = sp.osc.strip("/").split("/")[3]
                if not seg_match(segs[3], band):
                    continue
                for i in sorted(STATE.ch):
                    if seg_match(segs[1], "%02d" % i):
                        results.append((sp.kind, i))
        elif len(segs) == 5 and segs[0] == "ch" and segs[2] == "mix" \
                and segs[4] in SEND_BY_SEG:
            # 发送: /ch/01/mix/05/level —— 二维目标, idx = send_pack(通道, Bus)。
            # 两个位置都支持通配符 (/ch/*/mix/*/level 一次 512 条)。
            sp = SEND_BY_SEG[segs[4]]
            # 取点只存在于奇数 Bus: 通配时展开奇数, 但允许用**偶数**地址查
            # (真台界面上 Bus02 与 Bus01 是同一个取点, 用户按 02 查是合理的)。
            odd_only = (sp.kind == "send_tap")
            step = 2 if odd_only else 1
            for ch in sorted(STATE.ch):
                if not seg_match(segs[1], "%02d" % ch):
                    continue
                for bus in range(1, SEND_BUS + 1, step):
                    hit = seg_match(segs[3], "%02d" % bus)
                    if not hit and odd_only and bus + 1 <= SEND_BUS:
                        hit = seg_match(segs[3], "%02d" % (bus + 1))
                    if hit:
                        results.append((sp.kind, send_pack(ch, bus)))
        elif len(segs) == 5 and segs[0] == "ch" \
                and (segs[2], segs[3], segs[4]) in CH_DEEP_ADDR:
            # 侧链滤波器: /ch/01/gate/filter/on、/ch/01/dyn/filter/f …
            # 与 EQ 同为五段但结构不同 (EQ 是 /eq/<段号>/<参数>), 用独立的表,
            # 免得 /dyn/filter/f 被当成"EQ 第 4 段"去查而静默无回包。
            for sp in CH_DEEP_ADDR[(segs[2], segs[3], segs[4])]:
                for i in sorted(STATE.ch):
                    if seg_match(segs[1], "%02d" % i):
                        results.append((sp.kind, i))
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


def _send_norm(kind, idx):
    """取点按成对 Bus 共享 → 读写都归一到奇数 Bus (与真台同一份真相)。"""
    if kind == "send_tap":
        ch, bus = send_unpack(idx)
        if bus % 2 == 0:
            return send_pack(ch, bus - 1)
    return idx


def get_value(kind, idx):
    idx = _send_norm(kind, idx)
    with STATE.lock:
        # 表驱动: 推子/静音/12 个通道参数全部走登记表 (params.PARAM_SPECS)
        sp = PARAM_SPECS.get(kind)
        if sp is not None:
            return _row_of(sp.group, idx).get(sp.field)
        if kind == "ch_dca":    # 通道归属的 DCA 位掩码
            return sum(1 << (d - 1) for d in range(1, 9)
                       if idx in STATE.dca_members.get(d, []))
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
    idx = _send_norm(kind, idx)
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
        sp = PARAM_SPECS.get(kind)
        if sp is not None:
            # 表驱动: 钳制 + 档位量化 + 落库 (params.param_coerce)。
            # 推子/静音/12 个通道参数共用这一条, 与旧的分支写法逐字等价
            # (fader=_clamp01 / on=_truthy, 通道参数多一层量程与档位)。
            _row_of(sp.group, idx)[sp.field] = param_coerce(kind, value)
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
    idx = _send_norm(kind, idx)
    v = get_value(kind, idx)
    a = addr_for_write(kind, idx)
    if a is None or v is None:
        return None
    # 表驱动定参数类型: float 参数 (推子/增益/延时/频率/门限) 发浮点,
    # 开关发 0/1, 枚举发线上的那个值 —— 由 params.param_wire 统一换算。
    # ⚠️ dyn/ratio 这类 **index 型枚举** 本地存 4.0(4:1)、线上要发 6,
    #    曾经这里直接 int(v) → 4, 真台解析成 5:1 (第 5 档), 静默改值。
    sp = PARAM_SPECS.get(kind)
    if sp is not None:
        return OscMsg(a, [param_wire(sp, v)])
    if kind == "ch_dca":
        return OscMsg(a, [int(v)])
    if kind in ("fx_type", "fx_name"):
        return OscMsg(a, [str(v)])
    if kind == "fx_mix" or fx_par_num(kind) is not None:   # FX 机架: 混合比/参数是浮点
        return OscMsg(a, [float(v)])
    return OscMsg(a, [int(v)])


PUSH_CACHE = {}      # (kind, idx) -> 上次推送的值; 状态变化推给订阅者 (真台 /xremote 行为)
PUSH_KINDS = (list(FADER_ADDR.keys()) + list(ON_ADDR.keys()))

# 每个订阅者"已经推过哪些键"。
# 为什么不能只用 PUSH_CACHE（全局）: 它是"上次推给**任何**订阅者"的值。
# 第二个客户端连上来时缓存已经填满、值也没变 → 它一个包都收不到，界面全 0；
# 客户端 A 断开期间发生的变化，A 重连后同样补不回来。真台 /xremote 本来也
# 只推变化（不做初始全量），所以这不算协议不一致，但**作为模拟器**，
# 让新订阅者能自己对齐状态是刚需 —— 见 XREMOTE_SNAPSHOT 与 /subscribe。
SUB_SEEN = {}        # addr -> set((kind, idx))
XREMOTE_SNAPSHOT = [False]   # /xremote 首帧是否补一次全量（非真台行为，默认关）

# /subscribe 的按地址周期订阅: addr -> {pattern: {rate, due, expire}}
# 协议语义（Unofficial v4.06 / 官方 /subscribe）: 客户端说"我关心这个地址、
# 按这个倍数回报"，服务端在 10 秒内周期性回值；rate 的单位是 50ms 的倍数。
PATTERN_SUBS = {}
PATTERN_LOCK = threading.Lock()
SUB_TIMEOUT = 10.0


def subscribe_client(addr):
    """登记一个订阅者（/xremote、/subscribe、/meters 都走这里）。

    返回 True 表示这是**首次**出现的来源 —— 调用方可以据此决定要不要补快照。
    ⚠️ 以前三处各自 `METER_SUBSCRIBERS[addr] = time.time()`，SUB_SEEN 就没人建，
       新订阅者第一次遍历时拿不到 seen 集合（表现为"有时补、有时不补"）。
    """
    with METER_LOCK:
        first = addr not in METER_SUBSCRIBERS
        METER_SUBSCRIBERS[addr] = time.time()
        if first:
            SUB_SEEN[addr] = set()
        return first


def unsubscribe_client(addr):
    with METER_LOCK:
        METER_SUBSCRIBERS.pop(addr, None)
        SUB_SEEN.pop(addr, None)
    with PATTERN_LOCK:
        PATTERN_SUBS.pop(addr, None)


def subscribe_pattern(addr, pattern, rate=1):
    """按地址周期订阅（协议 /subscribe）。rate 单位 = 50ms 的倍数。"""
    try:
        rate = max(1, min(int(rate), 200))
    except (TypeError, ValueError):
        rate = 1
    now = time.time()
    with PATTERN_LOCK:
        d = PATTERN_SUBS.setdefault(addr, {})
        # due=now → 订阅后的第一轮立刻回一帧当前值，这正是"新客户端拿到初始值"
        # 的标准做法（真台也是这样：/subscribe 之后马上就能收到当前值）。
        d[pattern] = {"rate": rate * 0.05, "due": now, "expire": now + SUB_TIMEOUT}
    return rate


def unsubscribe_pattern(addr, pattern=None):
    with PATTERN_LOCK:
        if pattern is None:
            PATTERN_SUBS.pop(addr, None)
        else:
            PATTERN_SUBS.get(addr, {}).pop(pattern, None)


def _service_pattern_subs(sock):
    """按 due 时间把订阅地址的当前值回给订阅者（10 秒无续期自动过期）。"""
    now = time.time()
    with PATTERN_LOCK:
        due = []
        for a, d in list(PATTERN_SUBS.items()):
            for p, st in list(d.items()):
                if now > st["expire"]:
                    d.pop(p, None)
                    continue
                if now >= st["due"]:
                    st["due"] = now + st["rate"]
                    due.append((a, p))
            if not d:
                PATTERN_SUBS.pop(a, None)
    for a, p in due:
        matches = match_address(p)
        replies = [r for r in (value_reply(k, i) for k, i in matches) if r is not None]
        if not replies:
            continue
        try:
            pkt = encode_message(replies[0]) if len(replies) == 1 \
                else encode_bundle(replies)
            sock.sendto(pkt, a)
        except OSError:
            pass


def _push_state_diffs(sock, subscribers):
    """每次 tick 对比缓存, 把发生变化的推子/静音推给订阅者 (模拟真台变更推送)。

    ⚠️ 两个易错点（都踩过）:
      1. `PUSH_CACHE` 必须在**所有**订阅者都发完之后再更新。写在循环里的话，
         第一个订阅者发完就把缓存改了，后面的订阅者算出来"没变化"→ 漏发。
      2. 新订阅者（SUB_SEEN 里没有这个键）在 XREMOTE_SNAPSHOT 开启时要补一帧，
         否则它只能等下一次真实变化 —— 界面在很长一段时间里是空的。
    """
    sent = 0
    counts = profile().counts
    snapshot = XREMOTE_SNAPSHOT[0]
    for kind in PUSH_KINDS:
        ids = [0] if kind.startswith("main_") else \
            range(1, counts.get(kind_group(kind), 0) + 1)
        for i in ids:
            v = get_value(kind, i)
            if v is None:
                continue
            key = (kind, i)
            old = PUSH_CACHE.get(key)
            changed = old is None or abs(old - v) >= 1e-4
            pkt = None
            for s in subscribers:
                seen = SUB_SEEN.get(s)
                if seen is None:
                    seen = SUB_SEEN[s] = set()
                if not changed and not (snapshot and key not in seen):
                    continue
                if pkt is None:
                    reply = value_reply(kind, i)
                    if reply is None:      # 当前控台没有这个目标的地址 → 不推送
                        break
                    pkt = encode_message(reply)
                try:
                    sock.sendto(pkt, s)
                    sent += 1
                except OSError:
                    pass
                seen.add(key)
            if changed:
                PUSH_CACHE[key] = v
    return sent


def meter_streamer(sock):
    """50ms 基准循环: 电平与变更推送仍按 150ms (3 tick), /subscribe 按各自 rate。

    为什么拆成 50ms 基准: /subscribe 的 rate 单位就是 50ms 的倍数, 用 150ms 的
    循环去服务它, rate=1 只能做到 6.7 Hz, 与协议的 20 Hz 差三倍。
    """
    tick = 0
    while True:
        time.sleep(0.05)
        tick += 1
        now = time.time()
        with METER_LOCK:
            stale = [s for s, ts in METER_SUBSCRIBERS.items() if now - ts > 30.0]
            for s in stale:
                METER_SUBSCRIBERS.pop(s, None)
                SUB_SEEN.pop(s, None)
            if stale:
                log("METER 清理失活订阅者: %d 个" % len(stale))
            subscribers = list(METER_SUBSCRIBERS)
        for s in stale:
            with PATTERN_LOCK:
                PATTERN_SUBS.pop(s, None)
        try:
            _service_pattern_subs(sock)
        except Exception as e:
            log("按地址订阅推送出错: %r" % e)
        if tick % 3:            # 以下保持 150ms 一轮
            continue
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


def source_level_db(i, t=None):
    """**话筒信号本身**的电平 (dBFS, 增益与推子之前)。

    为什么要把"信号"和"推子位置"拆开: 自动增益/自动推子靠电平做判断,
    若电平表 = 推子位置 × 信号, 自动推子往下拉 → 表跟着掉 → 再往下拉,
    变成自激塌陷。有了独立的信号源, 电平表 = 信号 + 增益 + 推子, 才是
    真实调音台的叠加关系, 控制器才有稳定的被控对象。

    波形是"音乐化"的: 0.7 Hz 慢起伏 + 3.1 Hz 快起伏 + 0.15 Hz 段落起伏,
    每路相位错开, 幅度约 ±8 dB —— 足够让自动推子有活干, 又不会一帧跳变。
    """
    if t is None:
        t = time.time() - START_TIME
    base = -20.0 + 4.0 * math.sin(t * 0.7 + i * 1.3) + 1.5 * math.sin(t * 3.1 + i * 2.1)
    phrase = 3.0 * math.sin(t * 0.15 + i * 0.37)
    return base + phrase


def compute_levels():
    """返回当前电平 dB 列表:32 输入通道 + 16 输出 Bus (UDP 推送与 Web 界面共用)。

    通道电平 = 信号源 + 数字增益 + 推子 (真台的叠加关系), 静音为 -∞。
    Bus 仍沿用推子位置(虚拟混音无法逐路求和, 这里只求"看着像")。
    """
    t = time.time() - START_TIME
    floats = []
    with STATE.lock:
        for group, count in ((STATE.ch, 32), (STATE.bus, 16)):
            for i in range(1, count + 1):
                ch = group[i]
                if not ch["on"]:
                    floats.append(-32768.0)  # -∞
                    continue
                if group is STATE.ch:
                    db = (source_level_db(i, t) + float(ch.get("trim") or 0.0)
                          + STATE.fader_to_db(ch["fader"]))
                else:
                    # Bus: 沿用推子位置 + 轻微起伏 (无逐路求和, 不求精确)
                    db = STATE.fader_to_db(ch["fader"]) + 1.5 * math.sin(t * 0.9 + i)
                # 0.1 dB 足够画表 (屏幕上一个像素约 0.5 dB)。保留全精度会让
                # 每轮多传 350 B —— 4 Hz 下就是 5 MB/h, 换来的是看不见的精度。
                floats.append(round(max(-60.0, min(0.0, db)), 1))
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
