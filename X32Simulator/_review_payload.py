# -*- coding: utf-8 -*-
"""payload 分解：19KB 到底花在哪，新用户首屏多大。"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(BASE, "x32_simulator.py")
WEBP, OSCP = sys.argv[1], sys.argv[2]
WEB = "http://127.0.0.1:%s" % WEBP
_ENV = dict(os.environ)
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    _ENV.pop(k, None)
_ENV["no_proxy"] = _ENV["NO_PROXY"] = "127.0.0.1,localhost"


def post(p, b):
    r = urllib.request.Request(WEB + p, json.dumps(b).encode(), {"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=10).read().decode() or "{}")
    except Exception:
        return {}


def start(cfg):
    p = subprocess.Popen([sys.executable, SIM, "--web-port", WEBP, "--port", OSCP,
                          "--bind", "127.0.0.1", "--config", cfg],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=BASE, env=_ENV)
    for _ in range(80):
        time.sleep(0.1)
        try:
            urllib.request.urlopen(WEB + "/api/state", timeout=5).read()
            return p
        except Exception:
            pass
    return p


cfg = os.path.join(BASE, "_rv_payload.json")
for f in (cfg,):
    if os.path.exists(f):
        os.remove(f)

print("== 空台（新用户首屏）==")
p = start(cfg)
try:
    body = urllib.request.urlopen(WEB + "/api/state", timeout=10).read()
    d = json.loads(body)
    print("总大小 %.1f KB" % (len(body) / 1024))
    parts = []
    for k, v in d.items():
        n = len(json.dumps(v, ensure_ascii=False).encode())
        parts.append((n, k))
    for n, k in sorted(parts, reverse=True)[:10]:
        print("  %-12s %6.1f KB  %5.1f%%" % (k, n / 1024, n * 100.0 / len(body)))
finally:
    p.terminate()
    p.wait()

print("\n== 用了一阵之后（8 卡 + 8 预设 + 改过名字）==")
cfg2 = os.path.join(BASE, "_rv_payload2.json")
if os.path.exists(cfg2):
    os.remove(cfg2)
p = start(cfg2)
try:
    for i in range(8):
        post("/api/snapshot", {"action": "save", "name": "预设 %d" % i,
                               "scope": {"groups": ["ch", "bus"], "params": ["fader", "on"]}})
    for i in range(8):
        post("/api/cardadd", {"card": {"name": "卡片 %d" % i, "actions": [
            {"kind": "fade_ch", "ch": i + 1, "to": 0.5, "duration": 2.0},
            {"kind": "set", "target": {"group": "ch", "id": i + 1, "param": "fader"}, "value": 0.6},
            {"kind": "scene", "scene": i + 1}]}})
    for i in range(1, 33):
        post("/api/name", {"group": "ch", "idx": i, "name": "通道名称%02d" % i})
    time.sleep(0.5)
    body = urllib.request.urlopen(WEB + "/api/state", timeout=10).read()
    d = json.loads(body)
    print("总大小 %.1f KB" % (len(body) / 1024))
    parts = []
    for k, v in d.items():
        n = len(json.dumps(v, ensure_ascii=False).encode())
        parts.append((n, k))
    for n, k in sorted(parts, reverse=True)[:10]:
        print("  %-12s %6.1f KB  %5.1f%%" % (k, n / 1024, n * 100.0 / len(body)))
    # 电平在轮询里重复传输：单独看
    print("\n其中 meters（电平）%.1f KB；cards %d 张；snapshots 摘要 %d 条" % (
        len(json.dumps(d.get("meters")).encode()) / 1024,
        len(d.get("cards") or []), len(d.get("snapshots") or [])))
    print("4G 下（4 次/秒）：每小时约 %.0f MB" % (len(body) * 4 * 3600 / 1024 / 1024))
finally:
    p.terminate()
    p.wait()
