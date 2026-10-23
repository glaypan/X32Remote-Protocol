#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 X32 Remote 发布包。

用法:
    python tools/make_release.py                  # 用默认版本（见 DEFAULT_VERSION）
    python tools/make_release.py --version 1.2.0  # 指定版本
    python tools/make_release.py --apk outputs/X32Remote-1.1.0.apk

产出（都在 outputs/ 下）：
  X32Remote-v<版本>-full.zip     完整发布包：全部源码 + 产物 + 全部文档（不含 .git）
  X32Remote-v<版本>-assets.zip   仅产物：Android APK + iOS IPA + 网页试用版 + 发布说明

排除规则：.git / .gradle / .kotlin / .localverify / __pycache__ / .build /
build / DerivedData / 临时文件 / 签名密钥（x32release.jks 与 local.properties 不入包）。

Android 源码在仓库外（ROOT 同级的 X32RemoteAndroid/，须纯 ASCII 路径），
存在则纳入 full 包；不存在则跳过，不阻塞发布。
"""
import argparse
import os
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"

DEFAULT_VERSION = "1.4.0"
# 本版要随包发布的 Android 产物（相对 outputs/ 的文件名）
DEFAULT_APK = "X32Remote-1.2.0.apk"
DEFAULT_IPA = "X32Remote-unsigned.ipa"
# 网页试用版（单文件零依赖，v1.2.0 起随包发布）
DEFAULT_WEBDEMO = "X32Remote-WebDemo.html"
# 桌面版产物（可选）：存在就并入 full / assets 包，缺失不报错。
# 命名带版本号 —— 同一个 Release 里既有 macOS 又有 Windows 时，
# 不带版本的 zip 名称会让人分不清是哪个平台的哪一版。
MACOS_ZIP = "X32Remote-macOS-{ver}-unsigned.zip"
WIN_ZIP = "X32Remote-{ver}-windows-x64.zip"
# Android 工程在本仓库外（与 x32遥控/ 同级），存在才纳入 full 包
ANDROID_SRC = ROOT.parent / "X32RemoteAndroid"

# 目录名 / 文件名 黑名单（任一命中即跳过，含子串匹配）
# ⚠️ `build` 必须排除：Android app/build/ 下 700+ 个 dex/中间产物，
#    曾让 full 包从 5MB 涨到 28MB（源码包混入构建垃圾）
EXCLUDE_DIRS = {".git", ".gradle", ".kotlin", ".localverify", "__pycache__",
                ".build", "build", "DerivedData", "xcuserdata", ".swiftpm",
                ".git.bak", ".swift-setup"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".DS_Store", ".xcuserstate")
# 签名密钥与本机 SDK 路径一律不入包 —— 公开包里散落签名密钥等于把覆盖安装的可信度拱手让人
EXCLUDE_NAMES = {".DS_Store", "Package.resolved", "x32release.jks",
                 "local.properties"}


def full_items(apk: str, ipa: str, notes: str, ver: str) -> list:
    items = [
        ("X32Remote-ios",              "X32Remote-ios"),
        ("X32Simulator",               "X32Simulator"),
        ("tools",                      "tools"),
        ("Android-安装使用说明.md",     "docs/Android-安装使用说明.md"),
        ("iOS-真机直装指南.md",         "docs/iOS-真机直装指南.md"),
        ("共享协议规范.md",             "docs/共享协议规范.md"),
        (notes,                         f"docs/RELEASE_NOTES_v{ver}.md"),
        (f"outputs/{apk}",              f"artifacts/{apk}"),
        (f"outputs/{ipa}",              f"artifacts/{ipa}"),
        (f"outputs/{DEFAULT_WEBDEMO}",  f"artifacts/{DEFAULT_WEBDEMO}"),
    ]
    if ANDROID_SRC.is_dir():
        items.append((str(ANDROID_SRC), "X32RemoteAndroid"))
    return items


def asset_items(apk: str, ipa: str, notes: str) -> list:
    # 桌面版不进 assets.zip：它是几万兆的二进制，作为独立附件更省下载
    return [
        (f"outputs/{apk}",  apk),
        (f"outputs/{ipa}",  ipa),
        (f"outputs/{DEFAULT_WEBDEMO}", DEFAULT_WEBDEMO),
        (notes,             pathlib.Path(notes).name),
    ]


def desktop_artifacts(ver: str) -> list:
    """outputs/ 里真实存在的桌面版 zip（macOS 在前：体积大、优先被看到）。"""
    out = []
    for pat in (MACOS_ZIP, WIN_ZIP):
        name = pat.format(ver=ver)
        if (OUT / name).is_file():
            out.append(name)
    return out


def make_readme(ver: str, apk: str, ipa: str, webdemo: str) -> str:
    return f"""X32 Remote v{ver} —— 发布包说明
================================

适配调音台：Behringer X32 / Midas M32（OSC over UDP，端口 10023）

目录结构
--------
  docs/          所有说明文档
    RELEASE_NOTES_v{ver}.md   ← 先看这个：本版改动与升级注意
    iOS-真机直装指南.md         iOS 从签名到安装的完整流程
    Android-安装使用说明.md     Android 安装与使用
    共享协议规范.md             三端共同遵循的协议契约
  artifacts/     可直接使用的成品
    {ipa}     iOS（需自行签名，见 docs）
    {apk}        Android（直接安装）
    {webdemo}  网页试用版（单文件，浏览器直接打开，零依赖）
  桌面版（macOS / Windows）单独发布，见 Release 附件 —— 它们是几十 MB 的
  自带运行时二进制，塞进这里只会让同一个 Release 重复下载两次。
  X32Remote-ios/  iOS 工程源码（SwiftUI + SwiftPM 本地包）
  X32RemoteAndroid/ Android 工程源码（Kotlin）
  X32Simulator/   服务端 + Web 控制台（Python 单文件）
  tools/          发布与校验工具

快速开始
--------
Android：把 artifacts/{apk} 传到手机安装（Android 8.0+）。
iOS    ：读 docs/iOS-真机直装指南.md。IPA 未签名，需用 Sideloadly 以
         免费 Apple ID 签名后安装（每 7 天重签）。要求 iOS 17.0+。
网页试用：双击 artifacts/{webdemo}（或 python X32Simulator/x32_simulator.py
         --web-port 8080 后打开 http://localhost:8080 玩完整控制台）。

本版重点（v1.4.0）
--------
自动增益 / 自动推子（Vocal Rider 式），并把这套能力接到 iOS / Android：
  1. 目标电平（默认 -18 dBFS）+ 双执行器：推子模式真的动推子，增益模式动数字增益
  2. 三档速度 = 平均窗口 / 死区 / 每秒最大修正
     （0.9/0.35/0.12 s · 0.6/0.4/0.3 dB · 1.5/5/15 dB/s）
  3. 目标曲线（riding target）：时间-电平点列，可循环；主歌 -24、副歌 -16 这类段落差
  4. 侧链触发：看另一路电平、修这一路（主持人说话 → 音乐自动让开）
  5. 表源可切：推子后（闭环）/ 推子前（开环，推子怎么动都不影响测量）
  6. iOS「自动」页签 + Android 菜单入口：两端只做配置与显示，控制律在电脑端服务
  7. 电平模型重做：信号源与推子位置拆开，避免自动推子自激塌陷
另：v1.3.x 的扫码进入、75 项通道参数、/subscribe、161 档、dB 4 段曲线全部保留
App 两端（iOS / Android）本版无代码变动，安装包为同源码重新构建。

已知限制
--------
1. iOS 后台约 30 秒挂起 → 只能当前台遥控器，当不了常驻服务端。需常驻请用 Android。
2. iOS 卡片库升级后回落到出厂卡片（两代格式差异大，不做静默迁移）。
3. 混响卡片动作未进 iOS / Android（白名单保持 12 种，避免配置互不兼容）。
4. Wing / Yamaha / A&H 仅有驱动骨架（ready=false），写入被拒并记日志。
5. Android 签名密钥（x32release.jks）与 local.properties 按惯例不入包；
   如需自行重编 Android，请用自己的密钥配置签名。
6. 网页试用版（artifacts/ 下的 WebDemo.html）是 2026-10-18 的纯前端虚拟台快照，
   自带演示引擎、不依赖服务端，但**不含本版新增的通道参数页签**；
   要看完整新界面请用在线版，或本地跑 X32Simulator 后开 http://localhost:8080。
7. 26 项参数出厂默认值仍待真机核对（/api/info.calibration 会列出，
   tools/verify_defaults.py 可一键比对），未核对前不参与回归断言。
"""


def excluded(path: pathlib.Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    if path.suffix in EXCLUDE_SUFFIX or path.name in EXCLUDE_NAMES:
        return True
    return False


def add_path(zf: zipfile.ZipFile, src: pathlib.Path, arc: str, counter: dict) -> None:
    """把 src（文件或目录）以 arc 为路径写入 zip。"""
    if src.is_dir():
        for dirpath, dirnames, filenames in os.walk(src):
            d = pathlib.Path(dirpath)
            dirnames[:] = [x for x in dirnames
                           if x not in EXCLUDE_DIRS and not excluded(d / x)]
            for fn in filenames:
                fp = d / fn
                if excluded(fp):
                    continue
                rel = fp.relative_to(src)
                zf.write(fp, str(pathlib.PurePosixPath(arc) / rel))
                counter["n"] += 1
                counter["bytes"] += fp.stat().st_size
    elif src.is_file():
        zf.write(src, arc)
        counter["n"] += 1
        counter["bytes"] += src.stat().st_size
    else:
        raise FileNotFoundError(src)


def build(name: str, items: list, counter_out: dict, readme: str = None) -> pathlib.Path:
    target = OUT / name
    counter = {"n": 0, "bytes": 0}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        if readme:
            zf.writestr("README.txt", readme)
        for rel_src, arc in items:
            add_path(zf, ROOT / rel_src, arc, counter)
    mb = target.stat().st_size / 1048576
    print(f"  {name}: {counter['n']} 个文件, 未压缩 {counter['bytes']/1048576:.1f} MB, "
          f"压缩后 {mb:.2f} MB")
    counter_out.update(counter)
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--apk", default=DEFAULT_APK, help="outputs/ 下的 Android 产物名")
    ap.add_argument("--ipa", default=DEFAULT_IPA, help="outputs/ 下的 iOS 产物名")
    args = ap.parse_args()
    ver = args.version
    notes = f"RELEASE_NOTES_v{ver}.md"

    if not (ROOT / notes).is_file():
        print(f"! 缺少发布说明 {notes}，请先创建")
        return 1

    print(f"打包 v{ver} 发布包")
    full = build(f"X32Remote-v{ver}-full.zip",
                 full_items(args.apk, args.ipa, notes, ver), {},
                 readme=make_readme(ver, args.apk, args.ipa, DEFAULT_WEBDEMO))
    assets = build(f"X32Remote-v{ver}-assets.zip",
                   asset_items(args.apk, args.ipa, notes), {})

    print("\n输出:")
    for p in (full, assets):
        print(f"  {p}  ({p.stat().st_size/1048576:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
