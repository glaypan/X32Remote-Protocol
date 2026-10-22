# -*- coding: utf-8 -*-
"""订阅语义回归: /xremote 只推变化 vs /subscribe 按地址周期回报。

背景 (为什么单独一个脚本):
  `PUSH_CACHE` 是"上次推给任何订阅者的值"的全局缓存, 于是
    · 第一个订阅者 (冷缓存) 能收到全量 —— 看起来像"有初始值";
    · 第二个订阅者 (缓存已热、值没变) 一个包都收不到 —— 界面全 0。
  真台 /xremote 本来也只推变化 (Mixing Station 文档: "event 机制只在值变化时发数据,
  这也是它连接时需要一次 sync 的原因"), 所以这不是协议不一致, 而是**客户端对齐**
  手段缺失: 协议里负责"我现在就要这个值"的是 /subscribe <addr> [rate]。
  本脚本同时验证三件事:
    1. 默认: 后加入的订阅者收不到初始全量 (与真台一致), 说明不能依赖它;
    2. --xremote-snapshot: 后加入者能收到 (本地增强, 明确非真台行为);
    3. /subscribe: 无论何时订阅, 第一轮就回当前值, 按 rate 周期回报, 退订即停。

用法: python test_subscribe.py          # 自己起/停实例, 端口 21041/28141
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from x32sim.osc import OscMsg, decode_packet, encode_message  # noqa: E402

PY = sys.executable
PORT = int(os.environ.get("X32_SUB_PORT", "21041"))
WEBP = int(os.environ.get("X32_SUB_WEB", "28141"))
WEB = "http://127.0.0.1:%d" % WEBP
ENV = dict(os.environ, no_proxy="*", NO_PROXY="*")
CFG = os.path.join(BASE, "_sub_cfg.json")

results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + detail) if detail and not ok else ""))


def wait_web(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(WEB + "/api/info", timeout=1.5).read()
            return True
        except Exception:
            time.sleep(0.2)
    return False


def start(extra=None):
    if os.path.exists(CFG):
        os.remove(CFG)
    p = subprocess.Popen([PY, "x32_simulator.py", "--port", str(PORT),
                          "--web-port", str(WEBP), "--config", CFG] + (extra or []),
                         cwd=BASE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=ENV)
    if not wait_web():
        p.terminate()
        raise SystemExit("实例启动超时 (web %d)" % WEBP)
    return p


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=6)
    except Exception:
        p.kill()
    time.sleep(0.6)


def client():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    s.bind(("127.0.0.1", 0))
    return s


def send(s, addr, *args):
    s.sendto(encode_message(OscMsg(addr, list(args))), ("127.0.0.1", PORT))


def collect(s, seconds):
    """收一段时间内的所有包 (解码后按消息展开)。"""
    out = []
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            break
        s.settimeout(max(0.01, left))
        try:
            data, _ = s.recvfrom(65536)
        except socket.timeout:
            break
        except ConnectionResetError:
            break
        out.extend(decode_packet(data))
    return out


def faders(msgs):
    return [m for m in msgs if m.address.endswith("/mix/fader")
            or m.address.endswith("/mix/on")]


# ============================================================ 1. 默认实例
print("== 1. 默认 (--xremote-snapshot 关): 后加入者无初始全量 ==")
p = start()
try:
    a = client()
    send(a, "/xremote")
    got_a = faders(collect(a, 1.0))
    check("首个订阅者 (冷缓存) 收到全量推子/静音", len(got_a) >= 60, "%d 条" % len(got_a))
    check("首个订阅者收到的是 fader/on, 不是别的地址",
          all(m.address.endswith(("/mix/fader", "/mix/on")) for m in got_a),
          str({m.address for m in got_a if not m.address.endswith(("/mix/fader", "/mix/on"))}))

    b = client()
    send(b, "/xremote")
    got_b = faders(collect(b, 1.0))
    check("后加入的订阅者默认收不到初始全量 (与真台一致)", len(got_b) == 0, "%d 条" % len(got_b))

    # 变化仍然推给所有人
    send(a, "/ch/05/mix/fader", 0.42)
    time.sleep(0.1)
    got_a = faders(collect(a, 0.6))
    got_b = faders(collect(b, 0.6))
    check("值变化后 A 收到 diff",
          any(m.address == "/ch/05/mix/fader" for m in got_a), str(len(got_a)))
    check("值变化后 B 也收到 diff",
          any(m.address == "/ch/05/mix/fader" for m in got_b), str(len(got_b)))

    # ---- /subscribe: 协议里的正解 ----
    c = client()
    send(c, "/subscribe", "/ch/07/mix/fader", 4)      # rate=4 → 每 200ms 一帧
    got_c = collect(c, 0.15)
    check("/subscribe 订阅后第一轮立刻回当前值 (不用等变化)",
          any(m.address == "/ch/07/mix/fader" for m in got_c), str(len(got_c)))
    got_c = collect(c, 1.0)
    n = sum(1 for m in got_c if m.address == "/ch/07/mix/fader")
    check("/subscribe rate=4 (200ms) 一秒约 4-5 帧", 3 <= n <= 7, "%d 帧" % n)

    send(c, "/unsubscribe", "/ch/07/mix/fader")
    collect(c, 0.3)                                    # 清掉在途包
    got_c = collect(c, 0.8)
    n = sum(1 for m in got_c if m.address == "/ch/07/mix/fader")
    check("/unsubscribe 后不再回报", n == 0, "%d 帧" % n)

    # 通配符订阅 (一次多路)
    d = client()
    send(d, "/subscribe", "/ch/*/mix/fader", 25)       # rate=25 → 1.25s 一帧
    got_d = collect(d, 0.3)
    n = sum(1 for m in got_d if m.address.startswith("/ch/") and m.address.endswith("/mix/fader"))
    check("/subscribe 支持通配符 (32 路一次订阅)", n >= 30, "%d 条" % n)
finally:
    stop(p)

# ============================================================ 2. snapshot 实例
print("== 2. --xremote-snapshot: 后加入者补齐初始全量 ==")
p = start(["--xremote-snapshot"])
try:
    a = client()
    send(a, "/xremote")
    collect(a, 0.6)                                     # 先让缓存热起来
    b = client()
    send(b, "/xremote")
    got_b = faders(collect(b, 1.0))
    check("开启后新订阅者能收到全量 (>= 60 条)", len(got_b) >= 60, "%d 条" % len(got_b))
    # 不重复轰炸: 第二秒不该再收到同一批
    again = faders(collect(b, 1.0))
    check("只补一次, 不会每秒重发全量", len(again) == 0, "%d 条" % len(again))
finally:
    stop(p)

# ============================================================ 3. 续期
print("== 3. 续期与 /api/info 一致性 ==")
p = start()
try:
    e = client()
    send(e, "/subscribe", "/ch/01/mix/fader", 2)
    collect(e, 0.3)
    # 30 秒无续期才清理, 这里验证"续期 (/xremote) 不会把按地址订阅顶掉"
    time.sleep(0.5)
    send(e, "/xremote")
    got = collect(e, 0.6)
    check("续期 (/xremote) 后按地址订阅仍在工作 (>= 3 帧)", len(got) >= 3, "%d 帧" % len(got))
    info = urllib.request.urlopen(WEB + "/api/info", timeout=3).read().decode()
    check("/api/info 报告 xremoteSnapshot=false", '"xremoteSnapshot": false' in info, info[:80])
finally:
    stop(p)

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
for _n, _name, _detail in failed:
    print("失败项 #%d: %s%s" % (_n, _name, (" | " + _detail) if _detail else ""))
if os.path.exists(CFG):
    os.remove(CFG)
raise SystemExit(1 if fails else 0)
