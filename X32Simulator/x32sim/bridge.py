# -*- coding: utf-8 -*-
"""x32sim/bridge — 桥接真台: 转发合并 / 回读 / 回声抑制"""

import math
import os
import socket
import threading
import time

from .consts import FADER_ADDR, ON_ADDR
from .fxlib import algorithm_for_wing_mdl
from .kinds import fx_par_kind, fx_par_num, kind_group
from .logbus import EVENTS, EVT_SEQ, log
from .osc import OscMsg, decode_packet, encode_bundle, encode_message
from .params import PARAM_SPECS, param_from_wire, param_wire
from .profiles import addr_for_write, profile
from .state import FADE_LOCK, FADE_ZONE, INTERRUPTS, WriteSink, get_value, match_address, set_value, target_label
from .util import _truthy

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def _wire_arg(kind, value, p=None):
    """(kind, 本地值) → 线上值。返回 None = 当前控台不支持 / 值非法（调用方跳过）。

    抽成纯函数是为了可单测：这里每一条换算错了都是"写成功但声音错"，
    而它在真实链路里只在 20ms 合并器刷新时才跑到，靠抓包很难覆盖全。
    """
    if p is None:
        p = profile()
    sp = PARAM_SPECS.get(kind)
    if kind == "ch_dca":
        return int(value) & 0xFF
    if kind == "fx_type":
        # 内部算法 key → 线上枚举名。WING 是 `/fx/N/mdl` 且枚举名与 X32 不同；
        # 映射不到 = 这台控台没有这个算法 → 拒发并把名字说出来，
        # 否则槽位在界面上"装着"、真台其实没换。
        name = p.fx_wire_name(value)
        if name is None:
            log("BRIDGE 跳过 fx_type: 算法「%s」不在「%s」效果库中" % (value, p.label))
            return None
        return str(name)[:12]
    if kind == "fx_name":
        return str(value)[:12]
    if sp is not None:
        # 表驱动定线上类型 (与 state.value_reply 同一套规则, 都走 param_wire):
        #   float → 浮点 (推子/增益/延时/频率/门限); int → 整数 (低切斜率);
        #   bool  → 0/1 (静音/延时/低切/插入/门开关)
        #   index 型枚举 (dyn/ratio) → 0..11 的**索引**, 不是比值
        #   WING: 组级推子发 dB、组级静音发 1=muted (见 param_wire 的平台分支)
        # ⚠️ 曾经这里是 `endswith("_fader") → float, 其余 _truthy` —— 新增的
        #    通道参数全是非 _fader 结尾, 会被 _truthy 压成 0/1 发给真台
        #    (trim 3.5 dB 变成 1), 于是"桥接转发"这一层整个失真。
        arg = param_wire(sp, value, p)
        # 第二道闸: 万一将来哪条路径漏了 _finite, 宁可拒发也不把 NaN 发给真台
        if isinstance(arg, float) and not math.isfinite(arg):
            log("BRIDGE 拒发非有限值: %s = %r" % (kind, value))
            return None
        return arg
    if kind == "fx_mix" or fx_par_num(kind) is not None:
        arg = float(value)
        if kind == "fx_mix" and (p.fx_mix_scale or 1) != 1:
            # WING 的干湿比是 0..100 整数，不是 0..1
            arg = int(round(arg * p.fx_mix_scale))
        if not math.isfinite(arg):
            log("BRIDGE 拒发非有限值: %s = %r" % (kind, value))
            return None
        return arg
    return _truthy(value)


def _bridge_forward(kind, idx, value, source="manual", force=False):
    """桥接模式下把写入转成 OSC 发往真台, 并登记回声抑制记录。

    入参 value 必须是**已经过本地钳制/消毒的值**（调用方传 get_value 的结果）——
    真台收到什么, 就应该和本地台面看到的一致, 不能再出现"本地 1.0 / 真台 1.7"。
    source 决定合并限速档位 (手动灵敏 / 渐变放宽), 见 BRIDGE_FWD_MIN_GAP。
    force=True 用于"停止时同步冻结值": 绕过限速下一拍必出 (gap=0),
    保证卡片/回归中途停止后真台与本地台面一致, 不停在中间值上。
    """
    try:
        if value is None:
            return          # 本地没有这个目标（换控台后规模不匹配等）
        p = profile()
        arg = _wire_arg(kind, value, p)
        if arg is None:
            return
        addr = addr_for_write(kind, idx)      # ⚠️ Main 无 %02d, 必须走统一映射 (见该函数注释)
        if addr is None:
            # 当前控台不支持这个目标 → 明说, 不要静默吞掉 (静默是这类 bug 的老家)
            log("BRIDGE 跳过 %s %s: 当前控台「%s」未提供该地址%s"
                % (kind, idx, p.label, "（驱动待标定）" if not p.ready else ""))
            return
        sock = BRIDGE_SOCK[0]
        if sock is None:
            return
        # 渐变插值遇"已手动接管": 这条值是接管前算出来的, 发出去会把真台拽回
        # 旧位置, 回读回声还会把本地也覆盖回旧值 (用户白接管了)。入口就拦掉。
        # 显式动作 (set/mute/回归...) 不走这支 —— 它们的语义是"其余动作继续"。
        from_fade = (source == "fade")
        if from_fade and (kind, idx) in INTERRUPTS:
            return
        # 进合并队列 (同地址只保留最新值), 由 _bridge_fwd_flusher 按 Bundle 发出;
        # FWD_RECENT 也在实际发出时登记 —— 回声抑制要对得上真台真正收到的值。
        gap = 0.0 if force else BRIDGE_FWD_MIN_GAP.get(source, BRIDGE_FWD_GAP_DEFAULT)
        with BRIDGE_FWD["lock"]:
            BRIDGE_FWD["pending"][(kind, idx)] = (addr, arg, gap, from_fade)
    except Exception as e:
        log("BRIDGE 转发失败 %s: %r" % (kind, e))


def _bridge_force_sync(keys, skip_interrupted=False):
    """把一批 (kind, idx) 的**当前本地值**立即转发给真台 (绕过合并限速)。

    用于渐变被中途停止的场景: 本地冻结在当前值, 而最后一次插值可能还没
    转发 (或被合并器限速挡着) —— 不同步的话真台停在中间值, 与台面不一致。
    keys 是可迭代的 (kind, idx); 内部取快照, 调用方无需加锁。
    skip_interrupted: 跳过已被手动接管的通道 (卡片停止时用 —— 那些通道
    归手动管, 停止同步不该把它们拽回本地值)。
    """
    if BRIDGE["addr"] is None:
        return
    n = 0
    for kind, idx in list(keys):
        if skip_interrupted and (kind, idx) in INTERRUPTS:
            continue
        v = get_value(kind, idx)
        if v is None:
            continue
        _bridge_forward(kind, idx, v, source="action", force=True)
        n += 1
    if n:
        log("BRIDGE 停止同步: %d 个目标当前值已强制转发" % n)


def _bridge_fwd_flusher():
    """常驻合并器外壳: 任何异常写日志重启, 不静默死亡 (死亡=转发永远停)。"""
    while True:
        try:
            _bridge_fwd_flush_once()
        except Exception as e:
            log("BRIDGE 合并器异常 (已自动重启): %r" % e)
            time.sleep(0.5)


def _node_path(addr):
    """`/ch/1/fdr` → `/ch.1.fdr`（WING 节点批量写的路径写法：点分隔、无空段）。"""
    return "/" + ".".join([p for p in str(addr).split("/") if p])


def _node_value(v):
    """线上值 → 节点批量写里的文本。整数不带小数点，字符串原样。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "1" if v else "0"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f) and abs(f) < 1e15:
        return "%d" % int(f)
    return ("%.4f" % f).rstrip("0").rstrip(".")


def _send_node_batch(sock, batch, addr):
    """一条 OSC 写多路：`/ ,s "/ch.1.fdr=0,/ch.2.fdr=-10"`（WING 官方语法）。

    为什么默认不用：整包里只要有一条路径不被接受，WING 会整包回 `NODE NOT FOUND`，
    这一批写入就全丢了 —— 逐条发虽然包多，但一条失败不影响其余。所以这是
    **可选项**（环境变量 X32_WING_NODE_WRITE=1 / 配置项），且被拒一次就自动关掉，
    退回逐条发（见 recv 循环里的 /*,s 处理）。
    """
    parts = ["%s=%s" % (_node_path(_a), _node_value(v)) for _a, v, _g, _f in batch]
    payload = ",".join(parts)
    if len(payload) > 900:
        # 保守留余量：UDP 单包 32KB，但节点串越长越容易踩到台子的解析上限
        for _a, v, _g, _f in batch:
            sock.sendto(encode_message(OscMsg(_a, [v])), addr)
        return
    sock.sendto(encode_message(OscMsg("/", [payload])), addr)


def _send_msgs(sock, msgs, addr):
    """按控台能力发包。

    X32 吃 Bundle（一次发一批）；**WING 不吃**——官方文档写明其 OSC 包内容
    永远是单条 Message，Bundle 会被整包丢弃。发错的表现是最难查的一种：
    网页/卡片照常动、日志干净、真台纹丝不动。所以这里按 profile 分路。
    """
    if getattr(profile(), "osc_bundle", True):
        sock.sendto(encode_bundle(msgs), addr)
        return
    for m in msgs:
        sock.sendto(encode_message(m), addr)


def _bridge_fwd_flush_once():
    """一轮合并发送: 把到期的转发条目打包成一个 Bundle 发给真台。"""
    time.sleep(BRIDGE_FWD_TICK)
    sock = BRIDGE_SOCK[0]
    addr = BRIDGE["addr"]
    if sock is None or addr is None:
        with BRIDGE_FWD["lock"]:
            if BRIDGE_FWD["pending"]:
                BRIDGE_FWD["pending"].clear()
        return
    now = time.time()
    batch = []
    with BRIDGE_FWD["lock"]:
        pend = BRIDGE_FWD["pending"]
        overflow = len(pend) > BRIDGE_FWD_MAX_BATCH
        for key in list(pend.keys()):
            _a, _v, gap, from_fade = pend[key]
            if from_fade and key in INTERRUPTS:
                # 接管发生后积压的渐变插值: 过期, 直接丢弃 (真台不能收到它,
                # 否则会把用户刚抢回的手动值拽回旧位置, 回读回声还会覆盖本地)
                pend.pop(key)
                continue
            if overflow or now - BRIDGE_FWD["last"].get(key, 0.0) >= gap:
                batch.append((key, pend.pop(key)))
        for key, _av in batch:
            BRIDGE_FWD["last"][key] = now
    if not batch:
        return
    msgs = [OscMsg(a, [v]) for _k, (a, v, _g, _f) in batch]
    try:
        pairs = [av for _k, av in batch]
        use_nodes = (WING_NODE_WRITE[0] and not getattr(profile(), "osc_bundle", True)
                     and len(pairs) > 1
                     and all(not isinstance(av[1], str) for av in pairs))
        if use_nodes:
            _send_node_batch(sock, pairs, addr)
        else:
            _send_msgs(sock, msgs, addr)
    except OSError as e:
        log("BRIDGE 合并转发失败 (%d 条): %r" % (len(msgs), e))
        return
    with BRIDGE_LOCK:
        t = time.time()
        for key, (_a, v, _g, _f) in batch:
            FWD_RECENT[key] = [v, t]


def _ensure_fwd_flusher():
    """幂等启动合并器线程 (bridge_start 每次都会调, 不能起重复)。"""
    with BRIDGE_FWD["lock"]:
        t = BRIDGE_FWD_THREAD[0]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_bridge_fwd_flusher, daemon=True)
        t.start()
        BRIDGE_FWD_THREAD[0] = t


def _fwd_is_echo(key, value):
    """判断一条回读是不是自己刚转发造成的回声 (值相同 + 2 秒内)。"""
    with BRIDGE_LOCK:
        rec = FWD_RECENT.get(key)
    if not rec:
        return False
    if time.time() - rec[1] >= 2.0:
        return False
    a, b = rec[0], value
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    # 时间/预延迟这类原生单位参数跨度大, 绝对容差 0.02 不够用, 补一个 0.5% 相对容差
    return abs(fa - fb) <= max(0.02, abs(fb) * 0.005)


def _bridge_apply_readback(kind, idx, value):
    """真台回读: 回声(自己刚转发过的值)静默吸收; 台面上的手动变化按冲突策略处理。

    ⚠️ value 是**线上值**: dyn/ratio 这类 index 型枚举要换算成本地存的值再落库,
    否则真台第 6 档 (4:1) 会被存成 "6:1"。回声比对仍用线上值 (FWD_RECENT 记的
    也是线上值), 换算只在落库前一步做 —— 顺序别调, 调了回声就认不出来了。
    """
    key = (kind, idx)
    sp = PARAM_SPECS.get(kind)
    if kind == "fx_type" and profile().id == "wing":
        # WING 回读的是 mdl 枚举（大写，如 CHORUS），本库内部 key 是 CRS。
        # 反查不到 = 真台装的是 WING 独有算法（VSS3 / CONCERT / 吉他箱体…），
        # 本库没有对应项 —— 记一条日志保留不动，绝不猜成别的算法。
        _k = algorithm_for_wing_mdl(value)
        if _k is None:
            log("BRIDGE 真台 FX%s 装的是 WING 专有算法「%s」，本效果库无对应项" % (idx, value))
            return
        value = _k
    local = param_from_wire(sp, value, profile()) if sp is not None else value
    if _fwd_is_echo(key, value):
        set_value(kind, idx, local, source="sync")     # 自己写入的回声, 只对齐本地
        return
    # 真台端的变化: 渐变驱动中的通道 → 手动接管冲突; 其余静默同步
    with FADE_LOCK:
        zone = FADE_ZONE.get(key)
        if zone and key not in INTERRUPTS:
            INTERRUPTS.add(key)
            text = "⚠️ 真台上 %s 被手动干预 — 卡片「%s」停止驱动该通道, 其余动作继续" % (
                target_label(kind_group(kind), idx), zone)
            EVT_SEQ[0] += 1
            EVENTS.append({"id": EVT_SEQ[0], "time": time.strftime("%H:%M:%S"), "text": text})
            log("CONFLICT %s" % text)
    set_value(kind, idx, local, source="sync")


def _bridge_full_sync(sock):
    """全量状态回读: 向真台发一轮 GET (Bundle), 用回包填本地台面。

    地址一律经 addr_for_write 取, 当前控台不支持的目标直接跳过 —— 这样换控台后
    不会往真台发一堆它不认识的地址。
    """
    p = profile()
    counts = p.counts
    gets = []

    def _get(kind, idx):
        a = addr_for_write(kind, idx)
        if a:
            gets.append(OscMsg(a, []))

    for tmap in (FADER_ADDR, ON_ADDR):
        for k in tmap:
            grp = kind_group(k)
            ids = [0] if k.startswith("main_") else range(1, counts.get(grp, 0) + 1)
            for i in ids:
                _get(k, i)
    for i in range(1, counts.get("ch", 0) + 1):
        _get("ch_dca", i)
    # FX 机架: 类型 / 开关 / 混合比 / 名称, 以及**当前控台效果库**里用到的全部参数号。
    # ⚠️ 参数号不再写死 1/2/4 —— 不同控台的算法参数索引可能不同, 写死等于去查错位置。
    fx_pns = set()
    for a in p.fx_algos.values():
        for key in ("time", "predelay", "damp"):
            if isinstance(a.get(key), int):
                fx_pns.add(a[key])
    for s in range(1, counts.get("fx", 0) + 1):
        for k in ("fx_type", "fx_on", "fx_mix", "fx_name"):
            _get(k, s)
        for pn in sorted(fx_pns):
            _get(fx_par_kind(pn), s)
    for chunk in (gets[x:x + 64] for x in range(0, len(gets), 64)):
        try:
            _send_msgs(sock, chunk, BRIDGE["addr"])
        except OSError as e:
            log("BRIDGE 全量同步发送失败: %r" % e)
    log("BRIDGE 全量状态同步已请求 (%d 项, 控台 %s)" % (len(gets), p.label))


def fx_pull_from_console():
    """「从真台同步效果」：主动向桥接的真台重发一轮 FX 查询，把真台的效果槽拉回本地。

    与 _bridge_full_sync 的分工：那个是桥接建立时的**全量一次**，这个是**按需**
    只刷 FX —— 现场真正常用的场景是"我在台面上换了个效果器，让控制台跟着走"。

    返回 (ok, 描述 / 错误串)。
    """
    sock = BRIDGE_SOCK[0]
    if sock is None or BRIDGE.get("addr") is None:
        return False, "未桥接真台 —— 浏览器发不了 UDP，没有桥接就没有可同步的对象"
    p = profile()
    if not p.ready or "fx" not in p.caps:
        return False, "控台「%s」的效果槽未标定，无从同步" % p.label
    pns = set()
    for a in p.fx_algos.values():
        for key in ("time", "predelay", "damp"):
            if isinstance(a.get(key), int):
                pns.add(a[key])
    sent, failed = 0, 0
    for s in range(1, int(p.counts.get("fx", 0)) + 1):
        kinds = ["fx_type", "fx_on", "fx_mix", "fx_name"] + \
                [fx_par_kind(pn) for pn in sorted(pns)]
        for kind in kinds:
            addr = p.addr(kind, s)
            if addr is None:
                continue
            try:
                sock.sendto(encode_message(OscMsg(addr, [])), BRIDGE["addr"])
                sent += 1
            except OSError as e:
                failed += 1
                log("FX 同步查询发送失败 %s: %r" % (addr, e))
    log("FX 从真台同步: 已发出 %d 条查询 (%s · %d 个效果槽)%s" % (
        sent, p.label, p.counts.get("fx", 0),
        ("，失败 %d 条" % failed) if failed else ""))
    if not sent:
        return False, "没有可用的效果查询地址（控台「%s」地址表为空）" % p.label
    return True, "已向真台查询 %d 项效果状态，回包会自动同步到混响页" % sent


def bridge_client(ip, port):
    """桥接后台线程外壳: 任何异常写日志退出, 不静默死亡。"""
    try:
        _bridge_client_run(ip, port)
    except Exception as e:
        log("BRIDGE 桥接线程异常退出: %r" % e)
        BRIDGE_SOCK[0] = None
        if BRIDGE.get("addr") == (ip, port):
            BRIDGE["addr"] = None
            BRIDGE["synced"] = False


def _bridge_client_run(ip, port):
    """桥接后台线程: 按控台保活（X32 /xremote 5s；WING 订阅 9s），持续回读。"""
    _ensure_fwd_flusher()            # 转发合并器 (幂等; --bridge 启动参数也走这里)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    BRIDGE_SOCK[0] = sock
    BRIDGE["addr"] = (ip, port)
    BRIDGE["ip"], BRIDGE["port"], BRIDGE["synced"] = ip, port, False
    BRIDGE["started"] = time.time()
    BRIDGE["last_warn"] = 0.0
    p0 = profile()
    # 保活命令：X32 是 /xremote；WING 是订阅（/%<本地端口>/*S，10 秒不续即死）
    keep_cmd = str(getattr(p0, "keepalive_cmd", "/xremote") or "/xremote")
    if "{port}" in keep_cmd:
        # WING 要一个明确的回包端口。必须先 bind 再取端口 —— Windows 上对
        # 未 bind 的 UDP socket 调 getsockname() 会抛 OSError 10022(WSAEINVAL)，
        # 而这个异常发生在桥接线程最开头，表现就是"桥接启动成功、一个包都不发"。
        try:
            sock.bind(("0.0.0.0", 0))
        except OSError as e:
            log("BRIDGE 绑定本地回包端口失败: %r" % e)
        try:
            keep_cmd = keep_cmd.replace("{port}", str(sock.getsockname()[1]))
        except OSError:
            keep_cmd = keep_cmd.replace("{port}", "2223")
    keep_iv = float(getattr(p0, "keepalive_interval", 5.0) or 5.0)
    log("BRIDGE 桥接启动: 真台 %s:%d (写入转发 + %s 保活/订阅 + 状态回读)" % (
        ip, port, keep_cmd))
    if getattr(p0, "keepalive_note", ""):
        log("BRIDGE 注意: %s" % p0.keepalive_note)
    _bridge_full_sync(sock)
    BRIDGE["last_keep"] = 0.0        # 立即发首次保活完成订阅
    while BRIDGE["addr"] is not None:
        # 保活: X32 每 5 秒 /xremote；WING 每 9 秒续订阅（真台 10 秒无保活就断）
        if time.time() - BRIDGE["last_keep"] >= keep_iv:
            try:
                sock.sendto(encode_message(OscMsg(keep_cmd, [])), (ip, port))
                BRIDGE["last_keep"] = time.time()
            except OSError:
                pass
        # 真台长期无回包 → 每 30 秒提示一次 (排查 IP / 网线 / 是否同网段)
        ref = max(BRIDGE["last_recv"], BRIDGE.get("started", 0.0))
        if time.time() - ref > 20.0 and time.time() - BRIDGE["last_warn"] > 30.0:
            BRIDGE["last_warn"] = time.time()
            log("BRIDGE 真台无回包 (%s:%d) — 请检查地址、网线与是否在同一网段" % (ip, port))
        # 收真台回包/推送
        end = time.time() + 0.5
        while time.time() < end and BRIDGE["addr"] is not None:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                break
            except OSError:
                break
            BRIDGE["last_recv"] = time.time()
            if addr[0] != ip:
                continue
            try:
                msgs = decode_packet(data)
            except Exception:
                continue
            for msg in msgs:
                a = msg.address
                if a.startswith("/meters") or a == "/xinfo" or a.startswith("/xremote"):
                    continue
                if a == "/*":
                    # 节点批量写的回执：WING 只回 OK 或错误串（NODE NOT FOUND /
                    # VALUE ERROR / BUFFER OVERFLOW / NODE IS NOT PAR / INCOMPLETE DATA）
                    txt = str(msg.args[0]) if msg.args else ""
                    if txt and txt.upper() != "OK":
                        log("BRIDGE 批量写被拒: %s" % txt)
                        if WING_NODE_WRITE[0]:
                            WING_NODE_WRITE[0] = False
                            log("BRIDGE 已自动关闭 WING 节点批量写，退回逐条发送")
                    continue
                # WING 的地址空间与 X32 不通用，先让驱动自己认领；
                # 认不出再回落到 X32 的模板反查（M32 等仍走那条）。
                own = profile().parse_addr(a)
                matches = [own] if own else match_address(a)
                if not matches or not msg.args:
                    continue
                k, i = matches[0]
                raw = msg.args[0]
                if isinstance(raw, str) and raw:
                    try:
                        raw = float(raw)
                    except ValueError:
                        # 算法名（WING 的 mdl / X32 的 type）本来就是字符串，不能丢
                        if k not in ("fx_type", "fx_name"):
                            continue
                try:
                    _bridge_apply_readback(k, i, raw)
                except Exception as e:
                    log("BRIDGE 回读处理出错 %s: %r" % (a, e))
    BRIDGE_SOCK[0] = None
    BRIDGE["synced"] = False
    log("BRIDGE 桥接已断开 (%s:%d)" % (ip, port))


def bridge_start(addr_str):
    """启动桥接 (网页设置或 --bridge 参数): addr_str = 'IP:PORT'。"""
    try:
        ip, p = str(addr_str).strip().rsplit(":", 1)
        port = int(p)
    except (ValueError, AttributeError):
        return False, "地址格式应为 IP:端口"
    if not ip:
        return False, "地址格式应为 IP:端口"
    # 端口校验与 /api/ports 用同一个 valid_port，别处严、这里松
    if not (1 <= port <= 65535):
        return False, "端口必须是 1–65535"
    # 已有桥接先停
    bridge_stop()
    BRIDGE["addr"] = (ip, port)      # 先占位, 让线程启动前 set_value 也能拿到
    BRIDGE["ip"], BRIDGE["port"] = ip, port
    _ensure_fwd_flusher()            # 转发合并器 (常驻幂等)
    threading.Thread(target=bridge_client, args=(ip, port), daemon=True).start()
    return True, "桥接已启动 → %s:%d" % (ip, port)


def bridge_stop():
    if BRIDGE["addr"] is not None:
        ip, port = BRIDGE["addr"]
        BRIDGE["addr"] = None
        with BRIDGE_FWD["lock"]:
            BRIDGE_FWD["pending"].clear()   # 断开后不再有真台可发, 别留着旧值
        log("BRIDGE 停止桥接 (%s:%d)" % (ip, port))

# ---- 桥接模式: 网页/卡片写入转发到真台 (OSC), 真台状态回读同步到本地 ----
# 离线验证: 再开一个模拟器实例当"假真台", 本实例 --bridge 127.0.0.1:假台端口
BRIDGE = {"addr": None, "ip": "", "port": 0, "synced": False,
          "last_keep": 0.0, "last_recv": 0.0, "started": 0.0, "last_warn": 0.0}
BRIDGE_SOCK = [None]
BRIDGE_LOCK = threading.Lock()
# ⚠️ 可变容器: 网页断开/连接与配置加载都会改写它, main() 启动时要读最新值。
BRIDGE_TARGET = [""]   # 记住上次连过的真台地址, 下次启动自动重连 (B3 零配置的关键)
FWD_RECENT = {}      # (kind, idx) -> [value, time] 最近转发记录, 用于抑制回读回声

# ---- 桥接转发合并器 (治"写风暴") ----
# ⚠️ 曾经每条写入都单独 sendto: fade_all 时 80 个地址 × 20Hz ≈ 1000+ 个 UDP 包/秒,
#    真台 OSC 栈被刷满 (本机也忙)。现在高频写入先进合并队列 (同地址只留最新值),
#    由常驻线程每 20ms 按 **一个 Bundle** 发出:
#      - 同一地址按来源限速: 手动(用户拖推子) 50ms 保灵敏, 卡片/回归渐变 250ms
#       (真台 4Hz 跟随电动推子无感知, 而 fade_all 的包量直接降一个量级);
#      - 包数降一个量级 (真台每包只 recvfrom + 解析一次);
#      - **最后一个值一定送达**: pending 始终只留最新值, 渐变到点/被停止后,
#        一拍 (≤20ms + 间隔) 内一定发出, 不会卡在中间值上。
BRIDGE_FWD_MIN_GAP = {"manual": 0.05, "fade": 0.25, "action": 0.25, "dca": 0.1, "auto": 0.1}
BRIDGE_FWD_GAP_DEFAULT = 0.1
BRIDGE_FWD_TICK = 0.02       # 合并器轮询间隔
BRIDGE_FWD_MAX_BATCH = 256   # 单拍最多发出条数 (防极端堆积把 UDP 包撑爆)
BRIDGE_FWD = {"pending": {}, "last": {}, "lock": threading.Lock()}
BRIDGE_FWD_THREAD = [None]
# WING 节点批量写（`/ ,s "/ch.1.fdr=0,/ch.2.fdr=-10"`）—— **默认关**。
# WING 对整包做原子校验：一条路径不被接受就整批回 `NODE NOT FOUND`，这一拍
# 所有写入全丢；逐条发虽然包多，但一条失败不影响其余。需要时用环境变量
# X32_WING_NODE_WRITE=1 打开，且被台子拒一次会自动关回去（见 recv 里的 /*,s）。
WING_NODE_WRITE = [os.environ.get("X32_WING_NODE_WRITE", "").strip().lower()
                   not in ("", "0", "false", "off", "no")]


class _BridgeSink(WriteSink):
    """桥接写入转发 (作为写出口注册进 state.SINKS)。

    门槛条件 (原 set_value 尾部的 if) 收在这里: 桥接未连接或来源不在
    转发范围时直接返回。一律使用调用方传来的 after 值。
    """

    def on_write(self, kind, idx, value, source):
        if BRIDGE["addr"] is None or source not in ("manual", "action", "dca", "fade", "auto"):
            return
        _bridge_forward(kind, idx, value, source)


BRIDGE_SINK = _BridgeSink()
