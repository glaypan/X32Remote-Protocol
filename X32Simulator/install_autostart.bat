@echo off
rem Register auto-start at user logon (no admin required)
cd /d "%~dp0"
schtasks /create /tn "X32ConsoleService" /tr "%~dp0start_service.bat" /sc onlogon /f
echo.
echo Done. The console service will start automatically next logon.
echo To remove: run remove_autostart.bat
pause
