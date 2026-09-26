#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键全套回归测试：自动管理模拟器实例，避免手工起停与状态污染。

背景：
  - selftest / test_conflict / test_new_features 需要「全新台面」，
    状态被前一个测试改过就会误报，所以每个用例组前都重启一次实例。
  - test_bridge_p2 需要外部先起好「假真台 + 桥接端」两个实例。
  - test_b3_scan 自己会拉子进程，独占 10023/10024/28080/28081。

用法:
  python run_all_tests.py            # 跑全部
  python run_all_tests.py selftest   # 只跑指定组
"""
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 本地请求不经过任何代理（否则 ZeroTier/系统代理会让 127.0.0.1 也走代理）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

FAKE_CFG = os.path.join(BASE, "_regr_fake.json")
BR_CFG = os.path.join(BASE, "_regr_bridge.json")


def wait_web(port, timeout=45):
    """轮询直到 Web 端口可响应 /api/state"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            OPENER.open("http://127.0.0.1:%d/api/state" % port, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def start(args):
    """后台拉起一个模拟器实例"""
    return subprocess.Popen([PY, "x32_simulator.py"] + args, cwd=BASE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(*procs):
    """停实例（Windows 下 terminate 即 TerminateProcess）"""
    for p in procs:
        if p is None:
            continue
        try:
            p.terminate()
            p.wait(timeout=8)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    time.sleep(0.8)          # 等端口真正释放


def run(script):
    """跑一个测试脚本，返回 (是否通过, 摘要行)"""
    r = subprocess.run([PY, script], cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    tail = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    headline = " | ".join(tail[-3:]) if tail else "(无输出)"
    # 失败项单独列出，便于定位
    fails = [ln for ln in tail if ln.startswith("失败项")]
    if fails:
        headline = fails[0][:400]
    return r.returncode == 0, headline


def cleanup_cfg():
    for f in (FAKE_CFG, BR_CFG):
        try:
            os.remove(f)
        except OSError:
            pass


# ---------------------------------------------------------------- 用例组

def group_single(name, script, args, web_port):
    """需要一个干净单实例的用例组"""
    p = start(args)
    try:
        if not wait_web(web_port):
            return False, "实例启动超时 (web %d)" % web_port
        return run(script)
    finally:
        stop(p)


def group_bridge():
    """桥接双实例"""
    fake = start(["--port", "20023", "--web-port", "28081", "--config", FAKE_CFG])
    br = start(["--port", "10023", "--web-port", "28080",
                "--bridge", "127.0.0.1:20023", "--config", BR_CFG])
    try:
        if not wait_web(28081, 45):
            return False, "假真台启动超时"
        if not wait_web(28080, 45):
            return False, "桥接端启动超时"
        time.sleep(1.5)       # 等桥接完成首次全量回读
        return run("test_bridge_p2.py")
    finally:
        stop(br, fake)


def group_b3():
    """test_b3_scan 自己拉实例，前置确保端口空闲"""
    time.sleep(0.5)
    return run("test_b3_scan.py")


# 组名 -> (显示名, 执行函数)
GROUPS = [
    ("selftest", ("自检 (OSC 全流程)", lambda: group_single(
        "selftest", "selftest.py", ["--port", "10023", "--web-port", "28080"], 28080))),
    ("conflict", ("冲突策略", lambda: group_single(
        "conflict", "test_conflict.py", ["--port", "10023", "--web-port", "28080"], 28080))),
    ("new_features", ("新特性 (时间轴/曲线/增量)", lambda: group_single(
        "new_features", "test_new_features.py", ["--port", "10023", "--web-port", "28080"], 28080))),
    ("bridge", ("桥接双实例", group_bridge)),
    ("b3_scan", ("B3 自动发现", group_b3)),
]


def main():
    wanted = sys.argv[1:] or [g[0] for g in GROUPS]
    selected = [g for g in GROUPS if g[0] in wanted]
    if not selected:
        print("未知用例组: %s" % wanted)
        print("可选: %s" % ", ".join(g[0] for g in GROUPS))
        return 2

    print("=" * 62)
    print("X32 模拟器 全套回归  (%d 个用例组)" % len(selected))
    print("=" * 62)

    results = []
    for key, (title, fn) in selected:
        print("\n>>> [%s] %s ..." % (key, title), flush=True)
        t0 = time.time()
        try:
            ok, headline = fn()
        except Exception as e:
            ok, headline = False, "异常: %s" % e
        dt = time.time() - t0
        results.append((key, ok, headline, dt))
        print("    %s (%.1fs)  %s" % ("通过" if ok else "失败", dt, headline), flush=True)

    cleanup_cfg()

    print("\n" + "=" * 62)
    print("汇总")
    print("=" * 62)
    for key, ok, headline, dt in results:
        print("  %-14s %s  %5.1fs" % (key, "PASS" if ok else "FAIL", dt))
    npass = sum(1 for r in results if r[1])
    print("\n  %d/%d 用例组通过" % (npass, len(results)))
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
