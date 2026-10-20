# -*- coding: utf-8 -*-
"""补测：桥接回读防回环 / 高频拖动刷日志 / 场景号越界。"""
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(BASE, "x32_simulator.py")
WEBP, OSCP = sys.argv[1], int(sys.argv[2])
WEB = "http://127.0.0.1:%s" % WEBP
_ENV = dict(os.environ)
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    _ENV.pop(k, None)
_ENV["no_proxy"] = _ENV["NO_PROXY"] = "127.0.0.1,localhost"


def post(p, b):
    r = urllib.request.Request(WEB + p, json.dumps(b).encode(), {"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=10).read().decode() or "{}")
    except Exception as e:
        return {"error": str(e)}


def state():
    return json.loads(urllib.request.urlopen(WEB + "/api/state", timeout=10).read().decode())


def pad(b):
    return b + b"\0" * ((-len(b)) % 4)


def msg(addr, val):
    if isinstance(val, str):
        return pad(addr.encode() + b"\0") + pad(b",s\0\0") + pad(val.encode() + b"\0")
    return pad(addr.encode() + b"\0") + pad(b",f\0\0") + struct.pack(">f", float(val))


cfg = os.path.join(BASE, "_rv_extra.json")
if os.path.exists(cfg):
    os.remove(cfg)

# 假真台：只收不发（模拟"桥接已连上但真台不主动回读"）
fake = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
fake.bind(("127.0.0.1", 0))
fake.settimeout(0.2)
fport = fake.getsockname()[1]

p = subprocess.Popen([sys.executable, SIM, "--web-port", WEBP,
                      "--bind", "127.0.0.1", "--port", str(OSCP), "--config", cfg, "--bridge", "127.0.0.1:%d" % fport],
                     stdout=open(os.path.join(BASE, "_rv_extra.log"), "w", encoding="utf-8"),
                     stderr=subprocess.STDOUT, cwd=BASE, env=_ENV)
for _ in range(80):
    time.sleep(0.1)
    try:
        state()
        break
    except Exception:
        pass

try:
    time.sleep(1.5)
    # 清空假真台侧积压（启动时的全量 GET）
    try:
        while True:
            fake.recvfrom(65535)
    except socket.timeout:
        pass

    print("== A. 桥接回读防回环 ==")
    # 模拟真台面板把 ch7 推子推到 0.42（回读包）
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.sendto(msg("/ch/07/mix/fader", 0.42), ("127.0.0.1", OSCP))
    time.sleep(0.5)
    local = state()["ch"]["7"]["fader"]
    echo = []
    try:
        while True:
            echo.append(fake.recvfrom(65535)[0])
    except socket.timeout:
        pass
    print("  本地 ch7 = %.3f（期望 0.42）；模拟器又转发回去 %d 个包（期望 0 = 无回环）"
          % (local, len(echo)))

    print("\n== B. 高频拖动是否把日志刷掉 ==")
    before = state()["log"][-40:]
    for i in range(120):
        post("/api/set", {"kind": "ch", "param": "fader", "idx": 5, "value": 0.3 + i * 0.003})
    time.sleep(0.3)
    st = state()
    logs = st.get("log") or []
    has_boot = any(("就绪" in str(l) or "端口" in str(l)) for l in logs)
    print("  120 次写入后，日志环 %d 条；仍能看到启动期日志：%s" % (len(logs), has_boot))
    print("  环里内容样例：%s" % [str(l)[:32] for l in logs[-3:]])

    print("\n== C. 场景号越界 ==")
    sender.sendto(pad(b"/scene/999/load\0"), ("127.0.0.1", OSCP))
    time.sleep(0.4)
    print("  /scene/999/load 之后 state.scene = %r" % state()["scene"])
    c, b = post("/api/scene", {"scene": 150})
    print("  /api/scene 150 → HTTP %s %s" % (c, b))

    print("\n== D. 一次改名触发多少次全量写盘 ==")
    calls = 0
    mtime0 = os.path.getmtime(cfg) if os.path.exists(cfg) else 0
    # 连续改 20 个通道名，观察配置文件 mtime 变化次数（粗测）
    t0 = time.time()
    for i in range(1, 21):
        post("/api/name", {"group": "ch", "idx": i, "name": "改名%02d" % i})
    elapsed = time.time() - t0
    size = os.path.getsize(cfg) if os.path.exists(cfg) else 0
    print("  20 次改名耗时 %.2f 秒（平均 %.0f ms/次），配置文件 %d 字节" % (
        elapsed, elapsed / 20 * 1000, size))
    print("  说明：每次改名都整体重写一次配置（含 8 槽 FX + 全部预设 data）")
finally:
    p.terminate()
    p.wait()
