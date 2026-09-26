#!/bin/bash
# ============================================================
# X32 虚拟调音台模拟器 - macOS 双击启动器
# 把本文件与 x32_simulator.py 放在同一文件夹,双击即可运行
# (需已安装 Python 3;未安装请先运行 install.sh)
# ============================================================
cd "$(dirname "$0")"

PY3=""
for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$cand" >/dev/null 2>&1; then PY3="$cand"; break; fi
done

if [ -z "$PY3" ]; then
    echo "未检测到 Python 3。"
    echo "请把 X32Simulator 文件夹里的 install.sh 拖进终端回车,或在 python.org 安装 Python 3。"
    read -p "按回车关闭..."
    exit 1
fi

echo "X32 模拟器启动中(端口 10023)... 手机 App 填本机 IP 即可连接"
echo "---------------------------------------------"
"$PY3" x32_simulator.py "$@"
STATUS=$?
echo "---------------------------------------------"
read -p "模拟器已退出(代码 $STATUS)。按回车关闭..."
