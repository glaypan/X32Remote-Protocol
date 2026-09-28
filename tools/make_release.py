#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 X32 Remote 发布包。

用法:
    python tools/make_release.py                  # 用默认版本（见 DEFAULT_VERSION）
    python tools/make_release.py --version 1.1.1  # 指定版本
    python tools/make_release.py --apk outputs/X32Remote-1.1.0.apk

产出（都在 outputs/ 下）：
  X32Remote-v<版本>-full.zip     完整发布包：全部源码 + 产物 + 全部文档（不含 .git）
  X32Remote-v<版本>-assets.zip   仅产物：Android APK + iOS IPA + 发布说明

排除规则：.git / .localverify / __pycache__ / .build / DerivedData / 临时文件。
"""
import argparse
import os
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"

DEFAULT_VERSION = "1.1.1"
# 本版要随包发布的 Android 产物（相对 outputs/ 的文件名）
DEFAULT_APK = "X32Remote-1.1.0.apk"
DEFAULT_IPA = "X32Remote-unsigned.ipa"

# 目录名 / 文件名 黑名单（任一命中即跳过，含子串匹配）
EXCLUDE_DIRS = {".git", ".localverify", "__pycache__", ".build", "DerivedData",
                "xcuserdata", ".swiftpm", ".git.bak", ".swift-setup"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".DS_Store", ".xcuserstate")
EXCLUDE_NAMES = {".DS_Store", "Package.resolved"}


def full_items(apk: str, ipa: str, notes: str, ver: str) -> list:
    return [
        ("X32Remote-ios",              "X32Remote-ios"),
        ("X32Simulator",               "X32Simulator"),
        ("tools",                      "tools"),
        ("Android-安装使用说明.md",     "docs/Android-安装使用说明.md"),
        ("iOS-真机直装指南.md",         "docs/iOS-真机直装指南.md"),
        ("共享协议规范.md",             "docs/共享协议规范.md"),
        (notes,                         f"docs/RELEASE_NOTES_v{ver}.md"),
        (f"outputs/{apk}",              f"artifacts/{apk}"),
        (f"outputs/{ipa}",              f"artifacts/{ipa}"),
    ]


def asset_items(apk: str, ipa: str, notes: str) -> list:
    return [
        (f"outputs/{apk}",  apk),
        (f"outputs/{ipa}",  ipa),
        (notes,             pathlib.Path(notes).name),
    ]


def make_readme(ver: str, apk: str, ipa: str) -> str:
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
  artifacts/     可直接安装的成品
    {ipa}     iOS（需自行签名，见 docs）
    {apk}        Android（直接安装）
  X32Remote-ios/  iOS 工程源码（SwiftUI + SwiftPM 本地包）
  X32Simulator/   服务端 + Web 控制台（Python 单文件）
  tools/          发布与校验工具

快速开始
--------
Android：把 artifacts/{apk} 传到手机安装。
iOS    ：读 docs/iOS-真机直装指南.md。IPA 未签名，需用 Sideloadly 以
         免费 Apple ID 签名后安装（每 7 天重签）。要求 iOS 17.0+。
网页版 ：python X32Simulator/x32_simulator.py --port 10023 --web-port 28080
         然后浏览器打开 http://localhost:28080

本版重点
--------
把 v1.1.0 在 iOS 端修好的三处语义偏差，同样修到 Android：
  1. wait 曾 Thread.sleep 阻塞整个引擎线程且双重计时 → 改真 no-op
  2. unmute_all 只写 48 路 → 补齐 8 组共 79 路
  3. fade_from 未预置 frm → 取起点前先写入 frm
另修 Android 卡片链接续会把自己停掉（误调 run()）并补 0.4 秒喘息。

已知限制
--------
1. iOS 后台约 30 秒挂起 → 只能当前台遥控器，当不了常驻服务端。需常驻请用 Android。
2. iOS 卡片库升级后回落到出厂卡片（两代格式差异大，不做静默迁移）。
3. M32 / Wing 地址差异未适配，仅覆盖 X32 系列。
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
                 readme=make_readme(ver, args.apk, args.ipa))
    assets = build(f"X32Remote-v{ver}-assets.zip",
                   asset_items(args.apk, args.ipa, notes), {})

    print("\n输出:")
    for p in (full, assets):
        print(f"  {p}  ({p.stat().st_size/1048576:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
