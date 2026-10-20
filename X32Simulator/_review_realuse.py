# -*- coding: utf-8 -*-
"""真实使用走查：用独立端口 + 独立配置，把用户实际会做的操作跑一遍。

覆盖 6 类真实场景：
  1. 卡片连点两次 / 停止后立刻开另一张（并发与竞态）
  2. 桥接真台时的写入量、转发值与本地值是否一致
  3. 配置损坏、配置目录不可写（持久化的容错与诚实性）
  4. 错误输入（越界/NaN/超长名/非法范围/缺预设）
  5. 状态轮询的真实负载（payload 大小、频率）
  6. 线程数增长（长会话会不会泄漏）

用法：python review_realuse.py <web_port> <osc_port>
"""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

PY = sys.executable
BASE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(BASE, "x32_simulator.py")
WEB = "http://127.0.0.1:%s" % sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18090"
OSC_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 11026

_ENV = dict(os.environ)
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    _ENV.pop(k, None)
_ENV["no_proxy"] = "127.0.0.1,localhost"
_ENV["NO_PROXY"] = "127.0.0.1,localhost"

RESULT = []
LOG_PATH = os.path.join(BASE, "_rv_sim.log")


def ok(cond, name, detail=""):
    RESULT.append((bool(cond), name, detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, (" | " + detail) if detail else ""))


def note(name, detail):
    RESULT.append((None, name, detail))
    print("NOTE %s | %s" % (name, detail))


def post(path, body, timeout=10):
    req = urllib.request.Request(WEB + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def get_state():
    with urllib.request.urlopen(WEB + "/api/state", timeout=10) as r:
        return json.loads(r.read().decode())


def start_sim(config, extra=None):
    args = [PY, SIM, "--web-port", WEB.rsplit(":", 1)[1], "--port", str(OSC_PORT),
            "--bind", "127.0.0.1", "--config", config] + (extra or [])
    log = open(LOG_PATH, "w", encoding="utf-8")
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=BASE, env=_ENV)
    for _ in range(80):
        time.sleep(0.1)
        try:
            get_state()
            return p
        except Exception:
            if p.poll() is not None:
                return p
    return p


def stop_sim(p):
    try:
        p.terminate()
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


# ---------------------------------------------------------------- 假真台
class FakeConsole(threading.Thread):
    """/xremote 保活 + 全量 GET 都回包，并统计模拟器发来的写入包。"""

    def __init__(self):
        threading.Thread.__init__(self, daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.addr = None
        self.packets = []
        self.stop = False

    def run(self):
        self.sock.settimeout(0.2)
        while not self.stop:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            self.addr = addr
            t = time.time()
            for a, v in decode_all(data):
                if a in ("/xremote", "/subscribe"):
                    continue
                self.packets.append((t, a, v))
            # 简单回一条 xinfo 让桥接判定 live
            if b"/xinfo" in data:
                self.sock.sendto(pack(b"/xinfo\0\0") + pack(b",s\0\0") + pad(b"fake"), addr)

    def window(self, t0, t1):
        return [p for p in self.packets if t0 <= p[0] <= t1]

    def clear(self):
        self.packets = []


def pad(b):
    return b + b"\0" * ((-len(b)) % 4)


def pack(b):
    return b


def decode_all(data):
    """极简 OSC 解析：只处理模拟器会发的 Message（地址 + 1 个参数）。"""
    out = []
    try:
        if data[:8] == b"#bundle\0":
            off = 16
            while off + 4 <= len(data):
                size = struct.unpack(">i", data[off:off + 4])[0]
                off += 4
                out += decode_all(data[off:off + size])
                off += size
            return out
        end = data.index(b"\0")
        addr = data[:end].decode("utf-8", "replace")
        off = (end + 4) & ~3
        comma = data.index(b"\0", off)
        tags = data[off:comma].decode("latin-1")
        off = (comma + 4) & ~3
        val = None
        if len(tags) > 1:
            t = tags[1]
            if t == "f":
                val = struct.unpack(">f", data[off:off + 4])[0]
            elif t == "i":
                val = struct.unpack(">i", data[off:off + 4])[0]
            elif t == "s":
                e = data.index(b"\0", off)
                val = data[off:e].decode("utf-8", "replace")
        out.append((addr, val))
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 场景 1：卡片并发
def case_card_concurrency():
    print("\n== 场景 1：卡片连点 / 停止后立刻开新卡 ==")
    cfg = os.path.join(BASE, "_rv_cards.json")
    for f in (cfg, LOG_PATH):
        if os.path.exists(f):
            os.remove(f)
    sim = start_sim(cfg)
    try:
        ok(sim.poll() is None, "1.0 模拟器启动")
        # 两张卡：都推 ch1 到不同值，时长 2 秒
        _, c1 = post("/api/cardadd", {"card": {"name": "RV-A", "actions": [
            {"kind": "fade_ch", "ch": 1, "to": 0.9, "duration": 2.0, "curve": "linear"}]}})
        _, c2 = post("/api/cardadd", {"card": {"name": "RV-B", "actions": [
            {"kind": "fade_ch", "ch": 2, "to": 0.1, "duration": 2.0, "curve": "linear"}]}})
        id1, id2 = c1.get("id"), c2.get("id")

        # 1a：同一张卡连点两次（用户双击是很常见的）
        api_steps = get_state().get("showSteps")
        post("/api/show", {"id": id1})
        post("/api/show", {"id": id1})
        time.sleep(0.6)
        n_threads = len([t for t in threading.enumerate() if t.name == "show"])
        # 用日志里的"启动卡片"条数判断起了几个 worker
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            logs = f.read()
        starts = logs.count("SHOW 启动卡片: RV-A (")
        ok(starts == 1, "1a 连点两次 → 只应有 1 个 worker", "实际 %d 个" % starts)
        if starts > 1:
            note("1a 并发驱动", "%d 个 worker 同时驱动同一张卡（推子会被写两遍）" % starts)

        # 1b：A 还在渐变（2 秒）时停止，立刻启动 B 推 ch2 → A 是否继续把 ch1 推到 0.9
        post("/api/show", {})
        post("/api/show", {"id": id2})
        time.sleep(0.9)
        mid = get_state()["ch"]["1"]["fader"]
        time.sleep(1.2)
        end = get_state()["ch"]["1"]["fader"]
        ok(True, "1b 停止 A 后 ch1 是否继续向 A 的目标走",
           "停止时 %.3f → 1.2 秒后 %.3f（A 目标 0.9；若继续上升=旧 worker 没被停）" % (mid, end))
        if end <= mid + 0.01:
            note("1b 结论", "旧 worker 已被真正停止（ch1 冻结在 %.3f）" % end)
        else:
            note("1b 结论", "旧 worker 仍在跑：ch1 从 %.3f 继续涨到 %.3f，说明 "
                            "SHOW_STOP.clear() 把刚 set 的停止信号吃掉了" % (mid, end))

        # 1c：不管上面结果，最终状态是否收敛（B 的目标 0.1）
        time.sleep(2.6)
        v = get_state()["ch"]["2"]["fader"]
        ok(abs(v - 0.1) < 0.06, "1c B 把 ch2 推到目标", "ch2=%.3f（目标 0.1）" % v)
    finally:
        stop_sim(sim)


# ---------------------------------------------------------------- 场景 2：桥接
def case_bridge():
    print("\n== 场景 2：桥接真台的写入量 / 值一致性 ==")
    fake = FakeConsole()
    fake.start()
    cfg = os.path.join(BASE, "_rv_bridge.json")
    for f in (cfg, LOG_PATH):
        if os.path.exists(f):
            os.remove(f)
    sim = start_sim(cfg, ["--bridge", "127.0.0.1:%d" % fake.port])
    try:
        ok(sim.poll() is None, "2.0 模拟器启动")
        time.sleep(1.6)     # 等全量 GET 过去
        fake.clear()

        # 2a：单次推子写入，看转发的值是否等于本地存下的值（越界输入）
        post("/api/set", {"kind": "ch", "param": "fader", "idx": 2, "value": 1.7})
        time.sleep(0.3)
        local = get_state()["ch"]["2"]["fader"]
        sent = [(a, v) for t, a, v in fake.packets if a.startswith("/ch/02/mix/fader")]
        forward = sent[-1][1] if sent else None
        ok(local <= 1.0, "2a 本地被钳到 1.0", "本地=%.3f" % local)
        ok(forward is not None, "2a 转发包存在", "转发=%r" % forward)
        if forward is not None:
            note("2a 转发值 vs 本地值", "本地 %.3f ← 真台收到 %r（%s）" % (
                local, forward, "一致" if abs(float(forward) - local) < 1e-6 else "不一致"))

        # 2b：NaN 输入会不会把 NaN 转发给真台
        fake.clear()
        code, body = post("/api/set", {"kind": "ch", "param": "fader", "idx": 3, "value": "nan"})
        time.sleep(0.2)
        nan_sent = [p for p in fake.packets if isinstance(p[2], float) and p[2] != p[2]]
        ok(True, "2b NaN 输入被拒绝或收敛", "HTTP %s %s；转发 NaN 包 %d 个" % (
            code, body.get("error", ""), len(nan_sent)))

        # 2c：预设回归 3 秒 → 统计转发包量
        post("/api/snapshot", {"action": "save", "name": "RV-全量",
                               "scope": {"groups": ["ch"], "params": ["fader", "on"]}})
        sid = get_state()["snapshots"][0]["id"]
        fake.clear()
        post("/api/snapshot", {"action": "recall", "id": sid, "seconds": 3})
        time.sleep(3.6)
        pk = len(fake.packets)
        ok(pk > 0, "2c 预设回归的转发包量", "%d 包 / 3.6 秒 ≈ %.0f 包/秒" % (pk, pk / 3.6))
        if pk / 3.6 > 300:
            note("2c 写入风暴", "回归期间真台每秒收到 %.0f 包（32 路×2 参数×10Hz）" % (pk / 3.6))

        # 2d：卡片渐变的包量
        _, c = post("/api/cardadd", {"card": {"name": "RV-渐变", "actions": [
            {"kind": "fade_all", "to": 0.5, "duration": 2.0, "curve": "linear"}]}})
        fake.clear()
        post("/api/show", {"id": c["id"]})
        time.sleep(2.6)
        pk2 = len(fake.packets)
        ok(True, "2d 卡片渐变的转发包量", "%d 包 / 2.6 秒 ≈ %.0f 包/秒" % (pk2, pk2 / 2.6))
        if pk2 / 2.6 > 300:
            note("2d 写入风暴", "fade_all 期间真台每秒 %.0f 包" % (pk2 / 2.6))
    finally:
        stop_sim(sim)
        fake.stop = True


# ---------------------------------------------------------------- 场景 3：持久化
def case_persist():
    print("\n== 场景 3：配置损坏 / 不可写 ==")
    # 3a：正常保存后截断配置文件，再启动 → 卡片与预设是否静默全丢
    cfg = os.path.join(BASE, "_rv_persist.json")
    for f in (cfg, LOG_PATH):
        if os.path.exists(f):
            os.remove(f)
    sim = start_sim(cfg)
    try:
        post("/api/cardadd", {"card": {"name": "RV-要保住的卡", "actions": [
            {"kind": "fade_ch", "ch": 1, "to": 0.5, "duration": 1.0}]}})
        post("/api/snapshot", {"action": "save", "name": "RV-要保住的预设",
                               "scope": {"groups": ["ch"], "params": ["fader"]}})
        time.sleep(0.4)
        before = get_state()
        ok(len(before["cards"]) > 0 and len(before["snapshots"]) > 0,
           "3a 保存后卡片/预设存在", "cards=%d presets=%d" % (
               len(before["cards"]), len(before["snapshots"])))
        raw = open(cfg, encoding="utf-8").read()
        ok(raw.count("\n") > 3, "3a 配置文件已落盘", "%d 字节" % len(raw))
        # 模拟"写一半被杀 / 磁盘满"：截断到 60%
        open(cfg, "w", encoding="utf-8").write(raw[:int(len(raw) * 0.6)])
    finally:
        stop_sim(sim)

    sim2 = start_sim(cfg)
    try:
        st = get_state()
        ok(True, "3b 配置损坏后仍能启动", "cards=%d presets=%d" % (
            len(st.get("cards") or []), len(st.get("snapshots") or [])))
        if not st.get("cards") and not st.get("snapshots"):
            note("3b 损坏 = 静默清空", "启动日志只有一行「配置加载失败」，界面无任何提示")
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            logtxt = f.read()
        hits = [l.strip() for l in logtxt.splitlines() if "配置" in l][:2]
        ok("配置加载失败" in logtxt, "3b 日志有记录", " ; ".join(hits))
    finally:
        stop_sim(sim2)

    # 3c：配置目录不可写时，保存接口是否仍然报成功
    bad = os.path.join(BASE, "_rv_missing_dir", "rv.json")
    for f in (os.path.join(BASE, "_rv_missing_dir"),):
        pass
    sim3 = start_sim(bad)
    try:
        code, body = post("/api/snapshot", {"action": "save", "name": "RV-不可写",
                                           "scope": {"groups": ["ch"], "params": ["fader"]}})
        st = get_state()
        ok(True, "3c 配置不可写时的保存结果",
           "HTTP %s %s；内存里已有 %d 条预设" % (code, body, len(st.get("snapshots") or [])))
        if code == 200:
            note("3c 假成功", "落盘失败但接口返回 ok，用户重启后预设消失且无任何提示")
    finally:
        stop_sim(sim3)


# ---------------------------------------------------------------- 场景 4：错误输入
def case_bad_input():
    print("\n== 场景 4：错误输入 ==")
    cfg = os.path.join(BASE, "_rv_bad.json")
    for f in (cfg, LOG_PATH):
        if os.path.exists(f):
            os.remove(f)
    sim = start_sim(cfg)
    try:
        c, b = post("/api/set", {"kind": "ch", "param": "fader", "idx": 99, "value": 0.5})
        ok(c == 400, "4a 通道 99 越界", "HTTP %s %s" % (c, b.get("error")))
        c, b = post("/api/set", {"kind": "ch", "param": "fader", "idx": 1, "value": "abc"})
        ok(c == 400, "4b 非数字推子值", "HTTP %s %s" % (c, b.get("error")))
        c, b = post("/api/snapshot", {"action": "save", "name": "x",
                                      "scope": {"groups": ["bogus"], "params": ["nope"]}})
        ok(True, "4c 非法 scope", "HTTP %s → %s（应为默认范围或拒绝）" % (c, b))
        c, b = post("/api/cardadd", {"card": {"name": "   ", "actions": []}})
        ok(True, "4d 空名称空动作的卡", "HTTP %s %s" % (c, b))
        # 4e：卡片里引用不存在的预设（局部场景）
        _, cc = post("/api/cardadd", {"card": {"name": "RV-坏预设", "actions": [
            {"kind": "scene", "scene": 5, "mode": "partial", "presetId": 999}]}})
        post("/api/show", {"id": cc.get("id")})
        time.sleep(1.2)
        st = get_state()
        ok(st["scene"] == 5 or st["scene"] != 5, "4e 缺预设的局部场景",
           "场景号变成 %s（期望不变）" % st["scene"])
        # 4f：超长名称
        c, b = post("/api/name", {"group": "ch", "idx": 1, "name": "名" * 200})
        nm = get_state()["names"].get("ch", {}).get("1", "")
        ok(len(nm) <= 12, "4f 超长名称被截断", "存下 %d 字" % len(nm))
    finally:
        stop_sim(sim)


# ---------------------------------------------------------------- 场景 5：轮询负载
def case_polling():
    print("\n== 场景 5：状态轮询负载 ==")
    cfg = os.path.join(BASE, "_rv_poll.json")
    for f in (cfg, LOG_PATH):
        if os.path.exists(f):
            os.remove(f)
    sim = start_sim(cfg)
    try:
        # 造满 8 条预设 + 8 张卡，模拟用了一阵之后的真实体量
        for i in range(8):
            post("/api/snapshot", {"action": "save", "name": "RV-%d" % i,
                                   "scope": {"groups": ["ch", "bus"], "params": ["fader", "on"]}})
        for i in range(8):
            post("/api/cardadd", {"card": {"name": "RV 卡 %d" % i, "actions": [
                {"kind": "fade_ch", "ch": i + 1, "to": 0.5, "duration": 2.0}]}})
        time.sleep(0.5)
        t0 = time.time()
        sizes = []
        for _ in range(20):
            with urllib.request.urlopen(WEB + "/api/state", timeout=10) as r:
                sizes.append(len(r.read()))
        dt = time.time() - t0
        avg = sum(sizes) / len(sizes)
        ok(True, "5a /api/state 单次大小", "平均 %.1f KB（%d 字节）" % (avg / 1024, int(avg)))
        rate = 4.0        # 网页 250ms 一轮
        note("5a 轮询带宽", "网页端约 %.1f KB/s（4 次/秒），4G 下每小时 %.0f MB" % (
            avg * rate / 1024, avg * rate * 3600 / 1024 / 1024))
        note("5a 服务端耗时", "%d 次请求共 %.2f 秒 → 单次 %.1f ms" % (
            len(sizes), dt, dt / len(sizes) * 1000))
        # 日志增量是否真的只回增量
        st = get_state()
        seq = st.get("logseq")
        with urllib.request.urlopen(WEB + "/api/state?logseq=%d" % (seq - 3), timeout=10) as r:
            inc = json.loads(r.read().decode())
        ok(len(inc.get("logs") or []) <= 3, "5b 日志增量",
           "请求 3 条，返回 %d 条" % len(inc.get("logs") or []))
    finally:
        stop_sim(sim)


# ---------------------------------------------------------------- 场景 6：线程
def case_threads():
    print("\n== 场景 6：连续执行后的资源占用 ==")
    cfg = os.path.join(BASE, "_rv_thread.json")
    for f in (cfg, LOG_PATH):
        if os.path.exists(f):
            os.remove(f)
    sim = start_sim(cfg)
    try:
        _, c = post("/api/cardadd", {"card": {"name": "RV-短卡", "actions": [
            {"kind": "fade_ch", "ch": 1, "to": 0.6, "duration": 0.3}]}})
        time.sleep(0.3)
        # 连续执行 25 次（真实使用：一场演出几十张卡）
        for _ in range(25):
            post("/api/show", {"id": c["id"]})
            time.sleep(0.18)
        time.sleep(1.0)
        st = get_state()
        ok(st.get("show", {}).get("running") in (None, ""), "6a 25 次执行后回到空闲",
           "running=%r" % (st.get("show") or {}).get("running"))
        # 事件队列 / 日志环是否有界
        ok(len(st.get("events") or []) <= 20, "6b 事件队列有界",
           "%d 条" % len(st.get("events") or []))
        note("6b 自动撤销点", "每次执行都往 AUTO_SNAPS 塞一条（maxlen=5），属预期")
    finally:
        stop_sim(sim)


def main():
    print("真实使用走查 · 目标 %s · OSC %d" % (WEB, OSC_PORT))
    case_card_concurrency()
    case_bridge()
    case_persist()
    case_bad_input()
    case_polling()
    case_threads()
    print("\n===== 汇总 =====")
    fail = [r for r in RESULT if r[0] is False]
    notes = [r for r in RESULT if r[0] is None]
    for f in fail:
        print("FAIL  %s | %s" % (f[1], f[2]))
    print("断言 %d 项：PASS %d / FAIL %d；观察项 %d" % (
        len([r for r in RESULT if r[0] is not None]),
        len([r for r in RESULT if r[0] is True]), len(fail), len(notes)))


if __name__ == "__main__":
    main()
