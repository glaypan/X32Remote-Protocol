# -*- coding: utf-8 -*-
"""核对 5 个「公开资料查不到」的出厂默认值 —— 在真台上跑一次就有答案。

背景: 这 5 个值在官方手册里只有**范围**，没有出厂值；社区场景文件里的是
使用者自己的设置，不是出厂状态。所以模拟器暂时给的是合理猜测，并在
ParamSpec.note 里标了「需真机确认」。这个脚本就是那个确认动作。

用法:
  1. 真台做一次出厂初始化 (开机时按住 Scenes/Undo，或 Setup/Config → Initialize)
  2. python tools/verify_defaults.py 192.168.1.100
     (只核对, 不改任何东西; 全程只发 GET)

判读: 输出里「真台」一列就是出厂值。若与「模拟器」不同，把真台值填回
x32sim/params.py 对应 ParamSpec 的 default，并删掉 note 里的「需真机确认」。
"""
import os
import socket
import sys
import time

# 脚本在 tools/ 下跑, sys.path[0] 是 tools/, 直接 import x32sim 会失败
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from x32sim.osc import OscMsg, decode_packet, encode_message  # noqa: E402

# (地址, 说明, 模拟器当前默认, 单位/档位)
CHECKS = [
    ("/ch/01/preamp/hpf", "低切频率", 80.0, "Hz (logf 20..400, 101 档)"),
    ("/ch/01/preamp/hpslope", "低切斜率", 12, "枚举 12/18/24"),
    ("/ch/01/insert/pos", "插入位置", 0, "枚举 0=PRE / 1=POST"),
    ("/ch/01/gate/thr", "噪声门门限", -40.0, "dB (linf -80..0, 0.5)"),
    ("/ch/01/gate/range", "噪声门范围", 15.0, "dB (linf 3..60, 1)"),
]


def ask(sock, addr, timeout=1.5):
    """发 GET 等一个回包。X32 对 GET 会回同地址的消息。"""
    sock.sendto(encode_message(OscMsg(addr, [])), CONSOLE)
    end = time.time() + timeout
    while time.time() < end:
        sock.settimeout(max(0.05, end - time.time()))
        try:
            data, _ = sock.recvfrom(65536)
        except socket.timeout:
            break
        for m in decode_packet(data):
            if m.address.rstrip("/") == addr.rstrip("/") and m.args:
                return m.args[0]
    return None


CONSOLE = None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    host = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 10023
    CONSOLE = (host, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(1.5)
    # 先发 /xinfo 确认对面在
    sock.sendto(encode_message(OscMsg("/xinfo", [])), CONSOLE)
    try:
        sock.recvfrom(65536)
    except socket.timeout:
        print("!! %s:%d 没有回应 —— 检查 IP / 是否同一网段 / 端口" % CONSOLE)
        sys.exit(2)

    print("%-24s %-10s %-12s %-12s %s"
          % ("地址", "项目", "真台", "模拟器", "结论"))
    print("-" * 78)
    bad = 0
    for addr, label, sim, unit in CHECKS:
        got = ask(sock, addr)
        if got is None:
            print("%-24s %-10s %-12s %-12s 读不到" % (addr, label, "-", sim))
            bad += 1
            continue
        same = (abs(float(got) - float(sim)) < 1e-6) if isinstance(sim, float) \
            else (int(got) == int(sim))
        verdict = "一致" if same else "★ 需修正"
        if not same:
            bad += 1
        print("%-24s %-10s %-12s %-12s %s"
              % (addr, label, got, sim, verdict + "  (" + unit + ")"))
    print("-" * 78)
    if bad:
        print("%d 项不一致 —— 把「真台」列的值填回 x32sim/params.py 的 default,"
              " 并删掉对应 note 里的「需真机确认」。" % bad)
    else:
        print("全部一致 —— 可以把 params.py 里这 5 条的「需真机确认」去掉，"
              "并把来源改成「真机核对 YYYY-MM-DD」。")
