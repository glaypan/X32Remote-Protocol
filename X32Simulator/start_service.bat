@echo off
rem X32 console service launcher: web console on 28080 + OSC on 10023 (bridge target auto-reconnects)
cd /d "%~dp0"
start "X32 Console Service" /min "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" x32_simulator.py --port 10023 --web-port 28080
echo Service starting... phone browser: http://THIS-PC-IP:28080
timeout /t 3 >nul
