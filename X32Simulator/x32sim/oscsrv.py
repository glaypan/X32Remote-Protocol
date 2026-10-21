# -*- coding: utf-8 -*-
"""x32sim/oscsrv — OSC 消息分发 (UDP 主循环的每个包)"""

import struct
import time

from .consts import KIND_LABEL, NAME_COUNTS
from .kinds import fx_par_num, kind_group
from .logbus import log
from .osc import OscMsg, encode_bundle, encode_message
from .params import PARAM_SPECS, param_fmt, param_from_wire, send_unpack
from .state import METER_LOCK, METER_SUBSCRIBERS, STATE, compute_levels, get_name, get_value, match_address, set_name, set_value, value_reply
from .version import SIM_FIRMWARE, SIM_MODEL, SIM_NAME

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def handle(msg: OscMsg, addr, sock):
    a = msg.address
    log("%-28s <- %s  args=%s" % (addr[0], a, msg.args))

    if a == "/xinfo":
        ip = sock.getsockname()[0]
        reply = OscMsg("/xinfo", [ip, SIM_NAME, SIM_MODEL, SIM_FIRMWARE])
        sock.sendto(encode_message(reply), addr)
        return

    if a == "/subscribe":
        on = bool(msg.args and msg.args[0] != 0)
        with METER_LOCK:
            if on:
                METER_SUBSCRIBERS[addr] = time.time()
            else:
                METER_SUBSCRIBERS.pop(addr, None)
        log("    台面变更推送订阅: %s (来源 %s, 共 %d 个)" %
            ("开启" if on else "关闭", addr, len(METER_SUBSCRIBERS)))
        return

    if a == "/xremote":
        # 真台保活: 续期该来源的订阅 (30 秒无续期自动清理)
        with METER_LOCK:
            METER_SUBSCRIBERS[addr] = time.time()
        return

    if a.startswith("/meters"):
        if msg.args and msg.args[0] == 0:
            with METER_LOCK:
                METER_SUBSCRIBERS.pop(addr, None)
            log("    电平表退订: %s" % (addr,))
        else:
            with METER_LOCK:
                METER_SUBSCRIBERS[addr] = time.time()
            log("    电平表订阅: %s (共 %d 个订阅者)" % (addr, len(METER_SUBSCRIBERS)))
            # 对齐真机: 每次请求立即回一帧
            floats = compute_levels()
            blob = struct.pack(">i", len(floats))
            for v in floats:
                blob += struct.pack(">f", v)
            sock.sendto(encode_message(OscMsg("/meters/1", [blob])), addr)
        return

    if a.startswith("/scene/") and a.endswith("/load"):
        try:
            n = int(a.split("/")[2])
        except (ValueError, IndexError):
            return
        with STATE.lock:
            STATE.scene = n
        log("    场景切换 -> %d" % n)
        return

    if a == "/reboot" or a == "/quit":
        log("    收到重启指令,忽略")

    # 通道/输出名称读写: /ch/01/config/name (字符串参数, 真台同款地址)
    segs = a.strip("/").split("/")
    if len(segs) == 4 and segs[2] == "config" and segs[3] == "name":
        g = segs[0]
        if g == "main" and segs[1] == "st":
            idx = 0
        else:
            try:
                idx = int(segs[1].rstrip("LR"))
            except ValueError:
                log("    (名称地址未识别: %s)" % a)
                return
        cnt = NAME_COUNTS.get(g)
        if g == "main":
            pass
        elif not cnt or not 1 <= idx <= cnt:
            log("    (名称地址未识别: %s)" % a)
            return
        if msg.args and isinstance(msg.args[0], str):
            set_name(g, idx, msg.args[0])
        else:
            sock.sendto(encode_message(OscMsg(a, [get_name(g, idx)])), addr)
        return

    matches = match_address(a)
    if not matches:
        log("    (未识别,忽略)")
        return

    # set: 带参数; get: 无参数
    if msg.args:
        kind, idx = matches[0]
        raw = msg.args[0]
        if isinstance(raw, str):
            try:
                raw = float(raw)      # X32 有时用字符串传小数
            except ValueError:
                pass                  # FX 类型/名称本来就是字符串("/fx/1/type HALL"), 原样往下传
        sp0 = PARAM_SPECS.get(kind)
        if sp0 is not None:
            # 入线换算 (与回包/转发用的 param_wire 成对): dyn/ratio 线上是
            # 0..11 的索引, 直接落库会把"第 6 档(4:1)"存成"6:1"。只在入线处做一次。
            raw = param_from_wire(sp0, raw)
        set_value(kind, idx, raw)
        label = KIND_LABEL.get(kind_group(kind), kind)
        sp = PARAM_SPECS.get(kind)
        if sp is not None and not sp.push:
            # 新增通道参数 (增益/反相/延时/低切/插入/门/动态/EQ): 标签与单位取自登记表。
            # 打的是**落库后**的值 (钳制/量化之后) 而不是原始入参 —— 曾经的
            # 通用分支会把 trim 打成 "fader=3.5 (11.1 dB)", 把 hpf_on 打成
            # "mute=开", 日志整个失去排查价值。
            where = ("%02d→Bus%02d" % send_unpack(idx)) if sp.group == "send" \
                else ("%02d" % idx)
            log("    SET %s %s %s=%s" % (label, where, sp.label,
                                         param_fmt(sp, get_value(kind, idx))))
        elif kind == "ch_dca":
            log("    SET %s %02d DCA掩码=%d" % (label, idx, int(raw) & 0xFF))
        elif kind == "fx_on":
            log("    SET FX%d 开关=%s" % (idx, "开" if raw else "旁通"))
        elif kind == "fx_type":
            log("    SET FX%d 算法=%s" % (idx, raw))
        elif kind == "fx_name":
            log("    SET FX%d 名称=%s" % (idx, raw))
        elif kind == "fx_mix":
            log("    SET FX%d 混合比=%.3f" % (idx, float(raw)))
        elif fx_par_num(kind) is not None:
            log("    SET FX%d 参数%02d = %.3f" % (idx, fx_par_num(kind), float(raw)))
        elif kind.endswith("_on"):
            log("    SET %s %02d mute=%s" % (label, idx if idx else 1, "开" if raw else "静音"))
        else:
            log("    SET %s %02d fader=%.3f (%.1f dB)" % (label, idx if idx else 1, float(raw), STATE.fader_to_db(float(raw))))
        return

    # get: 单条 -> 单消息回包; 多条 -> Bundle 回包
    replies = [r for r in (value_reply(k, i) for k, i in matches) if r is not None]
    if not replies:
        log("    (当前控台不支持 %d 个匹配目标, 无回包)" % len(matches))
        return
    if len(replies) == 1:
        sock.sendto(encode_message(replies[0]), addr)
    else:
        sock.sendto(encode_bundle(replies), addr)
