# -*- coding: utf-8 -*-
"""x32sim/app — 装配: main() / WebServer / sink 注册"""

import argparse
import os
import socket
import socketserver
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from .bridge import BRIDGE_TARGET, bridge_start
from .httpapi import WebHandler
from .logbus import log
from .osc import decode_packet
from .oscsrv import handle
from .persist import CONFIG_PATH, load_user_config
from .params import set_enum_wire, set_param_limits
from .profiles import profile
from .qr import block_lines as qr_block
from .pair import pair_text
from .rider import rider_loop
from .runtime import DEF_WEB_PORT, ENV_WEB_PORT, LAN_IP, MAIN_SOCK, PORT_CFG, UDP_PORT, WEB_PORT, get_lan_ip, resolve_ports
from .state import meter_streamer

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

class WebServer(ThreadingHTTPServer):
    """跳过 HTTPServer 默认 server_bind() 里的 socket.getfqdn() 反向 DNS 解析。

    本机装了 ZeroTier/多个虚拟网卡时, 这一步会卡十几秒, 期间 OSC 主循环还没开始 —
    表现为"服务刚起时连不上", 也是之前 Web 启动慢的真正原因。"""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _register_sinks():
    """装配写出口。state 只认 SINKS, 不认识 bridge (依赖方向: app → 两者)。"""
    from .state import SINKS
    from .bridge import BRIDGE_SINK
    if BRIDGE_SINK not in SINKS:
        SINKS.append(BRIDGE_SINK)


def main():
    parser = argparse.ArgumentParser(description="X32 虚拟调音台模拟器")
    # 端口默认值留空(None), 解析完配置文件后再定 —— 优先级见 resolve_ports()
    parser.add_argument("--port", type=int, default=None,
                        help="OSC/UDP 端口 (默认 10023, 或在网页设置页里自定义)")
    parser.add_argument("--bind", default="0.0.0.0", help="监听地址,默认所有网卡")
    # 发布为在线应用时,沙箱通过 PORT 环境变量注入唯一的 HTTP 端口
    _env_port = os.environ.get("PORT")
    if _env_port and _env_port.isdigit():
        ENV_WEB_PORT[0] = int(_env_port)
    parser.add_argument("--web-port", type=int, default=None,
                        help="网页控制台端口,0=关闭 (默认 8080, 或在网页设置页里自定义)")
    parser.add_argument("--bridge", default="", metavar="IP:PORT",
                        help="桥接真台: 写入转发+保活+回读 (联调可指向另一模拟器实例)")
    parser.add_argument("--config", default="", help="配置文件路径 (多实例联调时各自独立)")
    # 数字增益量程: 手册写 [-12,12] (旧固件), 实测 1.15+ 是 [-18,18]。
    # 默认 18 —— 若你的台子是老固件, 用 --trim-range 12 启动, 本地量程会同步
    # 收到 ±12: 界面显示多少, 台子上就是多少, 不会出现"本地 15 / 真台 12"的静默差。
    parser.add_argument("--trim-range", type=float, default=None, metavar="DB",
                        help="数字增益量程 ±db (12=旧固件, 18=新固件; 默认按控台能力)")
    # 真台 /xremote 只推变化, 不推初始全量；需要初始值的客户端应自己 GET 或 /subscribe。
    # 这个开关是**非标准增强**, 默认关: 打开后新订阅者第一轮能收到当前全台快照,
    # 便于自研客户端联调, 但不要依赖它 —— 上真台没有这个行为。
    parser.add_argument("--xremote-snapshot", action="store_true",
                        help="新订阅者首帧补一次全量 (非真台行为, 默认关)")
    # 启动时在终端打印扫码二维码 (手机扫一下就能进网页控制台, 不用手输 IP)。
    # 默认开, 但只在真正的终端里打印: 输出重定向到日志文件/在线沙箱时不打。
    parser.add_argument("--no-qr", action="store_true",
                        help="启动时不打印扫码二维码 (手机仍可手输 IP 访问)")
    # 发送取点 (enum) 的线上形式。协议规定枚举可以用整数索引或名字符串发送,
    # 真台两种都收; 回包形式随固件而异。默认索引; 若真台回字符串读不回来, 用 label。
    parser.add_argument("--tap-wire", choices=("index", "label"), default="index",
                        help="发送取点线上形式: index=整数(默认) / label=字符串")
    args = parser.parse_args()

    if args.xremote_snapshot:
        from .state import XREMOTE_SNAPSHOT
        XREMOTE_SNAPSHOT[0] = True
        log("订阅: 新订阅者首帧补全量 (非真台行为, 仅调试用)")
    if args.tap_wire != "index":
        _chg = set_enum_wire({"send_tap": args.tap_wire})
        log("枚举线上形式: %s (真台若回字符串, 用这个对齐)" % (", ".join(_chg) or "无"))

    _register_sinks()          # 写出口就位后, 任何 set_value 都能被桥接看到
    # 量程覆盖必须在 load_user_config 之前装好: 否则配置文件里的旧值会按宽量程
    # 读进来, 之后第一次写入才收窄, 中间那段时间界面和台子是对不上的。
    _lims = dict(getattr(profile(), "param_limits", {}) or {})
    if args.trim_range:
        _r = abs(float(args.trim_range))
        _lims["ch_trim"] = (-_r, _r)
        log("量程: 数字增益 ±%g dB (按固件能力收窄; 旧固件请用 12)" % _r)
    set_param_limits(_lims)
    if args.config:
        CONFIG_PATH[0] = args.config
    load_user_config()

    # 配置文件读完之后再定端口（命令行 > PORT 环境变量 > 配置文件 > 内置默认）
    args.port, args.web_port = resolve_ports(args.port, args.web_port)
    if args.port == args.web_port and args.web_port > 0:
        log("端口冲突: OSC/UDP 与网页端口不能相同 (%d), 网页端口回退为默认 %d"
            % (args.port, DEF_WEB_PORT))
        args.web_port = DEF_WEB_PORT
        if args.web_port == args.port:
            log("仍与 OSC 端口相同, 关闭网页控制台")
            args.web_port = 0
    if PORT_CFG["udp"] is not None or PORT_CFG["web"] is not None:
        log("使用自定义端口: OSC/UDP %d · 网页 %s"
            % (args.port, (args.web_port if args.web_port > 0 else "已关闭")))

    if args.bridge:
        ok, msg = bridge_start(args.bridge)
        log(("BRIDGE " + msg) if ok else ("BRIDGE 参数无效: " + msg))
    elif BRIDGE_TARGET[0]:
        # 上次连过的真台 → 启动后自动重连: 手机打开网页时就已经连上台子了
        def _auto_reconnect():
            time.sleep(3.0)
            ok, msg = bridge_start(BRIDGE_TARGET[0])
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
    LAN_IP[0], UDP_PORT[0], WEB_PORT[0] = ip, args.port, args.web_port

    if args.web_port > 0:
        try:
            web = WebServer(("0.0.0.0", args.web_port), WebHandler)
            threading.Thread(target=web.serve_forever, daemon=True).start()
            log("网页控制台已启动  http://localhost:%d  (手机/电脑浏览器均可打开)" % args.web_port)
            # 手机扫码入口: 比让用户在手机上敲 192.168.x.x:8080 省事得多。
            # 只在交互式终端里打 —— 重定向到日志、或被平台托管时没有意义。
            if not args.no_qr and sys.stdout.isatty() and not ip.startswith("127."):
                url = "http://%s:%d/" % (ip, args.web_port)
                log("手机扫码直达 (同一 WiFi): %s  [--no-qr 可关闭]" % url)
                try:
                    for line in qr_block(url, border=2):
                        print(line)
                    print()
                except Exception as e:      # 二维码只是便利功能, 失败不能挡住启动
                    log("二维码生成失败: %r" % e)
                try:
                    # App 扫码用：带 OSC 端口，改过端口也不会连错（见 x32sim/pair.py）
                    log("App 扫码配对码: %s" % pair_text(ip, args.port, args.web_port))
                except ValueError:
                    pass
        except OSError as e:
            log("网页控制台启动失败(端口 %d 被占用?): %s" % (args.web_port, e))

    log("iOS App 中把调音台 IP 填为: %s  (若为 0.0.0.0 请用本机局域网 IP)" % ip)
    log("等待指令... Ctrl+C 退出\n")

    threading.Thread(target=meter_streamer, args=(sock,), daemon=True).start()
    # 自动增益/推子: 50ms 一拍, 默认总开关开但单通道都要显式启用
    threading.Thread(target=rider_loop, daemon=True).start()

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
