
from .consts import FADER_ADDR, FX_COUNT, M32_FX_ALGOS, ON_ADDR, X32_FX_ALGOS
from .kinds import fx_par_num
from .logbus import log
# -*- coding: utf-8 -*-
"""x32sim/profiles — 控台驱动层: ConsoleProfile 与各控台地址表"""

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ================================================================ 控台驱动层
#
# 卡片动作、桥接转发、状态回包 全部走在 (kind, idx) 这个**语义管道**上 —— 它里面
# 不含任何协议信息（ch_fader 就是"某个输入通道的推子"）。把 kind 翻译成真实 OSC
# 地址的活，全部收敛到 ConsoleProfile。换控台 = 换一个 profile 实例，动作层不动。
#
# ⚠️ 约定（很重要）：对"本控台不支持"的 kind，addr() 必须返回 None，而不是抛异常
#    或返回一个"看着像对"的地址。调用方见到 None 要记一条日志再跳过 —— 让"不支持"
#    变成**看得见的降级**，而不是"静默写到了别的地方"。这正是本项目栽过的跟头
#    （见共享协议规范 §3.5：地址格式化异常被 try/except 吞掉，桥接真台时主输出
#    推子从来没被转发过，日志里只剩一行看不出问题的"转发失败"）。

# ⚠️ 可变容器: set_profile() 与 load_user_config() 都会改写它, 跨模块读取
#    必须看到最新值 (理由同 runtime 的 LAN_IP)。
CTRL_PROFILE_ID = ["x32"]


class ConsoleProfile:
    """控台驱动基类。子类只需填元信息 + 实现 addr()。

    接口刻意留了 transport 字段：X32/M32/Wing 与 Yamaha 大多走 OSC，但
    Allen & Heath 是 TCP MIDI、部分 Yamaha 型号也吃 MIDI —— 那种情况下
    "地址翻译"之外还需要一层消息编码。把传输类型摆在接口上，是为了将来接
    A&H 时不用推翻这套抽象，只需在编码层加一条分支。
    """

    id = ""
    label = ""
    vendor = ""
    transport = "osc"      # "osc" | "midi" | ...
    ready = False          # 地址表是否可用；False 时所有写入被拒绝并记日志
    verified = False       # True = 地址表经真机核对过（ready 但非 verified，
                           # 表示"照公开资料填了结构，但没上过真台"）
    note = ""
    counts = {}            # 各分组规模：卡片目标上限与导入校验用
    caps = ()              # 支持的语义能力：UI 灰显 / 导入校验用
    fx_algos = {}          # 本控台的效果库（算法名 → {label,time,predelay,damp,tmin,tmax}）
                           # 空表 = 该控台效果库未标定 → 混响页禁用、写入被拒绝

    def addr(self, kind, idx):
        """(kind, idx) → 真实地址；本控台不支持则返回 None。"""
        return None

    def supports(self, kind):
        return self.addr(kind, 0) is not None

    def pending(self):
        """未标定时列出还需要提供地址映射的 kind（供 UI 与规范 §12 使用）。"""
        return []


class X32Profile(ConsoleProfile):
    """X32 —— 权威实现，地址表与真机 OSC 表逐条对齐。

    M32 是 Midas 贴牌的同代产品，OSC 地址表与 X32 **完全一致**，因此直接继承
    本类（见 M32Profile），只换显示名 —— 所谓"支持 M32"不需要任何新协议代码。
    """

    id = "x32"
    label = "X32"
    vendor = "Behringer"
    ready = True
    verified = True
    counts = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 2,
              "fxret": 8, "mtx": 6, "main": 1, "fx": FX_COUNT}
    caps = ("fader", "mute", "dca", "dca_member", "scene", "fx", "fx_par",
            "name", "meter", "raw_osc")
    fx_algos = X32_FX_ALGOS

    def addr(self, kind, idx):
        if kind == "ch_dca":
            return "/ch/%02d/mix/dca" % idx
        if kind == "fx_type":
            return "/fx/%d/type" % idx
        if kind == "fx_name":
            return "/fx/%d/config/name" % idx
        if kind == "fx_on":
            return "/fx/%d/on" % idx
        if kind == "fx_mix":
            return "/fx/%d/mix" % idx
        p = fx_par_num(kind)
        if p is not None:
            return "/fx/%d/par/%02d" % (idx, p)
        # ⚠️ Main 必须单独返回：`/main/st/mix/fader` 里**没有 %02d 占位符**，
        #    走 `% idx` 会抛 TypeError（历史 bug，见共享协议规范 §3.5 第 4 条）。
        if kind == "main_fader":
            return "/main/st/mix/fader"
        if kind == "main_on":
            return "/main/st/mix/on"
        if kind.endswith("_fader"):
            pat = FADER_ADDR.get(kind)
            return pat % idx if pat else None
        pat = ON_ADDR.get(kind)
        return pat % idx if pat else None


class M32Profile(X32Profile):
    """M32 —— 与 X32 同协议（Midas 贴牌），驱动完全复用，仅显示名不同。

    效果库**单独持有一份**（当前与 X32 同内容，因为两者是同一 Klark Teknik
    效果引擎）。分开持有的意义在于：万一某版固件效果库出现出入，改这里即可，
    不必去动公共代码，也不会连带影响 X32。
    """

    id = "m32"
    label = "M32"
    vendor = "Midas"
    fx_algos = M32_FX_ALGOS


class _StubProfile(ConsoleProfile):
    """未标定控台的骨架基类。

    ⚠️ 这里**故意**让 addr() 保持返回 None，而不是编一套"看起来像"的地址。
    因为地址蒙错的后果是"写到了别的参数上" —— 现场听起来就是莫名其妙的声音
    变化，比"完全不动作"难排查得多。所以未标定的控台一律**拒绝写入并记日志**，
    把问题摆在明面上。

    接入一个新控台的做法：照 X32Profile 实现 addr()（清单见 pending()），
    然后把 ready 置 True；经真机核对后再置 verified = True。
    """

    ready = False

    def pending(self):
        base = ["ch_fader", "ch_on", "bus_fader", "bus_on", "dca_fader",
                "dca_on", "main_fader", "main_on"]
        base += [g + "_fader" for g in ("auxin", "usb", "fxret", "mtx")]
        base += [g + "_on" for g in ("auxin", "usb", "fxret", "mtx")]
        base += ["ch_dca", "fx_type", "fx_on", "fx_mix", "fx_name", "fx_par_NN"]
        return base


class WingProfile(_StubProfile):
    id = "wing"
    label = "Wing"
    vendor = "Behringer"
    note = "Wing 是全新平台，OSC 地址空间与 X32 不通用，地址表待填"
    counts = {"ch": 48, "bus": 16, "dca": 8, "auxin": 8, "usb": 4,
              "fxret": 8, "mtx": 8, "main": 1, "fx": 16}
    caps = ("fader", "mute", "dca", "scene", "fx", "raw_osc")


class YamahaProfile(_StubProfile):
    id = "yamaha"
    label = "Yamaha DM3 / TF / QL"
    vendor = "Yamaha"
    note = "Yamaha 各型号的 OSC/MIDI 接口不一致，需按型号分别标定"
    counts = {"ch": 48, "bus": 24, "dca": 12, "auxin": 8, "usb": 2,
              "fxret": 8, "mtx": 12, "main": 1, "fx": 8}
    caps = ("fader", "mute", "dca", "scene", "raw_osc")


class AHProfile(_StubProfile):
    id = "ah"
    label = "Allen & Heath dLive / SQ"
    vendor = "Allen & Heath"
    transport = "midi"
    note = "A&H 走 TCP MIDI 而非 OSC，除地址表外还需一层 MIDI 消息编码，接口已预留 transport 字段"
    counts = {"ch": 64, "bus": 32, "dca": 16, "auxin": 8, "usb": 2,
              "fxret": 8, "mtx": 16, "main": 1, "fx": 8}
    caps = ("fader", "mute", "dca", "scene")


CONSOLE_PROFILES = {p.id: p for p in (
    X32Profile(), M32Profile(), WingProfile(), YamahaProfile(), AHProfile())}


def profile():
    """当前生效的控台驱动（未知 id 回落到 X32）。"""
    return CONSOLE_PROFILES.get(CTRL_PROFILE_ID[0]) or CONSOLE_PROFILES["x32"]


def _fx_sync_on_console_change():
    """换控台后，把效果槽收敛到新控台的效果库里。

    为什么要做：算法名在新控台的库里可能不存在。留着它就成了"僵尸槽" ——
    界面上看起来装着混响，实际参数索引是旧控台的（写出去就是**参数串味**），
    或者根本写不出去。这里把它们明确置空并记日志，与"未标定控台拒绝写入"
    是同一条原则：**让降级看得见，而不是让它安静地错下去**。

    返回被清空的槽号列表。
    """
    # 延迟导入: profiles→state/fx 与 state→profiles 会成环, 只在函数体内打破
    # (合成单文件形态下该行被剥离, 同一命名空间直接解析)。
    from .fx import fx_algos
    from .state import STATE
    lib = fx_algos()
    cleared = []
    with STATE.lock:
        for s in range(1, FX_COUNT + 1):
            t = (STATE.fx[s].get("type") or "").upper()
            if t and t not in lib:
                STATE.fx[s]["type"] = ""
                STATE.fx[s]["par"] = {}
                cleared.append(s)
    if cleared:
        log("CONSOLE 效果槽同步: FX%s 的算法不在「%s」效果库中, 已置空" % (
            "/".join(str(x) for x in cleared), profile().label))
    return cleared


def set_profile(pid):
    """切换控台驱动。返回 (ok, 描述文本, 附加上下文 dict)。

    切完会立刻做一次**效果槽同步** —— 这正是"选了控台，混响页跟着变"的服务端一侧：
    效果库换了，槽位上不属于新库的算法要退场，否则页面显示与实际写入会对不上。
    """
    # 延迟导入: profiles→persist 与 persist→profiles 会成环 (见 _fx_sync_on_console_change)
    from .persist import save_user_config
    p = CONSOLE_PROFILES.get(str(pid))
    if p is None:
        return False, "未知控台: %s" % pid, {}
    CTRL_PROFILE_ID[0] = p.id
    cleared = _fx_sync_on_console_change()
    extra = {"fxCleared": cleared, "fxLib": len(p.fx_algos),
             "fxSlots": int(p.counts.get("fx", 0))}
    log("CONSOLE 切换到 %s%s（效果库 %d 种）" % (
        p.label, "" if p.ready else "（待标定：写入将被拒绝）", len(p.fx_algos)))
    save_user_config()
    return True, p.label, extra


def console_listing():
    """给 UI 用的控台清单（含能力与就绪状态）。"""
    return [{"id": p.id, "label": p.label, "vendor": p.vendor,
             "transport": p.transport, "ready": p.ready, "verified": p.verified,
             "note": p.note, "counts": dict(p.counts),
             "caps": list(p.caps), "pending": p.pending(),
             "fxLib": len(p.fx_algos),
             "fxLocked": (not p.ready) or ("fx" not in p.caps)}
            for p in CONSOLE_PROFILES.values()]


def addr_for_write(kind, idx):
    """(kind, idx) → 真台地址。桥接转发与状态回包共用同一入口，避免两处走样。

    返回 None 表示**当前控台不支持该 kind**（调用方须跳过并记日志）。
    """
    return profile().addr(kind, idx)
