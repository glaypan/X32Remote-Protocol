#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双形态差分测试: 开发形态 (python -m x32sim) 与分发形态 (x32_simulator.py)
必须行为完全一致。

同一串 OSC/HTTP 操作分别打到两个实例上, 逐条比对回包; 再比对 /api/state
(剔除随时间变化的字段)。任何一边"接口 ok 但台子没反应"都会在这里现形。

用法: python test_dual_form.py   (由 run_all_tests.py 调用, 端口写死避免冲突)
"""
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 端口刻意避开其它用例组 (10023/10024/10025/20023/28080/28081)
A_OSC, A_WEB, A_CFG = 21001, 29081, os.path.join(BASE, "_regr_dual_a.json")
B_OSC, B_WEB, B_CFG = 21002, 29082, os.path.join(BASE, "_regr_dual_b.json")

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + detail) if detail and not cond else ""))


def wait_web(port, timeout=45):
    end = time.time() + timeout
    while time.time() < end:
        try:
            OPENER.open("http://127.0.0.1:%d/api/state" % port, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def start(args, cwd=BASE):
    return subprocess.Popen([PY] + args, cwd=cwd,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=dict(os.environ, no_proxy="127.0.0.1,localhost"))


def stop(*procs):
    for p in procs:
        if p is None:
            continue
        try:
            p.terminate()
            p.wait(timeout=8)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(0.8)


# ---------------------------------------------------------------- OSC 客户端
sys.path.insert(0, BASE)
from x32_simulator import OscMsg, decode_packet, encode_bundle, encode_message


class Client:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2.0)
        self.sock.bind(("127.0.0.1", 0))
        self.port = port

    def rpc(self, msg, timeout=2.0):
        self.sock.sendto(encode_message(msg), ("127.0.0.1", self.port))
        end = time.time() + timeout
        replies = []
        while time.time() < end and not replies:
            try:
                data, _ = self.sock.recvfrom(8192)
                replies = decode_packet(data)
            except (socket.timeout, OSError):
                break
        return replies

    def send(self, msg):
        pkt = msg if isinstance(msg, (bytes, bytearray)) else encode_message(msg)
        self.sock.sendto(pkt, ("127.0.0.1", self.port))


def sig(replies):
    """回包签名 (地址 + 参数), 用于两边逐字节比对。"""
    return [(r.address, tuple(r.args)) for r in replies]


# ---------------------------------------------------------------- HTTP 辅助
def post(port, path, payload):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with OPENER.open(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(port, path):
    with OPENER.open("http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def normalize(state):
    """剔除随时间/端口变化的字段, 留下可确定比对的部分。"""
    s = json.loads(json.dumps(state))
    for k in ("meters", "log", "logseq", "ver", "events"):
        s.pop(k, None)
    s.pop("ports", None)                       # 两个实例端口本来就不同
    for snap in s.get("snapshots", []):
        snap.pop("time", None)
    s.get("show", {}).pop("progress", None)
    s.get("show", {}).pop("started", None)
    s.get("recall", {}).pop("progress", None)
    return s


def main():
    for f in (A_CFG, B_CFG):
        try:
            os.remove(f)
        except OSError:
            pass

    # A = 开发形态 (包), B = 分发形态 (单文件)
    pa = start(["-m", "x32sim", "--port", str(A_OSC), "--web-port", str(A_WEB),
                "--config", A_CFG])
    pb = start(["x32_simulator.py", "--port", str(B_OSC), "--web-port", str(B_WEB),
                "--config", B_CFG])
    try:
        if not (wait_web(A_WEB) and wait_web(B_WEB)):
            print("实例启动超时")
            return 1
        time.sleep(1.0)

        ca, cb = Client(A_OSC), Client(B_OSC)

        print("== 1. OSC 逐条比对 ==")
        cases = [
            ("xinfo", OscMsg("/xinfo")),
            ("set/get fader", OscMsg("/ch/05/mix/fader")),
            ("set/get mute", OscMsg("/ch/07/mix/on")),
            ("dca fader", OscMsg("/dca/03/fader")),
            ("main fader", OscMsg("/main/st/mix/fader")),
            ("dca mask", OscMsg("/ch/09/mix/dca")),
            ("fx mix", OscMsg("/fx/2/mix")),
            ("fx name", OscMsg("/fx/1/config/name")),
            ("wildcard 32", OscMsg("/ch/*/mix/fader")),
            ("wildcard dca", OscMsg("/dca/*/fader")),
            ("wildcard on", OscMsg("/bus/*/mix/on")),
        ]
        # 先写入同一串值
        for c in (ca, cb):
            c.send(OscMsg("/ch/05/mix/fader", [0.42]))
            c.send(OscMsg("/ch/07/mix/on", [0]))
            c.send(OscMsg("/dca/03/fader", [0.6]))
            c.send(OscMsg("/main/st/mix/fader", [0.8]))
            c.send(OscMsg("/ch/09/mix/dca", [5]))
            c.send(OscMsg("/fx/2/mix", [0.5]))
            c.send(OscMsg("/ch/01/config/name", ["DIFF-MIC"]))
            c.send(OscMsg("/scene/3/load"))
            c.send(encode_bundle([OscMsg("/ch/10/mix/fader", [0.33]),
                                  OscMsg("/dca/01/on", [0])]))
        time.sleep(0.6)
        for name, msg in cases:
            ra, rb = ca.rpc(msg), cb.rpc(msg)
            check("OSC %s 两边一致" % name, sig(ra) == sig(rb),
                  "A=%r B=%r" % (sig(ra)[:2], sig(rb)[:2]))

        # 电平 blob: 只比对结构 (值是时间的函数, 本来就不同)
        for c in (ca, cb):
            c.send(OscMsg("/meters/1"))
        time.sleep(0.4)
        blob_ok = True
        for c in (ca, cb):
            try:
                data, _ = c.sock.recvfrom(8192)
                msgs = decode_packet(data)
                if not msgs or msgs[0].address != "/meters/1":
                    blob_ok = False
                    continue
                (cnt,) = struct.unpack(">i", msgs[0].args[0][:4])
                if cnt != 48:
                    blob_ok = False
            except OSError:
                blob_ok = False
        check("meters blob 结构一致 (48 路)", blob_ok)

        print("== 2. HTTP 同一串操作后比对 /api/state ==")
        for port in (A_WEB, B_WEB):
            post(port, "/api/set", {"kind": "ch", "param": "fader", "idx": 11, "value": 0.33})
            post(port, "/api/setbatch", {"sets": [
                {"kind": "bus", "param": "fader", "idx": 3, "value": 0.5},
                {"kind": "dca", "param": "on", "idx": 2, "value": 0}]})
            post(port, "/api/keycfg", {"step_db": 4.5, "ramp": 0.3})
            post(port, "/api/fx", {"slot": 2, "field": "time", "value": 3.3})
            post(port, "/api/name", {"group": "ch", "idx": 2, "name": "KICK-DIFF"})
            r = post(port, "/api/cardadd", {"card": {
                "name": "差分卡", "desc": "dual form",
                "actions": [{"kind": "set", "target": {"group": "ch", "id": 4,
                                                        "param": "fader"}, "value": 0.66}]}})
            card_id = r.get("id")
            post(port, "/api/show", {"id": card_id})
            post(port, "/api/snapshot", {"action": "save", "name": "差分预设",
                                         "scope": {"groups": ["ch"], "params": ["fader"],
                                                   "indices": {"ch": [1, 2, 3]}}})
        time.sleep(2.5)          # 等卡片跑完 + 预设落盘

        sa = normalize(get_json(A_WEB, "/api/state"))
        sb = normalize(get_json(B_WEB, "/api/state"))
        if sa == sb:
            check("/api/state 规范化后完全一致 (%d 个顶层键)" % len(sa), True)
        else:
            # 指出第一个不同的键, 便于定位
            diffs = [k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k)]
            check("/api/state 规范化后完全一致", False, "差异键: %s" % sorted(diffs))
            for k in sorted(diffs)[:3]:
                print("      A.%s = %s" % (k, json.dumps(sa.get(k), ensure_ascii=False)[:200]))
                print("      B.%s = %s" % (k, json.dumps(sb.get(k), ensure_ascii=False)[:200]))

        print("== 3. 预设回归后再比对 ==")
        snaps = get_json(A_WEB, "/api/state")["snapshots"]
        if snaps:
            sid = snaps[0]["id"]
            for port in (A_WEB, B_WEB):
                post(port, "/api/snapshot", {"action": "recall", "id": sid, "seconds": 1.0})
            time.sleep(2.0)
            sa = normalize(get_json(A_WEB, "/api/state"))
            sb = normalize(get_json(B_WEB, "/api/state"))
            if sa == sb:
                check("回归后 /api/state 仍一致", True)
            else:
                diffs = [k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k)]
                check("回归后 /api/state 仍一致", False, "差异键: %s" % sorted(diffs))

        print("== 4. /api/info 端口字段 ==")
        ia = get_json(A_WEB, "/api/info")
        ib = get_json(B_WEB, "/api/info")
        check("/api/info 各自端口正确",
              ia.get("udpPort") == A_OSC and ib.get("udpPort") == B_OSC
              and ia.get("webPort") == A_WEB and ib.get("webPort") == B_WEB,
              "A=%r B=%r" % (ia, ib))

        print("== 4b. 扫码接口两种形态一致 (别名导入的守护) ==")
        # 单文件形态会剥掉相对导入; 若 `import x as y` 的别名没被保留, 这里会 500。
        qu = "/qr.svg?u=" + urllib.parse.quote("http://192.168.1.5:8080/", safe="")
        with OPENER.open("http://127.0.0.1:%d%s" % (A_WEB, qu), timeout=5) as r:
            qa = r.read()
        with OPENER.open("http://127.0.0.1:%d%s" % (B_WEB, qu), timeout=5) as r:
            qb = r.read()
        check("两种形态二维码逐字节一致", qa == qb and qa.startswith(b"<svg") and b"viewBox" in qa,
              "A=%d B=%d" % (len(qa), len(qb)))

        print("== 5. 配置文件可被对方读懂 (同 schema) ==")
        ok = True
        for f in (A_CFG, B_CFG):
            if not os.path.exists(f):
                ok = False
                continue
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            for key in ("quickKeys", "cards", "keyCfg", "presets", "names",
                        "bridgeTarget", "console", "ports"):
                if key not in d:
                    ok = False
        check("两种形态落盘的配置 schema 一致", ok)

        print()
        print("结果: %d 通过, %d 失败" % (len(passed), len(failed)))
        if failed:
            print("失败项:", failed)
        return 1 if failed else 0
    finally:
        stop(pa, pb)
        for f in (A_CFG, B_CFG):
            try:
                os.remove(f)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
