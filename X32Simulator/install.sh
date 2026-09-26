#!/bin/bash
# ============================================================
# X32 虚拟调音台模拟器 - macOS 一键安装脚本
# 适用: macOS 10.13+ (含 10.14.6 Mojave)
#
# 用法: 把 X32Simulator 文件夹拷到 Mac 后,终端执行:
#       bash install.sh
#
# 安装内容:
#   1. 检查/安装 Python 3(缺失时自动下载 python.org 官方安装包)
#   2. 复制模拟器到 ~/X32Simulator
#   3. 在桌面创建 "X32模拟器.command" 双击启动器
#   4. 创建卸载脚本 uninstall.sh
# ============================================================
set -e

SIM_NAME="x32_simulator.py"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
DST_DIR="$HOME/X32Simulator"
LAUNCHER="$HOME/Desktop/X32模拟器.command"

echo "=========================================="
echo " X32 虚拟调音台模拟器 · macOS 一键安装"
echo "=========================================="

# ---------- 1. Python 3 检测与安装 ----------
PY3=""
for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' 2>/dev/null; then
            PY3="$cand"
            break
        fi
    fi
done

if [ -z "$PY3" ]; then
    echo "→ 未检测到 Python 3,开始自动安装(需要管理员密码)..."

    ARCH=$(uname -m)
    # 3.11.9 universal2 支持 macOS 10.9+ (含 10.14.6 Mojave)
    PKG_URL="https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
    PKG_TMP="/tmp/python-3.11.9-macos11.pkg"

    echo "→ 下载 Python 3.11.9 官方安装包(约 60MB)..."
    curl -L "$PKG_URL" -o "$PKG_TMP" --retry 3 -sS

    echo "→ 安装 Python(可能弹出管理员密码提示)..."
    sudo installer -pkg "$PKG_TMP" -target /

    rm -f "$PKG_TMP"

    # Apple Silicon 旧路径 / Intel 路径
    for p in /usr/local/bin/python3 /opt/homebrew/bin/python3 \
             "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"; do
        if [ -x "$p" ]; then PY3="$p"; break; fi
    done
    command -v python3 >/dev/null 2>&1 && PY3=python3
fi

if [ -z "$PY3" ]; then
    echo "✗ Python 3 安装失败,请手动到 https://www.python.org/downloads/ 安装后重试"
    exit 1
fi
echo "✓ Python 就绪: $($PY3 --version)  ($($PY3 -c 'import sys;print(sys.executable)'))"

# ---------- 2. 复制模拟器 ----------
if [ ! -f "$SRC_DIR/$SIM_NAME" ]; then
    echo "✗ 找不到 $SRC_DIR/$SIM_NAME,请在 X32Simulator 文件夹内运行本脚本"
    exit 1
fi
mkdir -p "$DST_DIR"
cp "$SRC_DIR/$SIM_NAME" "$DST_DIR/"
echo "✓ 模拟器已安装到 $DST_DIR"

# ---------- 3. 桌面双击启动器 ----------
cat > "$LAUNCHER" <<EOF
#!/bin/bash
cd "$DST_DIR"
exec /usr/bin/env python3 "$DST_DIR/$SIM_NAME" "\$@"
EOF
chmod +x "$LAUNCHER"
echo "✓ 桌面启动器已创建: X32模拟器.command"

# ---------- 4. 卸载脚本 ----------
cat > "$DST_DIR/uninstall.sh" <<EOF
#!/bin/bash
rm -rf "$DST_DIR"
rm -f "$LAUNCHER"
echo "X32 模拟器已卸载(不影响 Python)"
EOF
chmod +x "$DST_DIR/uninstall.sh"

echo ""
echo "=========================================="
echo " ✓ 安装完成!"
echo "=========================================="
echo " 日常使用(二选一):"
echo "   1. 双击桌面上的  X32模拟器.command"
echo "   2. 终端运行:      ~/X32Simulator/x32_simulator.py"
echo ""
echo " 手机 App 设置:"
echo "   调音台 IP 填这台 Mac 的局域网 IP(端口 10023)"
echo "   或直接用 App 的自动发现扫描"
echo ""
echo " 卸载: bash ~/X32Simulator/uninstall.sh"
echo "=========================================="

# 询问是否立即启动
read -p "现在启动模拟器吗? [y/N] " go
if [ "$go" = "y" ] || [ "$go" = "Y" ]; then
    cd "$DST_DIR" && exec "$PY3" "$SIM_NAME"
fi
