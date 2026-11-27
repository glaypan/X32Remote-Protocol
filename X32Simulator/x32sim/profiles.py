
from .consts import FX_COUNT, M32_FX_ALGOS, X32_FX_ALGOS
from .fxlib import WING_FX_ALGOS, wing_mdl
from .kinds import fx_par_kind, fx_par_num
from .logbus import log
from .params import CH_PARAM_SPECS, PARAM_SPECS, SEND_PARAM_SPECS, spec_addr
from .wingfx import wing_par_name, wing_par_num
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
    param_limits = {}      # kind → (lo,hi): 控台实际接受的范围。空 = 用参数表量程。
                           # 目前只有 trim 会因固件版本不同而变 (见 app --trim-range)
    # --- 平台值转换开关（bridge / params 直接读字段，不再各自写 if id=="wing"）---
    fader_db = False       # True = 组级推子线上是 dB 浮点（WING 是 -144..+10）
    mute_inverted = False  # True = 组级静音线上 1=muted（X32 是 /mix/on 1=开）
    fx_mix_scale = 1       # 干湿比倍数（WING 是 0..100 整数）
    index_fmt = "%02d"     # 地址编号格式（WING 不补零，用 "%d"）
    fx_slots = FX_COUNT    # 协议侧效果槽数（本地仍受 FX_COUNT 限制）
    osc_bundle = True      # False = 真台不接受 Bundle，转发/回读要逐条发（WING）
    # 遥控会话保活：X32 是 /xremote 每 5 s；WING 是订阅命令（10 s 不续即死）。
    # {port} 会替换成本地 socket 端口 —— WING 支持 /%<port> 指定回包端口。
    keepalive_cmd = "/xremote"
    keepalive_interval = 5.0
    keepalive_note = ""

    def addr(self, kind, idx):
        """(kind, idx) → 真实地址；本控台不支持则返回 None。"""
        return None

    def fx_wire_name(self, algo):
        """内部算法名 → 线上枚举名。X32 直接发算法 key，WING 要换成 mdl。"""
        return str(algo)

    def parse_addr(self, addr):
        """真台回包地址 → (kind, idx)。返回 None = 不归本驱动管。

        X32 走通用的 match_address（地址模板反查）；WING 的地址空间与 X32 不同
        （`/ch/1/fdr` 而不是 `/ch/01/mix/fader`），必须自己解析，否则回读全部丢失
        —— 症状是"台面上动了推子，软件纹丝不动"，而日志只有一行"未识别"。
        """
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
        # 表驱动: 推子/静音/12 个通道参数的地址全在 params.PARAM_SPECS 里
        # (单一事实来源; 曾经这里是字面分支, 与 consts 的字典各存一份)
        sp = PARAM_SPECS.get(kind)
        if sp is not None:
            return spec_addr(sp, idx)
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
        # 表里没有、FX 也不是 → 本控台不支持这个 kind
        return None


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
        return list(_ALL_KINDS)


def _all_kinds():
    """语义管道里的全部 kind（卡片目标 / 预设范围 / 校验的并集）。"""
    base = ["ch_fader", "ch_on", "bus_fader", "bus_on", "dca_fader",
            "dca_on", "main_fader", "main_on"]
    base += [g + "_fader" for g in ("auxin", "usb", "fxret", "mtx")]
    base += [g + "_on" for g in ("auxin", "usb", "fxret", "mtx")]
    base += ["ch_dca", "fx_type", "fx_on", "fx_mix", "fx_name", "fx_par_NN"]
    # 通道参数 (增益/反相/延时/低切/插入/门/动态/EQ): 从登记表推导, 新增即跟上
    base += [sp.kind for sp in CH_PARAM_SPECS]
    # 发送 (通道→Bus): 二维地址, 同样从登记表推导
    base += [sp.kind for sp in SEND_PARAM_SPECS]
    return base


_ALL_KINDS = _all_kinds()


# ---------------------------------------------------------------- WING
#
# 地址与语义全部来自《WING Remote Protocols V3.1》(Behringer 授权,
# Patrick-Gilles Maillot 维护)。与 X32 的三处硬差异必须在 addr() 与值转换层
# 同时体现，任何一处漏掉都是"写成功但声音错"：
#   1. 编号**不补零**: `/ch/1/fdr`（X32 是 `/ch/01/mix/fader`）
#   2. 推子是 **dB 浮点**: -144 = -∞，上限 +10（X32 是 0..1 归一值）
#   3. 静音 **1 = muted**（X32 是 `/mix/on` 1 = 开）
# 另外 main 在 WING 是 4 路且带编号（本地只有 1 路、idx 恒 0 → 地址用 idx+1）。

# 内部组名 → WING 地址里的组名（fxret/usb 在 WING 没有对应组，故意缺席）
_WING_GROUP = {"ch": "ch", "auxin": "aux", "bus": "bus",
               "main": "main", "mtx": "mtx", "dca": "dca"}
# 内部 field → WING 叶子名
_WING_TAIL = {"fader": "fdr", "on": "mute", "pan": "pan"}
# 地址编号偏移（本地 main 的 idx 是 0，WING 的 main 从 1 起）
_WING_IDX_BASE = {"main": 1}
# 反查表（回读用）：地址里的组名/叶子名 → 内部组/field
_WING_GROUP_REV = {v: k for k, v in _WING_GROUP.items()}
_WING_TAIL_REV = {v: k for k, v in _WING_TAIL.items()}


def _slot_algo(slot):
    """槽位当前算法（延迟导入 STATE：profiles→state 会成环，只在调用时取）。
    取不到（未初始化/越界）一律当空槽 —— 空槽没有参数可写。"""
    try:
        from .state import STATE
        return (STATE.fx.get(int(slot), {}) or {}).get("type") or ""
    except Exception:
        return ""


class WingProfile(ConsoleProfile):
    """WING —— 按《WING Remote Protocols V3.1》实装，尚未上真台核对（verified=False）。

    已标定（有文档/实测证据，可直接写）：推子(dB) / 静音(反向) / 声像 /
    算法选择(`/fx/N/mdl`) / 干湿比(`/fx/N/fxmix`) /
    **FX 命名参数**(`/fx/1/time`、`/fx/2/pdel|dcy`，见 wingfx.py 的证据分级) /
    订阅回读(`/%<port>/*S`) / 节点批量写(`/ ,s "/ch.1.fdr=0,..."`，默认关)。

    仍未标定的部分**显式缺席**而不是猜地址：
      · 通道处理参数 (EQ/门/动态/发送) —— WING 的树结构与 X32 完全不同；
      · 场景召回 —— `$stat`/`show` 子树尚未核实；
      · FX 返回组 —— WING 没有 fxrtn 组（X32 的 8 条返回在 WING 里是普通通道）。
    这些在 UI 上灰显（caps 不含对应能力 / `w=false` 灰掉单个滑杆），
    写入时记一条明确日志后跳过。
    """

    id = "wing"
    label = "WING"
    vendor = "Behringer"
    ready = True
    verified = False
    note = ("地址表来自《WING Remote Protocols V3.1》：编号不补零、推子为 dB、"
            "静音 1=静音（与 X32 相反）、不支持 Bundle。FX 命名参数仅标定有证据的部分，"
            "其余滑杆在前端灰显；通道处理参数待标定")
    # counts = **本机能实际控制**的规模（受本地台面建模限制），不是协议规模。
    # 协议规模见 proto_counts —— 写 counts 用 40 会造成"卡片能选 ch40、实际无声"。
    counts = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 0,
              "fxret": 0, "mtx": 6, "main": 1, "fx": FX_COUNT}
    proto_counts = {"ch": 40, "aux": 8, "bus": 16, "main": 4, "mtx": 8,
                    "dca": 16, "fx": 16}
    caps = ("fader", "mute", "dca", "fx", "fx_par", "meter", "raw_osc")
    fx_algos = WING_FX_ALGOS
    # 值转换层的开关（bridge/params 读这三个，不再各自写 if profile==...）
    fader_db = True          # 推子线上是 dB，不是 0..1
    mute_inverted = True     # 静音线上 1 = muted
    fx_mix_scale = 100       # 干湿比 0..100，不是 0..1
    index_fmt = "%d"         # 地址编号不补零
    fx_slots = 16
    # WING 的 OSC 包内容**永远是单条 Message**（官方文档明说 Bundles are not
    # supported）——整包 Bundle 发过去会被丢弃，症状恰好是"界面全动、台子不动"。
    osc_bundle = False
    # 订阅: `/%<port>/*S`，10 秒不续即死（官方文档）。**全台只允许 1 个订阅**。
    keepalive_cmd = "/%{port}/*S"
    keepalive_interval = 9.0
    keepalive_note = ("WING 全台只允许 1 个订阅；与 Co-Pilot / Mixing Station "
                      "同时使用会互相抢（日志里会看到订阅无回包）")

    def addr(self, kind, idx):
        if kind == "fx_type":
            return "/fx/%d/mdl" % idx
        if kind == "fx_mix":
            return "/fx/%d/fxmix" % idx
        if fx_par_num(kind) is not None:
            # WING 的参数是**名字**而不是 par 号：`/fx/1/time`、`/fx/2/pdel`。
            # 名字由 (该槽当前算法, par 号→field) 查表得来；查不到 = 无证据支持，
            # 返回 None 拒发 —— 绝不发 `/fx/1/par/02` 这种 X32 地址。
            name = wing_par_name(_slot_algo(idx), fx_par_num(kind))
            if not name:
                return None
            return "/fx/%d/%s" % (idx, name)
        grp, _, field = kind.rpartition("_")
        tail = _WING_TAIL.get(field)
        g = _WING_GROUP.get(grp)
        if tail is None or g is None:
            return None
        n = int(idx) + _WING_IDX_BASE.get(grp, 0)
        if n < 1:
            return None
        return "/%s/%d/%s" % (g, n, tail)

    def fx_wire_name(self, algo):
        """内部算法 key → WING 的 mdl 枚举；该算法 WING 没有则 None（拒发）。"""
        return wing_mdl(str(algo or "").upper())

    def parse_addr(self, addr):
        s = str(addr or "").strip("/")
        parts = s.split("/")
        if len(parts) != 3:
            return None
        head, num, tail = parts
        try:
            n = int(num)
        except ValueError:
            return None
        if head == "fx":
            if n < 1:
                return None
            if tail == "mdl":
                return ("fx_type", n)
            if tail == "fxmix":
                return ("fx_mix", n)
            pnum = wing_par_num(_slot_algo(n), tail)
            return (fx_par_kind(pnum), n) if pnum else None
        g = _WING_GROUP_REV.get(head)
        f = _WING_TAIL_REV.get(tail)
        if g and f:
            return (g + "_" + f, n - _WING_IDX_BASE.get(g, 0))
        return None

    def pending(self):
        """尚未标定的 kind（供界面提示与规范使用）。"""
        return [k for k in _ALL_KINDS if self.addr(k, 0) is None]


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
