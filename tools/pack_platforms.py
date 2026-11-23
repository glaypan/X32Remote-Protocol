#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按平台分包：一个平台一个 zip（v1.8.0 起的发布形态）。

以前是 full.zip（全部源码）+ assets.zip（什么平台都塞一点），
用户下 60 MB 只为拿一个 APK。现在改成：

    X32Remote-<ver>-windows-x64.zip   Windows 便携版（build_windows.py 产出，直接复用）
    X32Remote-macOS-<mver>-unsigned.zip  macOS App（CI 产出，直接复用）
    X32Remote-<ver>-android.zip       APK + 安装说明
    X32Remote-<ver>-ios.zip           未签名 IPA + 真机直装指南
    X32Remote-<ver>-server.zip        服务端单文件 + 网页控制台 + 规范
    X32Remote-<ver>-source.zip        四端源码（供二次开发 / 自查）

外加 SHA256SUMS.txt。缺哪个产物就跳过那个包（不生成空壳、不报错），
这样只发服务端或只发 Android 时也能用。

用法:
    python tools/pack_platforms.py --version 1.8.0 --macos-version 1.2.0
    python tools/pack_platforms.py --version 1.8.0 --only android ios
"""
import argparse
import hashlib
import os
import pathlib
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_release import EXCLUDE_DIRS, add_path, excluded            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
ANDROID_SRC = ROOT.parent / "X32RemoteAndroid"


def notes(ver):
    return ROOT / ("RELEASE_NOTES_v%s.md" % ver)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(name, items, extra_text=None):
    """items = [(源路径, zip 内路径)]；源缺失就跳过该条，全缺则不出包。"""
    present = [(s, a) for s, a in items if s and pathlib.Path(s).exists()]
    if not present:
        print("跳过 %s（产物缺失）" % name)
        return None
    target = OUT / name
    counter = {"n": 0, "bytes": 0}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in present:
            add_path(zf, pathlib.Path(src), arc, counter)
        if extra_text:
            zf.writestr("README.txt", extra_text)
    mb = target.stat().st_size / 1048576
    print("生成 %-42s %5.1f MB  (%d 个文件)" % (name, mb, counter["n"]))
    return target


README_ANDROID = """X32 Remote · Android {ver}
=============================

安装
----
1. 把 X32Remote-{mver}.apk 传到手机（微信/QQ 传文件、U 盘、或直接下载均可）
2. 点开安装：系统会提示「未知来源应用」—— 允许本次安装即可
   （这是未上架应用商店的常规提示，不是应用本身有问题）
3. 要求 Android 8.0 或更高

连台子
------
- 手机与电脑/调音台在同一个 WiFi
- 打开 App → 顶部「🔗 连接」手填 IP，或「🔍 扫描」扫二维码
- 二维码在电脑端网页控制台 → 设置 → 「手机 / 安卓平板连接」，有两张：
  左边「浏览器」码给相机/微信，右边「App」码给 App 内扫码（带 OSC 端口）

本版新增
--------
效果器图鉴：效果器页顶栏「🖼 图鉴」→ 61 个 X32 算法 + 52 个 WING 效果的
真机界面截图；机架每行、详情页顶部也会显示当前算法的台面面板。
"""

README_IOS = """X32 Remote · iOS {mver}（未签名 IPA）
=====================================

本包里的 IPA **没有签名**，不能直接双击安装。完整流程见
`iOS-真机直装指南.md`，简要步骤：

1. Windows / macOS 上装 Sideloadly（https://sideloadly.io）
2. 手机插线连电脑，Sideloadly 选本包里的 IPA
3. Apple ID 填自己的（免费账号即可），点 Start
4. 手机上：设置 → 通用 → VPN与设备管理 → 信任你的开发者证书
5. 免费账号签的 App **7 天过期**，到期重签一次即可（数据不丢）

要求 iOS 17.0 或更高。

本版新增
--------
效果器图鉴：效果器列表每行带真机面板缩略图；详情页顶部显示大图；
工具栏「图鉴」可进图鉴页并点开放大 2 倍。
"""

README_SERVER = """X32 Remote · 服务端 / 网页控制台 {ver}
========================================

零依赖单文件：只需要 Python 3.9+，不需要 pip install 任何东西。

启动
----
    python x32_simulator.py

默认：OSC 监听 UDP 10023，网页控制台 http://localhost:8080/
手机与电脑同一 WiFi，手机浏览器打开 http://<电脑IP>:8080/ 即可遥控。

常用参数
--------
    --port 10023           OSC 端口（改过之后 App 侧也要改）
    --web-port 8080        网页端口
    --bridge 192.168.1.50:10023
                           桥接到真台：把本服务当成真台的中转，
                           这样网页与手机可以同时控一台真台
    --config my.json       指定配置文件

本版新增
--------
效果器图鉴：混响页「🖼 效果器图鉴」按钮；FX 槽卡片显示真机面板。
图片 base64 内嵌在单文件里（约 0.72 MB），无需外挂资源目录。

完整协议契约见 `共享协议规范.md`（v1.12）。
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="发布版本，如 1.8.0")
    ap.add_argument("--macos-version", default=None, help="macOS App 版本，默认同发布版本")
    ap.add_argument("--android-version", default=None, help="Android 版本号，默认同发布版本")
    ap.add_argument("--ios-version", default=None, help="iOS 版本号，默认同发布版本")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只打这几个平台: windows macos android ios server source")
    a = ap.parse_args()

    ver = a.version
    mver = a.macos_version or ver
    aver = a.android_version or ver
    iver = a.ios_version or ver
    nt = notes(ver)
    want = set(a.only) if a.only else {"windows", "macos", "android", "ios", "server", "source"}

    made = []

    if "windows" in want:
        w = OUT / ("X32Remote-%s-windows-x64.zip" % ver)
        if w.is_file():
            print("复用 %-42s %5.1f MB" % (w.name, w.stat().st_size / 1048576))
            made.append(w)
        else:
            print("跳过 Windows（先跑 tools/build_windows.py --version %s）" % ver)

    if "macos" in want:
        m = OUT / ("X32Remote-macOS-%s-unsigned.zip" % mver)
        if m.is_file():
            print("复用 %-42s %5.1f MB" % (m.name, m.stat().st_size / 1048576))
            made.append(m)
        else:
            print("跳过 macOS（需 CI 产出 X32Remote-macOS-%s-unsigned.zip）" % mver)

    if "android" in want:
        apk = OUT / ("X32Remote-%s.apk" % aver)
        p = build("X32Remote-%s-android.zip" % ver, [
            (apk, apk.name),
            (ROOT / "Android-安装使用说明.md", "Android-安装使用说明.md"),
            (nt, nt.name),
        ], README_ANDROID.format(ver=ver, mver=aver))
        if p:
            made.append(p)

    if "ios" in want:
        ipa = OUT / "X32Remote-unsigned.ipa"
        p = build("X32Remote-%s-ios.zip" % ver, [
            (ipa, "X32Remote-%s-unsigned.ipa" % iver),
            (ROOT / "iOS-真机直装指南.md", "iOS-真机直装指南.md"),
            (nt, nt.name),
        ], README_IOS.format(ver=ver, mver=iver))
        if p:
            made.append(p)

    if "server" in want:
        p = build("X32Remote-%s-server.zip" % ver, [
            (ROOT / "X32Simulator" / "x32_simulator.py", "x32_simulator.py"),
            (ROOT / "X32Simulator" / "使用说明.md", "使用说明.md"),
            (ROOT / "共享协议规范.md", "共享协议规范.md"),
            (nt, nt.name),
        ], README_SERVER.format(ver=ver))
        if p:
            made.append(p)

    if "source" in want:
        items = [
            (ROOT / "X32Remote-ios", "X32Remote-ios"),
            (ROOT / "X32Simulator", "X32Simulator"),
            (ROOT / "tools", "tools"),
            (ROOT / "共享协议规范.md", "docs/共享协议规范.md"),
            (nt, "docs/" + nt.name),
        ]
        if ANDROID_SRC.is_dir():
            items.append((ANDROID_SRC, "X32RemoteAndroid"))
        p = build("X32Remote-%s-source.zip" % ver, items,
                  "X32 Remote v%s 源码包（iOS / Android / 服务端 / 工具）。\n"
                  "构建说明见 docs/共享协议规范.md 与各子目录 README。\n" % ver)
        if p:
            made.append(p)

    # ---- SHA256SUMS：只覆盖本次实际存在的附件
    if made:
        sums = OUT / "SHA256SUMS.txt"
        lines = []
        for f in sorted(made):
            lines.append("%s  %s" % (sha256(f), f.name))
        sums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        print("\n写出 %s（%d 个附件）" % (sums, len(made)))
        for ln in lines:
            print("  " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
