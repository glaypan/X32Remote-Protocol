# -*- coding: utf-8 -*-
"""tools/collect_fx_shots.py — 从 X32-Edit 4.4 离线模式采集 61 个效果器面板图。

**只在 Windows 上跑**，只在需要重采时跑。产出是 `outputs/fxshots/*.png`，
再用 `tools/import_fx_shots.py` 的映射转成 `web/fx/x32/*.webp`。

前置
----
1. 装官方编辑器 X32-Edit 4.4（本次用的安装包：
   https://static.makemusic.tn/drivers/Behringer/X32/X32 - X32-Edit - Windows v4.4.zip
   —— 官方 downloads.music-group.com 当时 DNS 不可解析）
2. 打开 X32-Edit，**不连台子**（离线模式），窗口最大化，切到 FX 页、FX1 槽
3. `pip install pillow`

用法
----
    python X32Simulator/tools/collect_fx_shots.py --out outputs/fxshots --count 62

为什么是 62 张：从某一项开始按 61 次「下一项」会回到起点，多采一张用于
确认列表长度，也便于发现漏采。

⚠️ 两个必须记住的坑（都踩过）
------------------------------
1. **Qt 下拉框里 `Home` 只跳到"可见区首项"，不是列表第一项**。
   所以不能靠"先 Home 再从 0 数"确定当前是第几个算法 —— 本次因此整体错位 31 项。
   可靠做法：采完之后**读名称条反推偏移**（或人工核对名称），不要相信下拉位置。
2. **`PrintWindow` 有帧缓存延迟**：切换算法后立刻抓会拿到上一帧，
   出现"图是 Ultimo、名字是 Stereo Combinator"这种图文不同帧。
   所以这里用 `ImageGrab` 实时抓屏，并且切换后等 1.5 s。
   同理，点击要用 `ClientToScreen` 换算（`GetWindowRect` 含标题栏，与客户区差约 51 px）。
"""
import argparse
import ctypes
import ctypes.wintypes as w
import os
import time

from PIL import ImageGrab

u = ctypes.windll.user32
u.SetProcessDPIAware()          # 高分屏下坐标才是物理像素（本机 3840×2160 / DPI 192）

TITLE_KEY = "X32 EDIT"
# 面板区与名称条（窗口客户区像素，3840×2160 / 2x 缩放下实测）
PANEL = (23, 250, 503, 384)     # 480 × 134
NAME = (20, 198, 432, 246)
COMBO = (200, 222)              # 算法下拉框


def find_win(key=TITLE_KEY):
    res = []

    def cb(h, _l):
        EP = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if key in b.value:
                    res.append(h)
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)(cb), 0)
    if not res:
        raise SystemExit("没找到窗口标题含 %r 的窗口（X32-Edit 打开了吗？）" % key)
    return res[0]


def focus(h):
    """ALT 轻敲解锁前台限制，再置前。"""
    u.keybd_event(0x12, 0, 0, 0)
    time.sleep(0.05)
    u.keybd_event(0x12, 0, 2, 0)
    u.ShowWindow(h, 9)
    u.SetForegroundWindow(h)
    time.sleep(0.4)


def key(vk, delay=0.1):
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)
    time.sleep(delay)


def step_down(origin):
    """点开算法下拉框 → Down → Enter。滚轮切换无效（实测）。"""
    L, T = origin
    u.SetCursorPos(L + COMBO[0], T + COMBO[1])
    time.sleep(0.1)
    u.mouse_event(2, 0, 0, 0, 0)          # 左键按下
    time.sleep(0.07)
    u.mouse_event(4, 0, 0, 0, 0)          # 左键抬起
    time.sleep(0.9)                       # 等下拉展开
    key(0x28, 0.12)                       # Down
    key(0x0D, 1.5)                        # Enter，等足再抓（防帧缓存）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="outputs/fxshots")
    ap.add_argument("--count", type=int, default=62)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    h = find_win()
    r = w.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    origin = (r.left, r.top)
    box = (r.left, r.top, r.right, r.bottom)
    focus(h)

    for i in range(a.count):
        im = ImageGrab.grab(bbox=box, all_screens=True)     # 实时，不用 PrintWindow
        im.crop(PANEL).save(os.path.join(a.out, "%02d_panel.png" % i))
        im.crop(NAME).save(os.path.join(a.out, "%02d_name.png" % i))
        print("captured %02d" % i, flush=True)
        if i < a.count - 1:
            step_down(origin)
    print("done -> %s（下一步：按名称反推偏移，确认顺序）" % a.out)


if __name__ == "__main__":
    main()
