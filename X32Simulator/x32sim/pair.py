# -*- coding: utf-8 -*-
"""x32sim/pair — 手机 App 的扫码配对码

为什么不直接复用网页二维码：
  网页码编码的是 ``http://<ip>:<web>/``，手机相机扫了直接开控制台；
  但原生 App 要的是 **OSC 的 UDP 端口**（默认 10023），而网页地址里那个端口
  是 HTTP 的。用户把 UDP 端口改过之后两者不相等，App 若按 10023 猜就会连错
  —— 而且错得很难看出来（"已连接"提示都不会有）。

所以另立一种载荷，把"连到哪"说全::

    x32remote://pair?v=1&host=192.168.50.57&osc=10023&web=8080

三条约定：
  1. 只描述**连接目标**，不放任何凭据（服务端本来也没有鉴权）。
  2. 字段顺序固定、名字短，整串 < 80 字节，二维码版本 ≤ 4，扫得快。
  3. App 端解析器必须**同时吃下普通网页地址** —— 用户很可能扫的就是浏览器那张码，
     此时按默认 10023 处理（这也是绝大多数现场的情况）。

回环地址（127.x / localhost / 0.0.0.0）一律视为不可用：手机扫到它连的是手机自己。
"""

import re
import urllib.parse

SCHEME = "x32remote"
VER = 1
DEF_OSC = 10023

# 主机名/IPv4：字母数字开头，允许中间的点与连字符。刻意不收 IPv6 字面量，
# OSC 走的是 IPv4，且方括号会显著拉长二维码。
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-]{0,62})$")
_LOOPBACK = ("localhost", "127.", "0.0.0.0", "::1")


def valid_host(host):
    if not isinstance(host, str):
        return False
    h = host.strip()
    if not h or not _HOST_RE.match(h):
        return False
    low = h.lower()
    if low == "localhost" or low.startswith("127.") or low == "0.0.0.0" or low == "::1":
        return False
    # IPv4 的每段必须 ≤ 255；不合法就当成主机名，交给人眼判断
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", h):
        return all(0 <= int(p) <= 255 for p in h.split("."))
    return True


def valid_port(p):
    try:
        n = int(p)
    except (TypeError, ValueError):
        return False
    return 1 <= n <= 65535


def pair_text(host, osc_port, web_port=None):
    """拼配对码。参数不合法抛 ValueError（调用方负责转成 400 / 空值）。"""
    host = (host or "").strip()
    if not valid_host(host):
        raise ValueError("bad host: %r" % host)
    osc = int(osc_port)
    if not valid_port(osc):
        raise ValueError("bad osc port: %r" % osc_port)
    parts = ["v=%d" % VER, "host=" + host, "osc=%d" % osc]
    if web_port is not None:
        w = int(web_port)
        if not valid_port(w):
            raise ValueError("bad web port: %r" % web_port)
        parts.append("web=%d" % w)
    return "%s://pair?%s" % (SCHEME, "&".join(parts))


def _port(raw, default=None):
    """宽松取端口：空 → default；非法 → None（由调用方判错）。"""
    s = (raw or "").strip()
    if not s:
        return default
    try:
        n = int(s)
    except ValueError:
        return None
    return n if valid_port(n) else None


def parse(raw):
    """解析配对码 / 网页地址 / host[:port]，失败返回 None。

    返回值::

        {"host": "192.168.50.57", "osc": 10023, "web": 8080 or None}
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > 200:
        return None
    low = s.lower()
    host, osc, web = "", DEF_OSC, None

    if low.startswith(SCHEME + "://"):
        u = urllib.parse.urlparse(s)
        if u.netloc not in ("", "pair"):
            return None
        q = urllib.parse.parse_qs(u.query)
        host = (q.get("host") or [""])[0].strip()
        osc = _port((q.get("osc") or [""])[0], DEF_OSC)
        web = _port((q.get("web") or [""])[0], None)
    elif low.startswith(("http://", "https://")):
        try:
            u = urllib.parse.urlparse(s)
            host = (u.hostname or "").strip()
            web = u.port                     # 非法端口会抛 ValueError
        except ValueError:
            return None
        osc = DEF_OSC                        # 网页地址里没有 OSC 端口，按默认
    else:
        core = s.split("/", 1)[0].strip()
        if core.count(":") == 1:
            h, p = core.split(":")
            host = h.strip()
            osc = _port(p)
        else:
            host = core

    if not valid_host(host) or not valid_port(osc):
        return None
    if web is not None and not valid_port(web):
        return None
    return {"host": host, "osc": int(osc), "web": int(web) if web else None}
