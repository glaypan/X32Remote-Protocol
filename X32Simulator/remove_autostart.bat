@echo off
rem Remove the auto-start task
schtasks /delete /tn "X32ConsoleService" /f
pause
