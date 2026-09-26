# -*- coding: utf-8 -*-
"""B3 回归测试: 调音台自动发现 + 一键连接 + 记住地址自动重连。

自管理双实例:
  假真台   --port 10023 --web-port 28081 --config _t_fake.json
  桥接端   --port 10024 --web-port 28080 --config _t_bridge.json
跑完自动清理临时配置。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PY = r"C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
BASE = os.path.dirname(os.path.abspath(__file__))
OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FAKE_CFG, BR_CFG = "_t_fake.json", "_t_bridge.json"
procs = []
passed, failed = [], []


def check(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  " + str(extra)) if extra else ""))


def get(path, port=28080, timeout=20):
    return json.loads(OP.open("http://127.0.0.1:%d%s" % (port, path), timeout=timeout).read())


def post(path, obj, port=28080, timeout=20):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 json.dumps(obj).encode(), {"Content-Type": "application/json"})
    return json.loads(OP.open(req, timeout=timeout).read())


def start(args):
    p = subprocess.Popen([PY, "x32_simulator.py"] + args, cwd=BASE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    procs.append(p)
    return p


def wait_up(port, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            get("/api/state", port, timeout=3)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def wait_bridge(timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if get("/api/state")["bridge"]["on"]:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def cleanup():
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    time.sleep(1.5)
    for f in (FAKE_CFG, BR_CFG):
        try:
            os.remove(os.path.join(BASE, f))
        except OSError:
            pass


try:
    for f in (FAKE_CFG, BR_CFG):
        try:
            os.remove(os.path.join(BASE, f))
        except OSError:
            pass

    print("== 启动假真台 (OSC 10023 / Web 28081) ==")
    start(["--port", "10023", "--web-port", "28081", "--config", FAKE_CFG])
    check("假真台就绪", wait_up(28081))

    print("== 启动桥接端 (OSC 10024 / Web 28080) ==")
    start(["--port", "10024", "--web-port", "28080", "--config", BR_CFG])
    check("桥接端就绪", wait_up(28080))

    # ---------- 1. 自动发现 ----------
    print("== 1. 扫描局域网调音台 (/api/scan 广播 /xinfo) ==")
    res = get("/api/scan", 28080, timeout=30)
    found = res.get("found", [])
    print("    发现:", json.dumps(found, ensure_ascii=False))
    hit = next((f for f in found if f["port"] == 10023), None)
    check("扫描发现假真台 (端口 10023)", hit is not None)
    check("返回型号/名称信息", bool(hit and hit["model"]), hit)
    check("未把自己当调音台 (排除本机 10024)", all(f["port"] != 10024 for f in found))
    dups = [f for f in found if f["port"] == 10023]
    check("同一台设备的跨网卡重复已合并 (仅 1 条)", len(dups) == 1, dups)

    # ---------- 2. 一键连接 + 记住地址 ----------
    print("== 2. 一键连接并记住地址 ==")
    addr = "%s:%d" % (hit["ip"], hit["port"]) if hit else "127.0.0.1:10023"
    r = post("/api/bridge", {"addr": addr})
    check("连接返回 ok", r.get("ok") is True, r)
    check("桥接已建立", wait_bridge())
    st = get("/api/state")
    check("状态里记住地址 target", st["bridge"]["target"] == addr, st["bridge"])
    try:
        cfg = json.load(open(os.path.join(BASE, BR_CFG), encoding="utf-8"))
        check("地址已落盘 user_config(bridgeTarget)", cfg.get("bridgeTarget") == addr, cfg.get("bridgeTarget"))
    except Exception as e:
        check("地址已落盘 user_config(bridgeTarget)", False, repr(e))

    # ---------- 3. 转发链路 ----------
    print("== 3. 写入转发到真台 ==")
    post("/api/set", {"kind": "ch", "param": "fader", "idx": 3, "value": 0.424})
    time.sleep(0.6)
    fake = get("/api/state", 28081)
    check("真台 ch03 收到 0.424", abs(fake["ch"]["3"]["fader"] - 0.424) < 0.02,
          fake["ch"]["3"]["fader"])

    # ---------- 4. 重启后自动重连 ----------
    print("== 4. 服务重启后自动重连 (无人操作) ==")
    procs[1].terminate()
    time.sleep(2.0)
    start(["--port", "10024", "--web-port", "28080", "--config", BR_CFG])
    check("桥接端重启就绪", wait_up(28080))
    check("自动重连成功 (无需填 IP)", wait_bridge(25))
    st2 = get("/api/state")
    check("重连目标是记住的地址", st2["bridge"]["target"] == addr, st2["bridge"])
    post("/api/set", {"kind": "ch", "param": "fader", "idx": 4, "value": 0.66})
    time.sleep(0.6)
    fake2 = get("/api/state", 28081)
    check("重连后转发仍通", abs(fake2["ch"]["4"]["fader"] - 0.66) < 0.02,
          fake2["ch"]["4"]["fader"])

    # ---------- 5. 主动断开不再重连 ----------
    print("== 5. 主动断开 → 清除记忆 ==")
    post("/api/bridge", {"addr": ""})
    time.sleep(0.5)
    st3 = get("/api/state")
    check("桥接已停", st3["bridge"]["on"] is False)
    try:
        cfg = json.load(open(os.path.join(BASE, BR_CFG), encoding="utf-8"))
        check("记忆地址已清空", not cfg.get("bridgeTarget"), repr(cfg.get("bridgeTarget")))
    except Exception as e:
        check("记忆地址已清空", False, repr(e))

finally:
    cleanup()

print("\n===== %d 通过 / %d 失败 =====" % (len(passed), len(failed)))
if failed:
    print("失败项:", "、".join(failed))
sys.exit(1 if failed else 0)
