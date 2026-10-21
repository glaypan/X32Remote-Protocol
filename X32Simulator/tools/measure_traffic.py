# -*- coding: utf-8 -*-
"""4G 流量实测: 直接量 HTTP 线上字节 (含请求/响应头), 而不是只看 body。

用法: python tools/measure_traffic.py [host:port] [seconds]
输出: 各端点单次往返字节数 + 按 250ms 轮询折算的每小时/4 小时流量。
"""
import http.client
import json
import sys
import time

HOST = (sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:28080").split(":")
H, P = HOST[0], int(HOST[1])
DUR = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0


def call(path, extra_headers=None, gzip=False):
    """一次完整请求, 返回 (总字节, 响应体字节, 解析后的 JSON 或 None)。"""
    c = http.client.HTTPConnection(H, P, timeout=8)
    hdrs = {"Accept-Encoding": "gzip" if gzip else "identity"}
    if extra_headers:
        hdrs.update(extra_headers)
    req_bytes = len("GET %s HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % (path, H, P))
    c.request("GET", path, headers=hdrs)
    r = c.getresponse()
    body = r.read()
    wire = len(body)                      # 线上字节 (压缩后), 算流量必须用它
    if r.getheader("Content-Encoding") == "gzip":
        import gzip as _gz
        body = _gz.decompress(body)
    hdr_bytes = sum(len("%s: %s\r\n" % (k, v)) for k, v in r.getheaders())
    total = req_bytes + hdr_bytes + wire + 2        # +2: 状态行冗余估算
    c.close()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return total, len(body), data


# 1) 首轮全量 (含 ver)
full_total, full_body, snap = call("/api/state?logseq=0&v=", gzip=True)
ver = (snap or {}).get("ver") or ""
lseq = (snap or {}).get("logseq") or 0
print("全量 /api/state        : 总 %6d B (body %6d B) ver=%s" % (full_total, full_body, ver[:12]))

# 2) 条件轮询 (无变更)。⚠️ 必须带上 logseq —— 网页是带着上一次的序号来取日志的,
#    不带的话服务端每次都把整段启动日志塞回来 (3 KB), 量出来的不是真实稳态。
unch_total, unch_body, _ = call("/api/state?logseq=%d&v=%s" % (lseq, ver), gzip=True)
print("条件轮询 (无变更)      : 总 %6d B (body %6d B)" % (unch_total, unch_body))

# 3) 电平
met_total, met_body, _ = call("/api/meters", gzip=True)
print("电平 /api/meters       : 总 %6d B (body %6d B)" % (met_total, met_body))

# 4) 实测: 按网页真实的 250ms 节奏跑 DUR 秒
n = 0
bytes_state = bytes_meter = 0
end = time.time() + DUR
while time.time() < end:
    t, _, _ = call("/api/state?logseq=%d&v=%s" % (lseq, ver), gzip=True)
    bytes_state += t
    t, _, _ = call("/api/meters", gzip=True)
    bytes_meter += t
    n += 1
    time.sleep(0.25)

if n:
    per_pair = (bytes_state + bytes_meter) / n
    print("\n实测 %d 轮 (%.1fs): 每轮 state+meters = %.0f B" % (n, DUR, per_pair))
    print("  折合每小时 : %.1f MB/h" % (per_pair * 4 * 3600 / 1048576.0))
    print("  4 小时演出 : %.1f MB" % (per_pair * 4 * 3600 * 4 / 1048576.0))
    print("  其中 meters 占 %.0f%%" % (bytes_meter * 100.0 / (bytes_state + bytes_meter)))
    # 藏起来时前后端各降到 2s: 轮询次数变成 1/8
    hid = per_pair / 8.0
    print("\n按\"屏幕亮 30%% / 后台 70%%\"折算 4 小时演出:")
    print("  前台 %.1f MB + 后台 %.1f MB = %.1f MB"
          % (per_pair * 4 * 3600 * 0.3 * 4 / 1048576.0,
             hid * 4 * 3600 * 0.7 * 4 / 1048576.0,
             (per_pair * 0.3 + hid * 0.7) * 4 * 3600 * 4 / 1048576.0))
