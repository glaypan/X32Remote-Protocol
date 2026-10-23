"""Windows 桌面启动器：双击 X32Remote.exe 就得到一台「演出主机」。

为什么需要它（而不是直接双击 x32_simulator.py）：
  1. 目标机器上可能没装 Python —— 本 exe 由 PyInstaller 打包，自带运行时；
  2. 配置文件不该往安装目录写（Program Files / 下载目录常不可写），统一放
     %LOCALAPPDATA%\\X32Remote\\；
  3. 双击后要自动打开控制台页面，否则用户还要自己敲地址；
  4. 端口被占（常见：上次没退干净）要自动顺延，而不是静静失败。

本文件只做"外壳"，控制律与协议全部在内核 x32_simulator.py 里，避免两份逻辑漂移。
"""

import os
import socket
import sys
import threading
import time
import webbrowser

# 内核作为模块导入（而不是 runpy 跑一个数据文件）：
#   PyInstaller 只对**静态 import** 做依赖分析，runpy.run_path 的脚本里
#   `import socketserver` 之类不会被收集，运行时报 ModuleNotFoundError。
try:
    import x32_simulator as core
except ImportError:            # 开发态直接跑本文件时没有同目录内核
    core = None

APP_TITLE = "X32 Remote · 演出主机"
DEFAULT_WEB_PORT = 8080
PORT_SCAN = 10          # 8080 被占时顺延尝试 8081..8089


def base_dir():
    """开发态取脚本目录；PyInstaller 单文件态取解包临时目录 (_MEIPASS)。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local")
    folder = os.path.join(base, "X32Remote")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.dirname(sys.executable)
    return os.path.join(folder, "user_config.json")


def port_in_use(port):
    s = socket.socket()
    s.settimeout(0.25)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def pick_port(want):
    if not port_in_use(want):
        return want
    for p in range(want + 1, want + 1 + PORT_SCAN):
        if not port_in_use(p):
            print("[启动器] 端口 %d 已被占用（上次没退干净？），改用 %d" % (want, p))
            return p
    return want          # 全被占：交给内核报错，别在这里吞掉


def open_when_ready(port, timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_in_use(port):
            try:
                webbrowser.open("http://127.0.0.1:%d/" % port)
            except Exception:
                pass
            return
        time.sleep(0.3)


def set_console_title(title):
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def force_utf8_console():
    """控制台切到 UTF-8，与 Python 输出的 UTF-8 对齐。

    不这么做的话：中文 Windows 控制台是 cp936，而打包进去的字符串是 UTF-8，
    日志里的中文会变成乱码（ASCII 部分正常，最容易误判为"只是显示问题"）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    argv = sys.argv[1:]
    port, open_browser, rest = DEFAULT_WEB_PORT, True, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--web-port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if a.startswith("--web-port="):
            try:
                port = int(a.split("=", 1)[1])
            except ValueError:
                pass
            i += 1
            continue
        if a == "--no-browser":       # 排错/自动化用：不弹浏览器
            open_browser = False
            i += 1
            continue
        rest.append(a)
        i += 1

    port = pick_port(port)
    set_console_title(APP_TITLE)
    force_utf8_console()

    if core is None:                      # 开发态：同目录有内核就直接跑它
        script = os.path.join(base_dir(), "x32_simulator.py")
        if os.path.exists(script):
            print("=" * 56)
            print(" %s（开发态：%s）" % (APP_TITLE, script))
            print("=" * 56)
            sys.argv = [script, "--config", config_path(),
                        "--web-port", str(port)] + rest
            import runpy
            runpy.run_path(script, run_name="__main__")
            return
        print("!! 安装包不完整：找不到内置内核 x32_simulator（不要只拷 exe）。")
        input("按回车退出...")
        raise SystemExit(1)

    print("=" * 56)
    print(" %s" % APP_TITLE)
    print(" 配置文件: %s" % config_path())
    print(" 关闭本窗口 = 停止服务")
    print("=" * 56)

    if open_browser:
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()

    # 与命令行版走同一个入口：不在这里重写任何协议逻辑
    sys.argv = ["x32_simulator.py", "--config", config_path(),
                "--web-port", str(port)] + rest
    core.main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
