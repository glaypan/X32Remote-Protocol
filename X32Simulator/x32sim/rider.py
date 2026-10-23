# -*- coding: utf-8 -*-
"""自动增益 / 自动推子（Vocal Rider 式）。

控制目标：把选定通道的**电平**稳在设定的目标值（默认 -18 dBFS）。

控制律是"负反馈 + 三重平滑"，缺一条都会听起来像在抽气：

  1. **平均**：先把电平做线性功率的指数平均（窗口随速度 0.9 / 0.35 / 0.12 s）。
     直接用电平瞬时值 → 每个字都改一次推子，人耳听到的是颤动。
  2. **死区**：误差小于 0.6 / 0.4 / 0.3 dB 就不动。目标附近来回修正没有意义，
     只会让电动推子不停地"呼吸"。
  3. **速率**：每秒最多 1.5 / 5 / 15 dB。再大的偏差也慢慢爬过去，
     不会一帧从 -40 拉到 0（那一下就是明显的音量台阶）。

执行器二选一（`mode`）：
  - `fader` → `ch_fader`（推子，真台是电动推子，能看见它在动）
  - `gain`  → `ch_trim` （数字增益/前置放大，推子不动）

两者都走 `set_value(..., source="auto")` 这条既有写入管道，桥接模式下会真实下发给台子，
和卡片/手动操作共用同一个出口与限速。

安全边界（都是踩过或能想到的坑）：
  - **静音/无信号**（≤ -80 dB）冻结：绝不把静音往上推成噪声
  - **卡片正在渐变该通道**时让位（FADE_ZONE），不跟时间轴抢
  - **检测到不是自己写的改动**（有人手动拖了推子/拧了增益）→ 暂停 2 秒，
    让人优先；之后从新位置继续
  - **量程**默认 ±12 dB，到顶停手；推子到 0/1 两端也不硬顶
"""
import math
import threading
import time

from .logbus import log
from .state import FADE_LOCK, FADE_ZONE, STATE, compute_levels, db_to_fader, get_value, set_value, source_level_db  # noqa: E501

# 三档速度：窗口（平均）/ 死区 / 每秒最大修正
SPEEDS = {
    "slow":   {"window": 0.90, "deadband": 0.60, "rate": 1.5},
    "medium": {"window": 0.35, "deadband": 0.40, "rate": 5.0},
    "fast":   {"window": 0.12, "deadband": 0.30, "rate": 15.0},
}
SPEED_LABELS = {"slow": "慢（平稳）", "medium": "中（默认）", "fast": "快（贴合）"}
MODES = ("fader", "gain")
TICK = 0.05                 # 20 Hz：比电平表推送快，足够跟上"快"档
SILENCE_DB = -80.0          # 低于此视为无信号（静音是 -32768）
MANUAL_PAUSE = 2.0          # 检测到人工干预后让位多久
MANUAL_EPS = 0.25           # 与上次写入相差多少 dB 判定为"别人动的"
MAX_CH = 32

# 配置（持久化到 user_config.json 的 "rider" 键）
RIDER = {"enabled": True, "ch": {}}
RIDER_LOCK = threading.Lock()
# 运行时（不落盘）：每通道的平滑状态与上次写入值
RIDER_RT = {}


def default_cfg():
    return {"on": False, "target": -18.0, "mode": "fader", "speed": "medium", "range": 12.0,
            # 表源: post = 推子后(默认, 与界面电平表同源); pre = 推子前(推子动不影响测量)
            "tap": "post",
            # 侧链: 0 = 用本通道电平; 1-32 = 用那一路的电平来判断, 但仍修正本通道
            "sidechain": 0,
            # 目标曲线: None = 固定目标; {"loop": 秒(0=不循环), "points": [[t, dB], ...]}
            "curve": None}


def _num(v, lo, hi, default):
    """配置值消毒：非数字/NaN 一律回默认，再钳到量程。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return max(lo, min(hi, f))


def _clean_curve(raw):
    """把界面传来的曲线消毒成 {"loop": 秒, "points": [[t, dB], ...]}；非法返回 None。"""
    if raw in (None, "", [], {}):
        return None
    if not isinstance(raw, dict):
        return None
    pts = raw.get("points")
    if not isinstance(pts, list) or len(pts) < 2:
        return None
    clean = []
    for pt in pts[:32]:
        try:
            t = float(pt[0])
            db = float(pt[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(t) and math.isfinite(db)):
            continue
        clean.append([round(max(0.0, min(3600.0, t)), 2), round(max(-60.0, min(0.0, db)), 1)])
    clean.sort(key=lambda x: x[0])
    # 同一时刻只留一个（后者覆盖）
    dedup = []
    for t, db in clean:
        if dedup and abs(dedup[-1][0] - t) < 0.01:
            dedup[-1][1] = db
        else:
            dedup.append([t, db])
    if len(dedup) < 2:
        return None
    try:
        loop = float(raw.get("loop") or 0.0)
    except (TypeError, ValueError):
        loop = 0.0
    loop = max(0.0, min(3600.0, loop)) if math.isfinite(loop) else 0.0
    if 0 < loop < dedup[-1][0]:
        loop = dedup[-1][0]          # 循环长度不能短于最后一个点
    return {"loop": round(loop, 2), "points": dedup}


def target_at(curve, elapsed, fallback):
    """曲线在 elapsed 秒处的目标电平（线性插值，两端钳住，可循环）。"""
    if not curve:
        return fallback
    pts = curve.get("points") or []
    if len(pts) < 2:
        return fallback
    loop = float(curve.get("loop") or 0)
    t = max(0.0, elapsed if elapsed == elapsed else 0.0)
    if loop > 0:
        t = math.fmod(t, loop)
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for k in range(1, len(pts)):
        t0, v0 = pts[k - 1]
        t1, v1 = pts[k]
        if t <= t1:
            if t1 <= t0:
                return v1
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return pts[-1][1]


def measure(ch, cfg, levels, now=None):
    """本拍用于判断的电平：侧链优先；tap=pre 用推子前(信号+增益)，post 用推子后(电平表)。"""
    try:
        src = int(cfg.get("sidechain") or 0)
    except (TypeError, ValueError):
        src = 0
    if not (1 <= src <= MAX_CH):
        src = ch
    if cfg.get("tap") == "pre":
        try:
            trim = float((STATE.ch[src] or {}).get("trim") or 0.0)
        except Exception:
            trim = 0.0
        return source_level_db(src, now) + trim
    try:
        return levels[src - 1]
    except (IndexError, TypeError, KeyError):
        return None


def _rt(key):
    rt = RIDER_RT.get(key)
    if rt is None:
        rt = {"rms": 0.0, "avg": None, "applied": None, "written": None,
              "pause_until": 0.0, "writes": 0, "base": None}
        RIDER_RT[key] = rt
    return rt


def reset_channel(key):
    """清掉某通道的平滑状态（重新开骑时从当前电平重新起算，量程基准也重取）。"""
    RIDER_RT.pop(key, None)


def rider_reset_all():
    RIDER_RT.clear()


# ---------------------------------------------------------------- 核心：单通道一拍
def tick_channel(i, cfg, rt, level_db, dt, now=None):
    """按一拍电平均值推进一次。返回写入的 dB 值；没写入返回 None。

    纯逻辑、无睡眠、无全局副作用（除了 set_value 落库），测试可以直接调它。
    """
    if now is None:
        now = time.time()
    if not cfg.get("on"):
        rt["avg"] = None
        rt["rms"] = 0.0
        return None
    if level_db is None or level_db <= SILENCE_DB or not math.isfinite(level_db):
        # 无信号：冻结，并清掉平均值 —— 否则恢复出声瞬间会用旧的平均值猛拉一下
        rt["avg"] = None
        rt["rms"] = 0.0
        return None

    sp = SPEEDS.get(cfg.get("speed"), SPEEDS["medium"])
    dt = max(0.005, min(0.5, dt))

    # ① 平均：线性功率的指数滑动平均（比直接平均 dB 更接近听感上的能量）
    power = 10.0 ** (level_db / 10.0)
    if rt["rms"] <= 0.0:
        rt["rms"] = power                      # 首拍直接取当前值，不从 0 慢慢爬
    else:
        alpha = 1.0 - math.exp(-dt / sp["window"])
        rt["rms"] += alpha * (power - rt["rms"])
    avg_db = 10.0 * math.log10(max(rt["rms"], 1e-12))
    rt["avg"] = round(avg_db, 2)

    # 目标: 配了曲线就按曲线走（以本次启用为 0 时刻，可循环），否则用固定目标
    if rt.get("start") is None:
        rt["start"] = now
    target = target_at(cfg.get("curve"), now - rt["start"], cfg["target"])
    rt["target"] = round(target, 2)
    rng = cfg["range"]
    err = max(-rng, min(rng, target - avg_db))

    # ② 死区：够近就别动
    if abs(err) < sp["deadband"]:
        return None

    actor = "ch_fader" if cfg["mode"] == "fader" else "ch_trim"
    cur = get_value(actor, i)
    if cur is None:
        return None
    cur_db = STATE.fader_to_db(cur) if actor == "ch_fader" else float(cur)

    # ③ 人工干预检测：当前值与"上次自己写的"差得多 → 一定是别人动的
    wrote = rt.get("written")
    if wrote is not None and abs(cur_db - wrote) > MANUAL_EPS:
        rt["pause_until"] = now + MANUAL_PAUSE
        rt["written"] = cur_db
        rt["base"] = cur_db              # 量程基准跟到人的新位置
        log("RIDER CH%02d 检测到人工干预，让位 %.1fs" % (i, MANUAL_PAUSE))
        return None
    if now < rt.get("pause_until", 0.0):
        rt["written"] = cur_db
        return None

    # 卡片正在渐变这一路 → 让位（否则两边互相拉扯）
    with FADE_LOCK:
        if (actor, i) in FADE_ZONE:
            return None

    # ④ 速率限制：每拍最多走 rate*dt 个 dB
    step = max(-sp["rate"] * dt, min(sp["rate"] * dt, err))
    # 量程按"启用时的位置"为基准累计，而不是限制单拍误差 ——
    # 否则范围形同虚设：每拍都从新位置重新算 12 dB，推子能一路爬到 +10。
    if rt.get("base") is None:
        rt["base"] = cur_db
    lo, hi = rt["base"] - rng, rt["base"] + rng
    want_db = max(lo, min(hi, cur_db + step))
    if abs(want_db - cur_db) < 1e-4:                 # 已到量程边界
        rt["written"] = cur_db
        rt["applied"] = cur_db
        return None
    if actor == "ch_fader":
        want_val = db_to_fader(want_db)
        if want_val <= 0.0 or want_val >= 1.0:      # 顶到两端就不硬推
            rt["written"] = cur_db
            rt["applied"] = cur_db
            return None
    else:
        want_val = want_db
        if abs(want_val) > 18.0:                     # 数字增益默认量程
            rt["written"] = cur_db
            rt["applied"] = cur_db
            return None

    set_value(actor, i, want_val, source="auto")
    got = get_value(actor, i)
    got_db = (STATE.fader_to_db(got) if actor == "ch_fader" else float(got)) if got is not None else want_db
    rt["written"] = got_db
    rt["applied"] = round(got_db, 2)
    rt["writes"] += 1
    return got_db


# ---------------------------------------------------------------- 线程
def rider_loop():
    """每 50 ms 一拍。电平取 /api/meters 同一份数据（推子后），执行器写回同一条管道。"""
    last = time.time()
    while True:
        time.sleep(TICK)
        now = time.time()
        dt = now - last
        last = now
        if not RIDER["enabled"]:
            continue
        try:
            levels = compute_levels()
            with RIDER_LOCK:
                items = [(k, dict(v)) for k, v in RIDER["ch"].items()]
            for key, cfg in items:
                if not cfg.get("on"):
                    continue
                i = int(key)
                if not (1 <= i <= MAX_CH):
                    continue
                tick_channel(i, cfg, _rt(key), measure(i, cfg, levels, now), dt, now)
        except Exception as e:              # 控制器绝不能把主进程带崩
            log("RIDER 循环异常: %r" % e)
            time.sleep(0.5)


# ---------------------------------------------------------------- 配置读写 / HTTP
def rider_config():
    """给界面看的全量配置 + 实时值。"""
    with RIDER_LOCK:
        chans = {}
        for k, cfg in sorted(RIDER["ch"].items(), key=lambda kv: int(kv[0])):
            rt = RIDER_RT.get(k, {})
            chans[k] = dict(cfg)
            chans[k].update({
                "level": rt.get("avg"),
                "applied": rt.get("applied"),
                "target_now": rt.get("target", cfg["target"]),
                "paused": time.time() < rt.get("pause_until", 0.0),
                "writes": rt.get("writes", 0),
            })
    return {"enabled": RIDER["enabled"], "speeds": SPEEDS, "modes": list(MODES),
            "channels": chans}


def rider_snapshot():
    """给 /api/state 的紧凑快照：只带启用的通道（不把 32 路塞进状态包）。"""
    out = []
    with RIDER_LOCK:
        for k, cfg in sorted(RIDER["ch"].items(), key=lambda kv: int(kv[0])):
            if not cfg.get("on"):
                continue
            rt = RIDER_RT.get(k, {})
            out.append({"ch": int(k), "target": rt.get("target", cfg["target"]),
                        "mode": cfg["mode"], "speed": cfg["speed"],
                        "tap": cfg.get("tap", "post"), "sidechain": cfg.get("sidechain", 0),
                        "level": rt.get("avg"), "applied": rt.get("applied"),
                        "curve": bool(cfg.get("curve")),
                        "paused": time.time() < rt.get("pause_until", 0.0)})
    return {"enabled": RIDER["enabled"], "on": out}


def rider_set(data):
    """处理 POST /api/rider。

    支持三种写法：
      {"channel": 3, "on": true, "target": -18, "mode": "fader", "speed": "fast"}
      {"channel": "all", "on": false}          # 批量
      {"action": "reset"}                      # 清平滑状态（重新起算）
    """
    if not isinstance(data, dict):
        raise ValueError("body 必须是对象")
    if "enabled" in data:
        RIDER["enabled"] = bool(data["enabled"])

    action = data.get("action")
    if action == "reset":
        rider_reset_all()
        return rider_config()

    if data.get("remove"):
        try:
            i = int(data.get("channel"))
        except (TypeError, ValueError):
            raise ValueError("channel 必须是 1-32")
        with RIDER_LOCK:
            RIDER["ch"].pop(str(i), None)
        reset_channel(str(i))
        return rider_config()

    patch_keys = ("on", "target", "mode", "speed", "range", "tap", "sidechain", "curve")
    if not any(k in data for k in patch_keys) and "channel" not in data:
        return rider_config()

    ch = data.get("channel")
    with RIDER_LOCK:
        if ch == "all":
            keys = list(RIDER["ch"])
        elif ch is None:
            raise ValueError("缺少 channel（1-32 或 \"all\"）")
        else:
            try:
                i = int(ch)
            except (TypeError, ValueError):
                raise ValueError("channel 必须是 1-32")
            if not (1 <= i <= MAX_CH):
                raise ValueError("channel 超出 1-32")
            cfg = RIDER["ch"].setdefault(str(i), default_cfg())
            keys = [str(i)]

        for key in keys:
            cfg = RIDER["ch"].setdefault(key, default_cfg())
            was_on = cfg.get("on")
            if "on" in data:
                cfg["on"] = bool(data["on"])
            if "target" in data:
                cfg["target"] = round(_num(data["target"], -60.0, 0.0, cfg["target"]), 1)
            if "mode" in data:
                if data["mode"] not in MODES:
                    raise ValueError("mode 只能是 fader / gain")
                cfg["mode"] = data["mode"]
            if "speed" in data:
                if data["speed"] not in SPEEDS:
                    raise ValueError("speed 只能是 slow / medium / fast")
                cfg["speed"] = data["speed"]
            if "range" in data:
                cfg["range"] = round(_num(data["range"], 3.0, 24.0, cfg["range"]), 1)
            if "tap" in data:
                if data["tap"] not in ("pre", "post"):
                    raise ValueError("tap 只能是 pre / post")
                cfg["tap"] = data["tap"]
            if "sidechain" in data:
                try:
                    sc = int(data["sidechain"] or 0)
                except (TypeError, ValueError):
                    raise ValueError("sidechain 必须是 0-32")
                if not (0 <= sc <= MAX_CH):
                    raise ValueError("sidechain 必须是 0-32")
                cfg["sidechain"] = sc
            if "curve" in data:
                cfg["curve"] = _clean_curve(data["curve"])
            if cfg.get("on") and not was_on:
                reset_channel(key)          # 重新开骑：从当前电平重新起算
            if not cfg.get("on"):
                reset_channel(key)
    return rider_config()


def rider_dump():
    """落盘用：只存配置（平滑状态是运行时量）。"""
    with RIDER_LOCK:
        return {"enabled": RIDER["enabled"],
                "ch": {k: dict(v) for k, v in RIDER["ch"].items()}}


def rider_load(d):
    """从配置文件恢复；坏条目逐条丢弃，不因一条脏数据让整个配置读不进来。"""
    if not isinstance(d, dict):
        return
    RIDER["enabled"] = bool(d.get("enabled", True))
    ch = d.get("ch")
    if not isinstance(ch, dict):
        return
    clean = {}
    for k, v in ch.items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if not (1 <= i <= MAX_CH) or not isinstance(v, dict):
            continue
        cfg = default_cfg()
        cfg["on"] = bool(v.get("on"))
        cfg["target"] = round(_num(v.get("target"), -60.0, 0.0, cfg["target"]), 1)
        if v.get("mode") in MODES:
            cfg["mode"] = v["mode"]
        if v.get("speed") in SPEEDS:
            cfg["speed"] = v["speed"]
        cfg["range"] = round(_num(v.get("range"), 3.0, 24.0, cfg["range"]), 1)
        if v.get("tap") in ("pre", "post"):
            cfg["tap"] = v["tap"]
        try:
            sc = int(v.get("sidechain") or 0)
            if 0 <= sc <= MAX_CH:
                cfg["sidechain"] = sc
        except (TypeError, ValueError):
            pass
        cfg["curve"] = _clean_curve(v.get("curve"))
        clean[str(i)] = cfg
    RIDER["ch"] = clean
