#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X32 虚拟调音台模拟器 (Behringer X32/M32 OSC Server Simulator)
=============================================================

用途:在 Mac / Windows 电脑上模拟一台 X32 调音台,让 X32Remote iOS App
      在没有真机的情况下也能完整测试遥控功能。

用法:
    python3 x32_simulator.py                # 默认端口 10023
    python3 x32_simulator.py --port 10023   # 指定端口

然后在 iOS App 设置里把"调音台 IP"填成这台电脑的局域网 IP 即可连接。

支持的 OSC 指令 (UDP :10023):
    /xinfo                          -> 返回 [ip, name, model, firmware]
    /ch/NN/mix/fader  [get/set f]   -> 通道 01-32 推子 (0.0-1.0)
    /ch/NN/mix/on     [get/set i]   -> 通道静音 (1=开 0=静音)
    /dca/NN/fader      [get/set f]  -> DCA 01-08 推子
    /dca/NN/on         [get/set i]  -> DCA 静音
    /main/st/mix/fader [get/set f]  -> Main LR 推子
    /main/st/mix/on    [get/set i]  -> Main LR 静音
    /meters/1          [订阅]       -> 每 150ms 推送 32 通道电平 blob
    /meters/1 i 0      [退订]       -> 停止推送
    支持 "*" 通配符查询 (如 /ch/*/mix/fader,以 Bundle 形式回包)
    支持接收 OSC Bundle 形式的指令

仅使用 Python 标准库,兼容 macOS 10.14.6 (python.org 的 Python 3) 与 Windows。
"""

import argparse
import collections
import json
import math
import os
import socket
import socketserver
import struct
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- OSC 编解码

def pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def encode_string(s: str) -> bytes:
    return pad4(s.encode("utf-8") + b"\x00")


def next_aligned(offset: int) -> int:
    return (offset + 3) // 4 * 4


class OscMsg:
    def __init__(self, address, args=None):
        self.address = address
        self.args = args or []


def encode_message(msg: OscMsg) -> bytes:
    out = pad4(msg.address.encode("utf-8") + b"\x00")
    tags = b","
    payloads = b""
    for a in msg.args:
        if isinstance(a, int) and not isinstance(a, bool):
            tags += b"i"
            payloads += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += b"f"
            payloads += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += b"s"
            payloads += encode_string(a)
        elif isinstance(a, bytes):
            tags += b"b"
            payloads += struct.pack(">i", len(a)) + pad4(a)
        elif a is True:
            tags += b"T"
        elif a is False:
            tags += b"F"
    out += pad4(tags + b"\x00") + payloads
    return out


def encode_bundle(messages) -> bytes:
    out = b"#bundle\x00" + b"\x00" * 8  # timetag 置零
    for m in messages:
        body = encode_message(m) if isinstance(m, OscMsg) else m
        out += struct.pack(">i", len(body)) + body
    return out


def decode_packet(data: bytes):
    """解码收到的 UDP 包,返回 OscMsg 列表 (自动展开 Bundle)。"""
    if data.startswith(b"#bundle"):
        out = []
        offset = 16
        while offset + 4 <= len(data):
            (size,) = struct.unpack(">i", data[offset:offset + 4])
            offset += 4
            if size <= 0 or offset + size > len(data):
                break
            out.extend(decode_packet(data[offset:offset + size]))
            offset += size
        return out
    z = data.index(b"\x00")
    address = data[:z].decode("utf-8", errors="replace")
    offset = next_aligned(z + 1)
    if offset >= len(data) or data[offset:offset + 1] != b",":
        return [OscMsg(address)]
    zend = data.index(b"\x00", offset)
    tags = data[offset + 1:zend].decode("ascii", errors="replace")
    offset = next_aligned(zend + 1)
    args = []
    for t in tags:
        if t == "i":
            (v,) = struct.unpack(">i", data[offset:offset + 4]); args.append(v); offset += 4
        elif t == "f":
            (v,) = struct.unpack(">f", data[offset:offset + 4]); args.append(v); offset += 4
        elif t == "s":
            end = data.find(b"\x00", offset)
            if end < 0:                  # 畸形包: 字符串没有结尾符, 停止解析
                break
            args.append(data[offset:end].decode("utf-8", errors="replace"))
            offset = next_aligned(end + 1)
        elif t == "b":
            (n,) = struct.unpack(">i", data[offset:offset + 4]); offset += 4
            args.append(data[offset:offset + n]); offset = next_aligned(offset + n)
        elif t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
    return [OscMsg(address, args)]


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


# 各组通道数量 (名称读写与编辑器共用); main 单独处理 idx=0
NAME_COUNTS = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 2, "fxret": 8, "mtx": 6}
# 出厂默认名称: 与内置卡片/控制键呼应 (15=音乐, Bus15=耳返), 均可在网页或 OSC 里改
DEFAULT_NAMES = {
    ("ch", 1): "底鼓", ("ch", 2): "军鼓", ("ch", 3): "踩镲", ("ch", 4): "嗵鼓",
    ("ch", 5): "吊镲", ("ch", 6): "贝斯", ("ch", 7): "电吉1", ("ch", 8): "电吉2",
    ("ch", 9): "木吉", ("ch", 10): "键盘1", ("ch", 11): "键盘2", ("ch", 12): "采样",
    ("ch", 13): "无线麦1", ("ch", 14): "主持麦", ("ch", 15): "音乐", ("ch", 16): "视频声",
    ("bus", 1): "鼓监听", ("bus", 2): "乐队监听", ("bus", 3): "主唱监听", ("bus", 15): "耳返",
    ("dca", 1): "乐队", ("dca", 2): "人声", ("dca", 3): "鼓组",
    ("main", 0): "主输出",
}

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
    return None


# 网页分组前缀 → 推子/静音 kind 映射 (/api/set 与 /api/setbatch 共用)
SET_KINDMAP = {"ch": {"fader": "ch_fader", "on": "ch_on"},
               "bus": {"fader": "bus_fader", "on": "bus_on"},
               "dca": {"fader": "dca_fader", "on": "dca_on"},
               "main": {"fader": "main_fader", "on": "main_on"},
               "auxin": {"fader": "auxin_fader", "on": "auxin_on"},
               "usb": {"fader": "usb_fader", "on": "usb_on"},
               "fxret": {"fader": "fxret_fader", "on": "fxret_on"},
               "mtx": {"fader": "mtx_fader", "on": "mtx_on"}}


def set_value(kind, idx, value, source="manual"):
    # source="manual" (网页/控制键/外部 OSC 默认) 会触发冲突检测;
    # source="action"/"dca" 是卡片/回归/DCA 级联自己的写入, 不检测
    if source == "manual":
        _check_manual_takeover(kind, idx)
    cascade_from = None
    if kind == "dca_fader" and source == "manual":
        cascade_from = get_value(kind, idx)   # DCA 手动移动 → 成员级联基准
    with STATE.lock:
        if kind == "ch_fader":   STATE.ch[idx]["fader"] = min(max(float(value), 0.0), 1.0)
        elif kind == "ch_on":    STATE.ch[idx]["on"] = 1 if value else 0
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
        elif kind == "bus_fader": STATE.bus[idx]["fader"] = min(max(float(value), 0.0), 1.0)
        elif kind == "bus_on":   STATE.bus[idx]["on"] = 1 if value else 0
        elif kind == "dca_fader": STATE.dca[idx]["fader"] = min(max(float(value), 0.0), 1.0)
        elif kind == "dca_on":   STATE.dca[idx]["on"] = 1 if value else 0
        elif kind == "main_fader": STATE.main["fader"] = min(max(float(value), 0.0), 1.0)
        elif kind == "main_on":  STATE.main["on"] = 1 if value else 0
        else:
            for g in ("auxin", "usb", "fxret", "mtx"):
                if kind == g + "_fader":
                    STATE.__dict__[g][idx]["fader"] = min(max(float(value), 0.0), 1.0)
                elif kind == g + "_on":
                    STATE.__dict__[g][idx]["on"] = 1 if value else 0
    if source == "manual":
        _push_manual_now(kind, idx)
    if BRIDGE["addr"] is not None and source in ("manual", "action", "dca"):
        _bridge_forward(kind, idx, value)
    if cascade_from is not None:
        _dca_cascade(idx, cascade_from, float(value))


def _push_manual_now(kind, idx):
    """手动写入立即广播给订阅者 (真台行为: 面板动推子马上推送, 让桥接端及时察觉干预)。"""
    sock = MAIN_SOCK[0]
    if sock is None:
        return
    key = (kind, idx)
    now = time.time()
    with BRIDGE_LOCK:
        if now - NOTIFY_LAST.get(key, 0) < 0.08:
            return
        NOTIFY_LAST[key] = now
    v = get_value(kind, idx)
    if v is None:
        return
    PUSH_CACHE[key] = v           # 立即推送后, 周期推送不再重发
    with METER_LOCK:
        subs = list(METER_SUBSCRIBERS)
    if not subs:
        return
    try:
        pkt = encode_message(value_reply(kind, idx))
    except Exception:
        return
    for s in subs:
        try:
            sock.sendto(pkt, s)
        except OSError:
            pass


def _bridge_forward(kind, idx, value):
    """桥接模式下把写入转成 OSC 发往真台, 并登记回声抑制记录。"""
    try:
        if kind == "ch_dca":
            addr = "/ch/%02d/mix/dca" % idx
            arg = int(value) & 0xFF
        elif kind.endswith("_fader"):
            addr = FADER_ADDR[kind] % idx
            arg = min(max(float(value), 0.0), 1.0)
        else:
            addr = ON_ADDR[kind] % idx
            arg = 1 if value else 0
        sock = BRIDGE_SOCK[0]
        if sock is None:
            return
        sock.sendto(encode_message(OscMsg(addr, [arg])), BRIDGE["addr"])
        with BRIDGE_LOCK:
            FWD_RECENT[(kind, idx)] = [float(arg), time.time()]
    except Exception as e:
        log("BRIDGE 转发失败 %s: %r" % (kind, e))


def _bridge_apply_readback(kind, idx, value):
    """真台回读: 回声(自己刚转发过的值)静默吸收; 台面上的手动变化按冲突策略处理。"""
    key = (kind, idx)
    with BRIDGE_LOCK:
        rec = FWD_RECENT.get(key)
    if rec and time.time() - rec[1] < 2.0 and abs(rec[0] - float(value)) < 0.02:
        set_value(kind, idx, value, source="sync")     # 自己写入的回声, 只对齐本地
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
    set_value(kind, idx, value, source="sync")


def _bridge_full_sync(sock):
    """全量状态回读: 向真台发一轮 GET (Bundle), 用回包填本地台面。"""
    counts = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 2, "fxret": 8, "mtx": 6}
    gets = []
    for tmap in (FADER_ADDR, ON_ADDR):
        for k, tpl in tmap.items():
            ids = range(1, counts[kind_group(k)] + 1) if not k.startswith("main_") else [0]
            for i in ids:
                gets.append(OscMsg(tpl if k.startswith("main_") else tpl % i, []))
    for i in range(1, 33):
        gets.append(OscMsg("/ch/%02d/mix/dca" % i, []))
    for chunk in (gets[x:x + 64] for x in range(0, len(gets), 64)):
        try:
            sock.sendto(encode_bundle(chunk), BRIDGE["addr"])
        except OSError as e:
            log("BRIDGE 全量同步发送失败: %r" % e)
    log("BRIDGE 全量状态同步已请求 (%d 项)" % len(gets))


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
    """桥接后台线程: 5 秒 /xremote 保活, 持续接收真台回读并同步本地。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    BRIDGE_SOCK[0] = sock
    BRIDGE["addr"] = (ip, port)
    BRIDGE["ip"], BRIDGE["port"], BRIDGE["synced"] = ip, port, False
    BRIDGE["started"] = time.time()
    BRIDGE["last_warn"] = 0.0
    log("BRIDGE 桥接启动: 真台 %s:%d (写入转发 + /xremote 保活 + 状态回读)" % (ip, port))
    _bridge_full_sync(sock)
    BRIDGE["last_keep"] = 0.0        # 立即发首次 /xremote 完成订阅
    while BRIDGE["addr"] is not None:
        # 保活: 每 5 秒发一次 /xremote (真台 10 秒无保活会取消遥控链接)
        if time.time() - BRIDGE["last_keep"] >= 5.0:
            try:
                sock.sendto(encode_message(OscMsg("/xremote", [])), (ip, port))
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
                matches = match_address(a)
                if not matches or not msg.args:
                    continue
                raw = msg.args[0]
                if isinstance(raw, str) and raw:
                    try:
                        raw = float(raw)
                    except ValueError:
                        continue
                for k, i in matches[:1]:
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
    # 已有桥接先停
    bridge_stop()
    BRIDGE["addr"] = (ip, port)      # 先占位, 让线程启动前 set_value 也能拿到
    threading.Thread(target=bridge_client, args=(ip, port), daemon=True).start()
    return True, "桥接已启动 → %s:%d" % (ip, port)


def bridge_stop():
    if BRIDGE["addr"] is not None:
        ip, port = BRIDGE["addr"]
        BRIDGE["addr"] = None
        log("BRIDGE 停止桥接 (%s:%d)" % (ip, port))


def _local_ips():
    """本机所有 IPv4 地址, 用于在扫描结果里排除自己 (多网卡/ZeroTier 下会从各网卡回包)。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    if LAN_IP:
        ips.add(LAN_IP)
    return ips


def _local_subnets():
    """本机各网卡的 /24 定向广播地址 (路由器常拦截 255.255.255.255, 定向广播更可靠)。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    if LAN_IP and not LAN_IP.startswith("127."):
        ips.add(LAN_IP)
    out = []
    for ip in ips:
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            out.append("%s.%s.%s.255" % (parts[0], parts[1], parts[2]))
    return sorted(set(out))


def _scan_collect(sock, found, window):
    """收窗口内所有 /xinfo 应答, 填进 found。"""
    self_ips = _local_ips()
    end = time.time() + window
    while time.time() < end:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            # Windows 上扫不存在的地址会回 ICMP 端口不可达, 表现为 ConnectionResetError
            continue
        ip, port = addr[0], addr[1]
        if port == UDP_PORT and ip in self_ips:
            continue                      # 自己这台模拟器实例, 不算真台
        name, model, fw = "X32", "X32", ""
        try:
            for msg in decode_packet(data):
                if msg.address == "/xinfo" and msg.args:
                    a = [str(x) for x in msg.args]
                    if len(a) >= 2 and a[1]:
                        name = a[1]
                    if len(a) >= 3 and a[2]:
                        model = a[2]
                    if len(a) >= 4:
                        fw = a[3]
        except Exception:
            pass
        found[ip] = {"ip": ip, "port": port, "name": name, "model": model,
                     "firmware": fw, "local": ip in self_ips}


def _same_subnet24(a, b):
    try:
        return a.rsplit(".", 1)[0] == b.rsplit(".", 1)[0]
    except Exception:
        return False


def _dedupe_found(items):
    """同一台设备会从本机多张网卡各回一个包, 只保留与我们在同一网段的那条。"""
    groups = {}
    for it in items:
        groups.setdefault((it["port"], it["model"], it["name"]), []).append(it)
    out = []
    for _key, grp in groups.items():
        same = [x for x in grp if _same_subnet24(x["ip"], LAN_IP)]
        keep = dict((same or grp)[0])
        others = sorted(x["ip"] for x in grp if x["ip"] != keep["ip"])
        if others:
            keep["also"] = others       # 其他网卡地址, 仅作参考
        out.append(keep)
    return out


def scan_consoles(timeout=1.4):
    """发现局域网内的 X32/M32 (以及本机其他模拟器实例)。
    先发广播 /xinfo —— 这是 X32 官方发现指令, X32-Edit 就是靠它找台子的;
    广播被路由器拦截时退化为按 /24 逐地址单播扫一遍。"""
    found = {}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SIO_UDP_CONNRESET"):     # Windows: 关掉 UDP 假连接重置
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except OSError:
                pass
        sock.settimeout(0.2)
        sock.bind(("", 0))
    except OSError as e:
        log("SCAN 扫描套接字创建失败: %r" % e)
        return []
    pkt = encode_message(OscMsg("/xinfo", []))
    try:
        for target in (["255.255.255.255"] + _local_subnets()):
            try:
                sock.sendto(pkt, (target, 10023))
            except OSError:
                pass
        _scan_collect(sock, found, timeout)
        if not found:
            hosts = []
            for b in _local_subnets():
                base = b.rsplit(".", 1)[0]
                hosts += ["%s.%d" % (base, n) for n in range(1, 255)]
            for host in hosts:
                try:
                    sock.sendto(pkt, (host, 10023))
                except OSError:
                    pass
            _scan_collect(sock, found, timeout + 0.6)
    finally:
        sock.close()
    return sorted(_dedupe_found(list(found.values())), key=lambda x: x["ip"])


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


FADER_ADDR = {"ch_fader": "/ch/%02d/mix/fader",
              "bus_fader": "/bus/%02d/mix/fader",
              "dca_fader": "/dca/%02d/fader",
              "auxin_fader": "/auxin/%02d/mix/fader",
              "usb_fader": "/usb/%02d/mix/fader",
              "fxret_fader": "/fxret/%02d/mix/fader",
              "mtx_fader": "/mtx/%02d/mix/fader",
              "main_fader": "/main/st/mix/fader"}
ON_ADDR = {"ch_on": "/ch/%02d/mix/on",
           "bus_on": "/bus/%02d/mix/on",
           "dca_on": "/dca/%02d/on",
           "auxin_on": "/auxin/%02d/mix/on",
           "usb_on": "/usb/%02d/mix/on",
           "fxret_on": "/fxret/%02d/mix/on",
           "mtx_on": "/mtx/%02d/mix/on",
           "main_on": "/main/st/mix/on"}
KIND_LABEL = {"ch": "通道", "bus": "输出Bus", "dca": "DCA", "main": "Main",
              "auxin": "AuxIn", "usb": "USB", "fxret": "FX返回", "mtx": "Matrix"}


def kind_group(kind):
    """'ch_fader' -> 'ch'"""
    return kind.rsplit("_", 1)[0]


def get_name(group, idx):
    with STATE.lock:
        return STATE.names.get((group, idx), "")


def set_name(group, idx, name):
    name = str(name).strip()[:12]
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
    v = get_value(kind, idx)
    if kind.startswith("main_"):
        # Main 地址无 %02d 占位符, 不能走统一格式化
        addr = FADER_ADDR[kind] if kind.endswith("_fader") else ON_ADDR[kind]
        return OscMsg(addr, [v])
    if kind.endswith("_fader"):
        return OscMsg(FADER_ADDR[kind] % idx, [v])
    if kind == "ch_dca":
        return OscMsg("/ch/%02d/mix/dca" % idx, [int(v)])
    return OscMsg(ON_ADDR[kind] % idx, [int(v)])


def handle(msg: OscMsg, addr, sock):
    a = msg.address
    log("%-28s <- %s  args=%s" % (addr[0], a, msg.args))

    if a == "/xinfo":
        ip = sock.getsockname()[0]
        reply = OscMsg("/xinfo", [ip, "X32Remote-Sim", "X32", "3.06-sim"])
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
        if isinstance(raw, str) and raw:
            raw = float(raw)          # X32 有时用字符串传小数
        set_value(kind, idx, raw)
        label = KIND_LABEL.get(kind_group(kind), kind)
        if kind == "ch_dca":
            log("    SET %s %02d DCA掩码=%d" % (label, idx, int(raw) & 0xFF))
        elif kind.endswith("_on"):
            log("    SET %s %02d mute=%s" % (label, idx if idx else 1, "开" if raw else "静音"))
        else:
            log("    SET %s %02d fader=%.3f (%.1f dB)" % (label, idx if idx else 1, float(raw), STATE.fader_to_db(float(raw))))
        return

    # get: 单条 -> 单消息回包; 多条 -> Bundle 回包
    replies = [value_reply(k, i) for k, i in matches]
    if len(replies) == 1:
        sock.sendto(encode_message(replies[0]), addr)
    else:
        sock.sendto(encode_bundle(replies), addr)


PUSH_CACHE = {}      # (kind, idx) -> 上次推送的值; 状态变化推给订阅者 (真台 /xremote 行为)
PUSH_KINDS = (list(FADER_ADDR.keys()) + list(ON_ADDR.keys()))


def _push_state_diffs(sock, subscribers):
    """每次 tick 对比缓存, 把发生变化的推子/静音推给订阅者 (模拟真台变更推送)。"""
    sent = 0
    for kind in PUSH_KINDS:
        if kind.startswith("main_"):
            ids = [0]
        else:
            n = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 2,
                 "fxret": 8, "mtx": 6}[kind_group(kind)]
            ids = range(1, n + 1)
        for i in ids:
            v = get_value(kind, i)
            if v is None:
                continue
            key = (kind, i)
            old = PUSH_CACHE.get(key)
            if old is not None and abs(old - v) < 1e-4:
                continue
            PUSH_CACHE[key] = v
            pkt = encode_message(value_reply(kind, i))
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


LOG_LOCK = threading.Lock()
LOG_RING = collections.deque(maxlen=200)
LOG_SEQ = [0]        # 日志总条数(单调递增), 网页按 logseq 增量拉取


def log(text):
    with LOG_LOCK:
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), text)
        print(line, flush=True)
        LOG_RING.append(line)
        LOG_SEQ[0] += 1


def get_lan_ip():
    """探测本机局域网 IP(UDP connect 不发包,仅取路由源地址)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


LAN_IP, UDP_PORT, WEB_PORT = "127.0.0.1", 10023, 8080


class WebServer(ThreadingHTTPServer):
    """跳过 HTTPServer 默认 server_bind() 里的 socket.getfqdn() 反向 DNS 解析。

    本机装了 ZeroTier/多个虚拟网卡时, 这一步会卡十几秒, 期间 OSC 主循环还没开始 —
    表现为"服务刚起时连不上", 也是之前 Web 启动慢的真正原因。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def main():
    parser = argparse.ArgumentParser(description="X32 虚拟调音台模拟器")
    parser.add_argument("--port", type=int, default=10023)
    parser.add_argument("--bind", default="0.0.0.0", help="监听地址,默认所有网卡")
    # 发布为在线应用时,沙箱通过 PORT 环境变量注入唯一的 HTTP 端口
    _env_port = os.environ.get("PORT")
    _web_default = int(_env_port) if _env_port and _env_port.isdigit() else 8080
    parser.add_argument("--web-port", type=int, default=_web_default, help="网页控制台端口,0=关闭")
    parser.add_argument("--bridge", default="", metavar="IP:PORT",
                        help="桥接真台: 写入转发+保活+回读 (联调可指向另一模拟器实例)")
    parser.add_argument("--config", default="", help="配置文件路径 (多实例联调时各自独立)")
    args = parser.parse_args()

    global CONFIG_PATH
    if args.config:
        CONFIG_PATH = args.config
    load_user_config()

    if args.bridge:
        ok, msg = bridge_start(args.bridge)
        log(("BRIDGE " + msg) if ok else ("BRIDGE 参数无效: " + msg))
    elif BRIDGE_TARGET:
        # 上次连过的真台 → 启动后自动重连: 手机打开网页时就已经连上台子了
        def _auto_reconnect():
            time.sleep(3.0)
            ok, msg = bridge_start(BRIDGE_TARGET)
            log(("BRIDGE 自动重连: " + msg) if ok else ("BRIDGE 自动重连失败: " + msg))
        threading.Thread(target=_auto_reconnect, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.bind, args.port))
    except OSError as e:
        log("绑定端口失败: %s" % e)
        sys.exit(1)
    MAIN_SOCK[0] = sock

    ip = get_lan_ip()
    log("X32 虚拟调音台已启动  UDP %s:%d" % (ip, args.port))
    global LAN_IP, UDP_PORT, WEB_PORT
    LAN_IP, UDP_PORT, WEB_PORT = ip, args.port, args.web_port

    if args.web_port > 0:
        try:
            web = WebServer(("0.0.0.0", args.web_port), WebHandler)
            threading.Thread(target=web.serve_forever, daemon=True).start()
            log("网页控制台已启动  http://localhost:%d  (手机/电脑浏览器均可打开)" % args.web_port)
        except OSError as e:
            log("网页控制台启动失败(端口 %d 被占用?): %s" % (args.web_port, e))

    log("iOS App 中把调音台 IP 填为: %s  (若为 0.0.0.0 请用本机局域网 IP)" % ip)
    log("等待指令... Ctrl+C 退出\n")

    threading.Thread(target=meter_streamer, args=(sock,), daemon=True).start()

    try:
        while True:
            # Windows 特性: 客户端退出后端口不可达,下一次 recvfrom 会抛
            # ConnectionResetError (10054),必须捕获并继续,否则主循环崩掉
            try:
                data, addr = sock.recvfrom(4096)
            except ConnectionResetError:
                continue
            # 任何畸形包都不允许杀死主循环 (OSC 是 UDP, 什么包都可能飞进来)
            try:
                msgs = decode_packet(data)
            except Exception as e:
                log("收到无法解析的 UDP 包 (%d 字节), 已忽略: %r" % (len(data), e))
                continue
            for msg in msgs:
                try:
                    handle(msg, addr, sock)
                except Exception as e:
                    log("处理 %s 出错: %r" % (msg.address, e))
    except KeyboardInterrupt:
        log("\n已退出")


# ================================================================ 演出卡片
# 与 iOS App "演出"标签页同构: 动作按顺序瞬间执行, 渐变按时长分步执行

SHOW_CARDS = [
    {"id": "dinner", "name": "🍽 晚宴模式", "desc": "静音话筒 01/02 → 音乐(通道15)从 75% 经 10 秒渐弱到 30%",
     "actions": [{"kind": "mute", "ch": 1}, {"kind": "mute", "ch": 2}],
     "fades": [{"ch": 15, "from": 0.75, "to": 0.30, "duration": 10.0, "steps": 20}]},
    {"id": "speech", "name": "🎤 演讲模式", "desc": "切场景 2 → 音乐静音 → 话筒(通道01)从 75% 经 3 秒渐强到 90%",
     "actions": [{"kind": "scene", "scene": 2}, {"kind": "mute", "ch": 15}],
     "fades": [{"ch": 1, "from": 0.75, "to": 0.90, "duration": 3.0, "steps": 15}]},
    {"id": "leave", "name": "🚪 散场模式", "desc": "全部取消静音 → 音乐(通道15)从 0 经 5 秒渐强到 90%",
     "actions": [{"kind": "unmute_all"},
                 {"kind": "fade_from", "ch": 15, "frm": 0.0, "to": 0.90, "duration": 5.0, "curve": "linear"}],
     "fades": []},
    {"id": "alllive", "name": "🎵 All Live 自动混音演示",
     "desc": "前 8 路推子随音乐节奏自动起伏 12 秒——电动推子玩具同款效果",
     "actions": [], "fades": []},
    {"id": "sync_down", "name": "⏬ 全体推下 (3秒)",
     "desc": "全部推子(32通道+Aux/USB/FX返回+16 Bus+Matrix+8 DCA+主输出)3 秒内同步拉到底,先快后慢收尾",
     "actions": [{"kind": "fade_all", "to": 0.0, "duration": 3.0, "curve": "ease_out"}], "fades": []},
    {"id": "sync_up", "name": "⏫ 全体拉回 (3秒)",
     "desc": "全部推子 3 秒内同步平滑拉回 0 dB 位置,S 形缓入缓出",
     "actions": [{"kind": "fade_all", "to": 0.75, "duration": 3.0, "curve": "smooth"}], "fades": []},
    {"id": "odd_even", "name": "🎸 歌曲过渡 (基数进偶数出)",
     "desc": "奇数通道(1/3/5…)4 秒渐入到 80%,偶数通道同时渐出——换歌时乐器起、伴奏退",
     "actions": [{"kind": "fade_odd_even", "odd_to": 0.8, "even_to": 0.0, "duration": 4.0, "curve": "linear"}], "fades": []},
    {"id": "odd_even_back", "name": "🎹 休息过渡 (偶数进基数出)",
     "desc": "反向渐变:偶数通道渐入到 80%,奇数通道同时渐出——休息时伴奏起、乐器退",
     "actions": [{"kind": "fade_odd_even", "odd_to": 0.0, "even_to": 0.8, "duration": 4.0, "curve": "linear"}], "fades": []},
]

SHOW_STATE = {"running": None, "name": "", "progress": 0.0, "step": ""}
SHOW_STOP = threading.Event()

# ---- 卡片定时触发: 延时 N 秒后 / 定在 HH:MM 自动启动, 可取消 ----
SCHEDULES = []        # {"sid", "card_id", "card_name", "run_at"(epoch), "mode", "timer"}
SCHED_SEQ = [0]
SCHED_LOCK = threading.Lock()


def schedule_card(card, mode, delay=None, clock=None):
    """定时启动卡片。mode='delay' delay 秒后; mode='clock' 今天的 clock(HH:MM) 时刻。"""
    if mode == "delay":
        run_at = time.time() + max(float(delay), 0.5)
    else:
        h, m = [int(x) for x in str(clock).split(":")[:2]]
        t = time.localtime()
        run_at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, m, 0, 0, 0, -1))
        if run_at <= time.time():
            run_at += 86400          # 已过时刻 → 明天
    SCHED_SEQ[0] += 1
    sid = SCHED_SEQ[0]

    def fire():
        with SCHED_LOCK:
            SCHEDULES[:] = [s for s in SCHEDULES if s["sid"] != sid]
        if SHOW_STOP.is_set():
            SHOW_STOP.clear()
        cur = next((c for c in SHOW_CARDS if c["id"] == card["id"]), None)
        if not cur:
            log("SCHED 定时启动失败: 卡片已被删除")
            return
        EVT_SEQ[0] += 1
        EVENTS.append({"id": EVT_SEQ[0], "time": time.strftime("%H:%M:%S"),
                       "text": "⏰ 定时触发 — 卡片「%s」自动启动" % cur.get("name", "")})
        log("SCHED 定时启动卡片: %s" % cur.get("name"))
        run_show(cur)

    t = threading.Timer(max(run_at - time.time(), 0.1), fire)
    t.daemon = True
    t.start()
    with SCHED_LOCK:
        SCHEDULES.append({"sid": sid, "card_id": card["id"], "card_name": card.get("name", ""),
                          "run_at": run_at, "mode": mode, "timer": t})
    log("SCHED 定时任务 #%d: 「%s」 %s (%s)" %
        (sid, card.get("name", ""), time.strftime("%H:%M:%S", time.localtime(run_at)),
         "延时" if mode == "delay" else "定时"))


def cancel_schedule(sid):
    with SCHED_LOCK:
        s = next((x for x in SCHEDULES if x["sid"] == sid), None)
        if not s:
            return False
        s["timer"].cancel()
        SCHEDULES[:] = [x for x in SCHEDULES if x["sid"] != sid]
    log("SCHED 取消定时任务 #%d" % sid)
    return True

# ---- 桥接模式: 网页/卡片写入转发到真台 (OSC), 真台状态回读同步到本地 ----
# 离线验证: 再开一个模拟器实例当"假真台", 本实例 --bridge 127.0.0.1:假台端口
BRIDGE = {"addr": None, "ip": "", "port": 0, "synced": False,
          "last_keep": 0.0, "last_recv": 0.0, "started": 0.0, "last_warn": 0.0}
BRIDGE_SOCK = [None]
BRIDGE_LOCK = threading.Lock()
BRIDGE_TARGET = ""   # 记住上次连过的真台地址, 下次启动自动重连 (B3 零配置的关键)
FWD_RECENT = {}      # (kind, idx) -> [value, time] 最近转发记录, 用于抑制回读回声
MAIN_SOCK = [None]   # 主 UDP socket (手动变更立即广播用)
NOTIFY_LAST = {}     # 手动变更推送节流

# ---- 冲突策略: 手动干预只停该通道, 其余动作继续 ----
# 卡片执行期间, 正在被渐变驱动的 (kind, idx) 登记在 FADE_ZONE;
# 此时有手动写入 (网页推子 / 控制键 / 外部 OSC) 命中同一通道,
# 只把该通道标记为手动接管 (停在手动位置), 卡片其余通道与后续动作照常执行, 并推送网页通知。
FADE_LOCK = threading.Lock()
FADE_ZONE = {}        # (kind, idx) -> 卡片名 (正在被卡片渐变驱动)
INTERRUPTS = set()    # 本轮渐变中被手动接管的 (kind, idx)
EVT_SEQ = [0]
EVENTS = collections.deque(maxlen=20)   # 冲突/通知事件, 网页轮询弹提示


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


# ---------------------------------------------------------------- 推子快照
# 灵感来自"电动推子自动化":保存当前所有推子位置,一键让全部推子自动滑回

SNAPSHOTS = []                       # [{id, name, time, ch, dca, main}]
SNAP_ID = [0]
AUTO_SNAPS = collections.deque(maxlen=5)   # 自动撤销点: 每次卡片执行前自动保存, 最多留 5 个
RECALL_STATE = {"running": None, "name": "", "progress": 0.0}
RECALL_STOP = threading.Event()


def _grab_state_locked():
    """在持有 STATE.lock 时调用:深拷贝推子/静音状态。"""
    return {
        "ch": {i: dict(STATE.ch[i]) for i in range(1, 33)},
        "bus": {i: dict(STATE.bus[i]) for i in range(1, 17)},
        "dca": {i: dict(STATE.dca[i]) for i in range(1, 9)},
        "auxin": {i: dict(STATE.auxin[i]) for i in range(1, 7)},
        "usb": {i: dict(STATE.usb[i]) for i in range(1, 3)},
        "fxret": {i: dict(STATE.fxret[i]) for i in range(1, 9)},
        "mtx": {i: dict(STATE.mtx[i]) for i in range(1, 7)},
        "main": dict(STATE.main),
    }


def save_snapshot(name=None, auto=False):
    with STATE.lock:
        snap_state = _grab_state_locked()
    SNAP_ID[0] += 1
    snap = {"id": SNAP_ID[0],
            "name": name or "快照 %d" % SNAP_ID[0],
            "time": time.strftime("%H:%M:%S"),
            "scene": STATE.scene if hasattr(STATE, "scene") else 1}
    snap.update(snap_state)
    if auto:
        # 撤销点不进用户的预设列表, 单独存放
        AUTO_SNAPS.append(snap)
        log("SNAPSHOT 自动撤销点: %s" % snap["name"])
        return snap
    SNAPSHOTS.insert(0, snap)
    if len(SNAPSHOTS) > 8:
        SNAPSHOTS.pop()
    log("SNAPSHOT 保存: %s" % snap["name"])
    return snap


def recall_snapshot(snap_id, seconds=3.0):
    snap = next((s for s in SNAPSHOTS if s["id"] == snap_id), None)
    if not snap:
        return None
    return start_recall(snap, seconds)


def start_recall(snap, seconds=3.0):
    """按快照对象回归 (预设回归与一键撤销共用): 静音立即恢复, 推子平滑滑回。"""
    RECALL_STOP.set()                # 打断进行中的回归
    seconds = min(max(float(seconds), 0.5), 15.0)

    def worker():
        RECALL_STOP.clear()
        RECALL_STATE["running"] = snap["id"]
        RECALL_STATE["name"] = snap["name"]
        RECALL_STATE["progress"] = 0.0
        try:
            # 静音状态立即恢复,推子平滑滑动
            for i in range(1, 33):
                set_value("ch_on", i, snap["ch"][i]["on"])
            for i in range(1, 17):
                set_value("bus_on", i, snap["bus"][i]["on"])
            for i in range(1, 9):
                set_value("dca_on", i, snap["dca"][i]["on"])
            for g, n in (("auxin", 6), ("usb", 2), ("fxret", 8), ("mtx", 6)):
                for i in range(1, n + 1):
                    set_value(g + "_on", i, snap[g][i]["on"])
            set_value("main_on", 0, snap["main"]["on"])
            log("SNAPSHOT 回归开始: %s (静音已恢复,推子 %.1f 秒归位)" % (snap["name"], seconds))

            # 收集推子起点/终点
            starts = [(k, i, a, b) for k, i, a, b in
                      [("ch_fader", i, get_value("ch_fader", i), snap["ch"][i]["fader"]) for i in range(1, 33)] +
                      [("bus_fader", i, get_value("bus_fader", i), snap["bus"][i]["fader"]) for i in range(1, 17)] +
                      [("dca_fader", i, get_value("dca_fader", i), snap["dca"][i]["fader"]) for i in range(1, 9)] +
                      [(g + "_fader", i, get_value(g + "_fader", i), snap[g][i]["fader"])
                       for g, n in (("auxin", 6), ("usb", 2), ("fxret", 8), ("mtx", 6)) for i in range(1, n + 1)] +
                      [("main_fader", 0, get_value("main_fader", 0), snap["main"]["fader"])]]

            steps = max(1, int(seconds / 0.1))
            for s in range(1, steps + 1):
                if RECALL_STOP.is_set():
                    log("SNAPSHOT 回归被打断")
                    return
                time.sleep(0.1)
                ratio = s / steps
                for k, i, a, b in starts:
                    set_value(k, i, a + (b - a) * ratio, source="action")
                RECALL_STATE["progress"] = ratio
            log("SNAPSHOT 回归完成: %s" % snap["name"])
        except Exception as e:
            log("SNAPSHOT 回归出错: %r" % e)
        finally:
            if RECALL_STATE["running"] == snap["id"]:
                RECALL_STATE["running"] = None
                RECALL_STATE["progress"] = 1.0
    threading.Thread(target=worker, daemon=True).start()
    return snap


# ------------------------------------------------------------ 自定义控制键
# 歌手实时微调场景: 「耳返增大/减小」等大按钮,按步长(1.5/3/6 dB)调整,可带渐变
KEY_CFG = {"step_db": 3.0, "ramp": 0.4}   # ramp 秒:渐变速度,0=瞬间
QUICK_KEYS = [
    {"id": "iem_up",  "name": "耳返 增大", "icon": "🎧➕", "kind": "bus_fader", "idx": 15, "sign": +1},
    {"id": "iem_dn",  "name": "耳返 减小", "icon": "🎧➖", "kind": "bus_fader", "idx": 15, "sign": -1},
    {"id": "main_up", "name": "主输出 增大", "icon": "🔊➕", "kind": "main_fader", "idx": 0, "sign": +1},
    {"id": "main_dn", "name": "主输出 减小", "icon": "🔉➖", "kind": "main_fader", "idx": 0, "sign": -1},
    {"id": "dca1_up", "name": "乐器组 增大", "icon": "🎸➕", "kind": "dca_fader", "idx": 1, "sign": +1},
    {"id": "dca1_dn", "name": "乐器组 减小", "icon": "🎸➖", "kind": "dca_fader", "idx": 1, "sign": -1},
]
RAMP_STOP = threading.Event()   # 新的推子运动打断上一次


def ramp_to(kind, idx, target, seconds, curve="smooth"):
    """推子平滑运动到目标位置(电动推子),可被下一次运动打断。"""
    RAMP_STOP.set()

    def worker():
        RAMP_STOP.clear()
        start = get_value(kind, idx)
        steps = max(1, int(seconds / 0.05))
        try:
            for s in range(1, steps + 1):
                if RAMP_STOP.is_set():
                    return
                time.sleep(seconds / steps)
                ratio = curve_ratio(s / steps, curve)
                set_value(kind, idx, start + (target - start) * ratio)
        finally:
            if not RAMP_STOP.is_set():
                set_value(kind, idx, target)
    threading.Thread(target=worker, daemon=True).start()


def apply_key(key_id):
    key = next((k for k in QUICK_KEYS if k["id"] == key_id), None)
    if not key:
        return None
    step = float(KEY_CFG["step_db"])
    ramp = float(KEY_CFG["ramp"])
    cur_db = STATE.fader_to_db(get_value(key["kind"], key["idx"]))
    new_db = min(max(cur_db + key["sign"] * step, -90.0), 10.0)
    target = db_to_fader(new_db)
    if ramp > 0.01:
        ramp_to(key["kind"], key["idx"], target, ramp)
    else:
        set_value(key["kind"], key["idx"], target)
    log("KEY %s: %.1f dB → %.1f dB (%s)" %
        (key["name"], cur_db, new_db, "渐变 %.1f 秒" % ramp if ramp > 0.01 else "瞬间"))
    return {"id": key_id, "from_db": cur_db, "to_db": new_db}


# ---- 自定义配置持久化: 控制键/演出卡片/步长存 JSON, 重启不丢 ----
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_config.json")
ID_SEQ = [1000]


def next_id(prefix):
    ID_SEQ[0] += 1
    return "%s%d" % (prefix, ID_SEQ[0])


def save_user_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"quickKeys": QUICK_KEYS, "cards": SHOW_CARDS,
                       "keyCfg": KEY_CFG, "idSeq": ID_SEQ[0],
                       "bridgeTarget": BRIDGE_TARGET,
                       "names": {"%s|%d" % k: v for k, v in STATE.names.items()}},
                      f, ensure_ascii=False, indent=1)
    except Exception as e:
        log("配置保存失败: %r" % e)


def load_user_config():
    global BRIDGE_TARGET
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("quickKeys"), list):
            QUICK_KEYS[:] = d["quickKeys"]
        if isinstance(d.get("cards"), list):
            SHOW_CARDS[:] = d["cards"]
        # 迁移旧结构: 内置/历史卡片用独立 fades 字段, 统一转成 fade_from 动作, 保证简介与动作一致
        migrated = False
        for c in SHOW_CARDS:
            if isinstance(c, dict) and c.get("fades"):
                for f in c["fades"]:
                    try:
                        c.setdefault("actions", []).append({
                            "kind": "fade_from", "ch": int(f.get("ch", 1)),
                            "frm": float(f.get("from", 0.0)), "to": float(f.get("to", 0.9)),
                            "duration": float(f.get("duration", 5.0)),
                            "curve": f.get("curve", "linear")})
                    except (TypeError, ValueError):
                        pass
                c["fades"] = []
                migrated = True
        if migrated:
            save_user_config()
            log("已迁移旧版 fades 结构为 fade_from 动作")
        if isinstance(d.get("keyCfg"), dict):
            KEY_CFG.update(d["keyCfg"])
        if isinstance(d.get("names"), dict):
            for k, v in d["names"].items():
                try:
                    g, i = k.split("|")
                    STATE.names[(g, int(i))] = str(v)
                except (ValueError, TypeError):
                    pass
        if isinstance(d.get("idSeq"), int):
            ID_SEQ[0] = max(ID_SEQ[0], d["idSeq"])
        if isinstance(d.get("bridgeTarget"), str) and d["bridgeTarget"].strip():
            BRIDGE_TARGET = d["bridgeTarget"].strip()
        log("已加载自定义配置: %d 个控制键, %d 张演出卡片%s"
            % (len(QUICK_KEYS), len(SHOW_CARDS),
               (", 记住的真台地址 %s" % BRIDGE_TARGET) if BRIDGE_TARGET else ""))
    except FileNotFoundError:
        pass
    except Exception as e:
        log("配置加载失败: %r" % e)


def clear_console():
    """空台子:全部推子 1 秒同步拉底 + 全部取消静音,得到干净的空台。"""
    RAMP_STOP.set()
    RECALL_STOP.set()
    SHOW_STOP.set()

    def worker():
        RAMP_STOP.clear()
        kinds = ([("ch_fader", i) for i in range(1, 33)] +
                 [("bus_fader", i) for i in range(1, 17)] +
                 [("dca_fader", i) for i in range(1, 9)] +
                 [(g + "_fader", i) for g, n in (("auxin", 6), ("usb", 2), ("fxret", 8), ("mtx", 6))
                  for i in range(1, n + 1)] +
                 [("main_fader", 0)])
        starts = [(k, i, get_value(k, i)) for k, i in kinds]
        steps = 20
        try:
            for s in range(1, steps + 1):
                if RAMP_STOP.is_set():
                    return
                time.sleep(0.05)
                ratio = curve_ratio(s / steps, "ease_out")
                for (k, i, a) in starts:
                    set_value(k, i, a * (1.0 - ratio))
        except Exception:
            pass
        for i in range(1, 33):
            set_value("ch_on", i, 1)
        for i in range(1, 17):
            set_value("bus_on", i, 1)
        for i in range(1, 9):
            set_value("dca_on", i, 1)
        set_value("main_on", 0, 1)
        log("CONSOLE 空台子完成: 全部推子拉底,全部取消静音")
    threading.Thread(target=worker, daemon=True).start()


def recall_last():
    """回传数据:把最近保存的预设平滑写回台面(读取→空台→回传 流程的最后一步)。"""
    if not SNAPSHOTS:
        return None
    return recall_snapshot(SNAPSHOTS[0]["id"], 3.0)


def curve_ratio(r: float, curve: str = "linear") -> float:
    """渐变速度曲线。r∈[0,1] 线性进度 → 实际完成比例。
    linear   匀速
    ease_out 先快后慢 (收尾轻柔,适合推子拉下)
    ease_in  先慢后快 (蓄力起推)
    smooth   S 形缓入缓出
    """
    r = min(max(r, 0.0), 1.0)
    if curve == "ease_out":
        return 1.0 - (1.0 - r) ** 2
    if curve == "ease_in":
        return r ** 2
    if curve == "smooth":
        return r * r * (3.0 - 2.0 * r)
    return r


FADE_ACT_KINDS = ("fade_all", "fade_odd_even", "fade_ch", "fade_bus",
                  "fade_from", "fade_from_bus", "rel_db")


def _act_duration(act):
    """动作占用时间轴的秒数; 瞬时动作(静音/场景)为 0。"""
    k = act.get("kind")
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
            return "切场景 %d" % int(act.get("scene", 1))
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
    except Exception:
        pass
    return str(k)


def _fade_targets_for(act):
    """渐变动作 → [(kind, idx, to_v)]; 非渐变返回 None。起点在触发瞬间取当前值。"""
    k = act.get("kind")
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
        return [("ch_fader", int(i), float(act.get("to", 0.8)))
                for i in (act.get("chs") or [act.get("ch", 1)])]
    if k == "fade_bus":
        return [("bus_fader", int(i), float(act.get("to", 0.8)))
                for i in (act.get("buses") or [act.get("bus", 1)])]
    if k == "fade_from":
        return [("ch_fader", int(act.get("ch", 1)), float(act.get("to", 0.9)))]
    if k == "fade_from_bus":
        return [("bus_fader", int(act.get("bus", 1)), float(act.get("to", 0.9)))]
    if k == "rel_db":
        grp = act.get("group", "ch")
        fk = {"ch": "ch_fader", "bus": "bus_fader", "dca": "dca_fader"}[grp]
        key = {"ch": "chs", "bus": "buses", "dca": "dcas"}[grp]
        delta = float(act.get("delta_db", 3.0))
        out = []
        for i in (act.get(key) or [1]):
            cur_db = STATE.fader_to_db(get_value(fk, int(i)))
            out.append((fk, int(i), db_to_fader(min(max(cur_db + delta, -90.0), 10.0))))
        return out
    return None


def _exec_instant(act):
    """瞬时动作: 静音 / 静音Bus / 切场景 / 全部取消静音 (卡片写入, 不做冲突检测)。"""
    k = act["kind"]
    if k == "mute":
        chs = [int(c) for c in (act.get("chs") or [act.get("ch", 1)])]
        for c in chs:
            set_value("ch_on", c, 0, source="action")
        log("SHOW 静音: %s" % "、".join(target_label("ch", c) for c in chs))
    elif k == "mute_bus":
        buses = [int(b) for b in (act.get("buses") or [act.get("bus", 1)])]
        for b in buses:
            set_value("bus_on", b, 0, source="action")
        log("SHOW 静音: %s" % "、".join(target_label("bus", b) for b in buses))
    elif k == "scene":
        with STATE.lock:
            STATE.scene = int(act.get("scene", 1))
        log("SHOW 切换到场景 %d" % int(act.get("scene", 1)))
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


def _apply_fade(fade, ratio):
    for k, i, a, b in fade["starts"]:
        if _interrupted(k, i):
            continue
        set_value(k, i, a + (b - a) * ratio, source="action")


def run_show(card):
    """启动卡片 (时间轴调度器): 无 at 的动作顺序排布, 有 at 的按偏移并行;
    卡片 next 字段非空时执行完自动接续下一张 (防环, 链长上限 16)。"""
    threading.Thread(target=_show_worker, args=(card,), daemon=True).start()


def _show_worker(first_card):
    SHOW_STOP.clear()
    snapshot_done = False
    seen = set()
    cur = first_card
    depth = 0
    while cur is not None and not SHOW_STOP.is_set() and depth < 16:
        if cur["id"] in seen:
            log("SHOW 卡片链检测到循环, 停止接续")
            break
        seen.add(cur["id"])
        depth += 1
        if not snapshot_done:
            # 一键撤销: 执行前自动保存台面快照 (整条链只存一个点, 撤销即回到链开始前)
            try:
                save_snapshot("自动 · %s" % str(cur.get("name", "卡片"))[:20], auto=True)
                snapshot_done = True
            except Exception as e:
                log("SHOW 自动撤销点保存失败: %r" % e)
        _run_card_on_timeline(cur)
        if SHOW_STOP.is_set():
            break
        nid = str(cur.get("next") or "")
        nxt = next((c for c in SHOW_CARDS if c["id"] == nid), None) if nid else None
        if nxt:
            log("SHOW 卡片链: 「%s」完成 → 接续「%s」" % (cur.get("name"), nxt.get("name")))
            time.sleep(0.4)
        cur = nxt
    if depth >= 16:
        log("SHOW 卡片链超过 16 张, 停止接续")
    SHOW_STATE["running"] = None
    SHOW_STATE["progress"] = 1.0
    SHOW_STATE["step"] = ""


def _run_card_on_timeline(card):
    card_name = str(card.get("name", "卡片"))

    # All Live 自动混音演示: 推子随音乐节奏起伏 (电动推子玩具同款)
    if card["id"] == "alllive":
        duration = 12.0
        with STATE.lock:
            base = {i: STATE.ch[i]["fader"] for i in range(1, 9)}
        log("SHOW All Live 演示开始: 通道 01-08 推子自动起舞 %.0f 秒" % duration)
        demo_targets = [("ch_fader", i, 0, 0) for i in range(1, 9)]
        _register_fade(demo_targets, card_name)
        t0 = time.time()
        try:
            while not SHOW_STOP.is_set():
                t = time.time() - t0
                if t >= duration:
                    break
                time.sleep(0.08)
                for i in range(1, 9):
                    if _interrupted("ch_fader", i):
                        continue
                    v = base[i] + 0.20 * math.sin(t * 2.4 + i * 0.85) * math.sin(t * 0.7)
                    set_value("ch_fader", i, v, source="action")
                SHOW_STATE["progress"] = t / duration
            for i in range(1, 9):
                if not _interrupted("ch_fader", i):
                    set_value("ch_fader", i, base[i], source="action")
        finally:
            _unregister_fade(demo_targets)
        log("SHOW All Live 演示结束,推子已归位")
        return

    # 时间轴排布: 无 at 顺序排布, 有 at 并行
    actions = list(card.get("actions") or [])
    for lg in card.get("fades") or []:          # 旧 fades 结构兼容
        actions.append({"kind": "fade_from", "ch": lg["ch"], "frm": lg["from"],
                        "to": lg["to"], "duration": lg.get("duration", 3.0),
                        "curve": lg.get("curve", "linear")})
    timeline = []
    cursor = 0.0
    for n, act in enumerate(actions):
        at = act.get("at")
        try:
            start = float(at) if at is not None else cursor
        except (TypeError, ValueError):
            start = cursor
        dur = _act_duration(act)
        timeline.append({"n": n, "act": act, "start": start, "dur": dur})
        cursor = start + dur
    total = max([e["start"] + e["dur"] for e in timeline] + [0.001])

    SHOW_STATE["running"] = card["id"]
    SHOW_STATE["name"] = card.get("name", "")
    SHOW_STATE["progress"] = 0.0
    SHOW_STATE["step"] = ""
    log("SHOW 启动卡片: %s (%d 个动作, 时间轴 %.1f 秒)" % (card_name, len(actions), total))

    t0 = time.time()
    fired = set()
    active = {}            # n -> {"targets":..., "starts":...} 进行中的渐变
    TICK = 0.05
    try:
        while not SHOW_STOP.is_set():
            t = time.time() - t0
            for ev in timeline:
                n, act = ev["n"], ev["act"]
                if t < ev["start"]:
                    continue
                if n not in fired:
                    fired.add(n)
                    SHOW_STATE["step"] = "%d/%d · %s" % (n + 1, len(actions), _act_label(act))
                    if ev["dur"] <= 0.001:
                        _exec_instant(act)
                        continue
                    targets = _fade_targets_for(act)
                    if targets is None:
                        _exec_instant(act)
                        continue
                    if act["kind"] in ("fade_from", "fade_from_bus"):
                        fk, idx = targets[0][0], targets[0][1]
                        set_value(fk, idx, float(act.get("frm", 0.0)), source="action")
                    starts = [(k, i, get_value(k, i), b) for k, i, b in targets]
                    _register_fade([(k, i, 0, b) for k, i, b in targets], card_name)
                    active[n] = {"targets": [(k, i, 0, b) for k, i, b in targets],
                                 "starts": starts}
                    log("SHOW ▶ %s (%.1f 秒, 曲线 %s)" %
                        (_act_label(act), ev["dur"], act.get("curve", "linear")))
                if n in active:
                    if t >= ev["start"] + ev["dur"]:
                        _apply_fade(active[n], 1.0)
                        _unregister_fade(active[n]["targets"])
                        log("SHOW ✓ %s" % _act_label(act))
                        del active[n]
                    else:
                        ratio = curve_ratio((t - ev["start"]) / ev["dur"],
                                            act.get("curve", "linear"))
                        _apply_fade(active[n], ratio)
            if fired and not active and all(e["n"] in fired for e in timeline) \
                    and t >= total - TICK:
                break
            SHOW_STATE["progress"] = min(1.0, t / total)
            time.sleep(TICK)
        # 停止 (SHOW_STOP): 渐变就地冻结在当前值, 不写终值
    finally:
        for tg in active.values():
            _unregister_fade(tg["targets"])
        if SHOW_STATE["running"] == card["id"]:
            SHOW_STATE["running"] = None
            SHOW_STATE["progress"] = 1.0
            SHOW_STATE["step"] = ""


CURVES = ("linear", "ease_out", "ease_in", "smooth")


def _clean_action(a):
    """校验网页提交的动作, 只放行白名单类型与合法数值。"""
    if not isinstance(a, dict):
        return None
    k = a.get("kind")
    if k not in ("mute", "mute_bus", "scene", "unmute_all", "wait",
                 "fade_all", "fade_odd_even", "fade_ch", "fade_bus",
                 "fade_from", "fade_from_bus", "rel_db"):
        return None

    def num(key, default, lo, hi):
        try:
            v = float(a.get(key, default))
        except (TypeError, ValueError):
            v = float(default)
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
    if k == "mute":
        out["chs"] = ids("ch", "chs", 1, 32)
    elif k == "mute_bus":
        out["buses"] = ids("bus", "buses", 1, 16)
    elif k == "scene":
        out["scene"] = int(num("scene", 1, 1, 99))
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
        out.update(chs=ids("ch", "chs", 1, 32),
                   to=num("to", 0.8, 0.0, 1.0),
                   duration=num("duration", 3.0, 0.5, 30.0), curve=curve)
    elif k == "fade_bus":
        out.update(buses=ids("bus", "buses", 1, 16),
                   to=num("to", 0.8, 0.0, 1.0),
                   duration=num("duration", 3.0, 0.5, 30.0), curve=curve)
    elif k in ("fade_from", "fade_from_bus"):
        out.update(ch=int(num("ch", 1, 1, 32)) if k == "fade_from" else int(num("bus", 1, 1, 16)),
                   frm=num("frm", 0.0, 0.0, 1.0),
                   to=num("to", 0.9, 0.0, 1.0),
                   duration=num("duration", 5.0, 0.5, 30.0), curve=curve)
    elif k == "rel_db":
        # 相对增减 dB: 执行瞬间从当前推子位置换算 (+3dB 在高低端手感一致)
        grp = a.get("group") if a.get("group") in ("ch", "bus", "dca") else "ch"
        cap = {"ch": 32, "bus": 16, "dca": 8}[grp]
        key = {"ch": "chs", "bus": "buses", "dca": "dcas"}[grp]
        if grp == "dca":
            out_ids = ids("dca", "dcas", 1, 8)
        else:
            out_ids = ids("ch" if grp == "ch" else "bus", key, 1, cap)
        out.update(group=grp, **{key: out_ids},
                   delta_db=num("delta_db", 3.0, -30.0, 30.0),
                   duration=num("duration", 2.0, 0.5, 30.0), curve=curve)
    if a.get("at") is not None:
        # 时间轴偏移(秒): 给了 at 的动作并行执行, 缺省仍按顺序排布
        out["at"] = num("at", 0.0, 0.0, 600.0)
    return out


def snapshot_state(since_log=None):
    """聚合全量状态。since_log 给定时, log 字段只返回增量 (网页 250ms 轮询省带宽)。"""
    levels = compute_levels()
    with LOG_LOCK:
        ring = list(LOG_RING)
        seq = LOG_SEQ[0]
    if since_log is None or not isinstance(since_log, int):
        log_tail = ring[-40:]
    elif since_log >= seq:
        log_tail = []
    elif seq - since_log <= len(ring):
        log_tail = ring[len(ring) - (seq - since_log):]
    else:
        log_tail = ring[-40:]       # 落后太多, 直接给全量
    with STATE.lock:
        scene = STATE.scene
        names = {"main": STATE.names.get(("main", 0), "")}
        for g, cnt in NAME_COUNTS.items():
            names[g] = {str(i): STATE.names.get((g, i), "") for i in range(1, cnt + 1)}
    return {
        "ch": {str(i): STATE.ch[i] for i in range(1, 33)},
        "bus": {str(i): STATE.bus[i] for i in range(1, 17)},
        "dca": {str(i): STATE.dca[i] for i in range(1, 9)},
        "auxin": {str(i): STATE.auxin[i] for i in range(1, 7)},
        "usb": {str(i): STATE.usb[i] for i in range(1, 3)},
        "fxret": {str(i): STATE.fxret[i] for i in range(1, 9)},
        "mtx": {str(i): STATE.mtx[i] for i in range(1, 7)},
        "main": STATE.main,
        "meters": levels,
        "scene": scene,
        "cards": SHOW_CARDS,
        "show": dict(SHOW_STATE),
        "events": list(EVENTS)[-6:],
        "snapshots": [{"id": s["id"], "name": s["name"], "time": s["time"]} for s in SNAPSHOTS],
        "recall": dict(RECALL_STATE),
        "quickKeys": QUICK_KEYS,
        "keyCfg": dict(KEY_CFG),
        "names": names,
        "canUndo": len(AUTO_SNAPS) > 0,
        "subscribers": len(METER_SUBSCRIBERS),
        "log": log_tail,
        "logseq": seq,
        "bridge": {"on": BRIDGE["addr"] is not None,
                   "addr": ("%s:%d" % (BRIDGE["ip"], BRIDGE["port"])) if BRIDGE["addr"] else "",
                   "target": BRIDGE_TARGET,
                   "synced": BRIDGE["synced"],
                   "live": bool(BRIDGE["addr"] and time.time() - BRIDGE.get("last_recv", 0) < 8.0)},
        "schedules": [{"sid": s["sid"], "card": s["card_id"], "name": s["card_name"],
                       "runAt": s["run_at"], "mode": s["mode"]}
                      for s in list(SCHEDULES)],
    }


# ================================================================ Web 控制台

# PWA:安卓 Chrome「添加到主屏幕」后全屏运行,像原生 App 一样有图标
PWA_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<rect width="512" height="512" rx="96" fill="#14171c"/>
<g stroke-linecap="round" stroke-width="22">
<line x1="136" y1="120" x2="136" y2="392" stroke="#3a4150"/>
<line x1="256" y1="120" x2="256" y2="392" stroke="#3a4150"/>
<line x1="376" y1="120" x2="376" y2="392" stroke="#3a4150"/>
</g>
<g fill="#ffb454" stroke="none">
<rect x="106" y="150" width="60" height="56" rx="12"/>
<rect x="226" y="300" width="60" height="56" rx="12"/>
<rect x="346" y="200" width="60" height="56" rx="12"/>
</g></svg>"""

PWA_MANIFEST = json.dumps({
    "name": "X32 虚拟调音台",
    "short_name": "X32 调音台",
    "description": "Behringer X32/M32 虚拟调音台控制台",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#121419",
    "theme_color": "#121419",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ],
}, ensure_ascii=False)

# 网络优先、不缓存任何内容:控制台必须拿实时状态,SW 只为满足可安装性
PWA_SERVICE_WORKER = """self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
self.addEventListener('fetch', e => { /* 全部直连网络,控制台数据不允许离线缓存 */ });
"""

class WebHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # 静默 HTTP 访问日志

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", WEB_PAGE.encode("utf-8"))
        elif path == "/api/state":
            since = None
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "logseq" in qs:
                    since = int(qs["logseq"][0])
            except (TypeError, ValueError):
                since = None
            self._send(200, "application/json; charset=utf-8",
                       json.dumps(snapshot_state(since)).encode("utf-8"))
        elif path == "/api/info":
            self._send(200, "application/json; charset=utf-8", json.dumps({
                "lanIp": LAN_IP, "udpPort": UDP_PORT, "webPort": WEB_PORT,
                "lanUrl": "http://%s:%d" % (LAN_IP, WEB_PORT),
            }).encode("utf-8"))
        elif path == "/api/scan":
            # 局域网发现: 广播 /xinfo 找真台 (X32 官方发现指令), 无需手填 IP
            found = scan_consoles()
            log("SCAN 扫描完成: 发现 %d 台设备" % len(found))
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"ok": True, "found": found}).encode("utf-8"))
        elif path == "/api/export":
            # 配置导出: 卡片/控制键/命名/设置 一个 JSON 文件
            cfg = {"version": 1, "exportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "quickKeys": QUICK_KEYS, "cards": SHOW_CARDS, "keyCfg": KEY_CFG,
                   "idSeq": ID_SEQ[0],
                   "names": {"%s|%d" % k: v for k, v in STATE.names.items()}}
            body = json.dumps(cfg, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             "attachment; filename=\"x32_config_%s.json\""
                             % time.strftime("%Y%m%d_%H%M"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/manifest.webmanifest":
            self._send(200, "application/manifest+json; charset=utf-8",
                       PWA_MANIFEST.encode("utf-8"))
        elif path == "/icon.svg":
            self._send(200, "image/svg+xml; charset=utf-8",
                       PWA_ICON.encode("utf-8"))
        elif path == "/sw.js":
            self._send(200, "application/javascript; charset=utf-8",
                       PWA_SERVICE_WORKER.encode("utf-8"))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found".encode())

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            self._send(400, "text/plain; charset=utf-8", b"bad json")
            return

        if path == "/api/set":
            k = SET_KINDMAP.get(data.get("kind"), {}).get(data.get("param"))
            if not k:
                self._send(400, "text/plain; charset=utf-8", b"bad kind")
                return
            set_value(k, int(data.get("idx", 0)), data.get("value"))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/setbatch":
            # 批量写入 (网页拖动 50ms 合并发送, 省请求数)
            sets = data.get("sets")
            if not isinstance(sets, list) or not sets or len(sets) > 128:
                self._send(400, "text/plain; charset=utf-8", b"bad sets")
                return
            n = 0
            for it in sets:
                if not isinstance(it, dict):
                    continue
                k = SET_KINDMAP.get(it.get("kind"), {}).get(it.get("param"))
                if not k:
                    continue
                try:
                    set_value(k, int(it.get("idx", 0)), it.get("value"))
                    n += 1
                except (TypeError, ValueError):
                    pass
            self._send(200, "application/json",
                       json.dumps({"ok": True, "applied": n}).encode())

        elif path == "/api/key":
            # 自定义控制键: 按步长调整目标推子
            r = apply_key(str(data.get("id", "")))
            if not r:
                self._send(404, "text/plain; charset=utf-8", b"no such key")
                return
            self._send(200, "application/json",
                       json.dumps({"ok": True, **r}).encode())

        elif path == "/api/keycfg":
            # 步长 / 渐变速度设置
            if "step_db" in data:
                KEY_CFG["step_db"] = min(max(float(data["step_db"]), 0.5), 12.0)
            if "ramp" in data:
                KEY_CFG["ramp"] = min(max(float(data["ramp"]), 0.0), 5.0)
            log("KEYCFG 步长 %.1f dB, 渐变 %.1f 秒" % (KEY_CFG["step_db"], KEY_CFG["ramp"]))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/console":
            # 数据读取与调用流程: 空台子 / 回传数据
            act = data.get("action")
            if act == "clear":
                clear_console()
                self._send(200, "application/json", b'{"ok":true}')
            elif act == "recall_last":
                snap = recall_last()
                if not snap:
                    self._send(404, "text/plain; charset=utf-8", b"no snapshot")
                    return
                self._send(200, "application/json",
                           json.dumps({"ok": True, "id": snap["id"]}).encode())
            else:
                self._send(400, "text/plain; charset=utf-8", b"bad action")

        elif path == "/api/name":
            # 网页上改通道/输出名称 (与 OSC /ch/01/config/name 等价, 重启不丢)
            g = str(data.get("group", ""))
            try:
                idx = int(data.get("idx", 0))
            except (TypeError, ValueError):
                idx = 0
            cnt = NAME_COUNTS.get(g)
            ok = (g == "main" and idx == 0) or (cnt and 1 <= idx <= cnt)
            if not ok:
                self._send(400, "text/plain; charset=utf-8", b"bad target")
                return
            set_name(g, idx, str(data.get("name", "")))
            save_user_config()
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/undo":
            # 一键撤销: 回滚到最近一次卡片执行前的台面 (静音立即恢复, 推子 2 秒滑回)
            if not AUTO_SNAPS:
                self._send(404, "application/json", b'{"ok":false,"error":"no undo point"}')
                return
            snap = AUTO_SNAPS.pop()
            SHOW_STOP.set()          # 正在跑的卡片先停
            start_recall(snap, 2.0)
            log("SHOW 撤销上一次执行: 回滚到 %s" % snap["name"])
            self._send(200, "application/json",
                       json.dumps({"ok": True, "name": snap["name"]}).encode())

        elif path == "/api/show":
            sid = data.get("id")
            if sid is None:
                SHOW_STOP.set()
                SHOW_STATE["running"] = None
                SHOW_STATE["step"] = ""
                log("SHOW 手动停止")
                self._send(200, "application/json", b'{"ok":true,"stopped":true}')
                return
            card = next((c for c in SHOW_CARDS if c["id"] == sid), None)
            if not card:
                self._send(404, "text/plain; charset=utf-8", b"no such card")
                return
            SHOW_STOP.set()      # 若有正在执行的卡片,先停掉
            run_show(card)
            log("SHOW 启动卡片: %s" % card["name"])
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/schedule":
            # 卡片定时触发: mode='delay' delay 秒后; mode='clock' time=HH:MM
            card = next((c for c in SHOW_CARDS if c["id"] == data.get("id")), None)
            if not card:
                self._send(404, "application/json", b'{"ok":false,"error":"no such card"}')
                return
            mode = data.get("mode", "delay")
            try:
                if mode == "clock":
                    hh, mm = [int(x) for x in str(data.get("time", "20:00")).split(":")[:2]]
                    if not (0 <= hh < 24 and 0 <= mm < 60):
                        raise ValueError
                    schedule_card(card, "clock", clock=data.get("time"))
                else:
                    mode = "delay"
                    schedule_card(card, "delay", delay=float(data.get("delay", 10)))
            except (TypeError, ValueError):
                self._send(400, "application/json", b'{"ok":false,"error":"bad time"}')
                return
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/schedcancel":
            ok = cancel_schedule(int(data.get("sid", 0)))
            self._send(200, "application/json",
                       json.dumps({"ok": ok}).encode())

        elif path == "/api/bridge":
            # 桥接真台开关: addr='IP:PORT' 启动; addr='' 断开
            global BRIDGE_TARGET
            addr = str(data.get("addr", "")).strip()
            if not addr:
                bridge_stop()
                if BRIDGE_TARGET:          # 用户主动断开 → 不再自动重连
                    BRIDGE_TARGET = ""
                    save_user_config()
                self._send(200, "application/json", b'{"ok":true,"stopped":true}')
                return
            ok, msg = bridge_start(addr)
            if ok:
                ip, p = addr.rsplit(":", 1)
                BRIDGE_TARGET = "%s:%d" % (ip, int(p))
                save_user_config()
                log("BRIDGE 已记住真台地址 %s (下次启动自动重连)" % BRIDGE_TARGET)
            self._send(200 if ok else 400, "application/json",
                       json.dumps({"ok": ok, "msg": msg}).encode("utf-8"))

        elif path == "/api/import":
            # 配置导入: 覆盖卡片/控制键/命名/设置 (动作逐条清洗, 防脏数据)
            cfg = data.get("config")
            if not isinstance(cfg, dict):
                self._send(400, "application/json", b'{"ok":false,"error":"bad config"}')
                return
            cards = cfg.get("cards")
            keys = cfg.get("quickKeys")
            if not isinstance(cards, list) or not isinstance(keys, list):
                self._send(400, "application/json",
                           b'{"ok":false,"error":"config missing cards/quickKeys"}')
                return
            clean_cards = []
            for c in cards:
                if not isinstance(c, dict) or not str(c.get("name", "")).strip():
                    continue
                acts = [a for a in (_clean_action(x) for x in c.get("actions", [])
                                    if isinstance(x, dict)) if a]
                clean_cards.append({"id": str(c.get("id") or next_id("c")),
                                    "name": str(c.get("name")).strip(),
                                    "desc": str(c.get("desc", "")).strip() or "导入的演出卡片",
                                    "actions": acts, "fades": [],
                                    "pin": 1 if c.get("pin") else 0,
                                    "next": str(c.get("next") or "")})
            clean_keys = []
            kindmap = {"ch_fader", "bus_fader", "dca_fader", "main_fader",
                       "auxin_fader", "usb_fader", "fxret_fader", "mtx_fader"}
            for k in keys:
                if (isinstance(k, dict) and k.get("kind") in kindmap
                        and str(k.get("name", "")).strip()):
                    clean_keys.append({"id": str(k.get("id") or next_id("k")),
                                       "name": str(k["name"]).strip(),
                                       "icon": str(k.get("icon", "⚡"))[:4] or "⚡",
                                       "kind": k["kind"],
                                       "idx": min(max(int(k.get("idx", 1) or 1),
                                                      1 if k["kind"] != "main_fader" else 0), 32),
                                       "sign": -1 if int(k.get("sign", 1) or 1) < 0 else 1})
            keycfg = cfg.get("keyCfg") if isinstance(cfg.get("keyCfg"), dict) else {}
            QUICK_KEYS[:] = clean_keys
            SHOW_CARDS[:] = clean_cards
            KEY_CFG.update({"step_db": float(keycfg.get("step_db", KEY_CFG["step_db"])),
                            "ramp": float(keycfg.get("ramp", KEY_CFG["ramp"]))})
            if isinstance(cfg.get("idSeq"), int):
                ID_SEQ[0] = max(ID_SEQ[0], cfg["idSeq"])
            if isinstance(cfg.get("names"), dict):
                for kk, v in cfg["names"].items():
                    try:
                        g, i = kk.split("|")
                        STATE.names[(g, int(i))] = str(v)
                    except (ValueError, TypeError):
                        pass
            save_user_config()
            log("CONFIG 导入完成: %d 张卡片, %d 个控制键" % (len(clean_cards), len(clean_keys)))
            EVT_SEQ[0] += 1
            EVENTS.append({"id": EVT_SEQ[0], "time": time.strftime("%H:%M:%S"),
                           "text": "📦 配置导入完成 — %d 张卡片 / %d 个控制键" % (
                               len(clean_cards), len(clean_keys))})
            self._send(200, "application/json",
                       json.dumps({"ok": True, "cards": len(clean_cards),
                                   "keys": len(clean_keys)}).encode())

        elif path == "/api/keyadd":
            # 网页上新增自定义控制键
            kindmap = {"ch": "ch_fader", "bus": "bus_fader", "dca": "dca_fader",
                       "main": "main_fader", "auxin": "auxin_fader", "usb": "usb_fader",
                       "fxret": "fxret_fader", "mtx": "mtx_fader"}
            kind = kindmap.get(data.get("kind"))
            if not kind:
                self._send(400, "text/plain; charset=utf-8", "bad kind".encode())
                return
            try:
                idx = int(data.get("idx", 1))
            except (TypeError, ValueError):
                idx = 1
            idx = min(max(idx, 1 if kind != "main_fader" else 0), 32)
            QUICK_KEYS.append({
                "id": next_id("k"),
                "name": str(data.get("name", "")).strip() or "新按键",
                "icon": str(data.get("icon", "")).strip() or "⚡",
                "kind": kind, "idx": idx,
                "sign": -1 if int(data.get("sign", 1)) < 0 else 1,
            })
            save_user_config()
            log("KEY 新增控制键: %s" % QUICK_KEYS[-1]["name"])
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/keydel":
            QUICK_KEYS[:] = [k for k in QUICK_KEYS if k["id"] != data.get("id")]
            save_user_config()
            log("KEY 删除控制键: %s" % data.get("id"))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/keyedit":
            # 网页上编辑已有控制键
            target = next((k for k in QUICK_KEYS if k["id"] == data.get("id")), None)
            if not target:
                self._send(404, "application/json", b'{"ok":false,"error":"key not found"}')
                return
            kindmap = {"ch": "ch_fader", "bus": "bus_fader", "dca": "dca_fader",
                       "main": "main_fader", "auxin": "auxin_fader", "usb": "usb_fader",
                       "fxret": "fxret_fader", "mtx": "mtx_fader"}
            kind = kindmap.get(data.get("kind"))
            if kind:
                target["kind"] = kind
            try:
                idx = int(data.get("idx", target["idx"]))
            except (TypeError, ValueError):
                idx = target["idx"]
            target["idx"] = min(max(idx, 1 if target["kind"] != "main_fader" else 0), 32)
            name = str(data.get("name", "")).strip()
            if name:
                target["name"] = name
            icon = str(data.get("icon", "")).strip()
            if icon:
                target["icon"] = icon
            try:
                sign = int(data.get("sign", target["sign"]))
            except (TypeError, ValueError):
                sign = target["sign"]
            target["sign"] = -1 if sign < 0 else 1
            save_user_config()
            log("KEY 编辑控制键: %s" % target["name"])
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/cardedit":
            # 网页上编辑已有演出卡片
            target = next((c for c in SHOW_CARDS if c["id"] == data.get("id")), None)
            if not target:
                self._send(404, "application/json", b'{"ok":false,"error":"card not found"}')
                return
            card = data.get("card") if isinstance(data.get("card"), dict) else {}
            new_name = str(card.get("name", "")).strip()
            if new_name:
                target["name"] = new_name
            if "desc" in card:
                target["desc"] = str(card.get("desc", "")).strip() or "自定义演出卡片"
            if isinstance(card.get("actions"), list) and card["actions"]:
                actions = [c for c in (_clean_action(a) for a in card["actions"]) if c]
                if actions:
                    target["actions"] = actions
            if "pin" in card:
                target["pin"] = 1 if card.get("pin") else 0
            if "next" in card:      # 执行完自动接续的下一张卡片 (空=不接续)
                nid = str(card.get("next") or "").strip()
                target["next"] = nid if nid and any(c["id"] == nid for c in SHOW_CARDS) else ""
            save_user_config()
            log("SHOW 编辑卡片: %s" % target["name"])
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/cardpin":
            # 置顶/取消置顶
            target = next((c for c in SHOW_CARDS if c["id"] == data.get("id")), None)
            if not target:
                self._send(404, "application/json", b'{"ok":false,"error":"card not found"}')
                return
            target["pin"] = 0 if target.get("pin") else 1
            save_user_config()
            log("SHOW %s: %s" % ("置顶卡片" if target.get("pin") else "取消置顶", target["name"]))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/cardadd":
            # 网页上新建演出卡片
            card = data.get("card") if isinstance(data.get("card"), dict) else {}
            actions = []
            for a in card.get("actions", []):
                c = _clean_action(a)
                if c:
                    actions.append(c)
            SHOW_CARDS.append({
                "id": next_id("c"),
                "name": str(card.get("name", "")).strip() or "🎬 新卡片",
                "desc": str(card.get("desc", "")).strip() or "自定义演出卡片",
                "actions": actions, "fades": [],
                "pin": 1 if card.get("pin") else 0,
                "next": "",
            })
            save_user_config()
            log("SHOW 新建卡片: %s (%d 个动作)" % (SHOW_CARDS[-1]["name"], len(actions)))
            self._send(200, "application/json",
                       json.dumps({"ok": True, "id": SHOW_CARDS[-1]["id"]}).encode())

        elif path == "/api/carddel":
            SHOW_CARDS[:] = [c for c in SHOW_CARDS if c["id"] != data.get("id")]
            for s in [x for x in list(SCHEDULES) if x["card_id"] == data.get("id")]:
                cancel_schedule(s["sid"])
            save_user_config()
            log("SHOW 删除卡片: %s" % data.get("id"))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/snapshot":
            act = data.get("action")
            if act == "save":
                snap = save_snapshot(data.get("name"))
                self._send(200, "application/json",
                           json.dumps({"ok": True, "id": snap["id"]}).encode())
            elif act == "recall":
                snap = recall_snapshot(int(data.get("id", 0)), data.get("seconds", 3.0))
                if not snap:
                    self._send(404, "text/plain; charset=utf-8", b"no such snapshot")
                    return
                self._send(200, "application/json", b'{"ok":true}')
            elif act == "delete":
                SNAPSHOTS[:] = [s for s in SNAPSHOTS if s["id"] != int(data.get("id", 0))]
                self._send(200, "application/json", b'{"ok":true}')
            elif act == "stop":
                RECALL_STOP.set()
                RECALL_STATE["running"] = None
                log("SNAPSHOT 手动停止回归")
                self._send(200, "application/json", b'{"ok":true}')
            else:
                self._send(400, "text/plain; charset=utf-8", b"bad action")
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")


WEB_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="theme-color" content="#121419">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="X32 调音台">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="apple-touch-icon" href="/icon.svg">
<title>X32 模拟器 · 控制台</title>
<style>
  :root{--bg:#121419;--card:#1c2029;--card2:#232833;--txt:#e9ebf0;--sub:#8d93a0;
        --line:#2b303b;--accent:#ffb454;--accent2:#4da3ff;
        --green:#3ecf8e;--red:#ff6b6b;--track:#0d0f13}
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{overscroll-behavior-y:contain}
  body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
       background:var(--bg);color:var(--txt);padding-bottom:70px;
       -webkit-tap-highlight-color:transparent;touch-action:manipulation}
  button,.mute,input[type=range],.tile{touch-action:manipulation}
  ::selection{background:var(--accent);color:#141414}
  @media (max-width:768px){
    .mute{width:44px;height:44px}
    .row input[type=range]{height:38px}
    nav button{padding:11px 0 9px;font-size:12px}
    nav .ic{font-size:21px}
  }
  header{position:sticky;top:0;z-index:9;
         background:linear-gradient(180deg,#191d24,#14171d);color:#fff;
         padding:13px 18px;font-size:15px;font-weight:700;letter-spacing:.04em;
         display:flex;align-items:center;gap:10px;
         border-bottom:1px solid var(--line)}
  header .dot{width:9px;height:9px;border-radius:50%;background:var(--green);
              box-shadow:0 0 8px var(--green);animation:pulse 1.6s infinite}
  @keyframes pulse{50%{opacity:.4}}
  header small{font-weight:400;opacity:.65;margin-left:auto;font-size:12px;
               font-variant-numeric:tabular-nums}
  nav{position:fixed;bottom:0;left:0;right:0;display:flex;z-index:10;
      background:rgba(18,20,25,.92);backdrop-filter:blur(12px);
      border-top:1px solid var(--line)}
  nav button{flex:1;border:0;background:none;padding:9px 0 8px;font-size:11px;
             color:var(--sub);position:relative}
  nav button.on{color:var(--accent);font-weight:700}
  nav button.on::before{content:"";position:absolute;top:0;left:50%;transform:translateX(-50%);
                        width:26px;height:3px;border-radius:0 0 3px 3px;background:var(--accent)}
  nav .ic{display:block;font-size:19px;margin-bottom:2px}
  .page{display:none;padding:14px 14px 20px;max-width:760px;margin:0 auto}
  .page.on{display:block;animation:pagein .18s ease-out}
  @keyframes pagein{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  h3{display:flex;align-items:center;gap:10px;
     font-size:12px;color:var(--sub);margin:20px 2px 10px;font-weight:700;
     letter-spacing:.12em;text-transform:uppercase}
  h3::after{content:"";flex:1;height:1px;background:var(--line)}
  .ds{font-size:12px;color:var(--sub);line-height:1.6}
  .nkin{padding:9px 11px;border-radius:9px;border:1px solid var(--line);font-size:13px;
        background:var(--card2);color:var(--txt)}
  .nkin::placeholder{color:#5b6170}
  select{background:var(--card2);color:var(--txt)}
  /* ---- 卡片瓷砖 ---- */
  .tilegrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:9px;margin-bottom:6px}
  .tile{background:linear-gradient(180deg,var(--card2),var(--card));
        border:1px solid var(--line);border-radius:13px;padding:13px 6px 11px;
        text-align:center;font-size:14px;font-weight:700;cursor:pointer;user-select:none;
        min-height:62px;display:flex;flex-direction:column;align-items:center;
        justify-content:center;gap:4px;
        box-shadow:0 2px 8px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.05);
        transition:transform .12s cubic-bezier(.22,1,.36,1),border-color .15s}
  .tile small{font-weight:500;color:var(--sub);font-size:11px}
  .tile:active{transform:scale(.94)}
  .tile.off{background:linear-gradient(180deg,#3a2426,#2c1c1e);border-color:#5c3236;color:var(--red)}
  .tile.off small{color:#c98084}
  .tile.run{background:linear-gradient(180deg,#33291a,#282017);border-color:#6b5426;color:var(--accent)}
  .tile.run small{color:#c7a25e}
  .tile.add{border-style:dashed;border-width:1.5px;color:var(--sub);opacity:.8;font-size:24px}
  .tile.add small{font-weight:400;font-size:11px}
  .tile.add:active{transform:scale(.94)}
  /* ---- 名称 / 多选芯片 / 实时值 / 撤销 ---- */
  .chipgrid{display:flex;flex-wrap:wrap;gap:6px;width:100%;margin:2px 0;
            border-radius:10px;padding:2px}
  .chipgrid.inv{outline:1.5px solid var(--red);outline-offset:2px}
  .chip{padding:6px 9px;border-radius:8px;border:1px solid var(--line);background:var(--card2);
        font-size:12px;font-weight:700;cursor:pointer;user-select:none;color:var(--txt);
        display:inline-flex;gap:5px;align-items:baseline;transition:background .12s,border-color .12s}
  .chip em{font-style:normal;font-weight:400;font-size:10px;color:var(--sub)}
  .chip.on{background:rgba(255,180,84,.16);border-color:var(--accent);color:var(--accent)}
  .chip.on em{color:#c7a25e}
  .tile .live{font-size:11px;line-height:1.35;font-weight:500;color:var(--sub);
              display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .tile.undo{border-color:rgba(77,163,255,.5);color:var(--accent2)}
  .learnbtn{border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-size:12px;
            background:var(--card2);color:var(--accent);cursor:pointer;font-weight:700}
  .modal{display:none;position:fixed;inset:0;z-index:60;background:rgba(8,10,14,.72);
         backdrop-filter:blur(3px);align-items:flex-end;justify-content:center}
  .modal.on{display:flex}
  .mdlg{background:var(--card);border:1px solid var(--line);border-radius:18px 18px 0 0;
        width:100%;max-width:560px;max-height:88vh;overflow-y:auto;
        padding:18px 18px calc(18px + env(safe-area-inset-bottom));animation:mup .22s ease}
  @keyframes mup{from{transform:translateY(30px);opacity:0}}
  @media(min-width:700px){.modal{align-items:center}.mdlg{border-radius:16px}}
  .mhd{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .mhd b{font-size:16px}
  .mclose{border:0;background:var(--card2);color:var(--sub);width:32px;height:32px;
          border-radius:50%;font-size:15px;cursor:pointer;flex:none}
  .merr{display:none;background:rgba(255,107,107,.12);border:1px solid var(--red);
        color:var(--red);border-radius:10px;padding:8px 12px;font-size:13px;margin-top:10px}
  .merr.on{display:block}
  .btnspin{opacity:.55;pointer-events:none}
  .inv{border-color:var(--red)!important}
  .tile .bar{width:82%;margin-top:4px}
  /* ---- 推子行 ---- */
  .row{display:flex;align-items:center;gap:10px;background:var(--card);
       border:1px solid var(--line);border-radius:12px;padding:9px 12px;margin-bottom:8px;
       box-shadow:0 1px 4px rgba(0,0,0,.3)}
  .meter{width:9px;height:36px;border-radius:4px;background:var(--track);overflow:hidden;
         display:flex;align-items:flex-end;flex:none;border:1px solid #23272f}
  .meter i{display:block;width:100%;height:0%;
           background:linear-gradient(to top,#3ecf8e 0 62%,#ffb454 62% 85%,#ff6b6b 85%);
           transition:height .15s linear}
  .num{font-size:11px;font-weight:600;color:var(--sub);width:62px;flex:none;letter-spacing:.03em}
  .num b{display:block;font-size:15px;color:var(--txt);font-variant-numeric:tabular-nums}
  .num small{display:block;font-size:10px;font-weight:500;color:#c7a25e;
             white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .row input[type=range]{flex:1;-webkit-appearance:none;appearance:none;
                         background:transparent;height:30px;min-width:0}
  .row input[type=range]::-webkit-slider-runnable-track{height:7px;border-radius:4px;
    background:linear-gradient(90deg,#0d0f13,#31374a);
    box-shadow:inset 0 1px 3px rgba(0,0,0,.6)}
  .row input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:24px;height:32px;
    margin-top:-13px;border-radius:6px;
    background:linear-gradient(180deg,#3d4454,#22262f);
    border:1px solid #4d566b;
    box-shadow:0 3px 8px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.12)}
  .row input[type=range]::-moz-range-track{height:7px;border-radius:4px;
    background:linear-gradient(90deg,#0d0f13,#31374a)}
  .row input[type=range]::-moz-range-thumb{width:22px;height:30px;border-radius:6px;
    background:linear-gradient(180deg,#3d4454,#22262f);border:1px solid #4d566b;
    box-shadow:0 3px 8px rgba(0,0,0,.55)}
  .db{font-size:11px;color:var(--sub);width:56px;text-align:right;flex:none;
      font-variant-numeric:tabular-nums}
  .mute{width:32px;height:32px;border-radius:50%;border:1.5px solid var(--line);
        background:var(--card2);color:var(--sub);font-weight:800;font-size:13px;
        flex:none;cursor:pointer;transition:background .15s,border-color .15s}
  .mute.on{background:var(--red);border-color:var(--red);color:#fff;
           box-shadow:0 0 10px rgba(255,107,107,.4)}
  /* ---- 卡片 / 表单 ---- */
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
        padding:14px 16px;margin-bottom:10px;
        box-shadow:0 1px 4px rgba(0,0,0,.3)}
  .card .nm{font-size:15px;font-weight:700}
  .card .ds{font-size:12px;color:var(--sub);margin:5px 0 10px}
  .card button{border:0;border-radius:10px;padding:9px 18px;font-size:13px;
               font-weight:700;cursor:pointer;background:var(--accent);color:#17130c;
               transition:transform .12s cubic-bezier(.22,1,.36,1),filter .15s}
  .card button:active{transform:scale(.95)}
  .card button:hover{filter:brightness(1.08)}
  .card button.stop{background:var(--red);color:#fff}
  .keybtn{min-width:96px;min-height:48px;font-size:15px;border-radius:12px;
          border:0;background:var(--accent);color:#17130c;font-weight:800;cursor:pointer}
  .bar{height:5px;border-radius:3px;background:var(--track);margin-top:11px;overflow:hidden}
  .bar i{display:block;height:100%;width:0;
         background:linear-gradient(90deg,#c98a3a,var(--accent));
         transition:width .2s}
  .prog{font-size:11px;color:var(--sub);margin-top:5px}
  .infobox{background:var(--card);border:1px solid var(--line);border-radius:14px;
           padding:14px 16px;font-size:13px;line-height:2}
  .infobox b{color:var(--accent)}
  #logbox{background:#0b0d10;color:#8fd6a2;font:11px/1.7 Menlo,Consolas,monospace;
          border:1px solid var(--line);border-radius:14px;padding:12px 14px;
          height:300px;overflow-y:auto;white-space:pre-wrap}
  .scenechip{display:inline-block;background:rgba(255,180,84,.12);color:var(--accent);
             border:1px solid rgba(255,180,84,.3);
             border-radius:20px;padding:3px 12px;font-size:12px;font-weight:700;
             font-variant-numeric:tabular-nums}
  .toast{position:fixed;left:50%;bottom:84px;transform:translateX(-50%) translateY(14px);
         background:#241505;color:var(--accent);border:1px solid rgba(255,180,84,.55);
         border-radius:12px;padding:11px 18px;font-size:13px;font-weight:600;
         box-shadow:0 6px 22px rgba(0,0,0,.55);z-index:9999;opacity:0;
         transition:opacity .3s,transform .3s;max-width:86vw;pointer-events:none}
  .toast.in{opacity:1;transform:translateX(-50%) translateY(0)}
  .fadj{background:var(--card2);color:var(--sub);border:1px solid var(--line);
        border-radius:8px;min-width:26px;padding:6px 4px;font-size:13px;line-height:1;
        font-weight:700;flex:none}
  .fadj:active{background:var(--accent);color:#141414}
  @media (max-width:768px){ .fadj{min-width:30px;padding:9px 4px} }
  /* ---- 危险卡长按确认 ---- */
  .tile{position:relative}
  .card{position:relative}
  .holdbar{position:absolute;left:0;bottom:0;height:4px;width:0;
           background:var(--red);border-radius:3px;z-index:5;
           box-shadow:0 0 8px rgba(255,107,107,.6)}
  .readyok{color:#22c55e;font-weight:700}
  .readyn{color:var(--sub)}
  .schedrow{display:flex;gap:8px;align-items:center;background:rgba(255,180,84,.08);
            border:1px dashed rgba(255,180,84,.4);border-radius:12px;
            padding:8px 12px;margin-bottom:8px;font-size:13px;flex-wrap:wrap}
  .schedrow b{color:var(--accent)}
  .schedrow .cd{font-variant-numeric:tabular-nums;color:var(--sub)}
</style>
</head>
<body>
<header><span class="dot" id="conn"></span>X32 虚拟调音台
  <small id="hdinfo">连接中…</small></header>

<div class="page" id="pg-mix"><h3>🎚 输入通道 1-32 <span class="scenechip" id="scene">场景 1</span></h3>
  <div id="chlist"></div>
  <h3>Aux In 辅助输入 1-6</h3><div id="auxlist"></div>
  <h3>USB 播放返回 1-2</h3><div id="usblist"></div>
  <h3>FX Return 效果返回 1-8</h3><div id="fxretlist"></div>
  <h3>📤 输出 Bus 1-16 <span class="scenechip" id="scene2">场景 1</span></h3>
  <div id="buslist"></div>
  <h3>Matrix 矩阵输出 1-6</h3><div id="mtxlist"></div>
  <h3>主输出 Main LR</h3><div id="mainrow"></div>
  <h3>DCA 编组 1-8</h3><div id="dcalist"></div></div>

<div class="page on" id="pg-cards"><h3>🎬 演出卡片 (点按运行 · 实时显示台面值)</h3>
  <div class="ds">点卡片=执行,自动按顺序跑动作;执行前自动存撤销点,执行中再点一次停止。含「全体」类动作的危险卡需<b>长按 0.8 秒</b>执行。新卡片点最后的「➕ 添加」。</div>
  <div id="schedRow"></div>
  <div class="tilegrid" id="undoRow" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))"></div>
  <div class="tilegrid" id="tileCards"></div>
  <h3>📌 场景预设 (推子快照 · 点按回归)</h3>
  <div class="card"><div class="nm">保存当前台面为预设</div>
    <div class="ds">记录全部输入(32 通道 + Aux/USB/FX 返回) + 输出(Bus/Matrix) + DCA + 主输出的推子与静音;回归时静音立即恢复,所有推子像电动推子一样平滑滑回。</div>
    <button onclick="saveSnap()">💾 保存预设</button></div>
  <div id="snaplist"></div>
</div>

<div class="page" id="pg-keys"><h3>⚡ 控制键 (点按微调)</h3>
  <div class="ds">点按键=按步长(设置页可调)渐变增减目标推子,适合歌手耳返、主输出这类现场微调。新按键点最后的「➕ 添加」。</div>
  <div class="tilegrid" id="tileKeys"></div>
</div>

<div class="page" id="pg-set">
  <div class="infobox">
    UDP 端口 <b>10023</b> · 电平表订阅者 <b id="subs">0</b><br>
    手机 App:设置 → 调音台 IP 填本机局域网 IP,或用自动发现<br>
    本网页与 UDP 共享同一台"虚拟调音台",改动实时互通
  </div>
  <h3>📱 手机 / 安卓平板连接</h3>
  <div class="card">
    <div class="nm">手机连同一 WiFi,浏览器打开</div>
    <div class="ds" style="font-size:16px;font-weight:700;user-select:all" id="lanurl">…</div>
    <div class="ds">安卓 Chrome 菜单 → <b>添加到主屏幕</b>,即可像 App 一样全屏使用(图标自动生成)<br>
    其他设备(OSC 客户端)把调音台 IP 填为 <b id="lanip2">…</b>,端口 10023</div>
  </div>
  <h3>OSC 收发日志 (最近 40 条)</h3><div id="logbox"></div>
  <h3 id="sec-keycfg">🔧 控制键步长与渐变速度</h3>
  <div class="card">
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <label style="font-size:13px">步长
        <select id="stepSel" onchange="setKeyCfg()" style="padding:8px;border-radius:8px;border:1px solid var(--line);font-size:13px">
          <option value="1.5">1.5 dB</option><option value="3" selected>3 dB</option><option value="6">6 dB</option>
        </select></label>
      <label style="font-size:13px">渐变
        <select id="rampSel" onchange="setKeyCfg()" style="padding:8px;border-radius:8px;border:1px solid var(--line);font-size:13px">
          <option value="0">瞬间</option><option value="0.4" selected>0.4 秒</option><option value="1">1 秒</option><option value="2">2 秒</option>
        </select></label>
    </div>
  </div>
  <h3>🏷 通道命名 (调音台 / 卡片 / 控制键显示同一名称)</h3>
  <div class="card">
    <div class="ds">给通道起名字,卡片和推子行就会显示「音乐」「人声」而不是「通道15」。连真台后这些名字也可以从台子直接读取。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <select id="nmGroup" onchange="updNmIdx()" class="nkin">
        <option value="ch">输入通道</option><option value="auxin">Aux In</option>
        <option value="usb">USB 返回</option><option value="fxret">FX 返回</option>
        <option value="bus">Bus 输出</option><option value="mtx">Matrix</option>
        <option value="dca">DCA 编组</option><option value="main">主输出</option>
      </select>
      <select id="nmIdx" class="nkin"></select>
      <input id="nmVal" class="nkin" style="flex:1;min-width:120px" placeholder="名称(最长12字),留空=清除" maxlength="12">
      <button onclick="saveName()">💾 保存</button>
    </div>
  </div>
  <h3>📦 配置导入 / 导出 (卡片 · 控制键 · 通道命名 · 设置)</h3>
  <div class="card">
    <div class="ds">导出一个 JSON 备份文件;导入会覆盖现有的卡片、控制键、命名与步长设置(台面推子状态不受影响)。换手机、备份演出配置都用它。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button onclick="doExport()">📤 导出配置文件</button>
      <button style="background:#565d6d;color:#fff" onclick="$('#impFile').click()">📥 选择文件导入</button>
      <input type="file" id="impFile" accept=".json,application/json" style="display:none" onchange="impFileChg(this)">
    </div>
    <textarea id="impText" class="nkin" style="width:100%;min-height:64px;margin-top:8px;display:none"
      placeholder="或把配置 JSON 粘贴到这里"></textarea>
    <div id="impRow" style="display:none;margin-top:8px;gap:8px;align-items:center;flex-wrap:wrap">
      <button style="background:var(--green)" onclick="doImport()">✔ 确认导入</button>
      <span class="ds" style="margin:0">导入前请确认文件来源可信</span>
    </div>
  </div>
  <h3>🌉 桥接真台 (写入转发 · /xremote 保活 · 状态回读)</h3>
  <div class="card">
    <div class="ds">同一局域网内点「🔍 扫描」即可自动找到调音台,点一下就连上——手机端不用装任何东西。连接后:网页/卡片/控制键的写入自动转发到真台;真台面板上的手动改动回读同步到本页;卡片渐变中真台被人手动干预 → 只停该通道并通知。连过一台会被记住,下次开服务自动重连。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input id="brAddr" class="nkin" style="flex:1;min-width:170px" placeholder="真台地址,如 192.168.1.100:10023">
      <button onclick="brGo()">🔌 连接</button>
      <button style="background:#565d6d;color:#fff" onclick="api('/api/bridge',{addr:''});showToast('桥接已断开,已清除自动重连')">断开</button>
      <span class="scenechip" id="brStat">未桥接</span>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px">
      <button id="scanBtn" style="background:var(--accent);color:#141414" onclick="scanConsoles()">🔍 扫描局域网调音台</button>
      <span class="ds" style="margin:0" id="scanHint">同一网段直连即可,无需配置</span>
    </div>
    <div id="scanRes" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"></div>
  </div>
  <h3>🗑 管理 (编辑 / 删除卡片·控制键)</h3>
  <div id="cardlist"></div>
  <div id="keylist"></div>
  <h3>📭 数据读取与调用</h3>
  <div class="card"><div class="nm">调用预设 → 空台子 → 回传数据</div>
    <div class="ds">①「保存预设」记录台面 ②「空台子」全部推子拉底得到干净空台 ③「回传数据」把最近保存的预设平滑写回。<br>这套流程走标准 X32 OSC 指令,连真调音台同样适用。</div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button style="background:#565d6d;color:#fff" onclick="api('/api/console',{action:'clear'})">🧹 空台子</button>
      <button onclick="api('/api/console',{action:'recall_last'})">↩️ 回传数据</button>
    </div>
  </div>
</div>

<nav>
  <button class="on" data-p="cards"><span class="ic">🎬</span>卡片</button>
  <button data-p="keys"><span class="ic">⚡</span>控制键</button>
  <button data-p="mix"><span class="ic">🎚</span>调音台</button>
  <button data-p="set"><span class="ic">⚙️</span>设置</button>
</nav>

<div class="modal" id="cardModal" onclick="if(event.target===this)closeCardModal()">
  <div class="mdlg" role="dialog" aria-label="新建演出卡片">
    <div class="mhd"><b id="cardModalTitle">➕ 新建演出卡片</b>
      <button class="mclose" onclick="closeCardModal()" aria-label="关闭">✕</button></div>
    <div class="ds">把一组动作按顺序拼成一张卡片:先瞬时动作(静音/切场景),再渐变动作。保存后出现在「卡片」页卡片墙,重启也不丢。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0">
      <input id="ncName" placeholder="卡片名称 *必填,如:🎤 开场模式" style="flex:1;min-width:150px" class="nkin"
             oninput="this.classList.remove('inv');hideCardErr()">
      <input id="ncDesc" placeholder="一句话描述(可选)" style="flex:2;min-width:180px" class="nkin">
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <select id="caKind" onchange="updCaParams()" class="nkin">
        <option value="mute">静音通道 (可多选)</option><option value="mute_bus">静音 Bus (可多选)</option>
        <option value="scene">切换场景</option><option value="unmute_all">全部取消静音</option>
        <option value="wait">等待 / 停顿</option>
        <option value="fade_all">全体推子渐变</option><option value="fade_odd_even">奇偶过渡</option>
        <option value="fade_ch">通道渐变 (可多选)</option><option value="fade_bus">Bus 渐变 (可多选)</option>
        <option value="fade_from">通道从指定值渐变</option><option value="fade_from_bus">Bus 从指定值渐变</option>
        <option value="rel_db">相对增减 dB (可多选)</option>
      </select>
      <span id="caParams" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;width:100%"></span>
      <button onclick="addAct()">＋加入动作</button>
    </div>
    <div id="actChips" style="margin-top:8px"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
      <span style="font-size:12px;color:var(--sub)">执行完自动接续:</span>
      <select id="ncNext" class="nkin" style="flex:1"></select>
    </div>
    <div class="merr" id="cardErr"></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button style="background:var(--card2);color:var(--txt)" onclick="closeCardModal()">取消</button>
      <button id="cardSaveBtn" style="background:var(--green)" onclick="saveCard()">💾 保存卡片</button>
    </div>
  </div>
</div>

<div class="modal" id="keyModal" onclick="if(event.target===this)closeKeyModal()">
  <div class="mdlg" role="dialog" aria-label="新增控制键">
    <div class="mhd"><b id="keyModalTitle">➕ 新增控制键</b>
      <button class="mclose" onclick="closeKeyModal()" aria-label="关闭">✕</button></div>
    <div class="ds">选一个目标推子和方向,按键就按当前步长(设置页可调)渐变增减它。图标随便填个 emoji,留空则用 ⚡。</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0">
      <input id="nkIcon" placeholder="🎧 (可留空)" style="width:90px;text-align:center" class="nkin" maxlength="4">
      <input id="nkName" placeholder="名称 *必填,如:人声 增大" style="flex:1;min-width:150px" class="nkin"
             oninput="this.classList.remove('inv');hideKeyErr()">
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <select id="nkKind" onchange="updNkIdx()" class="nkin">
        <option value="ch">输入通道</option><option value="auxin">Aux In</option>
        <option value="usb">USB 返回</option><option value="fxret">FX 返回</option>
        <option value="bus">Bus 输出</option><option value="mtx">Matrix</option>
        <option value="dca">DCA 编组</option><option value="main">主输出</option>
      </select>
      <select id="nkIdx" class="nkin"></select>
      <select id="nkSign" class="nkin">
        <option value="1">＋ 增大</option><option value="-1">－ 减小</option>
      </select>
    </div>
    <div class="merr" id="keyErr"></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">
      <button style="background:var(--card2);color:var(--txt)" onclick="closeKeyModal()">取消</button>
      <button id="keySaveBtn" style="background:var(--green)" onclick="saveKey()">💾 添加控制键</button>
    </div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const dbOf = f => f<=0 ? -90 : (f>=0.75 ? (f-0.75)/0.25*10 : 20*Math.log10(f/0.75));
const dbTxt = db => db<=-89 ? "-∞" : db.toFixed(1);
let S = null;

// ---- 通道名称: 调音台 / 卡片 / 控制键统一显示 ----
const P2G={c:'ch',b:'bus',d:'dca',m:'main',a:'auxin',u:'usb',x:'fxret',t:'mtx'};
function nameOf(g,i){
  if(!S||!S.names) return '';
  return g==='main' ? (S.names.main||'') : ((S.names[g]||{})[String(i)]||'');
}
function dispName(g,i){
  const n=nameOf(g,i);
  if(n) return n;
  if(g==='main') return '主输出';
  if(g==='bus') return 'B'+i;
  if(g==='dca') return 'D'+i;
  return String(i).padStart(2,'0');
}

function rowHTML(prefix, i){
  const L={m:'<b>MAIN</b><small class="nm2"></small>', d:i=>`<b>D${i}</b><small class="nm2"></small>`, b:i=>`<b>B${i}</b><small class="nm2"></small>`,
           a:i=>`<b>A${i}</b><small class="nm2"></small>`, u:i=>`<b>U${i}</b><small class="nm2"></small>`, x:i=>`<b>FX${i}</b><small class="nm2"></small>`, t:i=>`<b>M${i}</b><small class="nm2"></small>`};
  const num = prefix==='m' ? L.m : (L[prefix] ? L[prefix](i) : `<b>${String(i).padStart(2,'0')}</b><small class="nm2"></small>`);
  return `<div class="row" id="r-${prefix}-${i}">
    <div class="meter"><i></i></div>
    <div class="num">${num}</div>
    <input type="range" min="0" max="1" step="0.01" value="0.75"
      oninput="drag('${prefix}',${i},this)" onchange="dragEnd('${prefix}',${i},this)"
      ondblclick="zeroFader('${prefix}',${i})" title="双击回 0 dB">
    <span class="db">0.0 dB</span>
    <button class="fadj" onclick="nudge('${prefix}',${i},-0.5)" title="-0.5 dB">−</button>
    <button class="fadj" onclick="nudge('${prefix}',${i},0.5)" title="+0.5 dB">＋</button>
    <button class="mute" onclick="toggle('${prefix}',${i},this)">M</button>
  </div>`;
}
// ±0.5 dB 精调 (与后端 fader_to_db 曲线一致) / 双击回 0 dB
function dbToFader(db){
  db=Math.min(Math.max(db,-90),10);
  if(db<=-89.5) return 0;
  if(db>=10) return 1;
  return db>=0 ? 0.75+db/10*0.25 : 0.75*Math.pow(10,db/20);
}
function nudge(p,i,d){
  const r=document.getElementById('r-'+p+'-'+i); if(!r) return;
  const sl=r.querySelector('input');
  const db=Math.min(Math.max(dbOf(+sl.value)+d,-90),10);
  const v=dbToFader(db);
  sl.value=v; sl.dataset.target=v; delete anim[r.id];
  r.querySelector('.db').textContent=(db<=-89.5?'-∞':db.toFixed(1))+' dB';
  send(p,i,'fader',+v.toFixed(4));
}
function zeroFader(p,i){
  const r=document.getElementById('r-'+p+'-'+i); if(!r) return;
  const sl=r.querySelector('input');
  sl.value=0.75; sl.dataset.target=0.75; delete anim[r.id];
  r.querySelector('.db').textContent='0.0 dB';
  send(p,i,'fader',0.75);
}

function init(){
  let h=''; for(let i=1;i<=32;i++) h+=rowHTML('c',i); $('#chlist').innerHTML=h;
  h='';  for(let i=1;i<=6;i++)  h+=rowHTML('a',i); $('#auxlist').innerHTML=h;
  h='';  for(let i=1;i<=2;i++)  h+=rowHTML('u',i); $('#usblist').innerHTML=h;
  h='';  for(let i=1;i<=8;i++)  h+=rowHTML('x',i); $('#fxretlist').innerHTML=h;
  h='';  for(let i=1;i<=16;i++)  h+=rowHTML('b',i); $('#buslist').innerHTML=h;
  h='';  for(let i=1;i<=6;i++)  h+=rowHTML('t',i); $('#mtxlist').innerHTML=h;
  h='';  for(let i=1;i<=8;i++)  h+=rowHTML('d',i); $('#dcalist').innerHTML=h;
  $('#mainrow').innerHTML=rowHTML('m',0);
  document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
    document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));
    document.querySelectorAll('.page').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); $('#pg-'+b.dataset.p).classList.add('on');
  });
  poll();
}

function drag(p,i,el){
  const r=el.closest('.row');
  const db=dbOf(+el.value);
  r.querySelector('.db').textContent=(+el.value<=0.001?'-∞':db.toFixed(1))+' dB';
  send(p,i,'fader',+el.value);
}
function dragEnd(){}
function toggle(p,i,el){
  const on = el.classList.contains('on') ? 1 : 0;
  send(p,i,'on',on);
}
// 写入节流: 拖动中 50ms 窗口合并, 多条走 /api/setbatch, 少请求量
const pend={}; let pendT=null;
function send(p,i,param,value){
  const KMAP={c:'ch',b:'bus',d:'dca',m:'main',a:'auxin',u:'usb',x:'fxret',t:'mtx'};
  const kind=KMAP[p]||'ch';
  pend[kind+'|'+i+'|'+param]={kind:kind,idx:i,param:param,value:value};
  if(!pendT) pendT=setTimeout(flushSend,50);
}
function flushSend(){
  pendT=null;
  const arr=Object.keys(pend).map(k=>pend[k]);
  for(const k in pend) delete pend[k];
  if(!arr.length) return;
  fetch(arr.length===1?'/api/set':'/api/setbatch',
    {method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify(arr.length===1?arr[0]:{sets:arr})});
}
function runShow(id){ fetch('/api/show',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})}); }
function stopShow(){ fetch('/api/show',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:null})}); }
function api(path,obj){ fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(obj)}); }
function saveSnap(){ api('/api/snapshot',{action:'save'}); }
function recallSnap(id,sel){ api('/api/snapshot',{action:'recall',id:id,seconds:+sel.value}); }
function delSnap(id){ api('/api/snapshot',{action:'delete',id:id}); }
function stopRecall(){ api('/api/snapshot',{action:'stop'}); }
function setKeyCfg(){ api('/api/keycfg',{step_db:+$('#stepSel').value,ramp:+$('#rampSel').value}); }

// 控制键长按连发: 按下立即触发一次, 按住 500ms 后每 150ms 重复
let kpT=null,kpI=null;
function kpStart(id){
  api('/api/key',{id:id});
  kpStop();
  kpT=setTimeout(()=>{ kpI=setInterval(()=>api('/api/key',{id:id}),150); },500);
}
function kpStop(){
  if(kpT){ clearTimeout(kpT); kpT=null; }
  if(kpI){ clearInterval(kpI); kpI=null; }
}
document.addEventListener('pointerup',kpStop);
document.addEventListener('pointercancel',kpStop);

// ---- 新增控制键 ----
function updNkIdx(){
  const k=$('#nkKind').value;
  const N={ch:32,bus:16,dca:8,auxin:6,usb:2,fxret:8,mtx:6,main:1}[k]||1;
  let h=''; for(let i=1;i<=N;i++) h+=`<option value="${i}">${dispName(k,i)}</option>`;
  $('#nkIdx').innerHTML=h;
}

// ---- 通道命名编辑器 (设置页) ----
const NM_COUNT={ch:32,bus:16,dca:8,auxin:6,usb:2,fxret:8,mtx:6};
function updNmIdx(){
  const g=$('#nmGroup').value,n=NM_COUNT[g];
  let h='';
  for(let i=1;i<=n;i++){
    const nm=nameOf(g,i);
    h+=`<option value="${i}">${String(i).padStart(2,'0')}${nm?' · '+nm:''}</option>`;
  }
  $('#nmIdx').innerHTML=h;
}
function saveName(){
  api('/api/name',{group:$('#nmGroup').value,idx:+$('#nmIdx').value,name:$('#nmVal').value.trim()});
  $('#nmVal').value='';
}
let editKeyId = null;
function saveKey(){
  const name=$('#nkName').value.trim();
  $('#nkName').classList.toggle('inv', !name);
  if(!name){ keyErr('请填写按键名称(必填)'); return; }
  const btn=$('#keySaveBtn');
  btn.classList.add('btnspin'); btn.textContent='保存中…';
  hideKeyErr();
  const url=editKeyId?'/api/keyedit':'/api/keyadd';
  const payload=editKeyId
    ?{id:editKeyId, icon:$('#nkIcon').value.trim()||'⚡', name:name,
      kind:$('#nkKind').value, idx:+$('#nkIdx').value, sign:+$('#nkSign').value}
    :{icon:$('#nkIcon').value.trim()||'⚡', name:name,
      kind:$('#nkKind').value, idx:+$('#nkIdx').value, sign:+$('#nkSign').value};
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)})
  .then(r=>{
    if(!r.ok) throw new Error('服务器错误 '+r.status);
    return r.json();
  })
  .then(d=>{
    if(!d || !d.ok) throw new Error('保存被拒绝');
    $('#nkName').value=''; $('#nkIcon').value='';
    closeKeyModal();
  })
  .catch(err=>keyErr('保存失败: '+(err&&err.message?err.message:'网络异常,请重试')))
  .finally(()=>{
    btn.classList.remove('btnspin'); btn.textContent='💾 添加控制键';
  });
}
function openKeyModal(key){
  editKeyId = key ? key.id : null;
  $('#keyModalTitle').textContent = key ? '✏️ 编辑控制键' : '➕ 新增控制键';
  $('#keySaveBtn').textContent = key ? '💾 保存修改' : '💾 添加控制键';
  $('#keyModal').classList.add('on');
  document.body.style.overflow='hidden';
  if(key){
    $('#nkIcon').value = key.icon || '';
    $('#nkName').value = key.name || '';
    $('#nkKind').value = String(key.kind || '').replace('_fader','');
    updNkIdx();
    $('#nkIdx').value = String(key.idx || 1);
    $('#nkSign').value = key.sign < 0 ? '-1' : '1';
  } else {
    $('#nkIcon').value=''; $('#nkName').value='';
    updNkIdx();
  }
  hideKeyErr();
  setTimeout(()=>$('#nkName').focus(),120);
}
function closeKeyModal(){
  $('#keyModal').classList.remove('on');
  document.body.style.overflow='';
  hideKeyErr();
  editKeyId = null;
  $('#nkName').classList.remove('inv');
}
function editCard(id){
  const c=(S.cards||[]).find(x=>x.id===id);
  if(c) openCardModal(c);
}
function editKey(id){
  const k=(S.quickKeys||[]).find(x=>x.id===id);
  if(k) openKeyModal(k);
}
function keyErr(msg){ const e=$('#keyErr'); e.textContent=msg; e.classList.add('on'); }
function hideKeyErr(){ $('#keyErr').classList.remove('on'); }
document.addEventListener('keydown',e=>{ if(e.key==='Escape'){ closeCardModal(); closeKeyModal(); } });
function delKey(id){ api('/api/keydel',{id:id}); }

// ---- 新建演出卡片 ----
let caActs=[];
let caMulti=[];                       // 当前动作类型下勾选的目标编号
const CA_MULTI={mute:'ch',mute_bus:'bus',fade_ch:'ch',fade_bus:'bus'};
const CA_LABEL={mute:'静音通道',mute_bus:'静音 Bus',scene:'切场景',unmute_all:'全部取消静音',wait:'等待',
  fade_all:'全体推子渐变',fade_odd_even:'奇偶过渡',fade_ch:'通道渐变',fade_bus:'Bus 渐变',
  fade_from:'通道从指定值渐变',fade_from_bus:'Bus 从指定值渐变',rel_db:'相对增减 dB'};
function numIn(id,label,def){
  return `<input id="${id}" type="number" value="${def}" placeholder="${label}" class="nkin" style="width:84px">`;
}
function curveSel(){
  return `<select id="caCurve" class="nkin"><option value="linear">直线</option><option value="ease_out">先快后慢</option><option value="ease_in">先慢后快</option><option value="smooth">S 形</option></select>`;
}
function chipGrid(g,n){
  let h='<div class="chipgrid" id="caChips">';
  for(let i=1;i<=n;i++){
    const nm=nameOf(g,i);
    h+=`<span class="chip" data-i="${i}" onclick="togChip(this)">${g==='ch'?String(i).padStart(2,'0'):'B'+i}${nm?'<em>'+nm+'</em>':''}</span>`;
  }
  return h+'</div>';
}
function togChip(el){
  el.classList.toggle('on');
  const i=+el.dataset.i;
  const p=caMulti.indexOf(i);
  if(p>=0) caMulti.splice(p,1); else caMulti.push(i);
  hideCardErr();
}
function updCaParams(){
  const k=$('#caKind').value;
  caMulti=[]; hideCardErr();
  let h='';
  if(CA_MULTI[k]) h+=chipGrid(CA_MULTI[k],CA_MULTI[k]==='ch'?32:16);
  if(k==='mute'||k==='mute_bus'){ $('#caParams').innerHTML=h; return; }
  if(k==='scene') h+=numIn('caScene','场景号',1);
  else if(k==='wait') h+=numIn('caSec','等待秒数',3);
  else if(k==='unmute_all'){ /* 无参数 */ }
  else if(k==='fade_all') h+=numIn('caP1','目标 %',0)+numIn('caSec','秒数',3)+curveSel();
  else if(k==='fade_odd_even') h+=numIn('caP1','奇数 %',80)+numIn('caP2','偶数 %',0)+numIn('caSec','秒数',4)+curveSel();
  else if(k==='fade_ch'||k==='fade_bus') h+=numIn('caP1','到 %',80)+numIn('caSec','秒数',3)+curveSel()
    +`<button type="button" class="learnbtn" onclick="learnTo()">⭐ 取当前值</button>`;
  else if(k==='fade_from') h+=numIn('caCh','通道号',15)+numIn('caP0','从 %',0)+numIn('caP1','到 %',90)+numIn('caSec','秒数',5)+curveSel();
  else if(k==='fade_from_bus') h+=numIn('caBus','Bus 号',1)+numIn('caP0','从 %',0)+numIn('caP1','到 %',90)+numIn('caSec','秒数',5)+curveSel();
  else if(k==='rel_db') h+=`<select id="caGrp" class="nkin" onchange="caMulti=[];updRelChips()">
      <option value="ch">输入通道</option><option value="bus">Bus 输出</option><option value="dca">DCA 编组</option></select>`
    +`<span id="caRelChips" style="display:inline-flex"></span>`
    +numIn('caDb','±dB',3)+numIn('caSec','秒数',2)+curveSel();
  $('#caParams').innerHTML=h;
  if(k==='rel_db') updRelChips();
}
function updRelChips(){
  const g=$('#caGrp')?$('#caGrp').value:'ch';
  $('#caRelChips').innerHTML=chipGrid(g,g==='ch'?32:g==='bus'?16:8);
}
function learnTo(){
  const k=$('#caKind').value;
  const g=CA_MULTI[k]; if(!g) return;
  if(caMulti.length!==1){ cardErr('「取当前值」需要先在列表里只勾选 1 个目标'); return; }
  const i=caMulti[0];
  const src=g==='ch'?(S.ch[i]||{}):(S.bus[i]||{});
  if(src.fader===undefined){ cardErr('暂时读不到当前值,请稍候'); return; }
  const el=document.getElementById('caP1');
  if(el) el.value=Math.round(src.fader*100);
  hideCardErr();
}
function pct(v){ return Math.min(Math.max(+(v||0),0),100)/100; }
function addAct(){
  const k=$('#caKind').value, a={kind:k};
  const g=id=>{const e=document.getElementById(id);return e?e.value:null;};
  if(k==='rel_db'){
    const grp=$('#caGrp')?$('#caGrp').value:'ch';
    if(!caMulti.length){
      const cg=$('#caChips');
      if(cg){ cg.classList.add('inv'); setTimeout(()=>cg.classList.remove('inv'),1200); }
      cardErr('请先勾选要增减的目标,再点「＋加入动作」');
      return;
    }
    a.group=grp;
    a[{ch:'chs',bus:'buses',dca:'dcas'}[grp]]=caMulti.slice().sort((x,y)=>x-y);
    a.delta_db=Math.min(Math.max(+g('caDb')||3,-30),30);
    a.duration=Math.max(+g('caSec')||2,0.5);
    a.curve=g('caCurve');
    caActs.push(a); renderActs(); return;
  }
  if(CA_MULTI[k]){
    if(!caMulti.length){
      const cg=$('#caChips');
      if(cg){ cg.classList.add('inv'); setTimeout(()=>cg.classList.remove('inv'),1200); }
      cardErr('请先在列表里勾选目标'+(k==='mute'||k==='fade_ch'?'通道':'Bus')+',再点「＋加入动作」');
      return;
    }
    const sel=caMulti.slice().sort((x,y)=>x-y);
    if(k==='mute') a.chs=sel;
    else if(k==='mute_bus') a.buses=sel;
    else if(k==='fade_ch'){ a.chs=sel; a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||3,0.5); a.curve=g('caCurve'); }
    else { a.buses=sel; a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||3,0.5); a.curve=g('caCurve'); }
  }
  else if(k==='scene') a.scene=+(g('caScene')||1);
  else if(k==='wait') a.duration=Math.max(+g('caSec')||3,0.5);
  else if(k==='unmute_all'){ /* 无参数 */ }
  else if(k==='fade_all'){ a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||3,0.5); a.curve=g('caCurve'); }
  else if(k==='fade_odd_even'){ a.odd_to=pct(g('caP1')); a.even_to=pct(g('caP2')); a.duration=Math.max(+g('caSec')||4,0.5); a.curve=g('caCurve'); }
  else if(k==='fade_from'){ a.ch=+(g('caCh')||15); a.frm=pct(g('caP0')); a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||5,0.5); a.curve=g('caCurve'); }
  else if(k==='fade_from_bus'){ a.bus=+(g('caBus')||1); a.frm=pct(g('caP0')); a.to=pct(g('caP1')); a.duration=Math.max(+g('caSec')||5,0.5); a.curve=g('caCurve'); }
  caActs.push(a); renderActs();
}
function actTxt(a){
  const cv={linear:'直线',ease_out:'先快后慢',ease_in:'先慢后快',smooth:'S 形'};
  const t=x=>Math.round(x*100)+'%';
  const lst=(g,arr)=>(arr||[]).map(i=>dispName(g,i)).join('、');
  if(a.kind==='mute') return '静音 '+lst('ch',a.chs||(a.ch?[a.ch]:[]));
  if(a.kind==='mute_bus') return '静音 '+lst('bus',a.buses||(a.bus?[a.bus]:[]));
  if(a.kind==='scene') return '切到场景 '+a.scene;
  if(a.kind==='unmute_all') return '全部取消静音';
  if(a.kind==='wait') return '等待 '+(+a.duration||1)+' 秒';
  if(a.kind==='fade_all') return `全体推子→${t(a.to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_odd_even') return `奇数→${t(a.odd_to)} 偶数→${t(a.even_to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_ch') return `${lst('ch',a.chs||(a.ch?[a.ch]:[]))} →${t(a.to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_bus') return `${lst('bus',a.buses||(a.bus?[a.bus]:[]))} →${t(a.to)} ${a.duration}秒 ${cv[a.curve]}`;
  if(a.kind==='fade_from') return `${dispName('ch',a.ch)} 从${t(a.frm)} 经${a.duration}秒→${t(a.to)} ${cv[a.curve]}`;
  if(a.kind==='fade_from_bus') return `${dispName('bus',a.bus)} 从${t(a.frm)} 经${a.duration}秒→${t(a.to)} ${cv[a.curve]}`;
  if(a.kind==='rel_db'){
    const g2=a.group||'ch';
    const ids=a[{ch:'chs',bus:'buses',dca:'dcas'}[g2]]||[];
    return `${lst(g2,ids)} ${a.delta_db>=0?'+':'−'}${Math.abs(+a.delta_db||0)}dB ${a.duration}秒 ${cv[a.curve]}`;
  }
  return '?';
}
function renderActs(){
  $('#actChips').innerHTML=caActs.map((a,i)=>
    `<span style="display:inline-flex;align-items:center;gap:6px;background:var(--card2);color:var(--txt);border:1px solid var(--line);border-radius:14px;padding:4px 10px;font-size:12px;margin:2px">
      ${i+1}. ${actTxt(a)} <b style="cursor:pointer;color:var(--red)" onclick="caActs.splice(${i},1);renderActs()">×</b></span>`).join('')
    || '<div class="ds" style="font-size:12px">还没有动作,选类型后点「＋加入动作」。</div>';
}
let editCardId = null;
function saveCard(){
  const name=$('#ncName').value.trim();
  $('#ncName').classList.toggle('inv', !name);
  if(!name){ cardErr('请填写卡片名称(必填)'); return; }
  if(!caActs.length){ cardErr('请至少加入一个动作,再保存卡片'); return; }
  const btn=$('#cardSaveBtn');
  btn.classList.add('btnspin'); btn.textContent='保存中…';
  hideCardErr();
  const url=editCardId?'/api/cardedit':'/api/cardadd';
  const cardData={name:name,desc:$('#ncDesc').value,actions:caActs,
                  next:($('#ncNext')?$('#ncNext').value:'')};
  const payload=editCardId
    ?{id:editCardId, card:cardData}
    :{card:cardData};
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)})
  .then(r=>{
    if(!r.ok) throw new Error('服务器错误 '+r.status);
    return r.json();
  })
  .then(d=>{
    if(!d || !d.ok) throw new Error('保存被拒绝');
    caActs=[]; renderActs(); $('#ncName').value=''; $('#ncDesc').value='';
    closeCardModal();
  })
  .catch(err=>cardErr('保存失败: '+(err&&err.message?err.message:'网络异常,请重试')))
  .finally(()=>{
    btn.classList.remove('btnspin'); btn.textContent='💾 保存卡片';
  });
}
function openCardModal(card){
  editCardId = card ? card.id : null;
  $('#cardModalTitle').textContent = card ? '✏️ 编辑演出卡片' : '➕ 新建演出卡片';
  $('#cardSaveBtn').textContent = card ? '💾 保存修改' : '💾 保存卡片';
  if(card){
    $('#ncName').value = card.name || '';
    $('#ncDesc').value = card.desc || '';
    caActs = JSON.parse(JSON.stringify(card.actions || []));
  }
  // 接续选择: 除自身外的所有卡片
  const nxt=$('#ncNext');
  nxt.innerHTML='<option value="">(无 · 执行完就停)</option>'
    +(S.cards||[]).filter(x=>x.id!==editCardId)
      .map(x=>`<option value="${x.id}">${x.name}</option>`).join('');
  nxt.value=(card&&card.next)||'';
  $('#cardModal').classList.add('on');
  document.body.style.overflow='hidden';
  updCaParams(); renderActs(); hideCardErr();
  setTimeout(()=>$('#ncName').focus(),120);
}
function closeCardModal(){
  $('#cardModal').classList.remove('on');
  document.body.style.overflow='';
  hideCardErr();
  if(editCardId){
    editCardId = null;
    caActs = []; renderActs();
    $('#ncName').value=''; $('#ncDesc').value='';
  }
  $('#ncName').classList.remove('inv');
}
function cardErr(msg){ const e=$('#cardErr'); e.textContent=msg; e.classList.add('on'); }
function hideCardErr(){ $('#cardErr').classList.remove('on'); }
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeCardModal(); });
function delCard(id){ api('/api/carddel',{id:id}); }
updNkIdx(); updCaParams(); renderActs(); updNmIdx();

// ---- 电动推子平滑动画: 显示值以缓动逼近目标值 ----
const anim = {};
function tick(){
  document.querySelectorAll('.row').forEach(r=>{
    const sl=r.querySelector('input');
    if(document.activeElement===sl) return;
    const t=parseFloat(sl.dataset.target);
    if(isNaN(t)) return;
    let c=anim[r.id];
    if(c===undefined) c=t;
    const d=t-c;
    if(Math.abs(d)>0.001){
      c += d*0.16;
      anim[r.id]=c;
      sl.value=c;
      r.querySelector('.db').textContent=(c<=0.001?'-∞':dbOf(c).toFixed(1))+' dB';
    } else {
      delete anim[r.id];
      sl.value=t;
    }
  });
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

// ---- 卡片实时值: 从台面状态生成一行摘要 (E1: 卡片显示实时参数) ----
function cardLive(c){
  if(!S) return '';
  const parts=[];
  for(const a of (c.actions||[])){
    if(a.kind==='mute'){
      const ids=a.chs||(a.ch?[a.ch]:[]);
      if(!ids.length) continue;
      const muted=ids.every(i=>S.ch[i]&&!S.ch[i].on);
      parts.push(ids.map(i=>dispName('ch',i)).join('、')+(muted?' 已静音':' 出声中'));
    } else if(a.kind==='mute_bus'){
      const ids=a.buses||(a.bus?[a.bus]:[]);
      if(!ids.length) continue;
      const muted=ids.every(i=>S.bus[i]&&!S.bus[i].on);
      parts.push(ids.map(i=>dispName('bus',i)).join('、')+(muted?' 已静音':' 出声中'));
    } else if(a.kind==='fade_ch'||a.kind==='fade_from'){
      const i=(a.chs||[a.ch])[0];
      if(!i) continue;
      const f=S.ch[i]?S.ch[i].fader:0;
      parts.push(dispName('ch',i)+' '+Math.round(f*100)+'%'+(a.to!==undefined?'→'+Math.round(a.to*100)+'%':''));
    } else if(a.kind==='fade_bus'||a.kind==='fade_from_bus'){
      const i=(a.buses||[a.bus])[0];
      if(!i) continue;
      const f=S.bus[i]?S.bus[i].fader:0;
      parts.push(dispName('bus',i)+' '+Math.round(f*100)+'%'+(a.to!==undefined?'→'+Math.round(a.to*100)+'%':''));
    } else if(a.kind==='wait'){
      parts.push('停 '+(+a.duration||1)+'s');
    } else if(a.kind==='rel_db'){
      const g2=a.group||'ch';
      const ids=a[{ch:'chs',bus:'buses',dca:'dcas'}[g2]]||[];
      if(!ids.length) continue;
      const f=S[g2]&&S[g2][ids[0]]?S[g2][ids[0]].fader:0;
      parts.push(ids.map(i=>dispName(g2,i)).join('、')+' '+Math.round(f*100)+'%'
        +(a.delta_db>=0?' +':' −')+Math.abs(+a.delta_db||0)+'dB');
    }
    if(parts.length>=2) break;
  }
  return parts.join(' · ');
}
function undoLast(){ api('/api/undo',{}); }

// ---- 危险卡防误触: 含全体类动作的卡片需长按 0.8 秒 (P2) ----
const DANGER=['unmute_all','fade_all','fade_odd_even'];
function isDanger(c){ return (c.actions||[]).some(a=>DANGER.includes(a.kind)); }
let holdRAF=null, holdEl=null, holdFired=false;
function holdStart(ev,id){
  const c=(S.cards||[]).find(x=>x.id===id);
  if(!c) return;
  if(!isDanger(c)){ runShow(id); return; }
  holdEl=ev.currentTarget; holdFired=false;
  ev.preventDefault();
  const bar=document.createElement('div'); bar.className='holdbar';
  holdEl.appendChild(bar);
  const t0=Date.now();
  const step=()=>{
    const p=Math.min(1,(Date.now()-t0)/800);
    bar.style.width=(p*100)+'%';
    if(p>=1){ holdFired=true; holdCancel(); runShow(id); return; }
    holdRAF=requestAnimationFrame(step);
  };
  holdRAF=requestAnimationFrame(step);
}
function holdCancel(){
  if(holdRAF){ cancelAnimationFrame(holdRAF); holdRAF=null; }
  if(holdEl){
    const b=holdEl.querySelector('.holdbar'); if(b) b.remove();
    if(!holdFired) showToast('⚠️ 危险卡片 — 需长按 0.8 秒确认执行');
    holdEl=null;
  }
}
document.addEventListener('pointerup',()=>holdCancel());

// ---- 卡片状态高亮: 动作目标已生效的计数 (P2) ----
function actSatisfied(a){
  const T=0.02;
  if(a.kind==='mute'){
    const ids=a.chs||(a.ch?[a.ch]:[]); if(!ids.length) return null;
    return ids.every(i=>S.ch[i]&&!S.ch[i].on);
  }
  if(a.kind==='mute_bus'){
    const ids=a.buses||(a.bus?[a.bus]:[]); if(!ids.length) return null;
    return ids.every(i=>S.bus[i]&&!S.bus[i].on);
  }
  if(a.kind==='scene') return S.scene===a.scene;
  if(a.kind==='unmute_all'){
    const all=x=>Object.values(x).every(v=>v.on);
    return all(S.ch)&&all(S.bus)&&all(S.dca)&&all(S.auxin)&&all(S.usb)&&all(S.fxret)&&all(S.mtx)&&S.main.on;
  }
  if(a.kind==='fade_ch'||a.kind==='fade_from'||a.kind==='fade_bus'||a.kind==='fade_from_bus'){
    const g=a.kind.indexOf('bus')>=0?'bus':'ch';
    const ids=a.chs||a.buses||(a.ch||a.bus?[a.ch||a.bus]:[]);
    if(!ids.length) return null;
    return ids.every(i=>S[g]&&S[g][i]&&Math.abs(S[g][i].fader-(a.to||0))<T);
  }
  if(a.kind==='rel_db') return null;
  return null;
}
function cardReady(c){
  let ok=0,tot=0;
  for(const a of (c.actions||[])){
    const s=actSatisfied(a);
    if(s!==null){ tot++; if(s) ok++; }
  }
  return tot?[ok,tot]:null;
}
function readyBadge(c){
  const r=cardReady(c);
  if(!r) return '';
  const [ok,tot]=r;
  return ok===tot
    ?` <span class="readyok">✔ ${ok}/${tot} 已就绪</span>`
    :` <span class="readyn">✔ ${ok}/${tot} 已生效</span>`;
}

// ---- 卡片定时触发 (P2) ----
function schedAsk(id){
  const v=prompt('定时启动卡片:\\n输入等待秒数(如 30)\\n或今天的时刻(如 20:30)','30');
  if(!v) return;
  const s=v.trim();
  let payload;
  if(/^[0-2]?\\d:[0-5]\\d$/.test(s)) payload={id:id,mode:'clock',time:s};
  else{
    const n=parseFloat(s);
    if(!(n>0)){ alert('格式不对:输入秒数或 HH:MM'); return; }
    payload={id:id,mode:'delay',delay:n};
  }
  fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)});
}
function fmtCd(sec){
  sec=Math.max(0,Math.round(sec));
  const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s2=sec%60;
  return (h?h+':':'')+String(m).padStart(2,'0')+':'+String(s2).padStart(2,'0');
}
function renderSched(){
  const el=$('#schedRow'); if(!el) return;
  const list=S.schedules||[];
  if(!list.length){ if(el._sig!==''){el._sig='';el.innerHTML='';} return; }
  const html=list.map(s=>{
    const cd=fmtCd(s.runAt-Date.now()/1000);
    const when=s.mode==='clock'?timeStr(s.runAt):(' '+cd+' 后');
    return `<div class="schedrow">⏱ 定时 <b>${s.name}</b>
      <span class="cd">${s.mode==='clock'?'今天 '+when:when+' 启动'}</span>
      <button style="background:#565d6d;color:#fff;padding:5px 12px" onclick="api('/api/schedcancel',{sid:${s.sid}})">取消</button></div>`;
  }).join('');
  if(el._sig!==html){ el._sig=html; el.innerHTML=html; }
}
function timeStr(epoch){
  const d=new Date(epoch*1000);
  return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0');
}

// ---- 配置导入 / 导出 (P2) ----
function doExport(){ window.open('/api/export','_blank'); }
function impFileChg(inp){
  const f=inp.files&&inp.files[0]; if(!f) return;
  const r=new FileReader();
  r.onload=()=>{
    $('#impText').style.display='block';
    $('#impText').value=r.result;
    $('#impRow').style.display='flex';
  };
  r.readAsText(f);
}
function doImport(){
  let cfg;
  try{ cfg=JSON.parse($('#impText').value); }
  catch(e){ alert('JSON 解析失败: '+e.message); return; }
  if(!cfg||typeof cfg!=='object'||!cfg.cards){ alert('不是有效的配置文件(缺 cards 字段)'); return; }
  if(!confirm('导入将覆盖现有的卡片/控制键/通道命名,确定继续?')) return;
  fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({config:cfg})})
  .then(r=>r.json()).then(d=>{
    if(d.ok){ $('#impText').value=''; $('#impRow').style.display='none'; $('#impFile').value=''; }
    else alert('导入失败: '+(d.error||'未知错误'));
  }).catch(()=>alert('导入失败: 网络异常'));
}

// ---- 桥接真台 ----
function brGo(){
  const addr=$('#brAddr').value.trim();
  if(!addr){ alert('请填写真台地址,格式 IP:端口'); return; }
  fetch('/api/bridge',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({addr:addr})})
  .then(r=>r.json()).then(d=>{
    showToast(d.ok?('🌉 '+d.msg):('桥接失败: '+(d.msg||'')));
  });
}
// 局域网扫描: 广播 /xinfo 发现调音台, 点结果即连 (手机零安装的关键一步)
function scanConsoles(){
  const btn=$('#scanBtn'), hint=$('#scanHint'), box=$('#scanRes');
  btn.disabled=true; btn.textContent='🔍 扫描中…';
  hint.textContent='正在广播发现指令…';
  box.innerHTML='';
  fetch('/api/scan').then(r=>r.json()).then(d=>{
    const found=(d&&d.found)||[];
    if(!found.length){
      hint.textContent='没找到调音台 — 请确认台子已通电、网线接好、与电脑在同一网段';
      box.innerHTML='';
      return;
    }
    hint.textContent='发现 '+found.length+' 台,点一下即连';
    box.innerHTML=found.map(f=>`
      <button class="scenechip" style="cursor:pointer;font-size:13px;padding:8px 14px"
        onclick="brPick('${f.ip}:${f.port}')">
        🎛 ${f.name||'调音台'} · ${f.ip}${f.local?' (本机模拟器)':''}
      </button>`).join('');
  }).catch(()=>{ hint.textContent='扫描失败: 网络异常'; })
   .finally(()=>{ btn.disabled=false; btn.textContent='🔍 扫描局域网调音台'; });
}
function brPick(addr){
  $('#brAddr').value=addr;
  brGo();
}

// ---- 冲突通知: 手动干预 → 只停该通道, 弹提示 (D 维安全策略) ----
let evtSeen=-1;
function showToast(t){
  const d=document.createElement('div'); d.className='toast'; d.textContent=t;
  document.body.appendChild(d);
  requestAnimationFrame(()=>d.classList.add('in'));
  setTimeout(()=>{ d.classList.remove('in'); setTimeout(()=>d.remove(),400); },6000);
}
function pollEvents(){
  const evs=S.events||[];
  if(evtSeen<0){ evtSeen=evs.length?evs[evs.length-1].id:0; return; }
  evs.forEach(e=>{ if(e.id>evtSeen){ evtSeen=e.id; showToast(e.time+' '+e.text); } });
}

function render(){
  if(!S) return;
  const upd=(p,map)=>{
    for(const k in map){
      const r=document.getElementById('r-'+p+'-'+k); if(!r) continue;
      const f=map[k].fader, on=map[k].on;
      const sl=r.querySelector('input');
      if(document.activeElement!==sl) sl.dataset.target=f;
      r.querySelector('.db').textContent=(f<=0.001?'-∞':dbOf(f).toFixed(1))+' dB';
      r.querySelector('.mute').classList.toggle('on',!on);
      const nmEl=r.querySelector('.nm2');
      if(nmEl){ const nm=nameOf(P2G[p],+k); nmEl.textContent=nm; }
    }
  };
  upd('c',S.ch); upd('b',S.bus); upd('d',S.dca); upd('m',{0:S.main});
  upd('a',S.auxin); upd('u',S.usb); upd('x',S.fxret); upd('t',S.mtx);

  // 电平表: 前 32 路是输入通道,后 16 路是输出 Bus
  const meters=[...document.querySelectorAll('#chlist .meter i'),
                ...document.querySelectorAll('#buslist .meter i')];
  meters.forEach((el,idx)=>{
    if(idx<S.meters.length){
      const db=S.meters[idx];
      el.style.height=(db<=-60?0:Math.min(100,(db+60)/60*100))+'%';
    }
  });
  $('#scene').textContent='场景 '+S.scene;
  if($('#scene2')) $('#scene2').textContent='场景 '+S.scene;
  $('#subs').textContent=S.subscribers;
  $('#hdinfo').textContent='UDP 10023 · 场景 '+S.scene+' · 订阅 '+S.subscribers
    +(S.bridge&&S.bridge.on?(' · 🌉'+(S.bridge.live?'真台':'真台?')+' '+S.bridge.addr):'');
  $('#conn').style.background=S?'#22c55e':'#ef4444';
  if($('#brStat')){
    const b=S.bridge||{};
    $('#brStat').textContent=!b.on?'未桥接':(b.live?'🌉 已连接 '+b.addr:'⏳ 等待真台 '+b.addr);
    $('#brStat').style.color=b.on&&b.live?'#22c55e':'';
    const inp=$('#brAddr');
    if(inp && document.activeElement!==inp && !inp.value && b.target) inp.value=b.target;
    if($('#scanHint') && b.target && !b.on)
      $('#scanHint').textContent='已记住 '+b.target+',服务启动时会自动重连';
  }
  renderSched();

  // 名称有变化时刷新命名编辑器与各处下拉 (避免每 250ms 重建破坏交互)
  if($('#nmGroup')){
    const sig=JSON.stringify(S.names||{});
    if(window._nmSig!==sig){ window._nmSig=sig; updNmIdx(); }
  }

  // 快捷键 (签名相同不重建, 避免打断交互)
  if($('#keylist') && S.quickKeys){
    $('#stepSel').value=String(S.keyCfg.step_db);
    $('#rampSel').value=String(S.keyCfg.ramp);
    setHTML('keylist',S.quickKeys.map(k=>`
      <div class="row" style="justify-content:space-between">
        <div style="font-size:15px;font-weight:600">${k.icon} ${k.name}</div>
        <div style="display:flex;gap:6px;align-items:center">
          <button class="keybtn" onpointerdown="kpStart('${k.id}')" title="按住可连发">${k.sign>0?'＋':'－'} ${S.keyCfg.step_db} dB</button>
          <button style="background:#565d6d;color:#fff;padding:8px 10px" onclick="editKey('${k.id}')">✏️</button>
          <button style="background:#565d6d;color:#fff;padding:8px 10px" onclick="delKey('${k.id}')">🗑</button>
        </div>
      </div>`).join(''));
  }

  // 卡片 (置顶优先; 执行中显示当前动作)
  const rv=S.show.running;
  const cards=[...(S.cards||[])].sort((a,b)=>(b.pin?1:0)-(a.pin?1:0));
  setHTML('cardlist',cards.map(c=>{
    const run=rv===c.id;
    const nxt=c.next?(cards.find(x=>x.id===c.next)||{}).name:'';
    const danger=isDanger(c);
    return `<div class="card"${c.pin?' style="border-color:rgba(255,180,84,.45)"':''}><div class="nm">${c.pin?'📌 ':''}${c.name}${readyBadge(c)}</div>
      <div class="ds">${c.desc}${nxt?` <span style="color:var(--accent)">→ 接续「${nxt}」</span>`:''}</div>
      ${run
        ?`<button class="stop" onclick="stopShow()">■ 停止</button>
          <div class="bar"><i style="width:${Math.round(S.show.progress*100)}%"></i></div>
          <div class="prog">执行中 ${Math.round(S.show.progress*100)}%${S.show.step?' · '+S.show.step:''}</div>`
        :`<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button ${danger?'title="危险卡片:长按 0.8 秒执行"':''}
              onpointerdown="holdStart(event,'${c.id}')" onpointerup="holdCancel()"
              onpointerleave="holdCancel()" oncontextmenu="event.preventDefault()">▶ 运行${danger?' ⚠️':''}</button>
            <button style="background:#565d6d;color:#fff" onclick="schedAsk('${c.id}')">⏱ 定时</button>
            <button style="background:#565d6d;color:#fff" onclick="api('/api/cardpin',{id:'${c.id}'})">📌 ${c.pin?'取消置顶':'置顶'}</button>
            <button style="background:#565d6d;color:#fff" onclick="editCard('${c.id}')">✏️ 编辑</button>
            <button style="background:#565d6d;color:#fff" onclick="delCard('${c.id}')">🗑 删除</button>
          </div>`}
    </div>`;
  }).join(''));

  // 快照列表 + 回归进度
  const rc=S.recall.running;
  setHTML('snaplist',S.snapshots.map(sp=>{
    const run=rc===sp.id;
    return `<div class="card"><div class="nm">📌 ${sp.name} <small style="color:var(--sub);font-weight:400">· ${sp.time}</small></div>
      ${run
        ?`<button class="stop" onclick="stopRecall()">■ 停止回归</button>
          <div class="bar"><i style="width:${Math.round(S.recall.progress*100)}%"></i></div>
          <div class="prog">电动推子回归中 ${Math.round(S.recall.progress*100)}% — 切到"通道"页看全部推子滑动</div>`
        :`<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button onclick="recallSnap(${sp.id},document.getElementById('dur-${sp.id}'))">⏪ 回归</button>
            <select id="dur-${sp.id}" style="padding:7px;border-radius:8px;border:1px solid var(--line);font-size:12px">
              <option value="1.5">1.5 秒</option><option value="3" selected>3 秒</option>
              <option value="6">6 秒</option><option value="10">10 秒</option>
            </select>
            <button style="background:#565d6d;color:#fff" onclick="delSnap(${sp.id})">删除</button>
          </div>`}
    </div>`;
  }).join('') || '<div class="ds" style="color:var(--sub);font-size:12px;padding:4px">还没有预设。调好台面后点上方"保存预设",再拖乱推子,点"回归"看它们自动滑回去。</div>');

  // 卡片遥控器页
  if($('#tileCards')){
    const rv2=S.show.running;
    setHTML('undoRow',S.canUndo
      ?`<div class="tile undo" onclick="undoLast()">↩<small>撤销上一次卡片执行 · 台面自动滑回执行前</small></div>`:'');
    setHTML('tileCards',cards.map(c=>{
      const run=rv2===c.id;
      const evts=run?`onclick="stopShow()"`
        :`onpointerdown="holdStart(event,'${c.id}')" onpointerup="holdCancel()"
          onpointerleave="holdCancel()" oncontextmenu="event.preventDefault()"`;
      return `<div class="tile ${run?'run':''}" ${evts}>${c.name}${isDanger(c)?' ⚠️':''}
        ${run?`<div class="bar"><i style="width:${Math.round(S.show.progress*100)}%"></i></div><small>${S.show.step||'点按停止'}</small>`
             :`<small class="live">${(cardLive(c)||'点按运行')+readyBadge(c)}</small>`}</div>`;
    }).join('') + `<div class="tile add" onclick="openCardModal()">➕<small>新建演出卡片</small></div>`);
    setHTML('tileKeys',S.quickKeys.map(k=>
      `<div class="tile" onpointerdown="kpStart('${k.id}')" title="按住可连发">${k.icon}<small>${k.name} ${k.sign>0?'＋':'－'}${S.keyCfg.step_db}dB</small></div>`).join('')
      + `<div class="tile add" onclick="openKeyModal()">➕<small>新增控制键</small></div>`);
  }

  const lg=$('#logbox');
  const txt=S.log.join('\\n');
  if(lg.dataset.t!==txt){ lg.textContent=txt; lg.scrollTop=lg.scrollHeight; lg.dataset.t=txt; }
}

function poll(){
  const q=window._logSeq===undefined?'/api/state':('/api/state?logseq='+window._logSeq);
  fetch(q).then(r=>r.json()).then(d=>{
    if(window._logSeq===undefined){ window._logAcc=(d.log||[]).slice(); }
    else if(d.log && d.log.length){
      window._logAcc=window._logAcc.concat(d.log);
      if(window._logAcc.length>300) window._logAcc=window._logAcc.slice(-300);
    }
    d.log=window._logAcc||d.log||[];
    window._logSeq=d.logseq;
    S=d; $('#conn').style.background='#22c55e'; pollEvents(); render();
  }).catch(()=>{ $('#conn').style.background='#ef4444'; $('#hdinfo').textContent='连接断开,重试中…'; })
   .finally(()=>setTimeout(poll,250));
}
// 局部刷新: 内容没变不重建 innerHTML (避免打断按钮交互/选中状态)
function setHTML(id,html){
  const el=document.getElementById(id);
  if(el && el._sig!==html){ el._sig=html; el.innerHTML=html; }
}
function loadInfo(){
  fetch('/api/info').then(r=>r.json()).then(d=>{
    $('#lanurl').textContent = d.lanUrl;
    $('#lanip2').textContent = d.lanIp;
  }).catch(()=>{});
}
init();
loadInfo();
if ('serviceWorker' in navigator) {
  try { navigator.serviceWorker.register('/sw.js').catch(()=>{}); } catch(e) {}
}
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
