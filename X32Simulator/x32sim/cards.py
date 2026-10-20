# -*- coding: utf-8 -*-
"""x32sim/cards — 内置演出卡片默认数据"""

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ================================================================ 演出卡片
# 与 iOS App "演出"标签页同构: 动作按顺序瞬间执行, 渐变按时长分步执行

SHOW_CARDS = [
    {"id": "dinner", "name": "🍽 晚宴模式", "desc": "静音话筒 01/02 → 音乐(通道15)从 75% 经 10 秒渐弱到 30%",
     "actions": [{"kind": "mute", "ch": 1}, {"kind": "mute", "ch": 2}],
     "fades": [{"ch": 15, "from": 0.75, "to": 0.30, "duration": 10.0, "steps": 20}]},
    {"id": "speech", "name": "🎤 演讲模式", "desc": "切场景 2 → 音乐静音 → 话筒(通道01)从 75% 经 3 秒渐强到 90%",
     "actions": [{"kind": "scene", "scene": 2}, {"kind": "mute", "ch": 15}],
     "fades": [{"ch": 1, "from": 0.75, "to": 0.90, "duration": 3.0, "steps": 15}]},
    {"id": "leave", "name": "🚪 散场模式", "desc": "全部取消静音 → 音乐(通道15)从 0 经 5 秒渐强到 90%",
     "actions": [{"kind": "unmute_all"},
                 {"kind": "fade_from", "ch": 15, "frm": 0.0, "to": 0.90, "duration": 5.0, "curve": "linear"}],
     "fades": []},
    {"id": "alllive", "name": "🎵 All Live 自动混音演示",
     "desc": "前 8 路推子随音乐节奏自动起伏 12 秒——电动推子玩具同款效果",
     "actions": [], "fades": []},
    {"id": "sync_down", "name": "⏬ 全体推下 (3秒)",
     "desc": "全部推子(32通道+Aux/USB/FX返回+16 Bus+Matrix+8 DCA+主输出)3 秒内同步拉到底,先快后慢收尾",
     "actions": [{"kind": "fade_all", "to": 0.0, "duration": 3.0, "curve": "ease_out"}], "fades": []},
    {"id": "sync_up", "name": "⏫ 全体拉回 (3秒)",
     "desc": "全部推子 3 秒内同步平滑拉回 0 dB 位置,S 形缓入缓出",
     "actions": [{"kind": "fade_all", "to": 0.75, "duration": 3.0, "curve": "smooth"}], "fades": []},
    {"id": "odd_even", "name": "🎸 歌曲过渡 (基数进偶数出)",
     "desc": "奇数通道(1/3/5…)4 秒渐入到 80%,偶数通道同时渐出——换歌时乐器起、伴奏退",
     "actions": [{"kind": "fade_odd_even", "odd_to": 0.8, "even_to": 0.0, "duration": 4.0, "curve": "linear"}], "fades": []},
    {"id": "odd_even_back", "name": "🎹 休息过渡 (偶数进基数出)",
     "desc": "反向渐变:偶数通道渐入到 80%,奇数通道同时渐出——休息时伴奏起、乐器退",
     "actions": [{"kind": "fade_odd_even", "odd_to": 0.0, "even_to": 0.8, "duration": 4.0, "curve": "linear"}], "fades": []},
]
