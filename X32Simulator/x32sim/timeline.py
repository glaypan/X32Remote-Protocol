# -*- coding: utf-8 -*-
"""x32sim/timeline — 时间轴调度器 + ShowRun (代际化) + 定时 + 冲突事件"""

import math
import threading
import time

from .actions import _act_duration, _act_label, _apply_fade, _exec_instant, _fade_targets_for
from .bridge import _bridge_force_sync
from .cards import SHOW_CARDS
from .logbus import EVENTS, EVT_SEQ, log
from .presets import save_snapshot
from .state import FADE_LOCK, FADE_ZONE, STATE, _interrupted, _register_fade, _unregister_fade, get_value, set_value
from .util import curve_ratio

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

SHOW_STATE = {"running": None, "name": "", "progress": 0.0, "step": ""}

# 卡片运行控制: 每次启动都是一个独立"代"(gen) + 专属停止事件 + 卡片 id。
# ⚠️ 曾经用过一个全局 Event (SHOW_STOP): 新 worker 开头 clear() 会把"停止旧卡片"
#    刚 set 的信号吃掉 → 连点两次出两个 worker 同时驱动同一张卡; 停止 A 再开 B,
#    A 收不到停止信号继续把通道推往 A 的目标。现在停止信号按代发放, 旧 worker
#    只认自己那个 Event, 物理上不可能被新运行清掉; 共享状态(SHOW_STATE)的清理
#    也带 gen 校验, 旧代退场时不会把新代的状态擦掉。
SHOW_RUN = {"gen": 0, "stop": None, "card": None}
SHOW_RUN_LOCK = threading.Lock()


def _show_stop_current():
    """停止当前卡片运行 (幂等)。没有在跑就什么也不做。"""
    with SHOW_RUN_LOCK:
        ev = SHOW_RUN["stop"]
        SHOW_RUN["stop"] = None
        SHOW_RUN["card"] = None
        SHOW_RUN["gen"] += 1
    if ev is not None:
        ev.set()
    # 渐变就地冻结在当前值 → 把 FADE_ZONE 里各目标的当前值立即同步给真台。
    # ⚠️ 不同步的话: 最后一次插值可能已被合并器限速挡下或根本没写,
    #    真台停在中间值, 本地台面却是冻结值 —— 桥接模式下两边不一致。
    #    取快照要趁 worker 还没跑 finally (FADE_ZONE 还未注销), 所以在 ev.set() 后立刻做。
    with FADE_LOCK:
        zone_keys = list(FADE_ZONE.keys())
    if zone_keys:
        _bridge_force_sync(zone_keys, skip_interrupted=True)
    SHOW_STATE["running"] = None
    SHOW_STATE["progress"] = 1.0
    SHOW_STATE["step"] = ""

# ---- 卡片定时触发: 延时 N 秒后 / 定在 HH:MM 自动启动, 可取消 ----
SCHEDULES = []        # {"sid", "card_id", "card_name", "run_at"(epoch), "mode", "timer"}
SCHED_SEQ = [0]
SCHED_LOCK = threading.Lock()


def schedule_card(card, mode, delay=None, clock=None):
    """定时启动卡片。mode='delay' delay 秒后; mode='clock' 今天的 clock(HH:MM) 时刻。"""
    if mode == "delay":
        run_at = time.time() + max(float(delay), 0.5)
    else:
        h, m = [int(x) for x in str(clock).split(":")[:2]]
        t = time.localtime()
        run_at = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, m, 0, 0, 0, -1))
        if run_at <= time.time():
            run_at += 86400          # 已过时刻 → 明天
    SCHED_SEQ[0] += 1
    sid = SCHED_SEQ[0]

    def fire():
        with SCHED_LOCK:
            SCHEDULES[:] = [s for s in SCHEDULES if s["sid"] != sid]
        cur = next((c for c in SHOW_CARDS if c["id"] == card["id"]), None)
        if not cur:
            log("SCHED 定时启动失败: 卡片已被删除")
            return
        EVT_SEQ[0] += 1
        EVENTS.append({"id": EVT_SEQ[0], "time": time.strftime("%H:%M:%S"),
                       "text": "⏰ 定时触发 — 卡片「%s」自动启动" % cur.get("name", "")})
        log("SCHED 定时启动卡片: %s" % cur.get("name"))
        run_show(cur)

    t = threading.Timer(max(run_at - time.time(), 0.1), fire)
    t.daemon = True
    t.start()
    with SCHED_LOCK:
        SCHEDULES.append({"sid": sid, "card_id": card["id"], "card_name": card.get("name", ""),
                          "run_at": run_at, "mode": mode, "timer": t})
    log("SCHED 定时任务 #%d: 「%s」 %s (%s)" %
        (sid, card.get("name", ""), time.strftime("%H:%M:%S", time.localtime(run_at)),
         "延时" if mode == "delay" else "定时"))


def cancel_schedule(sid):
    with SCHED_LOCK:
        s = next((x for x in SCHEDULES if x["sid"] == sid), None)
        if not s:
            return False
        s["timer"].cancel()
        SCHEDULES[:] = [x for x in SCHEDULES if x["sid"] != sid]
    log("SCHED 取消定时任务 #%d" % sid)
    return True


def run_show(card):
    """启动卡片 (时间轴调度器): 无 at 的动作顺序排布, 有 at 的按偏移并行;
    卡片 next 字段非空时执行完自动接续下一张 (防环, 链长上限 16)。

    同一张卡已在运行 → 直接忽略 (连点保护): 否则双击会起两个 worker 同时驱动
    同一张卡, 推子被写两遍、停止信号还会被后一个 worker 吃掉。
    返回 True=已启动, False=忽略 (该卡正在运行)。
    """
    with SHOW_RUN_LOCK:
        if SHOW_RUN["stop"] is not None and SHOW_RUN["card"] == card["id"]:
            return False
        old = SHOW_RUN["stop"]
        SHOW_RUN["gen"] += 1
        gen = SHOW_RUN["gen"]
        ev = threading.Event()
        SHOW_RUN["stop"] = ev
        SHOW_RUN["card"] = card["id"]
    if old is not None:
        old.set()      # 旧代立刻收到停止信号; 它清不掉新代的 Event
    threading.Thread(target=_show_worker, args=(card, ev, gen), daemon=True).start()
    return True


def _show_worker(first_card, run_ev, gen):
    snapshot_done = False
    seen = set()
    cur = first_card
    depth = 0
    while cur is not None and not run_ev.is_set() and depth < 16:
        if cur["id"] in seen:
            log("SHOW 卡片链检测到循环, 停止接续")
            break
        seen.add(cur["id"])
        depth += 1
        if not snapshot_done:
            # 一键撤销: 执行前自动保存台面快照 (整条链只存一个点, 撤销即回到链开始前)
            try:
                snap, _saved = save_snapshot("自动 · %s" % str(cur.get("name", "卡片"))[:20], auto=True)
                snapshot_done = True
            except Exception as e:
                log("SHOW 自动撤销点保存失败: %r" % e)
        _run_card_on_timeline(cur, run_ev, gen)
        if run_ev.is_set():
            break
        nid = str(cur.get("next") or "")
        nxt = next((c for c in SHOW_CARDS if c["id"] == nid), None) if nid else None
        if nxt:
            log("SHOW 卡片链: 「%s」完成 → 接续「%s」" % (cur.get("name"), nxt.get("name")))
            time.sleep(0.4)
        cur = nxt
    if depth >= 16:
        log("SHOW 卡片链超过 16 张, 停止接续")
    if SHOW_RUN["gen"] == gen:
        # 还是当前代才清理共享状态 —— 被新代顶掉的旧 worker 退场时不能擦新代的状态
        with SHOW_RUN_LOCK:
            if SHOW_RUN["gen"] == gen:
                SHOW_RUN["stop"] = None
                SHOW_RUN["card"] = None
        SHOW_STATE["running"] = None
        SHOW_STATE["progress"] = 1.0
        SHOW_STATE["step"] = ""


def _run_card_on_timeline(card, run_ev, gen):
    card_name = str(card.get("name", "卡片"))

    # All Live 自动混音演示: 推子随音乐节奏起伏 (电动推子玩具同款)
    if card["id"] == "alllive":
        duration = 12.0
        with STATE.lock:
            base = {i: STATE.ch[i]["fader"] for i in range(1, 9)}
        log("SHOW All Live 演示开始: 通道 01-08 推子自动起舞 %.0f 秒" % duration)
        demo_targets = [("ch_fader", i, 0, 0) for i in range(1, 9)]
        _register_fade(demo_targets, card_name)
        t0 = time.time()
        try:
            while not run_ev.is_set():
                t = time.time() - t0
                if t >= duration:
                    break
                time.sleep(0.08)
                for i in range(1, 9):
                    if _interrupted("ch_fader", i):
                        continue
                    v = base[i] + 0.20 * math.sin(t * 2.4 + i * 0.85) * math.sin(t * 0.7)
                    set_value("ch_fader", i, v, source="fade")
                SHOW_STATE["progress"] = t / duration
            for i in range(1, 9):
                if not _interrupted("ch_fader", i):
                    set_value("ch_fader", i, base[i], source="fade")
        finally:
            _unregister_fade(demo_targets)
        log("SHOW All Live 演示结束,推子已归位")
        return

    # 时间轴排布: 无 at 顺序排布, 有 at 并行
    actions = list(card.get("actions") or [])
    for lg in card.get("fades") or []:          # 旧 fades 结构兼容
        actions.append({"kind": "fade_from", "ch": lg["ch"], "frm": lg["from"],
                        "to": lg["to"], "duration": lg.get("duration", 3.0),
                        "curve": lg.get("curve", "linear")})
    timeline = []
    cursor = 0.0
    for n, act in enumerate(actions):
        at = act.get("at")
        try:
            start = float(at) if at is not None else cursor
        except (TypeError, ValueError):
            start = cursor
        dur = _act_duration(act)
        timeline.append({"n": n, "act": act, "start": start, "dur": dur})
        cursor = start + dur
    total = max([e["start"] + e["dur"] for e in timeline] + [0.001])

    SHOW_STATE["running"] = card["id"]
    SHOW_STATE["name"] = card.get("name", "")
    SHOW_STATE["progress"] = 0.0
    SHOW_STATE["step"] = ""
    log("SHOW 启动卡片: %s (%d 个动作, 时间轴 %.1f 秒)" % (card_name, len(actions), total))

    t0 = time.time()
    fired = set()
    active = {}            # n -> {"targets":..., "starts":...} 进行中的渐变
    TICK = 0.05
    try:
        while not run_ev.is_set():
            t = time.time() - t0
            for ev in timeline:
                n, act = ev["n"], ev["act"]
                if t < ev["start"]:
                    continue
                if n not in fired:
                    fired.add(n)
                    SHOW_STATE["step"] = "%d/%d · %s" % (n + 1, len(actions), _act_label(act))
                    if ev["dur"] <= 0.001:
                        _exec_instant(act)
                        continue
                    targets = _fade_targets_for(act)
                    if targets is None:
                        _exec_instant(act)
                        continue
                    if act["kind"] in ("fade_from", "fade_from_bus"):
                        fk, idx = targets[0][0], targets[0][1]
                        set_value(fk, idx, float(act.get("frm", 0.0)), source="action")
                    starts = [(k, i, get_value(k, i), b) for k, i, b in targets]
                    _register_fade([(k, i, 0, b) for k, i, b in targets], card_name)
                    active[n] = {"targets": [(k, i, 0, b) for k, i, b in targets],
                                 "starts": starts}
                    log("SHOW ▶ %s (%.1f 秒, 曲线 %s)" %
                        (_act_label(act), ev["dur"], act.get("curve", "linear")))
                if n in active:
                    if t >= ev["start"] + ev["dur"]:
                        _apply_fade(active[n], 1.0)
                        _unregister_fade(active[n]["targets"])
                        log("SHOW ✓ %s" % _act_label(act))
                        del active[n]
                    else:
                        ratio = curve_ratio((t - ev["start"]) / ev["dur"],
                                            act.get("curve", "linear"))
                        _apply_fade(active[n], ratio)
            if fired and not active and all(e["n"] in fired for e in timeline) \
                    and t >= total - TICK:
                break
            SHOW_STATE["progress"] = min(1.0, t / total)
            time.sleep(TICK)
        # 停止 (run_ev): 渐变就地冻结在当前值, 不写终值
    finally:
        for tg in active.values():
            _unregister_fade(tg["targets"])
        if SHOW_RUN["gen"] == gen and SHOW_STATE["running"] == card["id"]:
            SHOW_STATE["running"] = None
            SHOW_STATE["progress"] = 1.0
            SHOW_STATE["step"] = ""
