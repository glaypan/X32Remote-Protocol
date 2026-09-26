#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包 X32 Remote v1.1.0 发布包。

产出（都在 outputs/ 下）：
  X32Remote-v1.1.0-full.zip     完整发布包：三端源码 + 产物 + 全部文档（不含 .git）
  X32Remote-v1.1.0-assets.zip   仅产物：iOS IPA + Android APK + 发布说明（用于上传）

排除规则：.git / .localverify / __pycache__ / .build / DerivedData / 临时文件。
"""
import os
import pathlib
import zipfile

ROOT = pathlib.Path(r"E:/AI/workbuddy/x32遥控")
OUT = ROOT / "outputs"

# 目录名 / 文件名 黑名单（任一命中即跳过，含子串匹配）
EXCLUDE_DIRS = {".git", ".localverify", "__pycache__", ".build", "DerivedData",
                "xcuserdata", ".swiftpm", ".git.bak", ".swift-setup"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".log", ".DS_Store", ".xcuserstate")
EXCLUDE_NAMES = {".DS_Store", "Package.resolved"}

# 完整包的内容（源路径, 打包内的相对路径）；源为目录则整棵打入
FULL_ITEMS = [
    ("X32Remote-ios",                  "X32Remote-ios"),
    ("X32Simulator",                   "X32Simulator"),
    ("Android-安装使用说明.md",         "docs/Android-安装使用说明.md"),
    ("iOS-真机直装指南.md",             "docs/iOS-真机直装指南.md"),
    ("共享协议规范.md",                 "docs/共享协议规范.md"),
    ("RELEASE_NOTES_v1.1.0.md",        "docs/RELEASE_NOTES_v1.1.0.md"),
    ("outputs/X32Remote-1.0.0.apk",     "artifacts/X32Remote-1.0.0.apk"),
    ("outputs/X32Remote-unsigned.ipa",  "artifacts/X32Remote-unsigned.ipa"),
]

# 仅产物的包
ASSET_ITEMS = [
    ("outputs/X32Remote-unsigned.ipa",  "X32Remote-unsigned.ipa"),
    ("outputs/X32Remote-1.0.0.apk",     "X32Remote-1.0.0.apk"),
    ("RELEASE_NOTES_v1.1.0.md",         "RELEASE_NOTES_v1.1.0.md"),
]

README = """X32 Remote v1.1.0 —— 发布包说明
================================

适配调音台：Behringer X32 / Midas M32（OSC over UDP，端口 10023）

目录结构
--------
  docs/          所有说明文档
    RELEASE_NOTES_v1.1.0.md    ← 先看这个：本版新增与升级注意
    iOS-真机直装指南.md         iOS 从签名到安装的完整流程
    Android-安装使用说明.md     Android 安装与使用
    共享协议规范.md             三端共同遵循的协议契约（v1.1）
  artifacts/     可直接安装的成品
    X32Remote-unsigned.ipa     iOS（需自行签名，见 docs）
    X32Remote-1.0.0.apk        Android（直接安装）
  X32Remote-ios/  iOS 工程源码（SwiftUI + SwiftPM 本地包）
  X32Simulator/   服务端 + Web 控制台（Python 单文件）

快速开始
--------
Android：把 artifacts/X32Remote-1.0.0.apk 传到手机安装。
iOS    ：读 docs/iOS-真机直装指南.md。IPA 未签名，需用 Sideloadly 以
         免费 Apple ID 签名后安装（每 7 天重签）。要求 iOS 17.0+。
网页版 ：python X32Simulator/x32_simulator.py --port 10023 --web-port 28080
         然后浏览器打开 http://localhost:28080

本版重点
--------
iOS 端补齐「桥接模式」与「时间轴调度器」，三端功能全面对齐。
并修掉三处语义偏差（wait 双重计时 / unmute_all 漏 31 路 / 卡片链缺喘息）。

已知限制
--------
1. iOS 后台约 30 秒挂起 → 只能当前台遥控器，当不了常驻服务端。需常驻请用 Android。
2. iOS 卡片库升级后回落到出厂卡片（两代格式差异大，不做静默迁移）。
3. Android 的时间轴引擎存在同源三处偏差，计划下一版修复。
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
            # 原地剪枝，避免走进黑名单目录
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


def build(name: str, items: list, extra_readme: bool) -> pathlib.Path:
    target = OUT / name
    counter = {"n": 0, "bytes": 0}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        if extra_readme:
            zf.writestr("README.txt", README)
        for rel_src, arc in items:
            src = ROOT / rel_src
            add_path(zf, src, arc, counter)
    mb = target.stat().st_size / 1048576
    print(f"  {name}: {counter['n']} 个文件, 未压缩 {counter['bytes']/1048576:.1f} MB, "
          f"压缩后 {mb:.2f} MB")
    return target


def main() -> int:
    print("打包 v1.1.0 发布包")
    full = build("X32Remote-v1.1.0-full.zip", FULL_ITEMS, extra_readme=True)
    assets = build("X32Remote-v1.1.0-assets.zip", ASSET_ITEMS, extra_readme=False)

    print("\n输出:")
    for p in (full, assets):
        print(f"  {p}  ({p.stat().st_size/1048576:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
