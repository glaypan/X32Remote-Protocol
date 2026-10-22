# -*- coding: utf-8 -*-
"""真台协议探测: 枚举的线上形式 + level 的档位网格。

两个问题在没有真机时无法确认，但都可以用一条命令问出答案:

  1. **枚举发整数还是字符串?**
     协议规定 enum 既可以用 4 字节索引、也可以用名字字符串发送，真台两种都收；
     但**回包**形式随固件不同。模拟器默认发索引，如果真台回字符串，读回就会被
     当成非法值。本命令逐个枚举：发索引 → 读回 → 发名字 → 读回，
     直接告出"这台机子回的是 int 还是 str"。

  2. **level 的 161 档到底是不是 k/160?**
     模拟器按"f 等分 160 份"量化发送电平。真台是否也这样，扫一遍就知道：
     发 k/160，读回，看返回值集合是不是恰好 161 个点、是否与 k/160 重合。

用法:
  python tools/probe_console.py 192.168.1.100 enum
  python tools/probe_console.py 192.168.1.100 level
  python tools/probe_console.py 192.168.1.100 fader     # 1024 档, 约 40 秒
  python tools/probe_console.py 192.168.1.100 all

⚠️ 会临时改写目标参数（结束时恢复原值）。演出进行中不要跑。
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from x32sim.osc import OscMsg, decode_packet, encode_message  # noqa: E402

CONSOLE = None
SOCK = None

# (地址, 说明, 索引 → 线上名字)
ENUMS = [
    ("/ch/01/mix/01/type", "发送取点", ["<-EQ", "EQ->", "PRE", "POST", "GRP"]),
    ("/ch/01/insert/pos", "插入位置", ["PRE", "POST"]),
    ("/ch/01/gate/mode", "门类型", ["EXP2", "EXP3", "EXP4", "GATE", "DUCK"]),
    ("/ch/01/dyn/mode", "动态类型", ["COMP", "EXP"]),
]


def send(addr, *args):
    SOCK.sendto(encode_message(OscMsg(addr, list(args))), CONSOLE)


def get(addr, timeout=1.2):
    """GET 一个地址，返回 (值, 线上类型)。"""
    send(addr)
    end = time.time() + timeout
    while time.time() < end:
        SOCK.settimeout(max(0.05, end - time.time()))
        try:
            data, _ = SOCK.recvfrom(65536)
        except socket.timeout:
            break
        for m in decode_packet(data):
            if m.address.rstrip("/") == addr.rstrip("/") and m.args:
                v = m.args[0]
                return v, type(v).__name__
    return None, None


def probe_enum():
    print("== 枚举线上形式 ==")
    print("%-24s %-10s %-14s %-14s %s" % ("地址", "项目", "发索引→回包", "发名字→回包", "结论"))
    print("-" * 92)
    for addr, label, names in ENUMS:
        orig, _ = get(addr)
        if orig is None:
            print("%-24s %-10s 读不到（地址或固件不支持）" % (addr, label))
            continue
        # 发索引 1，看回包类型
        send(addr, 1)
        time.sleep(0.05)
        v_int, t_int = get(addr)
        # 发名字，看回包
        name = names[1] if len(names) > 1 else names[0]
        send(addr, name)
        time.sleep(0.05)
        v_str, t_str = get(addr)
        if t_int == "str":
            verdict = "回字符串 → 模拟器用 --tap-wire label 对齐"
        elif t_int in ("int", "float"):
            verdict = "回整数 → 默认 index 正确"
        else:
            verdict = "回包类型未知: %s" % t_int
        wrote_by_name = (v_str in (1, 1.0, name))
        if not wrote_by_name:
            verdict += "；但**不接受名字写入**"
        print("%-24s %-10s %-14s %-14s %s"
              % (addr, label, "%r(%s)" % (v_int, t_int), "%r(%s)" % (v_str, t_str), verdict))
        # 恢复
        if orig is not None:
            send(addr, orig)
    print()


def probe_level(addr, label, n, step_note):
    """扫描 n 个档位: 发 k/(n-1), 读回, 统计网格。"""
    print("== %s (%s, %d 档) ==" % (label, addr, n))
    orig, _ = get(addr)
    if orig is None:
        print("读不到 —— 跳过\n")
        return
    seen = {}
    t0 = time.time()
    for k in range(n):
        v = k / (n - 1.0)
        send(addr, v)
        got, _ = get(addr, timeout=0.5)
        if got is not None:
            seen[round(float(got), 6)] = k
    dt = time.time() - t0
    vals = sorted(seen)
    print("  发出 %d 个值, 读回 %d 个不同值 (耗时 %.1fs)" % (n, len(vals), dt))
    if len(vals) <= 1:
        print("  !! 只回一个值 —— 该地址可能不可写（检查目标是否被占用）\n")
        return
    # 与假设的 k/(n-1) 网格比对
    off = max(abs(v - round(v * (n - 1)) / (n - 1)) for v in vals)
    exact = all(abs(v - round(v * (n - 1)) / (n - 1)) < 1e-5 for v in vals)
    print("  与 k/%d 网格的最大偏差: %.6g → %s"
          % (n - 1, off, "完全吻合 (模拟器量化正确)" if exact else "★ 不吻合, 需要标定表"))
    if not exact:
        print("  实测档位（前 12 个）: %s" % ", ".join("%.5f" % v for v in vals[:12]))
        print("  → 把这些值填进 ParamSpec 的标定表（见 params.linsteps 注释）")
    # 恢复
    send(addr, orig)
    print()


def main():
    global CONSOLE, SOCK
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    host = sys.argv[1]
    rest = sys.argv[2:]
    port = 10023
    if rest and rest[0].isdigit():
        port = int(rest[0])
        rest = rest[1:]
    cmd = rest[0] if rest else "all"
    CONSOLE = (host, port)
    SOCK = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    SOCK.bind(("0.0.0.0", 0))
    SOCK.settimeout(1.5)
    send("/xinfo")
    try:
        SOCK.recvfrom(65536)
    except socket.timeout:
        print("!! %s:%d 没有回应" % CONSOLE)
        return 2

    if cmd in ("enum", "all"):
        probe_enum()
    if cmd in ("level", "all"):
        probe_level("/ch/01/mix/01/level", "发送电平 CH01→Bus01", 161, "161")
    if cmd in ("fader", "all"):
        probe_level("/ch/01/mix/fader", "通道推子 CH01", 1024, "1024")
    return 0


if __name__ == "__main__":
    sys.exit(main())
