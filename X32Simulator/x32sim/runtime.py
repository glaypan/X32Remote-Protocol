# -*- coding: utf-8 -*-
"""x32sim/runtime — 端口/LAN IP/主 UDP socket (跨模块可变, 一律用容器)"""

import socket

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

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


# ⚠️ 可变容器: 这三个值由 main() 在启动时写入, 但**跨模块读取**。
#    直接 from .runtime import UDP_PORT 拿到的是导入时副本, main() rebind 后
#    读取方永远看到旧值 —— 所以一律用容器 (与 MAIN_SOCK/ENV_WEB_PORT 同风格)。
LAN_IP, UDP_PORT, WEB_PORT = ["127.0.0.1"], [10023], [8080]

# ---- 网络端口可在设置页自由配置（存在 user_config.json，重启生效）----
# None = 该端口未自定义, 走内置默认；端口是进程启动时绑定的, 运行中改不了,
# 所以改动要重启模拟器才生效（页面上会明确提示）。
PORT_CFG = {"udp": None, "web": None}
DEF_UDP_PORT, DEF_WEB_PORT = 10023, 8080
ENV_WEB_PORT = [None]   # 发布为在线应用时, 平台通过 PORT 环境变量注入的网页端口


def valid_port(v, allow_zero=False):
    """端口合法值: 1–65535（allow_zero=True 时额外允许 0, 用于「关闭网页」）。
    非法一律返回 None —— 调用方据此报错, 不做静默兜底。"""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    if allow_zero and n == 0:
        return 0
    return n if 1 <= n <= 65535 else None


def resolve_ports(args_port, args_web_port):
    """定端口优先级: 命令行 > PORT 环境变量（线上平台指定）> 配置文件 > 内置默认。

    PORT 环境变量压过配置文件是有意的: 线上实例的端口由平台分配,
    若让本地保存的网页端口覆盖它, 发布出去的链接会直接起不来。"""
    udp = args_port if args_port is not None else (
        PORT_CFG["udp"] if PORT_CFG["udp"] is not None else DEF_UDP_PORT)
    if args_web_port is not None:
        web = args_web_port
    elif ENV_WEB_PORT[0] is not None:
        web = ENV_WEB_PORT[0]
    elif PORT_CFG["web"] is not None:
        web = PORT_CFG["web"]
    else:
        web = DEF_WEB_PORT
    return udp, web


def ports_pending_restart():
    """已保存的端口与当前实际生效的是否不一致（= 需要重启才生效）。"""
    if PORT_CFG["udp"] is not None and PORT_CFG["udp"] != UDP_PORT[0]:
        return True
    if PORT_CFG["web"] is not None and PORT_CFG["web"] != WEB_PORT[0]:
        return True
    return False
MAIN_SOCK = [None]   # 主 UDP socket (手动变更立即广播用)
