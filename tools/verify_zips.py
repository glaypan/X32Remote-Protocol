#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验发布包：CRC / 结构 / 大小 / 关键产物 / sha256。

用法:
    python tools/verify_zips.py                    # 默认版本（见 --version 默认值）
    python tools/verify_zips.py --version 1.3.0
"""
import argparse
import hashlib
import os
import zipfile

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")


def check(name: str) -> None:
    p = os.path.join(OUT, name)
    if not os.path.isfile(p):
        print("%s\n  !! 文件不存在" % name)
        return
    z = zipfile.ZipFile(p)
    bad = z.testzip()
    ipa = [n for n in z.namelist() if n.endswith(".ipa")]
    apk = [n for n in z.namelist() if n.endswith(".apk")]
    size = os.path.getsize(p)
    print("%s" % name)
    print("  items   : %d" % len(z.namelist()))
    print("  CRC     : %s" % ("OK" if bad is None else "BAD " + str(bad)))
    print("  size    : %.2f MB" % (size / 1024 / 1024))
    print("  IPA     : %s" % ipa)
    print("  APK     : %s" % apk)
    print("  sha256  : %s" % hashlib.sha256(open(p, "rb").read()).hexdigest())
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.3.0")
    args = ap.parse_args()
    for suffix in ("full", "assets"):
        check("X32Remote-v%s-%s.zip" % (args.version, suffix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
