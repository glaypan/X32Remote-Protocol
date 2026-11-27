"""WING 通道处理参数表 —— 只登记「两边语义真正等价」的参数。

来源：《WING Remote Protocols》（Patrick-Gilles Maillot 维护，Behringer 授权），
`### Channel Settings` / `### Aux Settings` / `### Bus Settings` /
`### Mains Settings` / `### Matrix Settings` 五张地址表。
解析脚本见 `_docs/parse_wing_doc.py`（去掉脚注引用后地址才是 `/dyn/thr`，
带脚注序号会读成 `/dyn/thr6` —— 发出去真台回 NODE NOT FOUND）。

⚠️ 这不是 X32 改个名。五处**结构**不同，逐条写在下面的注释里：

  1. EQ 段数：X32 ch 4 段 / bus·mtx·main 6 段；WING ch 也是 4 段、bus·mtx·main 6 段
     —— 段数正好对得上。但 WING 的 4 个中间段**没有类型**（固定 PEQ），
     而 X32 每段都能选 {LCut,LShv,PEQ,VEQ,HShv,HCut}。所以 `*_eqN_type` 一律不登记。
     WING 另有 low shelf（`/eq/lg|lf|lq`）与 high shelf（`/eq/hg|hf|hq`），
     与 X32 的"首末段兼作低切/高切"不是一回事 —— 不硬凑，留给以后单独做。
  2. Aux In：**WING 的 Aux 没有 EQ**（`/aux/N/eq/*` 整棵不存在）。X32 auxin 有 4 段。
     所以 auxin 的 20 个参数在 WING 全部不登记 = 界面不显示 = 不会误发。
  3. 低切：X32 在 `/ch/NN/preamp/hpf|hpon|hpslope`，WING 在 `/ch/N/flt/lc|lcf|lcs`
     （还多一个高切 `hc|hcf|hcs`，X32 侧没有对应 kind，不做）。
  4. 门：WING 的 `gate/mdl` 是 **35 种模型**（GATE/DUCK/E88/9000G/… 与动态共用一张表），
     X32 的 `gate/mode` 是 int 0..1。值域完全不同 → 不登记。
     侧链同理：WING 是 `gatesc/{type,f,q,src,tap}` 节点，X32 是 `gate/{keysrc,filter_*}`。
  5. 动态：WING 的 `dyn/mdl` 也是 35 种模型，X32 是 COMP/EXP 两档 → 不登记。
     `dyn/pos` 在 WING 是 `/ch/N/proc`（整个处理链顺序 "GEDI"/"GEID"/…），语义不同。
     侧链 `dynsc/*` 同理不登记。
     但**连续参数**（thr/knee/gain/att/hld/rel/mix/auto/det/env/ratio）语义一致，登记。

数值差异（不是结构差异，靠钳制与换算解决）：
  · WING `dyn/thr` 是 -60..0，X32 是 -80..0          → 钳到 -60
  · WING `dyn/hld` 是 1..200，X32 是 0.02..2000      → 钳到 1..200
  · WING `dyn/rel` 是 4..4000，X32 是 5..4000        → 钳到 4
  · WING `dyn/gain` 是 -6..12，X32 是 0..24          → 钳到 -6..12
  · WING `eq/N/q` 是 0.44..10，X32 是 0.3..10        → 钳到 0.44
  · WING `gate/hld` 是 0..200，X32 是 0.02..2000     → 钳到 0..200
  · WING `gate/rel` 是 4..4000，X32 是 5..4000       → 钳到 4
  · WING `flt/lcs` 是 {6,12,18,24}，X32 只有 {12,18,24} → 原样（X32 值域是子集）

三种**线上形态**与本地不同，必须转换（不做就是"能写但写坏"）：
  · 发送电平：本地 0..1（X32 的 161 档 level），WING 线上是 **dB**（-144=静音，上限 +10）
  · 压缩比：本地存比值，WING **ch** 要发字符串枚举（"4.0"），
    bus/main/mtx 要发数值（1.1..100）。发错类型真台静默拒绝。
  · 发送取点：X32 5 档 {<-EQ,EQ->,PRE,POST,GRP}，WING 3 档 {PRE,POST,GRP}。
    X32 的前两档在 WING 上不存在 → 都落到 PRE（最近语义，且是 EQ 之前）。
  · 检波/包络：X32 逻辑上发索引，WING 要发 "PEAK"/"RMS"、"LIN"/"LOG" 字符串。
  · 低切斜率：WING 是 S 类型，必须发字符串 "12" 而不是整数 12。
"""

# 组名映射：内部组名 → WING 地址段。main 在 WING 是 /main/1（有路号，只有 1 路）。
WING_GROUP = {"ch": "ch", "auxin": "aux", "bus": "bus",
              "main": "main", "mtx": "mtx", "dca": "dca"}

# WING 的路号从 1 起（不补零）；main 固定 1
WING_IDX_BASE = {"main": 1}

# EQ 段数：WING 与 X32 相同（aux 例外 —— WING 的 aux 没有 EQ）
WING_EQ_BANDS = {"ch": 4, "bus": 6, "mtx": 6, "main": 6}


def _addr_map():
    """kind → WING 地址模板。模板里的 %d 是路号，发送是两个 %d（源、目标）。

    只登记**地址与语义都对得上**的项；没登记的 kind 在 WING 上 addr() 返 None。
    """
    m = {}

    # ---- EQ（on + 每段的 f/g/q；type 不登记）----
    for g, bands in WING_EQ_BANDS.items():
        wg = WING_GROUP[g]
        m["%s_eq_on" % g] = "/%s/%%d/eq/on" % wg
        for b in range(1, bands + 1):
            m["%s_eq%d_f" % (g, b)] = "/%s/%%d/eq/%df" % (wg, b)
            m["%s_eq%d_g" % (g, b)] = "/%s/%%d/eq/%dg" % (wg, b)
            m["%s_eq%d_q" % (g, b)] = "/%s/%%d/eq/%dq" % (wg, b)

    # ---- 输入段（X32 的 preamp/trim|invert → WING 的 in/set/trim|inv）----
    # 值域两边都是 -18..18 dB / 0..1，直接用。
    # ⚠️ WING 的 in/set/$g 是**话放模拟增益**（-2.5..45，随输入源变化），
    #    与 X32 的 /headamp/NNN/gain 不是一条链 —— 不映射，避免"动了增益台面没反应"。
    m["ch_trim"] = "/ch/%d/in/set/trim"
    m["ch_invert"] = "/ch/%d/in/set/inv"
    m["auxin_trim"] = "/aux/%d/in/set/trim"
    m["auxin_invert"] = "/aux/%d/in/set/inv"

    # ---- 低切（X32 的 preamp/hpf → WING 的 flt/lc）----
    m["ch_hpf_on"] = "/ch/%d/flt/lc"
    m["ch_hpf_f"] = "/ch/%d/flt/lcf"
    m["ch_hpf_slope"] = "/ch/%d/flt/lcs"

    # ---- 噪声门（只有 ch；WING 的 aux 有门但**没有 on/mdl**，开关无从控制）----
    for f, tail in (("on", "on"), ("thr", "thr"), ("range", "range"),
                    ("attack", "att"), ("hold", "hld"), ("release", "rel")):
        m["ch_gate_%s" % f] = "/ch/%%d/gate/%s" % tail

    # ---- 动态（五组的连续参数；mode/pos/keysrc/filter_* 不登记）----
    # ⚠️ 只登记 X32 侧**确实存在对应 kind** 的项。kind 是全局语义管道，
    #    没有 ParamSpec 就没有界面、没有写入入口 —— 登记了只是幽灵条目。
    #    · X32 的 auxin 没有动态参数（真台的 /auxin/NN/dyn/* 也不存在）
    #    · X32 的 main 没有 mix/auto（params._DYN_SKIP），真台有但内部表达不了
    _dyn_skip = {"auxin": {"on", "thr", "knee", "mgain", "attack", "hold",
                           "release", "mix", "auto", "det", "env", "ratio"},
                 "main": {"mix", "auto"}}
    for g in ("ch", "auxin", "bus", "mtx", "main"):
        wg = WING_GROUP[g]
        for f, tail in (("on", "on"), ("thr", "thr"), ("knee", "knee"),
                        ("mgain", "gain"), ("attack", "att"), ("hold", "hld"),
                        ("release", "rel"), ("mix", "mix"), ("auto", "auto"),
                        ("det", "det"), ("env", "env"), ("ratio", "ratio")):
            if f in _dyn_skip.get(g, ()):
                continue
            m["%s_dyn_%s" % (g, f)] = "/%s/%%d/dyn/%s" % (wg, tail)

    # ---- 发送（X32 的 /mix/NN/level → WING 的 /send/K/lvl）----
    # ch/auxin 送 Bus 1-16；bus/main 只能送 Matrix（WING 写作 /send/MX<x>）；
    # mtx 在 WING 里没有发送子树，X32 的 mtx 也没有 —— 一致。
    # ⚠️ 取点（mode）只有 ch/auxin 登记：X32 的 bus/main 发送本来就没有 tap 参数
    #    （params._send_specs 里 has_tap=False），登记了就是**幽灵 kind** ——
    #    PARAM_SPECS 里查不到、界面不显示、写入无门，但会污染"地址不撞号"检查。
    for f, tail in (("on", "on"), ("level", "lvl")):
        m["send_%s" % f] = "/ch/%%d/send/%%d/%s" % tail
        m["auxin_send_%s" % f] = "/aux/%%d/send/%%d/%s" % tail
        m["bus_send_%s" % f] = "/bus/%%d/send/MX%%d/%s" % tail
        m["main_send_%s" % f] = "/main/%%d/send/MX%%d/%s" % tail
    m["send_tap"] = "/ch/%d/send/%d/mode"
    m["auxin_send_tap"] = "/aux/%d/send/%d/mode"

    return m


WING_PARAM_ADDR = _addr_map()

# 地址反查：模板 → kind（回读用）。发送的两个 %d 与普通的一个 %d 分开处理。
WING_ADDR_REV = {}


def _build_rev():
    """模板 → (kind, 路号是否为 1) —— 供 parse_addr 反查。

    只处理 %d 个位数的模板（WING 不补零）。发送模板的两个 %d 在 parse_addr
    里单独拆，不进这张表。
    """
    for kind, tpl in WING_PARAM_ADDR.items():
        if tpl.count("%") != 1:
            continue
        WING_ADDR_REV[tpl] = kind


_build_rev()

# ---- 值域钳制 -----------------------------------------------------------
# 只列**与 X32 不同**的项。写进真台前必须钳住：超范围的 OSC 参数真台要么
# 静默截断（界面显示 A、台面是 B），要么直接回 NODE NOT FOUND。
# ⚠️ 与 _addr_map 一样，只给**登记过的** kind 加限制：auxin 没有动态参数表，
#    给它加限制只会留下四个指向不存在参数的键。
WING_PARAM_LIMITS = {}
for _g in ("ch", "bus", "mtx", "main"):
    WING_PARAM_LIMITS["%s_dyn_thr" % _g] = (-60.0, 0.0)
    WING_PARAM_LIMITS["%s_dyn_hold" % _g] = (1.0, 200.0)
    WING_PARAM_LIMITS["%s_dyn_release" % _g] = (4.0, 4000.0)
    WING_PARAM_LIMITS["%s_dyn_mgain" % _g] = (-6.0, 12.0)
for _g, _b in WING_EQ_BANDS.items():
    for _i in range(1, _b + 1):
        WING_PARAM_LIMITS["%s_eq%d_q" % (_g, _i)] = (0.44, 10.0)
WING_PARAM_LIMITS["ch_gate_hold"] = (0.0, 200.0)
WING_PARAM_LIMITS["ch_gate_release"] = (4.0, 4000.0)


# ---- 档位吸附 -----------------------------------------------------------
# 本地（X32）与 WING 的档位表不同时，本地值可能"在 WING 上不存在"。
# ch 压缩比：本地 12 档 {1.1,1.3,1.5,2,2.5,3,4,5,**7**,10,20,100}，
# WING 17 档 {1.1,1.2,1.3,1.5,1.7,2,2.5,3,3.5,4,5,**6**,8,10,20,50,100}。
# 交集 11 档 —— 本地的 7.0 没有对应，最近的 6.0/8.0 又不在本地表里。
# 写入前吸附到交集，避免"界面 7.0、台面 5.0"。
# ⚠️ 这张表必须等于 params 里 ch_dyn_ratio enum 与 WING_RATIO 的交集，
#    回归 test_wing_profile 会校验（改了 enum 忘改这里就红）。
WING_VALUE_SNAP = {
    "ch_dyn_ratio": (1.1, 1.3, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0, 100.0),
}


# ---- 线上值转换 ---------------------------------------------------------
# 转换器名字 → 实现在 params.py（那边有 level_db / fader_to_db 等曲线函数）。
# 这里只给 kind → 转换器名，不重复实现任何数值逻辑。

WING_WIRE_CONV = {}

# 发送电平：本地 0..1 → dB。WING 的 -∞ 是 **-144**，不是 X32 的 -90。
for _k in ("send_level", "auxin_send_level", "bus_send_level", "main_send_level"):
    WING_WIRE_CONV[_k] = "send_db"

# 压缩比：ch 发字符串枚举，其余发数值
WING_WIRE_CONV["ch_dyn_ratio"] = "ratio_str"
for _g in ("bus", "mtx", "main"):
    WING_WIRE_CONV["%s_dyn_ratio" % _g] = "ratio_num"

# 检波 / 包络：WING 收字符串（aux 没有这两个参数，见 _addr_map 的跳过表）
for _g in ("ch", "bus", "mtx", "main"):
    WING_WIRE_CONV["%s_dyn_det" % _g] = "label_str"
    WING_WIRE_CONV["%s_dyn_env" % _g] = "label_str"

# 低切斜率：WING 是 S 类型，必须发字符串 "12"
WING_WIRE_CONV["ch_hpf_slope"] = "num_str"

# 发送取点：5 档 → 3 档（<-EQ / EQ-> 落到 PRE）
WING_WIRE_CONV["send_tap"] = "tap3"
WING_WIRE_CONV["auxin_send_tap"] = "tap3"

# ch 压缩比：本地比值 → WING 的字符串枚举。7.0 在 WING 上没有（它的表是
# 1.1,1.2,1.3,1.5,1.7,2,2.5,3,3.5,4,5,6,8,10,20,50,100）→ 吸附到最近的 6.0。
WING_RATIO = (1.1, 1.2, 1.3, 1.5, 1.7, 2.0, 2.5, 3.0, 3.5, 4.0,
              5.0, 6.0, 8.0, 10.0, 20.0, 50.0, 100.0)


def _fmt_ratio(v):
    """WING 的 ratio 枚举写法。协议 Text 列原文：
    `1.1, 1.2, 1.3, 1.5, 1.7, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0, 10, 20, 50, 100`
    —— 10 以下**保留一位小数**（"2.0" 而不是 "2"），10 及以上是整数。
    写成 "2" 真台不认（枚举是字符串精确匹配），表现为"设了压缩比、台面没动"。
    """
    return ("%.1f" % v) if v < 10 else "%d" % int(round(v))


def nearest_ratio(v, allowed=None):
    """本地比值 → WING enumerator 里最接近的比值（字符串形式）。

    本地（X32）只有 12 档 {1.1,1.3,1.5,2,2.5,3,4,5,7,10,20,100}，WING 有 17 档。
    必须在**两边的交集**里选，否则会漂移：本地 7.0 → WING 6.0 → 回读时
    X32 的表里没有 6.0 → 吸附到 5.0，于是"拖到 7 松手变 5"。

    `allowed` 传本地枚举（ParamSpec.enum），None 时退化为只看 WING 表。
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 4.0
    pool = list(WING_RATIO)
    if allowed:
        common = [x for x in pool if any(abs(x - float(a)) < 1e-6 for a in allowed)]
        if common:
            pool = common
    if not any(abs(v - e) < 1e-6 for e in pool):
        v = min(pool, key=lambda e: (abs(e - v), e))
    return _fmt_ratio(v)


# 发送取点：X32 {<-EQ,EQ->,PRE,POST,GRP} → WING {PRE,POST,GRP}
_TAP3 = ("PRE", "PRE", "PRE", "POST", "GRP")


def tap3(idx):
    """X32 取点索引 → WING 三档名。越界落到 POST（X32 的默认）。"""
    try:
        i = int(round(float(idx)))
    except (TypeError, ValueError):
        i = 3
    return _TAP3[i] if 0 <= i < len(_TAP3) else "POST"


def tap3_index(label):
    """WING 的三档名 → X32 的索引（PRE→2, POST→3, GRP→4）。"""
    s = str(label or "").strip().upper()
    return {"PRE": 2, "POST": 3, "GRP": 4}.get(s, 3)
