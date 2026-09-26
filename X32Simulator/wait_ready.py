# -*- coding: utf-8 -*-
"""等待模拟器就绪 (OSC 10023 + Web 28080), 用于回归测试前置等待。

用法: python wait_ready.py [--port 10023] [--web 28080] [--timeout 90]
就绪后退出码 0, 超时退出码 1。"""
import socket
import sys
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from x32_simulator import OscMsg, encode_message

udp_port, web_port, timeout = 10023, 28080, 90.0
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "--port" and i + 1 < len(args):
        udp_port = int(args[i + 1])
    elif a == "--web" and i + 1 < len(args):
        web_port = int(args[i + 1])
    elif a == "--timeout" and i + 1 < len(args):
        timeout = float(args[i + 1])

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.3)
pkt = encode_message(OscMsg("/xinfo", []))

t0 = time.time()
osc = web = None
while time.time() - t0 < timeout:
    if osc is None:
        try:
            sock.sendto(pkt, ("127.0.0.1", udp_port))
            sock.recvfrom(2048)
            osc = time.time() - t0
        except Exception:
            pass
    if web is None:
        try:
            op.open("http://127.0.0.1:%d/api/state" % web_port, timeout=1).read()
            web = time.time() - t0
        except Exception:
            pass
    if osc is not None and web is not None:
        print("就绪: OSC %.1fs / Web %.1fs" % (osc, web))
        sys.exit(0)
    time.sleep(0.2)
print("等待超时 (%.0fs): OSC=%s Web=%s" % (timeout, osc, web))
sys.exit(1)
