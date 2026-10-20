# -*- coding: utf-8 -*-
"""x32sim/scan — 局域网控台发现 (广播 /xinfo)"""

import socket
import time

from .logbus import log
from .osc import OscMsg, decode_packet, encode_message
from .runtime import LAN_IP, UDP_PORT

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def _local_ips():
    """本机所有 IPv4 地址, 用于在扫描结果里排除自己 (多网卡/ZeroTier 下会从各网卡回包)。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    if LAN_IP[0]:
        ips.add(LAN_IP[0])
    return ips


def _local_subnets():
    """本机各网卡的 /24 定向广播地址 (路由器常拦截 255.255.255.255, 定向广播更可靠)。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    if LAN_IP[0] and not LAN_IP[0].startswith("127."):
        ips.add(LAN_IP[0])
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
        if port == UDP_PORT[0] and ip in self_ips:
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
        same = [x for x in grp if _same_subnet24(x["ip"], LAN_IP[0])]
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
