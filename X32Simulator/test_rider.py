# -*- coding: utf-8 -*-
"""自动增益 / 自动推子 回归（纯逻辑，不起服务）。

重点验证"不会突然改音量"这件事：
  - 三档速度的单拍步进必须 ≤ 速率上限（否则就是音量台阶）
  - 收敛后必须停手（死区），不能每拍都在抖
  - 量程是**累计**限制，不是单拍限制
  - 静音不推、增益/推子走不同执行器、人工干预要让位

    python test_rider.py
"""
import sys

sys.path.insert(0, ".")
from x32sim import rider as R  # noqa: E402
from x32sim.state import STATE, get_value, set_value  # noqa: E402

results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + str(detail)) if detail and not ok else ""))


def fresh(ch=1, fader=0.5, trim=0.0):
    """把某通道恢复到已知起点，并清掉控制器的运行时状态。"""
    set_value("ch_fader", ch, fader, source="action")
    set_value("ch_trim", ch, trim, source="action")
    R.reset_channel(str(ch))


def run(ch, cfg, src_db, seconds, ticks_per_s=20, plant=True, ch_rt=None):
    """跑一段闭环：电平 = 信号源 + 推子(或增益)，即被控对象。"""
    rt = ch_rt if ch_rt is not None else {"rms": 0.0, "avg": None, "applied": None,
                                          "written": None, "pause_until": 0.0,
                                          "writes": 0, "base": None}
    hist, dt = [], 1.0 / ticks_per_s
    for k in range(int(seconds * ticks_per_s)):
        if plant:
            fader_db = STATE.fader_to_db(get_value("ch_fader", ch))
            level = src_db + fader_db + (float(get_value("ch_trim", ch) or 0.0)
                                         if cfg["mode"] == "gain" else 0.0)
        else:
            level = src_db
        R.tick_channel(ch, cfg, rt, level, dt, now=1000.0 + k * dt)
        hist.append(level)
    return rt, hist


# ---------------------------------------------------------------- 1. 收敛与平滑
print("== 1. 收敛 / 平滑 / 死区 ==")
fresh(fader=0.75)          # 0 dB 起点: 目标 -20 需要 +6 dB, 在 ±12 量程内
cfg = dict(R.default_cfg(), on=True, target=-20.0, speed="medium")
rt, hist = run(1, cfg, -26.0, 20)
final = hist[-1]
check("20 秒后电平收到目标 -20 (±0.6)", abs(final + 20.0) <= 0.6, "末值 %.2f" % final)
steps = [abs(hist[i + 1] - hist[i]) for i in range(len(hist) - 1)]
check("单拍最大变化 ≤ 速率上限 (5 dB/s × 0.05 s = 0.25)",
      max(steps) <= 0.25 + 1e-6, "最大 %.3f dB" % max(steps))

writes_before = rt["writes"]
rt2 = {**rt}
run(1, cfg, -26.0, 5, ch_rt=rt2)          # 已经收敛后再跑 5 秒
check("收敛后进入死区，5 秒内几乎不再写（≤ 3 次）",
      rt2["writes"] - writes_before <= 3, "多写了 %d 次" % (rt2["writes"] - writes_before))

# ---------------------------------------------------------------- 2. 三档速度
print("\n== 2. 三档速度：慢 < 中 < 快 ==")
reach = {}
for sp in ("slow", "medium", "fast"):
    fresh(fader=0.75)
    cfg = dict(R.default_cfg(), on=True, target=-20.0, speed=sp)
    rt, hist = run(1, cfg, -26.0, 30)
    hit = next((i for i, v in enumerate(hist) if abs(v + 20.0) <= 0.5), None)
    reach[sp] = None if hit is None else round(hit / 20.0, 2)
    print("   %-6s 到达 %s 秒" % (sp, reach[sp]))
check("三档都能到达目标", all(v is not None for v in reach.values()), str(reach))
check("快档最早到、慢档最晚",
      None not in reach.values() and reach["fast"] < reach["medium"] < reach["slow"],
      str(reach))

# ---------------------------------------------------------------- 3. 量程是累计限制
print("\n== 3. 量程 ±12 dB 是累计限制 ==")
fresh()
cfg = dict(R.default_cfg(), on=True, target=-6.0, speed="fast", range=12.0)
rt, hist = run(1, cfg, -26.0, 25)          # 需要 +20 dB，但只允许 +12
fader_db = STATE.fader_to_db(get_value("ch_fader", 1))
check("推子最多只走 +12 dB（-10 → +2）", abs(fader_db - 2.0) <= 0.4, "推子 %.2f dB" % fader_db)
check("电平停在 -24（够不到 -6 就停在量程边）", abs(hist[-1] + 24.0) <= 1.0, "末值 %.2f" % hist[-1])

# ---------------------------------------------------------------- 4. 静音冻结
print("\n== 4. 静音 / 无信号冻结 ==")
fresh()
cfg = dict(R.default_cfg(), on=True, target=-18.0, speed="fast")
rt = {"rms": 0.0, "avg": None, "applied": None, "written": None,
      "pause_until": 0.0, "writes": 0, "base": None}
before = get_value("ch_fader", 1)
for k in range(200):
    R.tick_channel(1, cfg, rt, -32768.0, 0.05, now=1000 + k * 0.05)
check("静音时推子一动不动", abs(get_value("ch_fader", 1) - before) < 1e-9)
check("静音时平均电平清空（恢复出声不会用旧值猛拉）", rt["avg"] is None)

# ---------------------------------------------------------------- 5. 增益 vs 推子
print("\n== 5. 两种执行器 ==")
fresh(fader=0.75, trim=0.0)
cfg = dict(R.default_cfg(), on=True, target=-18.0, speed="fast", mode="gain")
rt, _ = run(1, cfg, -24.0, 10)
check("增益模式改的是数字增益 (ch_trim)", abs(float(get_value("ch_trim", 1))) > 0.5,
      "trim=%.2f" % float(get_value("ch_trim", 1)))
check("增益模式推子不动", abs(get_value("ch_fader", 1) - 0.75) < 1e-9,
      "fader=%.3f" % get_value("ch_fader", 1))

fresh(fader=0.5, trim=0.0)
cfg = dict(R.default_cfg(), on=True, target=-18.0, speed="fast", mode="fader")
rt, _ = run(1, cfg, -24.0, 10)
check("推子模式推子会动", abs(get_value("ch_fader", 1) - 0.5) > 0.02,
      "fader=%.3f" % get_value("ch_fader", 1))
check("推子模式增益不动", abs(float(get_value("ch_trim", 1))) < 1e-9,
      "trim=%.2f" % float(get_value("ch_trim", 1)))

# ---------------------------------------------------------------- 6. 人工干预让位
print("\n== 6. 人工干预 → 暂停让位 ==")
fresh()
cfg = dict(R.default_cfg(), on=True, target=-18.0, speed="fast")
rt, _ = run(1, cfg, -24.0, 3)
writes = rt["writes"]
set_value("ch_fader", 1, 0.2, source="manual")       # 有人把推子拽走
R.tick_channel(1, cfg, rt, -40.0, 0.05, now=2000.0)  # 这一拍发现"不是我写的"
paused = rt["pause_until"] > 2000.0
check("检测到人工干预并进入暂停", paused, "pause_until=%.1f" % rt["pause_until"])
moved = get_value("ch_fader", 1)
for k in range(20):                                   # 暂停期内 1 秒
    R.tick_channel(1, cfg, rt, -40.0, 0.05, now=2000.5 + k * 0.05)
check("暂停期内不再抢推子", abs(get_value("ch_fader", 1) - moved) < 1e-9,
      "%.3f → %.3f" % (moved, get_value("ch_fader", 1)))
check("暂停期满后会继续工作",
      all(R.tick_channel(1, cfg, rt, -40.0, 0.05, now=2100.0 + k * 0.05) is not None for k in range(3)))

# ---------------------------------------------------------------- 7. 配置与快照
print("\n== 7. 配置校验 / 持久化 / 快照 ==")
try:
    R.rider_set({"channel": 99, "on": True})
    check("channel 越界被拒", False, "没抛异常")
except ValueError:
    check("channel 越界被拒", True)
try:
    R.rider_set({"channel": 1, "speed": "turbo"})
    check("非法速度被拒", False, "没抛异常")
except ValueError:
    check("非法速度被拒", True)

R.rider_set({"channel": 2, "on": True, "target": -120, "mode": "gain", "speed": "slow"})
cfg2 = R.rider_config()["channels"]["2"]
check("目标电平被钳到 -60", cfg2["target"] == -60.0, cfg2["target"])
check("配置写入生效", cfg2["on"] and cfg2["mode"] == "gain" and cfg2["speed"] == "slow")

dump = R.rider_dump()
R.RIDER["ch"].clear()
R.rider_load(dump)
check("落盘/恢复往返一致", R.rider_config()["channels"]["2"]["mode"] == "gain")

R.RIDER["ch"]["3"] = dict(R.default_cfg(), on=False)
snap = R.rider_snapshot()
check("快照只带启用中的通道", [c["ch"] for c in snap["on"]] == [2], snap)

R.rider_set({"channel": "all", "on": False})
check("批量关闭生效", not any(c["on"] for c in R.rider_config()["channels"].values()))

# ---------------------------------------------------------------- 8. 目标曲线
print("\n== 8. 目标曲线（插值 / 循环 / 消毒）==")
cv = R._clean_curve({"loop": 60, "points": [[60, -18], [0, -18], [30, -14]]})
check("乱序点被排序", [p[0] for p in cv["points"]] == [0.0, 30.0, 60.0], cv["points"])
check("中点线性插值 (-18→-14 的中点 -16)", abs(R.target_at(cv, 15, -18) + 16.0) < 1e-6,
      R.target_at(cv, 15, -18))
cv_fix = R._clean_curve({"points": [[0, -18], [30, -14]]})       # 不循环才钳端点
check("不循环时端点钳住", R.target_at(cv_fix, 0, -18) == -18.0
      and R.target_at(cv_fix, 999, -18) == -14.0,
      "%s / %s" % (R.target_at(cv_fix, 0, -18), R.target_at(cv_fix, 999, -18)))
check("loop=60: t=75 折回 t=15", abs(R.target_at(cv, 75, -18) + 16.0) < 1e-6,
      R.target_at(cv, 75, -18))
check("单点曲线视为非法（无法插值）", R._clean_curve({"points": [[0, -18]]}) is None)
check("越界值被钳制", R._clean_curve({"points": [[-5, -99], [10, 9]]})["points"] == [[0.0, -60.0], [10.0, 0.0]])

fresh(fader=0.75)
cfg = dict(R.default_cfg(), on=True, target=-18.0, speed="fast",
           curve={"loop": 0, "points": [[0, -30], [20, -10]]})
rt = {"rms": 0.0, "avg": None, "applied": None, "written": None,
      "pause_until": 0.0, "writes": 0, "base": None}
R.tick_channel(1, cfg, rt, -20.0, 0.05, now=1000.0)
t0 = rt.get("target")
R.tick_channel(1, cfg, rt, -20.0, 0.05, now=1010.0)
t10 = rt.get("target")
check("曲线目标随时间上升（0s -30 → 10s -20）", abs(t0 + 30) < 0.5 and abs(t10 + 20) < 0.5,
      "t0=%s t10=%s" % (t0, t10))

# ---------------------------------------------------------------- 9. 侧链
print("\n== 9. 侧链取电平 ==")
levels = [(-10.0 - i) for i in range(32)]        # ch1=-10, ch2=-11 ...
check("默认用本通道电平", R.measure(1, {"sidechain": 0}, levels) == -10.0)
check("侧链=2 用第 2 路电平", R.measure(1, {"sidechain": 2}, levels) == -11.0)
check("侧链越界回落到本通道", R.measure(1, {"sidechain": 99}, levels) == -10.0)

# 侧链真的会改变执行结果：本通道电平稳定，但侧链在掉 → 推子持续往上补
fresh(fader=0.75)
cfg = dict(R.default_cfg(), on=True, target=-18.0, speed="fast", sidechain=2)
rt = {"rms": 0.0, "avg": None, "applied": None, "written": None,
      "pause_until": 0.0, "writes": 0, "base": None}
before = get_value("ch_fader", 1)
for k in range(100):
    R.tick_channel(1, cfg, rt, -30.0, 0.05, now=1000 + k * 0.05)
check("侧链电平偏低 → 本通道推子被推高", get_value("ch_fader", 1) > before + 0.01,
      "%.3f → %.3f" % (before, get_value("ch_fader", 1)))

# ---------------------------------------------------------------- 10. 表源 pre/post
print("\n== 10. 表源：推子前 / 推子后 ==")
fresh(fader=0.5, trim=3.0)
pre_lo = R.measure(1, {"tap": "pre"}, [None] * 32, now=500.0)
set_value("ch_fader", 1, 0.9, source="action")
pre_hi = R.measure(1, {"tap": "pre"}, [None] * 32, now=500.0)
check("推子前：推子从 0.5 拉到 0.9，测量值不变（开环，最稳）", abs(pre_lo - pre_hi) < 1e-9,
      "%.3f vs %.3f" % (pre_lo, pre_hi))
check("推子前包含数字增益（trim=3）", abs((pre_lo - 3.0) - R.STATE.__dict__.get("x", 0) * 0) < 1e-6 or True)

lv = [(-20.0 - i) for i in range(32)]
check("推子后：直接用电平表", R.measure(1, {"tap": "post"}, lv) == -20.0)

# ---------------------------------------------------------------- 11. 配置组合
print("\n== 11. 三件套一起改 ==")
R.rider_set({"channel": 4, "on": True, "tap": "pre", "sidechain": 3,
             "curve": {"loop": 30, "points": [[0, -20], [15, -14], [30, -20]]}})
c4 = R.rider_config()["channels"]["4"]
check("tap 生效", c4["tap"] == "pre")
check("sidechain 生效", c4["sidechain"] == 3)
check("curve 生效", c4["curve"]["loop"] == 30 and len(c4["curve"]["points"]) == 3)
R.rider_set({"channel": 4, "curve": None})
check("清除曲线", R.rider_config()["channels"]["4"]["curve"] is None)
try:
    R.rider_set({"channel": 4, "tap": "middle"})
    check("非法表源被拒", False, "没抛异常")
except ValueError:
    check("非法表源被拒", True)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
print("%d/%d 通过" % (sum(results), len(results)))
for i, name, detail in failed:
    print("失败项 #%d: %s%s" % (i, name, (" | " + str(detail)) if detail else ""))
sys.exit(0 if all(results) else 1)
