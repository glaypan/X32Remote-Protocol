@echo off
rem ============================================================
rem X32 虚拟调音台模拟器 - Windows 双击启动器
rem 与 x32_simulator.py 放在同一文件夹,双击即可运行
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python x32_simulator.py %*
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 x32_simulator.py %*
    ) else (
        echo 未检测到 Python,请到 https://www.python.org/downloads/ 安装 Python 3
        echo 安装时勾选 "Add Python to PATH"
    )
)
pause
