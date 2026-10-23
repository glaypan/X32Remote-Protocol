#!/usr/bin/env python3
"""打包 Windows 便携版：单个 X32Remote.exe（自带运行时，免安装 Python）。

产物: outputs/X32Remote-<版本>-windows-x64.zip
  内容: X32Remote.exe / 使用说明.txt / VERSION.txt

依赖: 开发机需装 pyinstaller（仅打包时用，运行时不引入任何第三方库）。
内置内核永远是 X32Simulator/x32_simulator.py（单文件形态，与命令行版、macOS 版同源）。
"""

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(ROOT, "X32Simulator", "x32_simulator.py")
LAUNCHER = os.path.join(ROOT, "tools", "win_launcher.py")
OUTPUTS = os.path.join(ROOT, "outputs")

README = """X32 Remote · Windows 便携版 {ver}
=================================

这是一个免安装的单文件程序：双击 X32Remote.exe 就得到一台「演出主机」。

一、系统要求
------------
- Windows 10（64 位）或 Windows 11
- 无需安装 Python（exe 已内置运行时）
- 首次启动时 Windows 防火墙会弹窗：请勾选「专用网络」并允许，
  否则手机连不上（公用网络默认拦截入站）

二、使用
--------
1. 双击 X32Remote.exe
2. 会打开一个黑色窗口，显示局域网地址与二维码，同时自动打开浏览器控制台
   （地址通常是 http://127.0.0.1:8080/）
3. 手机连同一个 WiFi，扫描窗口里的二维码即可进入同一控制台
4. **关闭那个黑窗口 = 停止服务**（没有后台残留）

如果 8080 被占用（常见于上次没退干净），启动器会自动顺延到 8081、8082…，
并在窗口里说明。

三、接真调音台
--------------
控制台「设置」里填调音台的 IP 与 UDP 端口 10023；电脑与调音台要在同一网段。
手机端只是这块网页的另一个窗口，所有 OSC 都由这台电脑发出。

四、配置文件与日志
------------------
- 配置：%LOCALAPPDATA%\\X32Remote\\user_config.json
- 删除该文件即恢复出厂设置
- 卸载：直接删掉本文件夹（注册表里没有写入任何东西）

五、命令行参数（排错用）
------------------------
  X32Remote.exe --web-port 8080     指定网页端口
  X32Remote.exe --port 10023        指定 OSC/UDP 端口
  X32Remote.exe --bridge 192.168.1.50:10023   桥接到真调音台并转发
  X32Remote.exe --no-browser        不自动打开浏览器
  X32Remote.exe --no-qr             不在窗口里打印二维码
  X32Remote.exe --help              全部参数

六、已知限制
------------
- 仅 64 位；未做代码签名，SmartScreen 可能提示「未知发布者」→ 选「更多信息 → 仍要运行」
- 网页服务监听 0.0.0.0 且无鉴权（与命令行版同一设计）——请在可信网络使用
- 内核版本见同目录 VERSION.txt
"""


def run(cmd, **kw):
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.1.0")
    ap.add_argument("--no-zip", action="store_true", help="只构建不压缩（调试用）")
    args = ap.parse_args()
    ver = args.version

    if not os.path.exists(SIM):
        raise SystemExit("找不到内核: %s（先跑 tools/make_single.py）" % SIM)
    if not os.path.exists(LAUNCHER):
        raise SystemExit("找不到启动器: %s" % LAUNCHER)

    build = os.path.join(ROOT, "build", "win")
    dist = os.path.join(build, "dist")
    work = os.path.join(build, "work")
    stage = os.path.join(build, "stage")
    for d in (dist, work, stage):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)

    # 内核与入口放同一目录：PyInstaller 才会把 `import x32_simulator` 当成
    # 源码依赖去分析（否则 socketserver 之类不会被收集，运行时报缺模块）。
    os.makedirs(build, exist_ok=True)
    shutil.copy2(SIM, os.path.join(build, "x32_simulator.py"))
    entry = os.path.join(build, "entry.py")
    shutil.copy2(LAUNCHER, entry)

    run([sys.executable, "-m", "PyInstaller",
         "--noconfirm", "--clean", "--onefile", "--console",
         "--name", "X32Remote",
         "--distpath", dist, "--workpath", work, "--specpath", build,
         entry])

    exe = os.path.join(dist, "X32Remote.exe")
    if not os.path.exists(exe):
        raise SystemExit("PyInstaller 没有产出 exe")

    shutil.copy2(exe, os.path.join(stage, "X32Remote.exe"))
    with io.open(os.path.join(stage, "使用说明.txt"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(README.format(ver=ver))
    with io.open(os.path.join(stage, "VERSION.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("X32 Remote for Windows %s\n" % ver)
        f.write("内核: x32_simulator.py（与协议仓 spec 同源）\n")
        f.write("打包: PyInstaller onefile / 64 位\n")

    zip_path = os.path.join(OUTPUTS, "X32Remote-%s-windows-x64.zip" % ver)
    if not args.no_zip:
        os.makedirs(OUTPUTS, exist_ok=True)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for name in ("X32Remote.exe", "使用说明.txt", "VERSION.txt"):
                z.write(os.path.join(stage, name), name)
        data = open(zip_path, "rb").read()
        print("\n== 产物 ==")
        print("  %s  %.2f MB  sha256 %s" % (
            os.path.basename(zip_path), len(data) / 1048576.0,
            hashlib.sha256(data).hexdigest()))
    print("  exe: %.2f MB" % (os.path.getsize(exe) / 1048576.0))


if __name__ == "__main__":
    main()
