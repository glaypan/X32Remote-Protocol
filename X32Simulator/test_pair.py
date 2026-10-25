# -*- coding: utf-8 -*-
"""手机 App 扫码配对码回归（纯标准库，不起服务、不占端口）。

为什么单独一组：
  网页二维码编码的是 ``http://ip:web/``，手机相机扫了直接开控制台；
  但原生 App 要的是 **OSC 的 UDP 端口**。用户把 UDP 端口从 10023 改成
  别的值之后，网页地址里那个 HTTP 端口顶替不了它 —— 而且错得很安静：
  App 会"连上了"却一条回包都没有。这组把拼串、解析、坏值拒绝
  和"App 也能吃网页地址"逐条钉死。

    python test_pair.py
"""
import sys

sys.path.insert(0, ".")
from x32sim.pair import parse, pair_text, valid_host, valid_port  # noqa: E402

results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + str(detail)) if detail and not ok else ""))


def raises(fn, *a):
    try:
        fn(*a)
    except ValueError:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- 1. 拼串
print("== 1. 配对码拼装 ==")
CODE = pair_text("192.168.50.57", 10023, 8080)
check("格式固定 x32remote://pair", CODE == "x32remote://pair?v=1&host=192.168.50.57&osc=10023&web=8080", CODE)
check("长度 < 80 字节（二维码版本 4，扫得快）", len(CODE) < 80, len(CODE))
check("不带网页端口时不出现 web=", "web=" not in pair_text("10.0.0.9", 10023))
check("主机名可用（不限于 IP）", pair_text("mixer.local", 10025, 8080) ==
      "x32remote://pair?v=1&host=mixer.local&osc=10025&web=8080")

print("\n== 2. 坏值必须拒绝 ==")
check("拒绝回环 127.0.0.1", raises(pair_text, "127.0.0.1", 10023, 8080))
check("拒绝 0.0.0.0", raises(pair_text, "0.0.0.0", 10023, 8080))
check("拒绝 localhost", raises(pair_text, "localhost", 10023, 8080))
check("拒绝空主机", raises(pair_text, "", 10023))
check("拒绝含空格主机", raises(pair_text, "192.168.1.5 x", 10023))
check("拒绝 256.1.1.1", not valid_host("256.1.1.1"))
check("接受 255.255.255.255", valid_host("255.255.255.255"))
check("拒绝端口 0", raises(pair_text, "192.168.1.5", 0))
check("拒绝端口 65536", raises(pair_text, "192.168.1.5", 65536))
check("拒绝非数字端口", raises(pair_text, "192.168.1.5", "abc"))
check("valid_port 边界 1/65535", valid_port(1) and valid_port(65535) and not valid_port(65536))

# ---------------------------------------------------------------- 3. 解析
print("\n== 3. 解析（App 端同规则）==")
check("原样回读", parse(CODE) == {"host": "192.168.50.57", "osc": 10023, "web": 8080}, parse(CODE))
check("无 web 字段 → None", parse(pair_text("192.168.1.5", 10025))["web"] is None)
check("网页地址 → 默认 10023 + 网页端口",
      parse("http://192.168.50.57:8080/") == {"host": "192.168.50.57", "osc": 10023, "web": 8080})
check("网页地址无端口 → web None",
      parse("http://192.168.50.57/") == {"host": "192.168.50.57", "osc": 10023, "web": None})
check("host:port 视作 OSC 端口",
      parse("192.168.1.9:10025") == {"host": "192.168.1.9", "osc": 10025, "web": None})
check("裸 IP → 默认 10023",
      parse(" 192.168.1.9 ") == {"host": "192.168.1.9", "osc": 10023, "web": None})
check("拒绝回环地址（扫到它会连手机自己）", parse("127.0.0.1:10023") is None)
check("拒绝 javascript: 伪协议", parse("javascript:alert(1)") is None)
check("拒绝 ftp://", parse("ftp://192.168.1.5/") is None)
check("拒绝超长串", parse("x32remote://pair?host=" + "a" * 220) is None)
check("拒绝端口越界", parse("192.168.1.5:70000") is None)
check("拒绝空串/非字符串", parse("") is None and parse(None) is None)
check("容忍前后空白", parse("  " + CODE + "  ") == parse(CODE))

print("\n== 4. 往返一致性 ==")
ok = True
for host in ("192.168.0.2", "10.1.2.3", "mixer-1.local", "172.16.31.250"):
    for osc, web in ((10023, 8080), (10025, 8090), (65535, 1)):
        p = parse(pair_text(host, osc, web))
        if p != {"host": host, "osc": osc, "web": web}:
            ok = False
            print("  往返失败:", host, osc, web, "→", p)
check("12 组 host×port 往返一致", ok)

print("\n== 5. 与 python-qrcode 交叉验证（装了才跑）==")
try:
    import qrcode  # noqa: E402
    from qrcode.constants import ERROR_CORRECT_M  # noqa: E402
    from x32sim import qr  # noqa: E402

    _q = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=1, border=0)
    _q.add_data(CODE, optimize=0)          # 关掉自动分段，对齐"单一字节段"
    _q.make(fit=True)
    _ref = [[bool(v) for v in row] for row in _q.get_matrix()]
    _cw = qr._interleave(qr._encode_bits(CODE.encode(), _q.version), _q.version)
    _same = any(qr._build(_q.version, _cw, mk) == _ref for mk in range(8))
    check("配对码矩阵与参考实现逐位一致（v%d / %d×%d）" % (_q.version, len(_ref), len(_ref)), _same)
    check("配对码用到的版本 ≤ 5（扫得快、屏幕上也够大）", _q.version <= 5, _q.version)
except ImportError:
    print("SKIP 未安装 python-qrcode（运行时不需要它）")

print("\n" + "=" * 60)
print("%d/%d 通过" % (sum(results), len(results)))
for i, name, detail in failed:
    print("失败项 #%d: %s%s" % (i, name, (" | " + str(detail)) if detail else ""))
sys.exit(0 if all(results) else 1)
